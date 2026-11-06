#!/bin/bash
# Install unpacman on a Windows computer — inside WSL, Windows' own Linux — or
# repair or update an installation, with one line typed in Ubuntu
# (2026-10-17, branch unpacman_windows; WINDOWS.md says how to get WSL first).
#
# unpacman runs inside WSL 2 — an Ubuntu there — and opens in the Windows
# browser. From a package (unpacman-windows.zip: its install.sh runs this), or
# from GitHub, where unpacman is public (no account needed):
#
#     bash install-unpacman-wsl.sh
#
# From a copy of the repository already in Ubuntu (a clone, say):
#
#     UNPACMAN_REPO=~/atelier bash ~/atelier/install-unpacman-wsl.sh
#
# What it does, inside your own Ubuntu home folder:
#   1. installs the Linux programs a reading needs — bubblewrap and socat, for
#      the sandbox each reading runs in; ripgrep, git, curl, xz and perl; and
#      Poppler's PDF tools, with which a reading takes a figure from the paper —
#      which asks for your Ubuntu password, once;
#   2. installs uv, which fetches the right Python and unpacman's packages;
#   3. puts unpacman in ~/.unpacman, from GitHub or the copy named;
#   4. installs its packages, and puts an Unpacman shortcut in the Start menu;
#   5. opens it. The page then installs Claude Code or Codex, signs you in, and
#      installs LaTeX, each with one click.
#
# Running it again is safe: it brings an installation up to date and repairs
# what is missing. If unpacman is open it is closed while that happens —
# unless a reading is running, which closing it would stop. Your papers and
# readings are never touched. However it was installed, the copy then follows
# unpacman's public repository, where it looks for new versions by itself; a
# copy made before unpacman was public is moved over to it, keeping
# everything — unless it has changes of its own.
#
# On a Linux computer — not inside Windows — the same installer does the same,
# started by install-unpacman-linux.sh (which says which branch: `linux`): the
# Linux programs come from that Linux's own package manager (apt, dnf, pacman
# or zypper), the shortcut is an entry in the applications menu (linux.py),
# and unpacman opens in the background, in the browser, as on a Mac.
#
# For testing: UNPACMAN_DIR, UNPACMAN_REPO, UNPACMAN_FOLLOW (what the copy
# follows), UNPACMAN_BRANCH, UNPACMAN_NO_OPEN=1, UNPACMAN_NO_APT=1 (no step 1),
# UNPACMAN_NO_SHORTCUT=1.

set -u

REPO_SLUG="Tancredi-Schettini-Gherardini/unpacman"
PUBLIC="https://github.com/$REPO_SLUG.git"
REPO="${UNPACMAN_REPO:-$PUBLIC}"
# What the copy follows afterwards, and looks for new versions in: the public
# repository (a package's install.sh says so), or the source named for testing.
FOLLOW="${UNPACMAN_FOLLOW:-${UNPACMAN_REPO:-$PUBLIC}}"
BRANCH="${UNPACMAN_BRANCH:-windows}"
DIR="${UNPACMAN_DIR:-$HOME/.unpacman}"
BIN="$HOME/.local/bin"

bold=""; dim=""; red=""; off=""
if [ -t 1 ]; then
  bold=$(printf '\033[1m'); dim=$(printf '\033[2m'); red=$(printf '\033[31m'); off=$(printf '\033[0m')
fi

say()  { printf '%s\n' "$*"; }
step() { printf '\n%s%s%s\n' "$bold" "$*" "$off"; }
note() { printf '  %s%s%s\n' "$dim" "$*" "$off"; }
fail() {
  printf '\n%s%s%s\n' "$red$bold" "unpacman could not be installed." "$off" >&2
  printf '%s\n' "$*" >&2
  printf '\n%s\n' "Nothing you had was changed. Fix that, then run the same line again." >&2
  exit 1
}

