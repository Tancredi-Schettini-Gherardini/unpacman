"""Publishing: the private repository's branches made into the public one's.

unpacman's public repository (github.com/Tancredi-Schettini-Gherardini/unpacman)
is where every copy looks for new versions (updates.py), and where its
packages are downloaded. It has four branches, each its own history:

    main      the front page: README.md (how to get unpacman) and LICENSE
    mac       the code a Mac copy follows        (from the private `unpackman`)
    windows   the code a Windows copy follows    (from the private `unpacman_windows`)
    website   unpacman's website (GitHub Pages)

Each publication adds one commit on top of each branch that changed — a
snapshot of the private branch as committed, without the developers' notes
(EXCLUDED) — so a copy always moves forward (`git merge --ff-only`) and the
public history holds only published versions, under the publisher's name and
GitHub's no-reply address: no private commit, message or address is ever in
it, because no snapshot has a private commit as its parent.

The work is done in a bare clone of the public repository (`--public`,
~/Code/unpacman.git by default), never on GitHub itself: pushing, and making
the release whose zips the website links to, come after, once looked at.

    python -m deslopify.app.publish --message "What is new, in a line" [--packages FOLDER]
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

PUBLIC_URL = "git@github.com:Tancredi-Schettini-Gherardini/unpacman.git"
PUBLIC_DIR = Path("~/Code/unpacman.git")

# Who publishes: the name GitHub shows, and its no-reply address, which links
# the commits to the account without publishing an e-mail address.
NAME = "Tancredi Schettini Gherardini"
EMAIL = "149483324+Tancredi-Schettini-Gherardini@users.noreply.github.com"

# public branch -> the private branch its code comes from.
CODE = {"mac": "unpackman", "windows": "unpacman_windows"}
# Where, in the private `unpackman`, the other two branches are kept.
FRONT = "deslopify/public/README.md"
WEBSITE = "deslopify/public/website"
LICENSE = "LICENSE"

# The developers' working notes, which stay private — and the front page and
# website, which have branches of their own.
EXCLUDED = (
    "CLAUDE.md",
    "WINDOWS.md",
    "deslopify/DESIGN.md",
    "deslopify/ISSUES.md",
    "docs/PLAN.md",
    "docs/onboarding-message.md",
    "docs/unpacman-onboarding-message.md",
    "deslopify/public",
)

# A public commit names its publisher only, under GitHub's no-reply address:
# no co-author line, and no e-mail address in its message but such a one.
_FORBIDDEN = re.compile(r"co-authored-by", re.IGNORECASE)
_NO_REPLY = re.compile(r"[0-9]+\+[A-Za-z0-9-]+@users\.noreply\.github\.com")
_ADDRESS = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")


def _addresses(text: str) -> list[str]:
    """The e-mail addresses in `text` that are not GitHub no-reply ones."""
    return [a for a in _ADDRESS.findall(text) if not _NO_REPLY.fullmatch(a)]


class PublishError(RuntimeError):
    """A publication that could not be made, said plainly."""


@dataclass(slots=True)
class Published:
    branch: str
    before: str | None          # the public branch's tip before, if any
    after: str                  # its tip now (the same when nothing changed)

    @property
    def changed(self) -> bool:
        return self.before != self.after


def _git(cwd: Path, *args: str, env: dict[str, str] | None = None,
         stdin: str | None = None) -> str:
    done = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True,
                          input=stdin, env=env, check=False,
                          **({} if stdin is not None else {"stdin": subprocess.DEVNULL}))
    if done.returncode != 0:
        raise PublishError(f"git {' '.join(args[:2])} failed: "
                           + (done.stderr.strip().splitlines() or ["no reason given"])[-1])
    return done.stdout.strip()


def _tip(repo: Path, ref: str) -> str | None:
    done = subprocess.run(["git", "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}"],
                          cwd=repo, capture_output=True, text=True,
                          stdin=subprocess.DEVNULL, check=False)
    return done.stdout.strip() or None


def code_tree(private: Path, ref: str) -> str:
    """The tree of `ref` without EXCLUDED, written to the private repository's
    objects; it must hold the licence."""
    with tempfile.TemporaryDirectory(prefix="unpacman-publish-") as raw:
        env = os.environ | {"GIT_INDEX_FILE": str(Path(raw) / "index")}
        _git(private, "read-tree", ref, env=env)
        for path in EXCLUDED:
            # -f: this index is the publication's own, never the checkout's,
            # whatever the files checked out hold.
            _git(private, "rm", "-r", "-q", "-f", "--cached", "--ignore-unmatch", "--", path,
                 env=env)
        tree = _git(private, "write-tree", env=env)
    if not _tip_has(private, tree, LICENSE):
        raise PublishError(f"{ref} has no {LICENSE}: the public code must carry it.")
    return tree


def _tip_has(repo: Path, tree: str, path: str) -> bool:
    done = subprocess.run(["git", "cat-file", "-e", f"{tree}:{path}"], cwd=repo,
                          capture_output=True, stdin=subprocess.DEVNULL, check=False)
    return done.returncode == 0


def front_tree(private: Path, ref: str) -> str:
    """The front page's tree: README.md and LICENSE, from `ref`."""
    entries = []
    for source, name in ((FRONT, "README.md"), (LICENSE, "LICENSE")):
        if not _tip_has(private, ref, source):
            raise PublishError(f"{ref} has no {source} for the front page.")
        blob = _git(private, "rev-parse", f"{ref}:{source}")
        entries.append(f"100644 blob {blob}\t{name}\n")
    return _git(private, "mktree", stdin="".join(entries))


