#!/bin/bash
# Install unpacman on a Linux computer — Ubuntu, Debian, Mint, Pop!_OS, Fedora,
# Arch, openSUSE — or repair or update an installation, with one line typed in
# a terminal (2026-11-05; the public branch `linux`):
#
#   bash -c "$(curl -fsSL https://raw.githubusercontent.com/Tancredi-Schettini-Gherardini/unpacman/linux/install-unpacman-linux.sh || wget -qO- https://raw.githubusercontent.com/Tancredi-Schettini-Gherardini/unpacman/linux/install-unpacman-linux.sh)"
#
# (curl, or wget where there is no curl: a new Ubuntu has wget only.)
#
# The work is done by install-unpacman-wsl.sh, the installer of Linux inside
# Windows, from the same branch: on a Linux computer it installs the Linux
# programs a reading needs with that Linux's own package manager, puts unpacman
# in ~/.unpacman, makes an entry "Unpacman" in the applications menu and opens
# unpacman in the browser. The copy follows the branch `linux` and looks for
# new versions there by itself. Running the line again is safe: it updates and
# repairs, and never touches papers or readings.
#
# For testing, besides install-unpacman-wsl.sh's own switches: UNPACMAN_BRANCH
# (default `linux`), UNPACMAN_INSTALLER_REF (where the installer is fetched
# from, when not beside this file: a commit, say; default the branch).

set -u

SLUG="Tancredi-Schettini-Gherardini/unpacman"
export UNPACMAN_BRANCH="${UNPACMAN_BRANCH:-linux}"

# Beside this file (a clone, or a folder it was copied into): that one.
here=""
case "${BASH_SOURCE[0]:-}" in
  */*) here=$(cd "$(dirname "${BASH_SOURCE[0]}")" 2>/dev/null && pwd -P) ;;
esac
if [ -n "$here" ] && [ -f "$here/install-unpacman-wsl.sh" ]; then
  exec /bin/bash "$here/install-unpacman-wsl.sh" "$@"
fi

# Run from the one line: fetched from the same branch on GitHub.
url="https://raw.githubusercontent.com/$SLUG/${UNPACMAN_INSTALLER_REF:-$UNPACMAN_BRANCH}/install-unpacman-wsl.sh"
script=""
if command -v curl >/dev/null 2>&1; then
  script=$(curl -fsSL "$url" 2>/dev/null)
fi
if [ -z "$script" ] && command -v wget >/dev/null 2>&1; then
  script=$(wget -qO- "$url" 2>/dev/null)
fi
if [ -z "$script" ]; then
  printf '\n%s\n%s\n' "unpacman could not be installed." \
    "Its installer could not be downloaded ($url). Check the internet connection, then run the same line again." >&2
  exit 1
fi
exec /bin/bash -c "$script" install-unpacman-wsl.sh "$@"