# A Windows program, from Linux: on PATH, where WSL puts Windows' folders, or
# where Windows keeps it (wsl.py windows_program does the same).
windows_exe() {
  command -v "$1" 2>/dev/null && return 0
  for folder in /mnt/c/Windows/System32/WindowsPowerShell/v1.0 /mnt/c/Windows/System32; do
    if [ -x "$folder/$1" ]; then printf '%s\n' "$folder/$1"; return 0; fi
  done
  return 1
}

# Open a page in the Windows browser, from Linux (wsl.py open_url does the same).
open_page() {
  ps=$(windows_exe powershell.exe)
  if [ -n "$ps" ]; then
    (cd /mnt/c 2>/dev/null; "$ps" -NoLogo -NoProfile -NonInteractive -Command \
      "Start-Process '$1'" >/dev/null 2>&1) && return 0
  fi
  if command -v wslview >/dev/null 2>&1; then wslview "$1" >/dev/null 2>&1 && return 0; fi
  return 1
}

# ------------------------------------------------------------------ checks

[ "$(uname -s)" = "Linux" ] \
  || fail "This installer is for Linux inside Windows (WSL). On a Mac, use install-unpacman.sh."
if [ "$(id -u)" -eq 0 ]; then
  fail "Run it as yourself, without sudo: it installs into your own home folder."
fi
release=""
[ -r /proc/sys/kernel/osrelease ] && release=$(tr 'A-Z' 'a-z' </proc/sys/kernel/osrelease)
wsl=""
if [ -n "${WSL_DISTRO_NAME:-}" ] || case "$release" in *microsoft*) true ;; *) false ;; esac; then
  # WSL 1 has no Linux kernel: what it calls one is "4.4.0-<build>-Microsoft".
  # WSL 2's is "…-microsoft-standard[-WSL2]", or one of the person's own.
  case "$release" in
    *microsoft-standard*|*wsl2*) wsl=2 ;;
    *-microsoft) wsl=1 ;;
    *) wsl=2 ;;
  esac
fi
if [ "$wsl" = 1 ]; then
  fail "This Ubuntu runs on WSL 1, which cannot give a reading its sandbox. In Windows, open PowerShell and type:  wsl --set-version ${WSL_DISTRO_NAME:-Ubuntu} 2  — then run this line again."
fi
arch=$(uname -m)

say ""
say "${bold}Installing unpacman${off}"
if [ -n "$wsl" ]; then
  say "Everything goes into your own Ubuntu home folder. It takes a few minutes, most of"
  say "them downloading. Step 1 asks for your Ubuntu password, once."
else
  say "Everything goes into your own home folder. It takes a few minutes, most of them"
  say "downloading. Step 1 may ask for your password, once."
  note "This is a Linux computer, not Linux inside Windows: unpacman gets an entry in"
  note "your applications menu, and opens in your browser."
fi

mkdir -p "$BIN"
export PATH="$BIN:$PATH"

# ----------------------------------------------- 1. Linux programs

step "1. The Linux programs a reading needs"
# Which package manager this Linux has: Ubuntu's and Debian's (apt — always,
# under WSL), Fedora's (dnf), Arch's (pacman) or openSUSE's (zypper).
manager=""
for candidate in apt-get dnf pacman zypper; do
  if command -v "$candidate" >/dev/null 2>&1; then manager="$candidate"; break; fi
done
need=""
packages=""
for tool in bwrap socat rg git curl xz perl pdftoppm; do
  if ! command -v "$tool" >/dev/null 2>&1; then
    need="$need $tool"
    case "$tool:$manager" in
      bwrap:*)           packages="$packages bubblewrap" ;;
      rg:*)              packages="$packages ripgrep" ;;
      xz:apt-get|xz:)    packages="$packages xz-utils" ;;
      pdftoppm:pacman)   packages="$packages poppler" ;;
      pdftoppm:zypper)   packages="$packages poppler-tools" ;;
      pdftoppm:*)        packages="$packages poppler-utils" ;;   # pdftoppm, pdftotext, pdfinfo
      *)                 packages="$packages $tool" ;;
    esac
  fi
