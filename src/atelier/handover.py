"""Handing a reading's files between its AI and the controller (2026-10-31).

A reading's AI works in its own folder — the run's `workspace/` — confined by
its program's sandbox. Between its steps the controller, which is this
application's own unconfined code, checks that folder, typesets the companion,
copies it for the next step, puts back what a step was not allowed to change,
and at the end archives it. A program the AI started can outlive its step and
go on changing the folder while the controller works on it: a file checked a
moment ago may be a symbolic link to any file of the user's by the time it is
read or written, and the controller would read or write through it what the AI
itself may not (a security check of 2026-10-31 did both, on dummy files).

So between steps the controller never works in that folder. It *collects* what
it needs into a private copy beside the run's records, which no reading's
sandbox may write: every file is read through a handle opened without
following links, so what is checked is exactly what is copied, whatever
changes afterwards. It works on that copy. Just before the next step, and at
the end, it *delivers* the result back by moving folders, never writing
through a name in the AI's folder; whatever stands there by then is moved
aside, and a link among it is an attempt to steer the controller.

Anything but plain files and folders — a symbolic link, a hard-linked file, a
pipe — is refused, as `access.check_job_tree` always has: set aside in the
run's `rejected-files/`, never followed, never read.

Only standard operations relative to folder handles are used (`O_NOFOLLOW`,
`O_DIRECTORY`, `openat`/`renameat` through `dir_fd`): the same on macOS and on
Linux (WSL), and independent of how any sandbox works.

A reading that stopped before its run could archive it — unpacman stopped in
the middle of it — is archived afterwards the same way, by `archive`
(2026-11-02): its folder is never used as it stands.
"""

from __future__ import annotations

import errno
import os
import shutil
import stat
import uuid
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path

from .access import AccessPolicyError

HELD = ".handover"              # the private copy, beside the run's records
SET_ASIDE = ".handover-old"     # the AI's folder as it stood when given back
QUARANTINE = "rejected-files"   # as access.quarantine_job_links has it

_DIR = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
# O_NONBLOCK: a pipe put where a file was must not hang the controller on open.
_FILE = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC
_NEW = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC
_CHUNK = 1 << 20

# How the walk treats an entry: copied with everything under it, a folder on
# the way to something copied (made, but only some of it copied), or only
# checked.
_COPY, _ON_THE_WAY, _CHECK = "copy", "on the way", "check"


@dataclass(slots=True)
class Collected:
    """The private copy, and the entries refused on the way (relative to the
    folder collected from), each moved into the quarantine. `left_out`: the
    folder's other plain files and folders, checked but not copied — what it
    held outside the parts when the copy was taken."""
    root: Path
    unsafe: list[str] = field(default_factory=list)
    left_out: frozenset[str] = frozenset()


def unsafe_message(where: Path | str) -> str:
    """The words `access.check_job_tree` has always used for such an entry."""
    return f"Unsafe link or special file in paper working files: {where}"


def collect(workspace: Path, private: Path, parts: Iterable[str],
            quarantine: Path) -> Collected:
    """Copy `parts` of `workspace` (relative paths: folders, or single files)
    into `private`, laid out as in the workspace, and check everything else
    in the workspace as well.

    `private` is made afresh; anything left there by an interrupted controller
    is removed first (it is the controller's own folder). An entry that is not
    a plain, singly linked file or a folder is neither followed nor copied: it
    is moved, itself, into `quarantine`, and listed in the result.
    """
    parts = _parts(parts)
    if private.exists() or private.is_symlink():
        shutil.rmtree(private)
    private.mkdir(mode=0o700)
    walk = _Walk(lambda rel: _mode(rel, parts), quarantine=quarantine, left_out=set())
    source = os.open(workspace, _DIR)
    try:
        target = os.open(private, _DIR)
        try:
            walk.folder(source, "", target)
        finally:
            os.close(target)
    finally:
        os.close(source)
        walk.close()
    return Collected(private, walk.unsafe, frozenset(walk.left_out or ()))


