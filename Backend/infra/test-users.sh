#!/usr/bin/env bash
# Creates the HomeFront hackathon test accounts. Self-signup is off in the pool
# (AllowAdminCreateUserOnly=true), so accounts exist only because an admin runs this script.
#
#   bash Backend/infra/test-users.sh                      dry run: prints the plan, calls nothing
#   bash Backend/infra/test-users.sh --apply              really creates the accounts (needs aws credentials)
#   bash Backend/infra/test-users.sh --apply --reset-passwords   also gives existing accounts new passwords
#   bash Backend/infra/test-users.sh --apply --demo-passwords    easy name-based passwords (see below)
#
# For each account: create the Cognito user (no email sent), set a permanent password, add it to
# its group, and write its STAFFER#<username> / META item to DynamoDB (active=true, office, office
# role), which the gateway needs before it accepts that user's sessions. The office role decides
# which tools the assistant may use (docs/ROLES.md); Cedar enforces it. Safe to run again: existing
# users are not created twice and keep their passwords unless --reset-passwords is passed.
#
# Passwords are generated locally and never printed. They go only to test-users.local.txt next to
# this script (git-ignored, mode 600) as "username<TAB>password" lines.
# --demo-passwords: PROOF OF CONCEPT ONLY. Instead of a random password, each new (or, with
# --reset-passwords, each) account gets an easy one from its name, e.g. bronco -> Bronco@2026
# (staffer_accounts.py demo_password). Anyone who reads this file can guess them; never use
# them outside the hackathon demo.
set -euo pipefail
set +x  # never trace: some variables hold passwords

# --- Edit the accounts here: username:group:office:role ------------------------------------------
# group is the Cognito group (admin sees the admin pages); role is the office role from docs/ROLES.md:
# chief_of_staff, caseworker, communications_director, legislative_aide or intern.
TEST_USERS=(
  "bronco:staffer:rep-x:caseworker"
  "scuacm:staffer:rep-x:communications_director"
  "a10:staffer:rep-x:legislative_aide"
  "aws:staffer:rep-x:intern"
  "admin:admin:rep-x:chief_of_staff"
  "maya:staffer:rep-x:caseworker"
  "chiefofstaff:admin:rep-x:chief_of_staff"
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
DEMO_PASSWORDS=false

while [ $# -gt 0 ]; do
  case "$1" in
    --apply) DRY_RUN=false ;;
    --dry-run) ;;
    --reset-passwords) RESET_PASSWORDS=true ;;
    --demo-passwords) DEMO_PASSWORDS=true ;;
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
  IFS=: read -r username group office role <<< "$entry"
  case "$group" in staffer|admin) ;; *) die "bad group '$group' in TEST_USERS ($entry)";; esac
  # Builds the item once up front, so an unknown role stops the script before any AWS call.
  "$PYTHON" "$HELPER" item "$username" "$office" "$group" "$role" > /dev/null 2>&1 || die "bad role '$role' in TEST_USERS ($entry)"
done
PASSWORD_KIND=random
[ "$DEMO_PASSWORDS" = true ] && PASSWORD_KIND=demo

if [ "$DRY_RUN" = true ]; then
  echo "DRY RUN: nothing is changed. Pass --apply to create the accounts."
  echo "Pool $POOL_ID ($REGION), table $TABLE."
  for entry in "${TEST_USERS[@]}"; do
    IFS=: read -r username group office role <<< "$entry"
    echo
    echo "== $username (group $group, office $office, role $role)"
    echo "+ aws cognito-idp admin-create-user --user-pool-id $POOL_ID --username $username --message-action SUPPRESS   (only if missing)"
    if [ "$RESET_PASSWORDS" = true ]; then
      echo "+ aws cognito-idp admin-set-user-password --user-pool-id $POOL_ID --username $username --password **** --permanent"
    else
      echo "+ aws cognito-idp admin-set-user-password --user-pool-id $POOL_ID --username $username --password **** --permanent   (only for a new user)"
    fi
    echo "+ aws cognito-idp admin-add-user-to-group --user-pool-id $POOL_ID --username $username --group-name $group"
    echo "+ aws dynamodb put-item --table-name $TABLE --item '$("$PYTHON" "$HELPER" item "$username" "$office" "$group" "$role")'"
  done
  echo
  echo "Passwords ($PASSWORD_KIND) would be written to $PASSWORD_FILE (mode 600, never printed)."
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
  IFS=: read -r username group office role <<< "$entry"
  echo "== $username ($role)"

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
    if [ "$DEMO_PASSWORDS" = true ]; then
      password=$("$PYTHON" "$HELPER" password-input "$POOL_ID" "$username" "$input" demo)
    else
      password=$("$PYTHON" "$HELPER" password-input "$POOL_ID" "$username" "$input")
    fi
    aws_call "setting the password of $username" cognito-idp admin-set-user-password \
      --cli-input-json "file://$input" > /dev/null
    rm -f "$input"
    save_password "$username" "$password"
    unset password
  fi

  aws_call "adding $username to group $group" cognito-idp admin-add-user-to-group \
    --user-pool-id "$POOL_ID" --username "$username" --group-name "$group"
  item=$("$PYTHON" "$HELPER" item "$username" "$office" "$group" "$role")
  aws_call "writing STAFFER#$username" dynamodb put-item --table-name "$TABLE" --item "$item"
done

echo
echo "Created: ${created[*]:-none}"
echo "Password reset: ${reset[*]:-none}"
echo "Unchanged password: ${kept[*]:-none}"
echo "Passwords ($PASSWORD_KIND): $PASSWORD_FILE (mode 600). Share them through a password manager, not chat."