done
if [ -z "$need" ]; then
  note "Already installed."
elif [ "${UNPACMAN_NO_APT:-0}" = "1" ]; then
  note "Not installed (UNPACMAN_NO_APT):$packages"
elif [ -z "$manager" ]; then
  fail "This Linux has no package manager unpacman knows (apt, dnf, pacman or zypper). Install these with its own:$packages — then run this line again."
else
  say "  Installing:$packages"
  if [ -n "$wsl" ]; then
    say "  Ubuntu asks for your password here — the one you chose when you set Ubuntu up."
  else
    say "  Your password may be asked for here: installing programs needs it."
  fi
  # shellcheck disable=SC2086
  case "$manager" in
    apt-get)
      sudo apt-get update -qq >/dev/null 2>&1 || true
      sudo apt-get install -y -qq $packages >/dev/null ;;
    dnf)
      sudo dnf install -y -q $packages >/dev/null ;;
    pacman)
      sudo pacman -S --needed --noconfirm $packages >/dev/null ;;
    zypper)
      sudo zypper --non-interactive --quiet install $packages >/dev/null ;;
  esac \
    || fail "These Linux programs could not be installed:$packages. Check the internet connection, then run this line again."
  note "Installed."
fi
git --version >/dev/null 2>&1 || fail "git does not run here, so unpacman cannot be fetched."

# ------------------------------------------------------------------ 2. uv

step "2. uv, which installs Python and unpacman's packages"
UV=""
for candidate in "$(command -v uv 2>/dev/null)" "$BIN/uv" "$HOME/.cargo/bin/uv"; do
  if [ -n "$candidate" ] && [ -x "$candidate" ]; then UV="$candidate"; break; fi
done
if [ -n "$UV" ]; then
  note "Already installed: $UV"
else
  curl -LsSf https://astral.sh/uv/install.sh | env UV_INSTALL_DIR="$BIN" sh >/dev/null 2>&1 \
    || fail "uv could not be downloaded. Check the internet connection."
  UV="$BIN/uv"
  [ -x "$UV" ] || fail "uv was downloaded but is not where expected ($UV)."
  note "Installed."
fi

# ------------------------------------------------------------ 3. unpacman

step "3. unpacman itself"

# Where unpacman comes from now: the public repository, which needs no GitHub
# account — or the package it is installed from (its install.sh names it).
if GIT_TERMINAL_PROMPT=0 git ls-remote --heads "$REPO" "$BRANCH" 2>/dev/null | grep -q .; then
  note "unpacman can be read where it comes from; no GitHub account needed."
else
  case "$REPO" in
    https://github.com/*) fail "unpacman could not be reached on GitHub. Check the internet connection." ;;
    *) fail "unpacman could not be read from $REPO (branch $BRANCH)." ;;
  esac
fi

# Open right now? Its files must not change under it, so it is asked to close
# first. It refuses while a reading runs: then nothing changes. It answers only
# to the control token in its record (security.py), and only here: never
# through a proxy.
running=0
url=""
token=""
record="$DIR/var/unpacman-server.json"
if [ -f "$record" ]; then
  url=$(sed -n 's/.*"url": *"\([^"]*\)".*/\1/p' "$record")
  token=$(sed -n 's/.*"token": *"\([^"]*\)".*/\1/p' "$record")
  if [ -n "$url" ] && curl -fsS -m 2 --noproxy '*' -H "X-Unpacman-Token: $token" \
       "${url}api/hello" 2>/dev/null | grep -q '"unpacman"'; then
    running=1
  fi
