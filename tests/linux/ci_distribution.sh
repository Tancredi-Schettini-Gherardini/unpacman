#!/bin/bash
# unpacman installed on another Linux, as a person on it would (2026-11-05;
# deslopify/public/github/linux.yml runs this as root in a fresh container of
# debian:12, fedora:41 or archlinux): what a desktop of that Linux already has
# (sudo, a way to download, certificates), a person with sudo, the one line,
# then unpacman's own check — its packages installed by that Linux's own
# package manager, its sandbox working, its menu entry made. Nothing is spent.
#   SHA, REPO: the commit published, and the public repository.
set -eu
. /etc/os-release
echo "== $PRETTY_NAME"

if command -v apt-get >/dev/null 2>&1; then
  # A Debian desktop has wget, not curl: the one line must manage with wget.
  apt-get update -qq >/dev/null
  apt-get install -y -qq sudo wget ca-certificates >/dev/null
elif command -v dnf >/dev/null 2>&1; then
  dnf install -y -q sudo curl ca-certificates >/dev/null
elif command -v pacman >/dev/null 2>&1; then
  pacman-key --init >/dev/null 2>&1 || true
  pacman -Sy --noconfirm --needed archlinux-keyring >/dev/null
  pacman -S --noconfirm --needed sudo curl ca-certificates >/dev/null
fi

useradd -m -s /bin/bash ada
echo 'ada ALL=(ALL) NOPASSWD: ALL' > /etc/sudoers.d/ada
chmod 440 /etc/sudoers.d/ada

cat > /home/ada/install.sh <<EOF
export UNPACMAN_NO_OPEN=1 UNPACMAN_INSTALLER_REF=$SHA
url=https://raw.githubusercontent.com/$REPO/$SHA/install-unpacman-linux.sh
bash -c "\$(curl -fsSL "\$url" 2>/dev/null || wget -qO- "\$url")"
EOF
chown ada: /home/ada/install.sh
# sudo, not su: a container of Fedora has no su (its desktop has).
sudo -iu ada bash /home/ada/install.sh

# Getting ready's own summary, from the installed copy (it exits non-zero: no
# AI is installed here, which is as it should be).
sudo -iu ada bash -c 'cd ~/.unpacman && .venv/bin/python -m deslopify.app --check' > /tmp/check.txt 2>&1 || true
cat /tmp/check.txt
status=0
grep -q "Protection for readings (bubblewrap): ready" /tmp/check.txt \
  || { echo "FAIL the sandbox does not work here"; status=1; }
[ -f /home/ada/.local/share/applications/unpacman.desktop ] \
  || { echo "FAIL no menu entry"; status=1; }
[ -f /home/ada/.unpacman/var/unpacman-app.json ] \
  || { echo "FAIL not marked as installed"; status=1; }
for tool in bwrap socat rg git curl xz perl pdftoppm; do
  command -v "$tool" >/dev/null 2>&1 || { echo "FAIL $tool missing"; status=1; }
done
[ "$status" = 0 ] && echo "== installed on $PRETTY_NAME: ok"
exit "$status"
