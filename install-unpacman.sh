#!/bin/bash
# Install unpacman on a Mac — or repair or update an installation — with one line.
#
# From a package (a zip made by `python -m deslopify.app --make-package`, with
# unpacman.bundle beside this file), no GitHub account is needed:
#
#     bash ~/Downloads/unpacman-<date>/install-unpacman.sh
#
# From GitHub, where unpacman is public (no account needed either):
#
#     /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Tancredi-Schettini-Gherardini/unpacman/mac/install-unpacman.sh)"
#
# What it does, all inside your own home folder, with no administrator password:
#   1. makes sure Apple's command-line tools are there (git comes with them);
#   2. installs uv, which fetches the right Python and unpacman's packages;
#   3. puts unpacman in ~/.unpacman, from the package or from GitHub;
#   4. installs its packages, and puts an Unpacman icon in ~/Applications;
#   5. opens it. The page then installs Claude Code or Codex, signs you in,
#      and installs LaTeX, each with one click.
#
# Running it again is safe: it brings an existing installation up to date (a
# newer package, or GitHub) and repairs what is missing. If unpacman is open it
# is closed while that happens and opened again at the end — unless a reading
# is running, which closing it would stop. Your papers and readings are never
# touched. However it was installed, the copy then follows unpacman's public
# repository, where it looks for new versions by itself (updates.py); a copy
# made before unpacman was public, from a package or from the private
# repository, is moved over to it, keeping everything — unless it has changes
# of its own.
#
# For testing, these can be set: UNPACMAN_DIR, UNPACMAN_REPO (a source other
# than GitHub, which the copy then follows; also turns off the package beside
# this file), UNPACMAN_FOLLOW (what the copy follows instead), UNPACMAN_BRANCH,
# UNPACMAN_APPS (where the icon goes), UNPACMAN_NO_OPEN=1.
#
# Written for the bash every Mac has (3.2): no newer syntax.

set -u

REPO_SLUG="Tancredi-Schettini-Gherardini/unpacman"
PUBLIC="https://github.com/$REPO_SLUG.git"
REPO="${UNPACMAN_REPO:-$PUBLIC}"
# What the copy follows afterwards, and looks for new versions in: the public
# repository, or the source named for testing.
FOLLOW="${UNPACMAN_FOLLOW:-${UNPACMAN_REPO:-$PUBLIC}}"
BRANCH="${UNPACMAN_BRANCH:-mac}"
DIR="${UNPACMAN_DIR:-$HOME/.unpacman}"
APPS="${UNPACMAN_APPS:-$HOME/Applications}"
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

# ------------------------------------------------------------------ checks

[ "$(uname -s)" = "Darwin" ] || fail "This installer is for Macs. On Linux, follow the README."
if [ "$(id -u)" -eq 0 ]; then
  fail "Run it as yourself, without sudo: it installs into your own home folder."
fi
macos=$(sw_vers -productVersion 2>/dev/null || echo 0)
major=${macos%%.*}
if [ "${major:-0}" -lt 12 ] 2>/dev/null; then
  fail "This Mac runs macOS $macos; unpacman needs macOS 12 (Monterey) or later."
fi
arch=$(uname -m)
if [ "$arch" = "x86_64" ] && [ "$(sysctl -n sysctl.proc_translated 2>/dev/null)" = "1" ]; then
  arch="arm64"          # a Terminal running under Rosetta on an Apple-silicon Mac
fi

say ""
say "${bold}Installing unpacman${off}"
say "Everything goes into your own home folder; no password is needed. It takes a few"
say "minutes, most of them downloading. You can leave this window while it works."

mkdir -p "$BIN"
export PATH="$BIN:$PATH"

# ----------------------------------------------- 1. Apple's command-line tools

step "1. Apple's command-line tools"
if xcode-select -p >/dev/null 2>&1; then
  note "Already installed."
elif command -v git >/dev/null 2>&1 && [ "$(command -v git)" != "/usr/bin/git" ]; then
  note "Not installed, but another git is: $(command -v git)."
else
  say "  macOS will now ask to install its command-line tools, which include git."
  say "  ${bold}Click Install${off} in the window that appears, and agree to the licence."
  say "  The download takes 5 to 15 minutes; this window carries on by itself afterwards."
  xcode-select --install >/dev/null 2>&1 || true
  waited=0
  until xcode-select -p >/dev/null 2>&1 && [ -x "$(xcode-select -p)/usr/bin/git" ]; do
    sleep 10
    waited=$((waited + 10))
    if [ $((waited % 60)) -eq 0 ]; then note "Still waiting for the command-line tools ($((waited / 60)) min)…"; fi
    if [ "$waited" -ge 3600 ]; then
      fail "The command-line tools did not finish installing within an hour. If the window was closed, run \`xcode-select --install\` in Terminal, wait for it to finish, then run this line again."
    fi
  done
  note "Installed."
fi
git --version >/dev/null 2>&1 || fail "git does not run on this Mac, so unpacman cannot be fetched."

# ------------------------------------------------------------------ 2. uv

step "2. uv, which installs Python and unpacman's packages"
UV=""
for candidate in "$(command -v uv 2>/dev/null)" "$BIN/uv" "$HOME/.cargo/bin/uv" /opt/homebrew/bin/uv /usr/local/bin/uv; do
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

# A package carries unpacman beside this installer (unpacman.bundle): it is
# installed from there, with nothing to download for unpacman itself. Either
# way the copy then follows unpacman's public repository (FOLLOW), where it
# looks for new versions by itself.
HERE=""
if [ -f "$0" ]; then HERE=$(cd "$(dirname "$0")" 2>/dev/null && pwd); fi
BUNDLE=""
if [ -z "${UNPACMAN_REPO:-}" ] && [ -n "$HERE" ] && [ -f "$HERE/unpacman.bundle" ]; then
  BUNDLE="$HERE/unpacman.bundle"