def deliver(private: Path, workspace: Path, parts: Iterable[str],
            set_aside: Path) -> list[str]:
    """Put each of `parts` of `private` back into `workspace`: what stands in
    the workspace under that name is first moved, itself, into `set_aside`;
    the private part is then moved in. A part the private copy no longer has
    is only set aside. Nothing in the workspace is ever written through.

    Returns the entries among what was set aside that are not plain files or
    folders (relative to the workspace) — which can only have appeared after
    the copy was taken, since the copy refused any. `set_aside` is left for the
    caller, to keep as evidence or remove; `private` is removed.
    """
    parts = _parts(parts)
    if set_aside.exists() or set_aside.is_symlink():
        shutil.rmtree(set_aside)
    set_aside.mkdir(mode=0o700)
    moved: list[tuple[str, str]] = []         # (name in set_aside, part)
    work = os.open(workspace, _DIR)
    held = os.open(private, _DIR)
    aside = os.open(set_aside, _DIR)
    try:
        for part in parts:
            *chain, name = part.split("/")
            here = _descend(work, chain, create=True, aside=aside, moved=moved, part=part)
            there = _descend(held, chain, create=False)
            try:
                for _ in range(8):
                    if (stored := _move_aside(here, name, aside)) is not None:
                        moved.append((stored, part))
                    if there is None or not _present(there, name):
                        break
                    try:
                        os.rename(name, name, src_dir_fd=there, dst_dir_fd=here)
                        break
                    except OSError as exc:
                        # Something was put there again in the meantime: set
                        # that aside too, and try again.
                        if exc.errno not in (errno.EEXIST, errno.ENOTEMPTY, errno.ENOTDIR,
                                             errno.EISDIR):
                            raise
                else:
                    raise AccessPolicyError(
                        f"The reading's files could not be given back: {part} was "
                        "replaced again and again.")
            finally:
                if here not in (work,):
                    os.close(here)
                if there is not None and there != held:
                    os.close(there)
    finally:
        os.close(aside)
        os.close(held)
        os.close(work)
    shutil.rmtree(private, ignore_errors=True)
    unsafe: list[str] = []
    for stored, part in moved:
        for rel in unsafe_entries(set_aside / stored):
            unsafe.append(part if rel == "." else f"{part}/{rel}")
    return unsafe


def unsafe_entries(root: Path) -> list[str]:
    """Every entry under `root` (or `root` itself, as ".") that is not a plain,
    singly linked file or a folder, without following anything."""
    try:
        info = root.lstat()
    except FileNotFoundError:
        return []
    if stat.S_ISREG(info.st_mode):
        return [] if info.st_nlink == 1 else ["."]
    if not stat.S_ISDIR(info.st_mode):
        return ["."]
    walk = _Walk(lambda rel: _CHECK, quarantine=None)
    try:
        handle = os.open(root, _DIR)
    except OSError:
        return ["."]
    try:
        walk.folder(handle, "", None)
    finally:
        os.close(handle)
    return walk.unsafe


def copy_without_links(source: Path, destination: Path, *, into_existing: bool = False) -> None:
    """Copy the folder `source`, which another process may still be changing,
    to `destination` — new, or with `into_existing` an existing folder of the
    caller's own, whose files of the same name are replaced.

    Raises `AccessPolicyError` at the first entry that is not a plain, singly
    linked file or a folder, and copies nothing of it; `source` is not
    changed.
    """
    if into_existing:
        if not destination.is_dir() or destination.is_symlink():
            raise AccessPolicyError(unsafe_message(destination))
    else:
        destination.mkdir(parents=True)
    walk = _Walk(lambda rel: _COPY, quarantine=None, strict=source, replace=into_existing)
    handle = os.open(source, _DIR)
    try:
        target = os.open(destination, _DIR)
        try:
            walk.folder(handle, "", target)
        finally:
            os.close(target)
    finally:
        os.close(handle)


