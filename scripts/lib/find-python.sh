# Sourced by the setup scripts. Prints the first Python 3.12+ on PATH:
# python3.12 first (on some Macs the default python3 is an older or broken build), then python3.
find_python() {
  local candidate
  for candidate in python3.12 python3; do
    if command -v "$candidate" >/dev/null 2>&1 &&
      "$candidate" -c 'import sys; sys.exit(sys.version_info < (3, 12))' >/dev/null 2>&1; then
      command -v "$candidate"
      return 0
    fi
  done
  echo "error: HomeFront needs Python 3.12 or newer, and none was found on PATH." >&2
  echo "Install it from https://www.python.org/downloads/ (or: brew install python@3.12), then open a new terminal." >&2
  return 1
}