fi
if [ "$running" = 1 ] && [ -d "$DIR/.git" ]; then
  answer=$(curl -sS -m 10 --noproxy '*' -X POST -H 'Content-Type: application/json' \
           -H "X-Unpacman-Token: $token" -d '{}' -w ' %{http_code}' "${url}api/quit" 2>/dev/null)
  case "$answer" in
    *" 200")
      note "unpacman was open: it is closed while it is updated."
      for _ in $(seq 1 30); do
        curl -fsS -m 1 --noproxy '*' -H "X-Unpacman-Token: $token" "${url}api/hello" \
          >/dev/null 2>&1 || break
        sleep 0.5
      done
      running=0 ;;
    *reading*)
      note "A reading is running in unpacman, and closing it would stop the reading, so"
      note "it is left as it is. Run this line again once the reading has finished." ;;
    *)
      note "unpacman is open and could not be closed from here, so it is left as it is."
      note "Quit it (Advanced settings › This computer), then run this line again." ;;
  esac
fi

# From now on the copy follows FOLLOW's branch — that branch only: the public
# repository's others hold its front page, its website and the Mac version —
# with $1, the version it now has, as the last one seen there.
follow() {
  git -C "$DIR" remote set-url origin "$FOLLOW" 2>/dev/null \
    || git -C "$DIR" remote add origin "$FOLLOW"
  git -C "$DIR" config remote.origin.fetch "+refs/heads/$BRANCH:refs/remotes/origin/$BRANCH"
  git -C "$DIR" update-ref "refs/remotes/origin/$BRANCH" "$1"
  git -C "$DIR" branch --quiet --set-upstream-to="origin/$BRANCH" "$BRANCH" >/dev/null 2>&1
  # A public repository needs no sign-in: a helper left by an installation from
  # the private one goes, and so does the package an older one kept to follow.
  git -C "$DIR" config --local --unset-all credential.https://github.com.helper 2>/dev/null
  rm -rf "$HOME/.unpacman-package"
}

if [ -d "$DIR/.git" ] && [ "$running" = 1 ]; then
  :
elif [ -d "$DIR/.git" ]; then
  note "Already here, in $DIR. Bringing it up to date…"
  if [ -n "$(git -C "$DIR" status --porcelain --untracked-files=no)" ]; then
    note "It has edited files of its own, so it is left as it is."
  else
    # The new version — nothing changes yet.
    GIT_TERMINAL_PROMPT=0 git -C "$DIR" fetch --quiet "$REPO" "+refs/heads/$BRANCH:refs/unpacman/new" \
      2>/dev/null || fail "The newest version could not be fetched."
    new=$(git -C "$DIR" rev-parse --verify --quiet "refs/unpacman/new^{commit}")
    git -C "$DIR" update-ref -d refs/unpacman/new
    [ -n "$new" ] || fail "The new version of unpacman could not be read."
    was=$(git -C "$DIR" rev-parse --abbrev-ref HEAD 2>/dev/null)
    # Commits of its own: what its branch has that it did not get from where it
    # came from. Unknown (empty) for a branch that follows nothing.
    own=$(git -C "$DIR" rev-list --count '@{u}..HEAD' 2>/dev/null)
    if git -C "$DIR" merge-base --is-ancestor HEAD "$new"; then
      # The same history, moved on (or as it was).
      git -C "$DIR" checkout --quiet -B "$BRANCH" "$new" 2>/dev/null \
        || fail "unpacman's files in $DIR could not be updated."
      follow "$new"
    elif git -C "$DIR" merge-base --is-ancestor "$new" HEAD; then
      note "It is newer than this version, so it is left at its own."
      follow "$(git -C "$DIR" rev-parse HEAD)"
    elif ! git -C "$DIR" merge-base HEAD "$new" >/dev/null 2>&1 && [ "$own" = "0" ]; then
      # Another history altogether: unpacman as it was before it was public.
      # With nothing of its own, the copy moves to the public one.
      git -C "$DIR" checkout --quiet -B "$BRANCH" "$new" 2>/dev/null \
        || fail "unpacman's files in $DIR could not be updated."
      follow "$new"
      if [ -n "$was" ] && [ "$was" != "$BRANCH" ] && [ "$was" != "HEAD" ]; then
        git -C "$DIR" branch --quiet -D "$was" 2>/dev/null
        git -C "$DIR" update-ref -d "refs/remotes/origin/$was" 2>/dev/null
      fi
      note "From now on it follows unpacman's public version."
    else
      note "It has changes of its own, so it is left at its version."
    fi
  fi