def archive(workspace: Path, part: str, destination: Path,
            quarantine: Path) -> list[str] | None:
    """Keep the folder `part` of `workspace` (a path relative to it, such as
    "progress/reading") — which another process may still be changing — as
    `destination`, which must not exist yet: a reading's record, made after
    its run when the run could not make it (2026-11-02).

    Reached as `collect` reaches what it copies: `workspace` is opened without
    following a link in its own place — the folders above it are the
    controller's own — and each name of `part` is entered through the handle
    of the folder before it, never following a link either. (Opened by its
    whole path, as it first was, the folder was reached through a link put
    in place of `progress`: a link is refused only as the last name of a
    path. Found by another agent's review, 2026-11-02.)

    Copied as `collect` copies: nothing is followed, and an entry that is not
    a plain, singly linked file or a folder is moved, itself, into
    `quarantine` and listed (relative to `part`), never read. Built beside
    `destination` and put in place whole, so that it is found complete or not
    at all; files and folders keep their permissions and times.

    Returns the entries set aside; None when nothing was kept — `part` is not
    a folder there (missing, or with a link anywhere on the way, which is not
    followed) or `destination` exists. Other errors are raised, after
    removing anything half built.
    """
    [part] = _parts([part])
    if destination.exists() or destination.is_symlink():
        return None
    try:
        root = os.open(workspace, _DIR)
    except OSError:
        return None
    try:
        handle = _descend(root, part.split("/"), create=False)
    finally:
        os.close(root)
    if handle is None:
        return None
    walk = _Walk(lambda rel: _COPY, quarantine=quarantine)
    staging = destination.parent / f".{destination.name}-{uuid.uuid4().hex[:12]}"
    try:
        staging.mkdir(mode=0o700)
        target = os.open(staging, _DIR)
        try:
            walk.folder(handle, "", target)
            try:
                os.rename(staging, destination)
            except OSError:
                if destination.exists():          # made meanwhile, by another request
                    shutil.rmtree(staging, ignore_errors=True)
                    return None
                raise
            # Set last, once nothing more is put in it, as copytree does.
            info = os.fstat(handle)
            os.chmod(target, stat.S_IMODE(info.st_mode))
            os.utime(target, ns=(info.st_atime_ns, info.st_mtime_ns))
        finally:
            os.close(target)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    finally:
        os.close(handle)
        walk.close()
    return walk.unsafe


# --------------------------------------------------------------------------
# the walk
# --------------------------------------------------------------------------


def _parts(parts: Iterable[str]) -> list[str]:
    out: list[str] = []
    for part in parts:
        clean = Path(part).as_posix().strip("/")
        if not clean or clean == "." or any(p in ("", ".", "..") for p in clean.split("/")):
            raise ValueError(f"not a part of the working folder: {part!r}")
        if clean not in out:
            out.append(clean)
    return out


def _mode(rel: str, parts: list[str]) -> str:
    if any(rel == p or rel.startswith(p + "/") for p in parts):
        return _COPY
    if any(p.startswith(rel + "/") for p in parts):
        return _ON_THE_WAY
    return _CHECK