def website_tree(private: Path, ref: str) -> str:
    """The website's tree: the folder WEBSITE of `ref`, as it is."""
    if not _tip_has(private, ref, f"{WEBSITE}/index.html"):
        raise PublishError(f"{ref} has no {WEBSITE}/index.html for the website.")
    return _git(private, "rev-parse", f"{ref}:{WEBSITE}")


def check_message(message: str) -> str:
    message = message.strip()
    if not message:
        raise PublishError("A publication needs a message: what is new, in a line.")
    if _FORBIDDEN.search(message):
        raise PublishError("A public commit names its publisher only: no co-author lines.")
    if _addresses(message):
        raise PublishError("A public commit carries no e-mail address.")
    return message + "\n"


def publish(private: Path, public: Path, message: str, *, name: str = NAME,
            email: str = EMAIL, sources: dict[str, str] | None = None) -> list[Published]:
    """Add a commit to each public branch whose content changed; what happened
    to each. `public` is a bare clone of the public repository; nothing is
    pushed. A public branch is only ever moved forward. `sources` names, for
    `mac` and `windows`, the private commit to publish (default: CODE's
    branches); the front page and the website come from `mac`'s."""
    message = check_message(message)
    if not _NO_REPLY.fullmatch(email):
        raise PublishError(f"{email} is not a GitHub no-reply address: a public commit "
                           "never carries an e-mail address.")
    if _git(public, "rev-parse", "--is-bare-repository") != "true":
        raise PublishError(f"{public} is not a bare repository; make it with "
                           f"`git clone --bare {PUBLIC_URL} {public}`.")
    verify(public)                      # as it is: nothing is added to a doubtful one
    sources = {branch: f"refs/heads/{source}" for branch, source in CODE.items()} \
        | (sources or {})
    trees = {}
    for branch, source in sources.items():
        if _tip(private, source) is None:
            raise PublishError(f"The private repository has no {source}.")
        trees[branch] = code_tree(private, source)
    trees["main"] = front_tree(private, sources["mac"])
    trees["website"] = website_tree(private, sources["mac"])

    identity = os.environ | {"GIT_AUTHOR_NAME": name, "GIT_AUTHOR_EMAIL": email,
                             "GIT_COMMITTER_NAME": name, "GIT_COMMITTER_EMAIL": email}
    done: list[Published] = []
    try:
        for branch, tree in trees.items():
            before = _tip(public, f"refs/heads/{branch}")
            parent: list[str] = []
            if before is not None:
                # The public history, into the private repository's objects: a
                # snapshot's parent is the last published one, never a private commit.
                _git(private, "fetch", "--quiet", "--no-tags", str(public),
                     f"+refs/heads/{branch}:refs/publish/base")
                if _git(private, "rev-parse", f"{before}^{{tree}}") == tree:
                    done.append(Published(branch, before, before))
                    continue
                parent = ["-p", before]
            commit = _git(private, "commit-tree", tree, *parent, env=identity, stdin=message)
            _git(private, "update-ref", "refs/publish/new", commit)
            # Into the public clone: a fast-forward, or nothing.
            _git(public, "fetch", "--quiet", "--no-tags", str(private),
                 f"refs/publish/new:refs/heads/{branch}")
            done.append(Published(branch, before, commit))
    finally:
        for ref in ("refs/publish/base", "refs/publish/new"):
            subprocess.run(["git", "update-ref", "-d", ref], cwd=private,
                           capture_output=True, stdin=subprocess.DEVNULL, check=False)
    verify(public)
    return done