elif [ -e "$DIR" ]; then
  fail "$DIR already exists and is not a copy of unpacman. Move it aside, then run this line again."
else
  GIT_TERMINAL_PROMPT=0 git clone --quiet --single-branch --branch "$BRANCH" "$REPO" "$DIR" \
    || fail "unpacman could not be fetched."
  follow "$(git -C "$DIR" rev-parse HEAD)"
  note "Fetched into $DIR."
fi

# ------------------------------------------------- 4. packages and shortcut

if [ -n "$wsl" ]; then
  step "4. unpacman's packages, and its shortcut"
else
  step "4. unpacman's packages, and its menu entry"
fi
note "Downloading Python and unpacman's packages (about 60 MB the first time)…"
(cd "$DIR" && "$UV" sync --frozen --quiet) || fail "unpacman's packages could not be installed. Check the internet connection."
note "Packages installed."
shortcut_ready=0
menu_ready=0
if [ "$wsl" = 2 ] && [ "${UNPACMAN_NO_SHORTCUT:-0}" != "1" ]; then
  # From inside the copy: unpacman finds its folder from where it is run first
  # (config.find_root), so run from inside another copy the shortcut would open that.
  if (cd "$DIR" && "$DIR/.venv/bin/python" -m deslopify.app --make-app); then
    shortcut_ready=1
  else
    note "The Start-menu shortcut could not be made (what it said is above). unpacman"
    note "still opens from Ubuntu:  $DIR/.venv/bin/python -m deslopify.app"
  fi
elif [ -z "$wsl" ] && [ "${UNPACMAN_NO_SHORTCUT:-0}" != "1" ]; then
  # A Linux computer: an entry in the applications menu (linux.py), made from
  # inside the copy, for the reason above.
  if (cd "$DIR" && "$DIR/.venv/bin/python" -m deslopify.app --make-app); then
    menu_ready=1
  else
    note "The menu entry could not be made (what it said is above). unpacman still"
    note "opens from a terminal:  cd $DIR && .venv/bin/python -m deslopify.app --app"
  fi
fi

# ------------------------------------------------------------------ 5. open

step "Done."
if [ "$shortcut_ready" = 1 ]; then
  say "  Open unpacman any time from the Start menu: type ${bold}unpacman${off} and press Enter."
  say "  It opens in a window of its own — keep that window open while you use unpacman."
elif [ "$menu_ready" = 1 ]; then
  # As for the Start menu: said only once the entry is really there.
  say "  Open unpacman any time from your applications menu: search for ${bold}unpacman${off}."
  say "  It runs in the background, in your browser; Quit is in its Advanced settings."
elif [ -z "$wsl" ]; then
  say "  It runs in the background, in your browser; Quit is in its Advanced settings."
fi
say "  It tells you when a new version is ready, and takes it in with one click."
say ""
if [ "${UNPACMAN_NO_OPEN:-0}" != "1" ] && [ -z "$wsl" ]; then
  # As the menu entry does: in the background, then the browser — this window
  # can be closed.
  say "  It is opening now, in your browser. The first page installs what a reading needs."
  say ""
  cd "$DIR" && exec "$DIR/.venv/bin/python" -m deslopify.app --app
elif [ "${UNPACMAN_NO_OPEN:-0}" != "1" ]; then
  say "  It is opening now, in this window. The first page installs what a reading needs."
  say ""
  cd "$DIR" && exec "$DIR/.venv/bin/python" -m deslopify.app
fi
