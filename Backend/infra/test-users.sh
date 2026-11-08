#!/usr/bin/env bash
# Creates the HomeFront hackathon test accounts. Self-signup is off in the pool
# (AllowAdminCreateUserOnly=true), so accounts exist only because an admin runs this script.
#
#   bash Backend/infra/test-users.sh                      dry run: prints the plan, calls nothing
#   bash Backend/infra/test-users.sh --apply              really creates the accounts (needs aws credentials)
#   bash Backend/infra/test-users.sh --apply --reset-passwords   also gives existing accounts new passwords
#
# For each account: create the Cognito user (no email sent), set a permanent password, add it to
# its group, and write its STAFFER#<username> / META item to DynamoDB (active=true), which the
# gateway needs before it accepts that user's sessions. Safe to run again: existing users are not
# created twice and keep their passwords unless --reset-passwords is passed.
#
# Passwords are generated locally and never printed. They go only to test-users.local.txt next to
# this script (git-ignored, mode 600) as "username<TAB>password" lines.
set -euo pipefail
set +x  # never trace: some variables hold passwords

# --- Edit the accounts here: username:group:office (group is staffer or admin) -----------------
TEST_USERS=(
  "bronco:staffer:rep-x"
  "scuawm:staffer:rep-x"
  "a10:staffer:rep-x"
  "aws:staffer:rep-x"
  "admin:admin:rep-x"
)
# -----------------------------------------------------------------------------------------------

REGION=us-east-1
POOL_ID=${COGNITO_USER_POOL_ID:-us-east-1_MLfQpodxG}
TABLE=${MANDATE_TABLE:-mandate}
ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
HELPER=$ROOT/Backend/infra/staffer_accounts.py
PYTHON=${PYTHON:-$ROOT/.venv/bin/python}
PASSWORD_FILE=${TEST_USERS_FILE:-$ROOT/Backend/infra/test-users.local.txt}
DRY_RUN=true
RESET_PASSWORDS=false

while [ $# -gt 0 ]; do
  case "$1" in
    --apply) DRY_RUN=false ;;
    --dry-run) ;;
    --reset-passwords) RESET_PASSWORDS=true ;;
    *) echo "unknown option: $1" >&2; exit 2 ;;
  esac
  shift
done

die() { echo "error: $*" >&2; exit 1; }

# Runs an aws command (never echoed: callers pass passwords only through a private file).
# The first failure stops the whole script.
aws_call() {
  local what=$1; shift
  aws "$@" --region "$REGION" || die "AWS call failed while $what; stopping here. Fix it and re-run (the script is safe to re-run)."
}

# Rewrites PASSWORD_FILE with this user's line replaced, keeping everyone else's. Mode 600 throughout.
save_password() {
  local username=$1 password=$2 tmp
  tmp=$(umask 077 && mktemp "$PASSWORD_FILE.XXXXXX")
  if [ -f "$PASSWORD_FILE" ]; then
    awk -F'\t' -v u="$username" '$1 != u' "$PASSWORD_FILE" > "$tmp"
  fi
  printf '%s\t%s\n' "$username" "$password" >> "$tmp"
  chmod 600 "$tmp"
  mv "$tmp" "$PASSWORD_FILE"
}

[ -x "$PYTHON" ] || PYTHON=python3
for entry in "${TEST_USERS[@]}"; do
  IFS=: read -r _ group _ <<< "$entry"
  case "$group" in staffer|admin) ;; *) die "bad group '$group' in TEST_USERS ($entry)";; esac
done

if [ "$DRY_RUN" = true ]; then
  echo "DRY RUN: nothing is changed. Pass --apply to create the accounts."
  echo "Pool $POOL_ID ($REGION), table $TABLE."
  for entry in "${TEST_USERS[@]}"; do
    IFS=: read -r username group office <<< "$entry"
    echo
    echo "== $username (group $group, office $office)"
    echo "+ aws cognito-idp admin-create-user --user-pool-id $POOL_ID --username $username --message-action SUPPRESS   (only if missing)"
    if [ "$RESET_PASSWORDS" = true ]; then
      echo "+ aws cognito-idp admin-set-user-password --user-pool-id $POOL_ID --username $username --password **** --permanent"
    else
      echo "+ aws cognito-idp admin-set-user-password --user-pool-id $POOL_ID --username $username --password **** --permanent   (only for a new user)"
    fi
    echo "+ aws cognito-idp admin-add-user-to-group --user-pool-id $POOL_ID --username $username --group-name $group"
    echo "+ aws dynamodb put-item --table-name $TABLE --item '$("$PYTHON" "$HELPER" item "$username" "$office" "$group")'"
  done
  echo
  echo "Passwords would be written to $PASSWORD_FILE (mode 600, never printed)."
  exit 0
fi

command -v aws >/dev/null || die "aws CLI not found"
case "$PASSWORD_FILE" in
  "$ROOT"/*) git -C "$ROOT" check-ignore -q "$PASSWORD_FILE" || die "$PASSWORD_FILE is not git-ignored" ;;
esac

WORK_DIR=$(umask 077 && mktemp -d)
trap 'rm -rf "$WORK_DIR"' EXIT  # the password input files never outlive the run

created=() reset=() kept=()
for entry in "${TEST_USERS[@]}"; do
  IFS=: read -r username group office <<< "$entry"
  echo "== $username"

  # "None" means no such user; any aws error stops the script instead of looking like "missing".
  status=$(aws_call "looking up $username" cognito-idp list-users --user-pool-id "$POOL_ID" \
    --filter "username = \"$username\"" --query 'Users[0].UserStatus' --output text)

  set_password=$RESET_PASSWORDS
  if [ "$status" = None ]; then
    aws_call "creating $username" cognito-idp admin-create-user --user-pool-id "$POOL_ID" \
      --username "$username" --message-action SUPPRESS > /dev/null
    set_password=true
    created+=("$username")
  elif [ "$status" = FORCE_CHANGE_PASSWORD ]; then
    set_password=true  # created by an earlier run that stopped before its password was set
    created+=("$username")
  elif [ "$RESET_PASSWORDS" = true ]; then
    reset+=("$username")
  else
    kept+=("$username")
  fi

  if [ "$set_password" = true ]; then
    # The password travels in a private file, never on the command line, so `ps` cannot see it.
    input=$WORK_DIR/$username.json
    password=$("$PYTHON" "$HELPER" password-input "$POOL_ID" "$username" "$input")
    aws_call "setting the password of $username" cognito-idp admin-set-user-password \
      --cli-input-json "file://$input" > /dev/null
    rm -f "$input"
    save_password "$username" "$password"
    unset password
  fi

  aws_call "adding $username to group $group" cognito-idp admin-add-user-to-group \
    --user-pool-id "$POOL_ID" --username "$username" --group-name "$group"
  item=$("$PYTHON" "$HELPER" item "$username" "$office" "$group")
  aws_call "writing STAFFER#$username" dynamodb put-item --table-name "$TABLE" --item "$item"
done

echo
echo "Created: ${created[*]:-none}"
echo "Password reset: ${reset[*]:-none}"
echo "Unchanged password: ${kept[*]:-none}"
echo "Passwords: $PASSWORD_FILE (mode 600). Share them through a password manager, not chat."