# Commits of the public branches made outside this tool, each looked at and
# accepted: they need not be under a no-reply address (all else still holds).
# The front page and the website are edited in the private deslopify/public,
# never on GitHub: main and website are remade from those files at every
# publication, so an edit made on GitHub would be undone by the next one, and
# verify() stops at it first. Such an edit is carried into deslopify/public,
# then its commit is named here, in full.
ACCEPTED = {
    "e08cbdfa4e5acdfac7f542f0a2aa82773816e0f8":
        "main: what unpacman is, in one sentence (Antoine Song, GitHub's editor, 2026-11-10)",
    "c19d8f9d0b0d2c014f7637183265a1fd93c68a93":
        "website: the same sentence (Antoine Song, GitHub's editor, 2026-11-10)",
}


def verify(public: Path) -> None:
    """Every commit of the public branches is a publication: made under a
    GitHub no-reply address (whoever published it — no e-mail address is ever
    public) unless named in ACCEPTED, without a co-author line, with nothing of
    EXCLUDED in the code."""
    for branch in (*CODE, "main", "website"):
        if _tip(public, f"refs/heads/{branch}") is None:
            continue
        log = _git(public, "log", "--format=%H%x00%an%x00%ae%x00%cn%x00%ce%x00%B%x01",
                   f"refs/heads/{branch}")
        for record in filter(str.strip, log.split("\x01")):
            sha, an, ae, cn, ce, body = record.strip().split("\x00", 5)
            if sha not in ACCEPTED and not (an and cn and _NO_REPLY.fullmatch(ae)
                                            and _NO_REPLY.fullmatch(ce)):
                hint = (" If it is an edit made on GitHub, carry it into deslopify/public, "
                        "then name its commit in publish.ACCEPTED."
                        if branch in ("main", "website") else "")
                raise PublishError(f"{branch} has a commit by someone else, or under an "
                                   f"e-mail address: {sha[:7]}.{hint}")
            if _FORBIDDEN.search(body) or _addresses(body):
                raise PublishError(f"{branch} has a commit with a co-author line or an "
                                   f"e-mail address: {sha[:7]}.")
            if branch in CODE:
                for path in EXCLUDED:
                    if _tip_has(public, sha, path):
                        raise PublishError(f"{branch} {sha[:7]} holds {path}.")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m deslopify.app.publish",
                                 description=__doc__.split("\n\n")[0])
    ap.add_argument("--message", required=True, help="what is new, in a line")
    ap.add_argument("--public", type=Path, default=PUBLIC_DIR,
                    help="a bare clone of the public repository (default: %(default)s)")
    ap.add_argument("--packages", type=Path, default=None,
                    help="also make unpacman-mac.zip and unpacman-windows.zip here")
    ap.add_argument("--mac-from", default=None,
                    help="the private commit to publish as mac (default: unpackman)")
    ap.add_argument("--windows-from", default=None,
                    help="the private commit to publish as windows (default: unpacman_windows)")
    ap.add_argument("--name", default=NAME, help="the publisher's name")
    ap.add_argument("--email", default=EMAIL, help="the publisher's no-reply address")
    args = ap.parse_args(argv)
    private = Path(__file__).resolve().parents[3]
    public = args.public.expanduser()
    try:
        sources = {b: s for b, s in (("mac", args.mac_from), ("windows", args.windows_from)) if s}
        results = publish(private, public, args.message, name=args.name, email=args.email,
                          sources=sources)
    except PublishError as exc:
        print(f"\n  Nothing was published: {exc}\n", file=sys.stderr)
        return 1
    print()
    for r in results:
        what = "unchanged" if not r.changed else (
            f"{(r.before or 'nothing')[:7]} -> {r.after[:7]}")
        print(f"  {r.branch:8} {what}")
    if args.packages:
        from .package import make_package, make_windows_package

        out = args.packages.expanduser()
        for made in (make_package(public, out, name="unpacman-mac"),
                     make_windows_package(public, out, name="unpacman-windows")):
            print(f"  {made} ({made.stat().st_size / 1e6:.1f} MB)")
    print(f"\n  Nothing is on GitHub yet. Look, then: git -C {public} push --atomic "
          "origin main mac windows website\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
