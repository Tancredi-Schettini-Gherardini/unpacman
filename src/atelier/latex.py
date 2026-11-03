"""LaTeX compilation.

Compiles in a scratch directory so the problem folder never accumulates .aux
noise, then copies the PDF back. On failure it extracts the first few real
errors — those go straight back to the agent through the PostToolUse hook, so
it repairs the document immediately instead of at the end of the session.
"""

from __future__ import annotations

import asyncio
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from .config import Config

# `file:line: message` (from -file-line-error) and plain `! message`.
_ERR_FILE_LINE = re.compile(r"^(?P<file>[^:\n]+):(?P<line>\d+):\s*(?P<msg>.+)$")
_ERR_BANG = re.compile(r"^!\s*(?P<msg>.+)$")


@dataclass(slots=True)
class CompileResult:
    ok: bool
    pdf: Path | None = None
    errors: list[str] = field(default_factory=list)
    log_tail: str = ""
    skipped: str = ""


def _scratch(progress_dir: Path) -> Path:
    d = progress_dir.parent / ".atelier" / "tex"
    d.mkdir(parents=True, exist_ok=True)
    return d


def extract_errors(log_text: str, limit: int = 12) -> list[str]:
    out: list[str] = []
    lines = log_text.splitlines()
    for i, line in enumerate(lines):
        line = line.rstrip()
        m = _ERR_FILE_LINE.match(line)
        if m and not line.lower().startswith(("this is", "output written")):
            out.append(f"{Path(m.group('file')).name}:{m.group('line')}: {m.group('msg')}")
        elif (m2 := _ERR_BANG.match(line)):
            context = ""
            for follow in lines[i + 1: i + 4]:
                if follow.startswith("l."):
                    context = f"  at {follow.strip()}"
                    break
            out.append(f"! {m2.group('msg')}{context}")
        if len(out) >= limit:
            break
    return out


def _build_args(cfg: Config, tex_name: str, outdir: Path) -> list[str]:
    return [cfg.latex.engine, *cfg.latex.args, f"-outdir={outdir}", tex_name]


async def compile_tex(cfg: Config, progress_dir: Path,
                      tex_name: str = "math.tex") -> CompileResult:
    """Compile `progress/<tex_name>` and copy the PDF next to the source."""
    src = progress_dir / tex_name
    if not src.is_file():
        return CompileResult(ok=False, skipped=f"{tex_name} does not exist")
    if not shutil.which(cfg.latex.engine):
        return CompileResult(ok=False, skipped=f"{cfg.latex.engine} is not installed")

    outdir = _scratch(progress_dir)
    try:
        proc = await asyncio.create_subprocess_exec(
            *_build_args(cfg, tex_name, outdir),
            cwd=str(progress_dir),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=cfg.latex.timeout_s)
        code = proc.returncode
    except TimeoutError:
        return CompileResult(ok=False, errors=[f"latexmk timed out after {cfg.latex.timeout_s}s"])
    except OSError as exc:
        return CompileResult(ok=False, errors=[f"could not run {cfg.latex.engine}: {exc}"])

    text = out.decode("utf-8", errors="replace")
    log_file = outdir / (Path(tex_name).stem + ".log")
    if log_file.is_file():
        text += "\n" + log_file.read_text(encoding="utf-8", errors="replace")

    produced = outdir / (Path(tex_name).stem + ".pdf")
    if code == 0 and produced.is_file():
        target = progress_dir / produced.name
        try:
            shutil.copy2(produced, target)
        except OSError as exc:
            return CompileResult(ok=False, errors=[f"could not copy PDF: {exc}"])
        return CompileResult(ok=True, pdf=target)

    return CompileResult(ok=False, errors=extract_errors(text),
                         log_tail="\n".join(text.splitlines()[-40:]))


def compile_tex_sync(cfg: Config, progress_dir: Path,
                     tex_name: str = "math.tex") -> CompileResult:
    """Blocking variant, used by the PostToolUse hook (a separate process)."""
    src = progress_dir / tex_name
    if not src.is_file():
        return CompileResult(ok=False, skipped=f"{tex_name} does not exist")
    if not shutil.which(cfg.latex.engine):
        return CompileResult(ok=False, skipped=f"{cfg.latex.engine} is not installed")

    outdir = _scratch(progress_dir)
    try:
        r = subprocess.run(
            _build_args(cfg, tex_name, outdir), cwd=str(progress_dir),
            capture_output=True, text=True, timeout=cfg.latex.timeout_s,
        )
    except subprocess.TimeoutExpired:
        return CompileResult(ok=False, errors=[f"latexmk timed out after {cfg.latex.timeout_s}s"])
    except OSError as exc:
        return CompileResult(ok=False, errors=[f"could not run {cfg.latex.engine}: {exc}"])

    text = (r.stdout or "") + "\n" + (r.stderr or "")
    log_file = outdir / (Path(tex_name).stem + ".log")
    if log_file.is_file():
        text += "\n" + log_file.read_text(encoding="utf-8", errors="replace")

    produced = outdir / (Path(tex_name).stem + ".pdf")
    if r.returncode == 0 and produced.is_file():
        target = progress_dir / produced.name
        try:
            shutil.copy2(produced, target)
        except OSError as exc:
            return CompileResult(ok=False, errors=[f"could not copy PDF: {exc}"])
        return CompileResult(ok=True, pdf=target)

    return CompileResult(ok=False, errors=extract_errors(text),
                         log_tail="\n".join(text.splitlines()[-40:]))