class _Walk:
    """One walk of a folder that another process may be changing: every
    folder is entered and every file opened through its parent's handle,
    never following a link."""

    def __init__(self, mode: Callable[[str], str], *, quarantine: Path | None,
                 strict: Path | None = None, replace: bool = False,
                 left_out: set[str] | None = None) -> None:
        self.mode = mode
        self.quarantine = quarantine
        self.strict = strict              # raise at the first unsafe entry
        self.replace = replace            # copy over existing files (own folder)
        self.left_out = left_out          # where to note the entries only checked
        self.unsafe: list[str] = []
        self._quarantine_fd: int | None = None

    def close(self) -> None:
        if self._quarantine_fd is not None:
            os.close(self._quarantine_fd)
            self._quarantine_fd = None

    def folder(self, source: int, rel: str, target: int | None) -> None:
        with os.scandir(source) as listing:
            names = sorted(entry.name for entry in listing)
        for name in names:
            child = f"{rel}/{name}" if rel else name
            mode = self.mode(child)
            self.entry(source, name, child, target if mode != _CHECK else None, mode)

    def entry(self, parent: int, name: str, rel: str, target: int | None,
              mode: str) -> None:
        try:
            info = os.stat(name, dir_fd=parent, follow_symlinks=False)
        except FileNotFoundError:
            return                                    # gone since it was listed
        if self.left_out is not None and mode == _CHECK and (
                stat.S_ISDIR(info.st_mode)
                or (stat.S_ISREG(info.st_mode) and info.st_nlink == 1)):
            self.left_out.add(rel)
        if stat.S_ISDIR(info.st_mode):
            self._enter(parent, name, rel, target, mode)
        elif stat.S_ISREG(info.st_mode) and info.st_nlink == 1:
            if target is not None and mode == _COPY:
                self._copy(parent, name, rel, target)
        else:
            self.refuse(parent, name, rel)

    def _enter(self, parent: int, name: str, rel: str, target: int | None,
               mode: str) -> None:
        try:
            handle = os.open(name, _DIR, dir_fd=parent)
        except FileNotFoundError:
            return
        except OSError:
            self.refuse(parent, name, rel)            # a link, or a file, by now
            return
        try:
            made = None
            if target is not None:
                try:
                    os.mkdir(name, 0o700, dir_fd=target)
                except FileExistsError:
                    if not self.replace:
                        raise
                made = os.open(name, _DIR, dir_fd=target)
            try:
                self.folder(handle, rel, made)
                if made is not None:
                    info = os.fstat(handle)
                    os.chmod(made, stat.S_IMODE(info.st_mode))
                    os.utime(made, ns=(info.st_atime_ns, info.st_mtime_ns))
            finally:
                if made is not None:
                    os.close(made)
        finally:
            os.close(handle)

    def _copy(self, parent: int, name: str, rel: str, target: int) -> None:
        try:
            handle = os.open(name, _FILE, dir_fd=parent)
        except FileNotFoundError:
            return
        except OSError:
            self.refuse(parent, name, rel)            # a link, or a socket, by now
            return
        try:
            info = os.fstat(handle)
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                self.refuse(parent, name, rel)
                return
            flags = _NEW
            if self.replace:
                flags = (flags & ~os.O_EXCL) | os.O_TRUNC
            out = os.open(name, flags, 0o600, dir_fd=target)
            try:
                while chunk := os.read(handle, _CHUNK):
                    view = memoryview(chunk)
                    while view:
                        view = view[os.write(out, view):]
                os.chmod(out, stat.S_IMODE(info.st_mode))
                os.utime(out, ns=(info.st_atime_ns, info.st_mtime_ns))
            finally:
                os.close(out)
        finally:
            os.close(handle)

    def refuse(self, parent: int, name: str, rel: str) -> None:
        if self.strict is not None:
            raise AccessPolicyError(unsafe_message(self.strict / rel), path=self.strict / rel)
        self.unsafe.append(rel)
        if self.quarantine is None:
            return
        try:
            if self._quarantine_fd is None:
                self.quarantine.mkdir(parents=True, exist_ok=True)
                self._quarantine_fd = os.open(self.quarantine, _DIR)
            os.rename(name, f"{uuid.uuid4().hex}-{name}",
                      src_dir_fd=parent, dst_dir_fd=self._quarantine_fd)
        except OSError:
            pass          # gone already, or on another volume: it is not copied either way


def _present(folder: int, name: str) -> bool:
    try:
        os.stat(name, dir_fd=folder, follow_symlinks=False)
        return True
    except FileNotFoundError:
        return False


def _move_aside(folder: int, name: str, aside: int) -> str | None:
    """Move whatever `name` is in `folder` into `aside`; its new name, or None
    if there was nothing."""
    stored = f"{uuid.uuid4().hex}-{name}"
    try:
        os.rename(name, stored, src_dir_fd=folder, dst_dir_fd=aside)
    except FileNotFoundError:
        return None
    return stored


def _descend(root: int, chain: list[str], *, create: bool, aside: int | None = None,
             moved: list[tuple[str, str]] | None = None, part: str = "") -> int | None:
    """The handle of the folder `chain` below `root`, entered without following
    links — made where missing if `create` (in the workspace, anything else
    standing in its way is moved aside first); None if it is missing and not
    to be made."""
    current = root
    for name in chain:
        try:
            nxt = os.open(name, _DIR, dir_fd=current)
        except FileNotFoundError:
            if not create:
                nxt = None
            else:
                os.mkdir(name, 0o755, dir_fd=current)
                nxt = os.open(name, _DIR, dir_fd=current)
        except OSError:
            if not create or aside is None:
                nxt = None
            else:
                if (stored := _move_aside(current, name, aside)) is not None and moved is not None:
                    moved.append((stored, part))
                os.mkdir(name, 0o755, dir_fd=current)
                nxt = os.open(name, _DIR, dir_fd=current)
        if current != root:
            os.close(current)
        if nxt is None:
            return None
        current = nxt
    return current