fi
# Where a copy installed from a package before unpacman was public kept that
# package, which it followed instead of GitHub. Removed once it follows GitHub.
STORE="$DIR/var/package"

if [ -n "$BUNDLE" ]; then
  note "From the package beside this installer: no GitHub account needed."
elif GIT_TERMINAL_PROMPT=0 git ls-remote --heads "$REPO" "$BRANCH" 2>/dev/null | grep -q .; then
  note "From unpacman's public repository: no GitHub account needed."
else
  fail "unpacman could not be reached on GitHub. Check the internet connection."
fi

# Open right now? Its files must not change under it — a page newer than the
# server showing it would misbehave — so it is asked to close first, and is
# opened again at the end. It refuses while a reading runs: then nothing changes.
# It answers only to the control token in its record (security.py, since
# 2026-10-15; until 2026-10-20 this asked without it, was refused, and took
# unpacman for closed), and only here: never through a proxy.
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
      note "unpacman was open: it is closed while it is updated, and opens again at the end."
      for _ in 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15 16 17 18 19 20 21 22 23 24 25 26 27 28 29 30; do
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
      note "Quit it (Advanced settings › This computer › Quit), then run this line again." ;;
  esac
fi

# From now on the copy follows FOLLOW's branch — that branch only: the public
# repository's others hold its front page, its website and the Windows version
# — with $1, the version it now has, as the last one seen there.
follow() {
  git -C "$DIR" remote set-url origin "$FOLLOW" 2>/dev/null \
    || git -C "$DIR" remote add origin "$FOLLOW"
  git -C "$DIR" config remote.origin.fetch "+refs/heads/$BRANCH:refs/remotes/origin/$BRANCH"
  git -C "$DIR" update-ref "refs/remotes/origin/$BRANCH" "$1"
  git -C "$DIR" branch --quiet --set-upstream-to="origin/$BRANCH" "$BRANCH" >/dev/null 2>&1
  # A public repository needs no sign-in: a helper left by an installation from
  # the private one goes, and so does an older package kept to follow.
  git -C "$DIR" config --local --unset-all credential.https://github.com.helper 2>/dev/null
  rm -rf "$STORE"
}

if [ -d "$DIR/.git" ] && [ "$running" = 1 ]; then
  :
elif [ -d "$DIR/.git" ]; then
  note "Already here, in $DIR. Bringing it up to date…"
  if [ -n "$(git -C "$DIR" status --porcelain --untracked-files=no)" ]; then
    note "It has edited files of its own, so it is left as it is."
  else
    # The new version, from the package or the repository — nothing changes yet.
    if [ -n "$BUNDLE" ]; then
      git -C "$DIR" fetch --quiet "$BUNDLE" "+refs/heads/$BRANCH:refs/unpacman/new" 2>/dev/null \
        || fail "The package's version of unpacman could not be read."
    else
      GIT_TERMINAL_PROMPT=0 git -C "$DIR" fetch --quiet "$REPO" "+refs/heads/$BRANCH:refs/unpacman/new" \
        2>/dev/null || fail "The newest version could not be fetched."
    fi
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
elif [ -n "$BUNDLE" ]; then
  git clone --quiet --single-branch --branch "$BRANCH" "$BUNDLE" "$DIR" 2>/dev/null \
    || fail "unpacman could not be unpacked from the package. Ask for a new package."
  follow "$(git -C "$DIR" rev-parse HEAD)"
  note "Unpacked into $DIR."
else
  GIT_TERMINAL_PROMPT=0 git clone --quiet --single-branch --branch "$BRANCH" "$REPO" "$DIR" \
    || fail "unpacman could not be fetched from GitHub."
  follow "$(git -C "$DIR" rev-parse HEAD)"
  note "Fetched into $DIR."
fi
# Which package it now is, for the page and the report — only if it is.
if [ -n "$BUNDLE" ] && [ -f "$HERE/package.json" ]; then
  packaged=$(sed -n 's/.*"commit": *"\([0-9a-f]*\)".*/\1/p' "$HERE/package.json")
  if [ -n "$packaged" ] && [ "$(git -C "$DIR" rev-parse HEAD 2>/dev/null)" = "$packaged" ]; then
    mkdir -p "$DIR/var" && cp "$HERE/package.json" "$DIR/var/unpacman-package.json"
  fi
fi

# ------------------------------------------------------ 4. packages and icon

step "4. unpacman's packages, and its icon"
note "Downloading Python and unpacman's packages (about 60 MB the first time)…"
(cd "$DIR" && "$UV" sync --frozen --quiet) || fail "unpacman's packages could not be installed. Check the internet connection."
note "Packages installed."
# From inside the copy: unpacman finds its folder from where it is run first
# (config.find_root), so run from inside another copy the icon would open that.
(cd "$DIR" && "$DIR/.venv/bin/python" -m deslopify.app --make-app --apps-dir "$APPS" >/dev/null) \
  || fail "The Unpacman icon could not be made."
note "The Unpacman icon is in $APPS."

# ------------------------------------------------------------------ 5. open

step "Done."
say "  Open unpacman any time from Launchpad, or press ⌘ Space, type ${bold}unpacman${off},"
say "  and press Return. To keep it in the Dock, drag it there from Applications."
say "  It tells you when a new version is ready, and takes it in with one click."
say ""
if [ "${UNPACMAN_NO_OPEN:-0}" != "1" ]; then
  say "  It is opening now. The first page installs what a reading needs: one click each."
  say ""
  open "$APPS/Unpacman.app" 2>/dev/null || (cd "$DIR" && "$DIR/.venv/bin/python" -m deslopify.app --app)
fi
