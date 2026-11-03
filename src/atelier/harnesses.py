"""Harness discovery, manifest parsing, and validation.

A harness is a *method*: a directory holding `harness.toml` plus a `SKILL.md`
describing the approach, optionally with subagents, on-demand skills, hooks,
and (for scripted multi-agent patterns) an `orchestrate.py`.

Harnesses live in the lab, never inside a problem folder — a method is reusable
across problems, and forking it per problem would make comparison meaningless.
"""

from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

EFFORTS = ("low", "medium", "high", "xhigh", "max")
PERMISSION_MODES = ("default", "acceptEdits", "auto", "dontAsk", "bypassPermissions", "plan")
RUNNERS = ("native", "script")
KINDS = ("solve", "read")
# Reading output contracts. "companion" adds generated exports and the
# companion's section structure; "guide" requires only a saved guide.md.
CONTRACTS = ("companion", "guide")
SEVERITIES = ("blocking", "worth-fixing", "minor")

_FRONTMATTER = re.compile(r"\A---\s*\n(.*?)\n---\s*\n", re.DOTALL)


class HarnessError(Exception):
    pass


# --------------------------------------------------------------------------
# manifest sections
# --------------------------------------------------------------------------


@dataclass(slots=True)
class Budget:
    max_usd: float | None = None
    max_minutes: float | None = None
    max_tokens_out: int | None = None
    max_turns: int | None = None
    wrapup_at: float = 0.80
    grace_seconds: int = 300

    def any_set(self) -> bool:
        return any(v is not None for v in
                   (self.max_usd, self.max_minutes, self.max_tokens_out, self.max_turns))


@dataclass(slots=True)
class ToolPolicy:
    allow: list[str] = field(default_factory=list)
    deny: list[str] = field(default_factory=list)
    bash_allow: list[str] = field(default_factory=list)
    bash_deny: list[str] = field(default_factory=list)
    none: bool = False          # `chat` baseline: no tools at all


@dataclass(slots=True)
class Permissions:
    mode: str = "acceptEdits"
    network: bool = True
    edit_static_files: bool = False


@dataclass(slots=True)
class ContextPolicy:
    include_statement: bool = True
    include_strategy: bool = True
    include_progress: bool = True
    include_state: bool = True
    include_labbook: str = "last:3"      # none | all | last:N
    math_tex: str = "outline"            # none | outline | full
    max_statement_chars: int = 0         # 0 = unlimited

    def labbook_count(self) -> int | None:
        """None means all; 0 means none."""
        v = (self.include_labbook or "none").strip().lower()
        if v == "none":
            return 0
        if v == "all":
            return None
        if v.startswith("last:"):
            try:
                return max(0, int(v.split(":", 1)[1]))
            except ValueError:
                return 3
        return 3


@dataclass(slots=True)
class HarnessDefaults:
    model: str | None = None
    effort: str | None = None
    account: str | None = None
    provider: str | None = None


@dataclass(slots=True)
class Role:
    """A method for one pass of a multi-pass run.

    The harness's SKILL.md is the method for the first pass. A run with a
    critique loop needs two more — one for reading the result adversarially and
    one for revising it — and those are different jobs, not the same job with a
    longer prompt. Each is a file in `roles/`.

    `writes` is the enforcement. A pass may create or modify the paths listed
    here and nothing else; anything else it touches is restored from the
    snapshot taken before it ran. Without that, the critic edits the guide
    directly and the round becomes a second draft wearing a reviewer's name.
    """
    name: str
    title: str
    body: str
    effort: str | None = None
    writes: list[str] = field(default_factory=list)
    include_document: bool = True


@dataclass(slots=True)
class Loop:
    """Propose, then critique, then revise — declared rather than hard-coded.

    Off unless a harness asks for it, so every existing single-pass run keeps
    exactly the shape it had.
    """
    enabled: bool = False
    rounds: int = 0                  # (critique, revise) pairs after the draft
    critic: str = "critic"
    reviser: str = "reviser"
    # Keep going while findings at this severity or worse are still being
    # raised. A round that raises nothing above it has converged, and spending
    # another pass to be told so again is waste.
    continue_at: str = "worth-fixing"
    share: dict[str, float] = field(default_factory=dict)
    # Optional explicit pipeline after the draft. When present, each named
    # role runs once, in order, as a fresh independent session.
    sequence: list[str] = field(default_factory=list)

    def passes(self) -> int:
        if not self.enabled:
            return 1
        return 1 + len(self.sequence) if self.sequence else 1 + 2 * self.rounds


@dataclass(slots=True)
class SubAgent:
    name: str
    description: str
    prompt: str
    tools: list[str] = field(default_factory=list)
    model: str | None = None
    effort: str | None = None

    def to_cli(self) -> dict[str, Any]:
        """Shape accepted by `claude --agents`."""
        d: dict[str, Any] = {"description": self.description, "prompt": self.prompt}
        if self.tools:
            d["tools"] = self.tools
        if self.model:
            d["model"] = self.model
        return d


@dataclass(slots=True)
class Harness:
    id: str
    root: Path
    title: str
    description: str
    version: int
    runner: str
    providers: list[str]
    tags: list[str]
    # "solve" advances a problem; "read" explains a document it must not touch.
    # The two need different wrap-up contracts, different policies and a
    # different Stop check, so the distinction is declared rather than guessed.
    kind: str
    defaults: HarnessDefaults
    budget: Budget
    tools: ToolPolicy
    permissions: Permissions
    context: ContextPolicy
    skill: str                                  # body of SKILL.md
    default_task: str
    loop: Loop = field(default_factory=Loop)
    roles: dict[str, Role] = field(default_factory=dict)
    agents: dict[str, SubAgent] = field(default_factory=dict)
    shared_skills: list[str] = field(default_factory=list)
    local_skills: list[str] = field(default_factory=list)
    shared_policies: list[str] = field(default_factory=list)
    hooks: dict[str, str] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    # Variants share one harness family but expose distinct methods in the UI.
    # The base harness remains available to the CLI as a compatibility alias.
    family: str | None = None
    variant: str | None = None
    variant_label: str | None = None
    contract: str = "companion"
    # Files available on demand, rather than concatenated into every prompt.
    skill_references: dict[str, str] = field(default_factory=dict)

    @property
    def has_script(self) -> bool:
        return self.runner == "script" and (self.root / "orchestrate.py").is_file()

    def supports(self, provider: str) -> bool:
        # An open-weight model runs through Claude Code pointed at another
        # endpoint (providers/openweight.py), so a harness that takes Claude
        # takes it too.
        if provider == "open" and "open" not in self.providers:
            return not self.providers or "claude" in self.providers
        return not self.providers or provider in self.providers


# --------------------------------------------------------------------------
# parsing
# --------------------------------------------------------------------------


def parse_frontmatter(text: str) -> tuple[dict[str, str], str]:
    """Split a leading `---` YAML-ish frontmatter block from a markdown body.

    Deliberately a minimal key: value parser — harness frontmatter is flat, and
    a YAML dependency for six keys is not worth it.
    """
    m = _FRONTMATTER.match(text)
    if not m:
        return {}, text
    meta: dict[str, str] = {}
    for line in m.group(1).splitlines():
        line = line.strip()
        if not line or line.startswith("#") or ":" not in line:
            continue
        key, _, value = line.partition(":")
        meta[key.strip()] = value.strip().strip('"').strip("'")
    return meta, text[m.end():]


def load_role(path: Path) -> Role:
    meta, body = parse_frontmatter(path.read_text(encoding="utf-8"))
    return Role(
        name=meta.get("name") or path.stem,
        title=meta.get("title", meta.get("name") or path.stem),
        body=body.strip(),
        effort=meta.get("effort") or None,
        writes=[w.strip() for w in meta.get("writes", "").split(",") if w.strip()],
        include_document=meta.get("document", "true").lower() not in ("false", "no", "0"),
    )


def load_agent(path: Path) -> SubAgent:
    text = path.read_text(encoding="utf-8")
    meta, body = parse_frontmatter(text)
    name = meta.get("name") or path.stem
    tools = [t.strip() for t in meta.get("tools", "").split(",") if t.strip()]
    return SubAgent(
        name=name,
        description=meta.get("description", f"Subagent {name}"),
        prompt=body.strip(),
        tools=tools,
        model=meta.get("model") or None,
        effort=meta.get("effort") or None,
    )


def load_harness(root: Path, *, variant: str | None = None) -> Harness:
    manifest_path = root / "harness.toml"
    if not manifest_path.is_file():
        raise HarnessError(f"{root.name}: missing harness.toml")
    try:
        with manifest_path.open("rb") as fh:
            raw = tomllib.load(fh)
    except tomllib.TOMLDecodeError as exc:
        raise HarnessError(f"{root.name}/harness.toml: invalid TOML: {exc}") from exc

    warnings: list[str] = []
    base_h = raw.get("harness", {})
    h = dict(base_h)
    variant_cfg: dict[str, Any] = {}
    if variant is not None:
        variants = raw.get("variants", {}) or {}
        if variant not in variants:
            raise HarnessError(f"{root.name}: no such harness variant: {variant}")
        variant_cfg = dict(variants[variant] or {})
        # A variant may change both its public identity and the pipeline behind
        # it.  Earlier manifests only varied the flat [harness] fields; keeping
        # nested sections out of `h` makes those old files behave identically
        # while allowing a reader-facing variant to use a shorter loop than the
        # family's benchmarking/CLI harness.
        nested = {"skill", "skill_includes", "skill_references", "defaults", "budget", "tools", "permissions",
                  "context", "loop", "skills", "policies", "hooks", "metadata"}
        h.update({k: v for k, v in variant_cfg.items() if k not in nested})

    def section(name: str) -> dict[str, Any]:
        """Base manifest section with a variant's keys overlaid."""
        out = dict(raw.get(name, {}) or {})
        override = variant_cfg.get(name, {}) if variant_cfg else {}
        if isinstance(override, dict):
            out.update(override)
        return out

    hid = h.get("id", root.name if variant is None else f"{root.name}-{variant}")
    if variant is None and hid != root.name:
        warnings.append(f"id {hid!r} differs from directory name {root.name!r}; using the id")

    skill_name = variant_cfg.get("skill", base_h.get("skill", "SKILL.md"))

    def read_skill(name: Any, *, required: bool) -> tuple[dict[str, str], str] | None:
        """One skill beside the manifest, never an arbitrary path.

        A compact variant can inherit the exact method of another variant while
        adding only its different document shape. Keep that composition local
        and explicit so a method never silently reads instructions elsewhere.
        """
        if not isinstance(name, str) or not name or Path(name).name != name:
            message = f"{hid}: skill names must be files beside harness.toml"
            if required:
                raise HarnessError(message)
            warnings.append(message)
            return None
        path = root / name
        if not path.is_file():
            message = f"no {name} — the harness contributes no method"
            if required:
                raise HarnessError(f"{hid}: {message}")
            warnings.append(message)
            return None
        return parse_frontmatter(path.read_text(encoding="utf-8"))

    included = variant_cfg.get("skill_includes", base_h.get("skill_includes", []))
    if not isinstance(included, list) or not all(isinstance(name, str) for name in included):
        raise HarnessError(f"{hid}: skill_includes must be a list of skill filenames")
    skill_body = ""
    default_task = ""
    parts: list[str] = []
    for name in included:
        loaded = read_skill(name, required=True)
        assert loaded is not None
        _, body = loaded
        if body.strip():
            parts.append(body.strip())
    selected = read_skill(skill_name, required=False)
    if selected is not None:
        meta, body = selected
        if body.strip():
            parts.append(body.strip())
        default_task = meta.get("default_task", "")
    skill_body = "\n\n---\n\n".join(parts)

    references = variant_cfg.get("skill_references", base_h.get("skill_references", []))
    if not isinstance(references, list) or not all(isinstance(name, str) for name in references):
        raise HarnessError(f"{hid}: skill_references must be a list of skill filenames")
    skill_references: dict[str, str] = {}
    for name in references:
        loaded = read_skill(name, required=True)
        assert loaded is not None
        _, body = loaded
        if not body.strip():
            raise HarnessError(f"{hid}: empty skill reference {name}")
        skill_references[name] = body

    runner = h.get("runner", "native")
    if runner not in RUNNERS:
        raise HarnessError(f"{hid}: runner must be one of {RUNNERS}")

    kind = h.get("kind", "solve")
    if kind not in KINDS:
        raise HarnessError(f"{hid}: kind must be one of {KINDS}")
    contract = str(h.get("contract", "companion"))
    if contract not in CONTRACTS:
        raise HarnessError(f"{hid}: contract must be one of {CONTRACTS}")
    if runner == "script" and not (root / "orchestrate.py").is_file():
        raise HarnessError(f"{hid}: runner='script' but orchestrate.py is missing")

    d = section("defaults")
    defaults = HarnessDefaults(
        model=d.get("model"), effort=d.get("effort"),
        account=d.get("account"), provider=d.get("provider"),
    )
    if defaults.effort and defaults.effort not in EFFORTS:
        raise HarnessError(f"{hid}: effort must be one of {EFFORTS}")

    b = section("budget")
    budget = Budget(**{k: v for k, v in b.items() if k in Budget.__slots__})
    if not 0.0 < budget.wrapup_at <= 1.0:
        raise HarnessError(f"{hid}: budget.wrapup_at must be in (0, 1]")

    t = section("tools")
    tools = ToolPolicy(**{k: v for k, v in t.items() if k in ToolPolicy.__slots__})

    p = section("permissions")
    permissions = Permissions(**{k: v for k, v in p.items() if k in Permissions.__slots__})
    if permissions.mode not in PERMISSION_MODES:
        raise HarnessError(f"{hid}: permissions.mode must be one of {PERMISSION_MODES}")

    c = section("context")
    context = ContextPolicy(**{k: v for k, v in c.items() if k in ContextPolicy.__slots__})
    if context.math_tex not in ("none", "outline", "full"):
        raise HarnessError(f"{hid}: context.math_tex must be none|outline|full")

    # subagents
    agents: dict[str, SubAgent] = {}
    agents_dir = root / "agents"
    enabled = set(raw.get("agents", {}).get("enabled", []) or [])
    if agents_dir.is_dir():
        for f in sorted(agents_dir.glob("*.md")):
            agent = load_agent(f)
            if enabled and agent.name not in enabled:
                continue
            agents[agent.name] = agent
    missing = enabled - set(agents)
    if missing:
        warnings.append(f"agents.enabled names with no file: {sorted(missing)}")

    # roles: the methods for the non-first passes of a looped run
    roles: dict[str, Role] = {}
    roles_dir = root / "roles"
    if roles_dir.is_dir():
        for f in sorted(roles_dir.glob("*.md")):
            role = load_role(f)
            roles[role.name] = role

    lp = section("loop")
    # `share` is itself a table.  A variant normally replaces the pass set, so
    # its shares replace the base table rather than being polluted by weights
    # for passes that do not exist in the shorter pipeline.
    if isinstance(variant_cfg.get("loop"), dict) \
            and "share" in variant_cfg["loop"]:
        lp["share"] = variant_cfg["loop"]["share"]
    loop = Loop(
        enabled=bool(lp.get("enabled", False)),
        rounds=int(lp.get("rounds", 1) or 0),
        critic=str(lp.get("critic", "critic")),
        reviser=str(lp.get("reviser", "reviser")),
        continue_at=str(lp.get("continue_at", "worth-fixing")),
        share={k: float(v) for k, v in (lp.get("share", {}) or {}).items()},
        sequence=[str(v) for v in (lp.get("sequence", []) or [])],
    )
    if loop.enabled:
        if loop.rounds < 1:
            raise HarnessError(f"{hid}: loop.rounds must be at least 1 when the "
                               f"loop is enabled")
        if loop.continue_at not in SEVERITIES:
            raise HarnessError(f"{hid}: loop.continue_at must be one of {SEVERITIES}")
        required_roles = ([('pipeline', name) for name in loop.sequence]
                          if loop.sequence else
                          [("critic", loop.critic), ("reviser", loop.reviser)])
        for which, name in required_roles:
            if name not in roles:
                raise HarnessError(
                    f"{hid}: loop.{which} names role {name!r}, but there is no "
                    f"roles/{name}.md"
                    + (f" (found: {', '.join(sorted(roles))})" if roles else ""))
        if tools.none:
            raise HarnessError(f"{hid}: a tool-free harness cannot run a critique "
                               f"loop — each pass reads the last one's files")
        allowed_shares = {"draft", loop.critic, loop.reviser, *loop.sequence}
        bad = [k for k in loop.share if k not in allowed_shares]
        if bad:
            warnings.append(f"loop.share has entries for no such pass: {sorted(bad)}")
        # A zero share does not mean "a small budget": a cap of zero is falsy
        # everywhere downstream, so that pass would run with no cap at all —
        # the exact opposite of what writing 0 looks like it asks for.
        if (zero := sorted(k for k, v in loop.share.items() if v <= 0)):
            raise HarnessError(
                f"{hid}: loop.share must be positive, but {', '.join(zero)} "
                f"is not. A share of zero would leave that pass uncapped, not "
                f"unfunded; to skip a pass, lower loop.rounds.")

    skills = section("skills")
    hooks = {k: str(v) for k, v in section("hooks").items()}
    metadata = section("metadata")

    return Harness(
        id=hid, root=root,
        title=h.get("title", hid),
        description=h.get("description", ""),
        version=int(h.get("version", 1)),
        runner=runner,
        providers=list(h.get("providers", ["claude"])),
        tags=list(h.get("tags", [])),
        kind=kind,
        defaults=defaults, budget=budget, tools=tools,
        permissions=permissions, context=context,
        skill=skill_body, default_task=default_task, skill_references=skill_references,
        loop=loop, roles=roles,
        agents=agents,
        shared_skills=list(skills.get("shared", [])),
        local_skills=list(skills.get("local", [])),
        shared_policies=list(section("policies").get("shared", []) or []),
        hooks=hooks,
        metadata=metadata,
        warnings=warnings,
        family=(str(base_h.get("id", root.name)) if variant is not None else None),
        variant=variant,
        variant_label=(str(variant_cfg.get("label", variant)).strip()
                       if variant is not None else None),
        contract=contract,
    )


def discover(harnesses_dir: Path) -> tuple[list[Harness], dict[str, str]]:
    """Load every harness. Returns (harnesses, {dir_name: error}).

    A broken harness must never prevent the others from loading.
    """
    found: list[Harness] = []
    errors: dict[str, str] = {}
    if not harnesses_dir.is_dir():
        return found, errors
    for entry in sorted(harnesses_dir.iterdir(), key=lambda p: p.name.lower()):
        if not entry.is_dir() or entry.name.startswith("."):
            continue
        try:
            manifest = entry / "harness.toml"
            with manifest.open("rb") as fh:
                raw = tomllib.load(fh)
            variants = raw.get("variants", {}) or {}
            if variants:
                for name in variants:
                    found.append(load_harness(entry, variant=str(name)))
            else:
                found.append(load_harness(entry))
        except HarnessError as exc:
            errors[entry.name] = str(exc)
        except tomllib.TOMLDecodeError as exc:
            errors[entry.name] = f"{entry.name}/harness.toml: invalid TOML: {exc}"
        except Exception as exc:                     # noqa: BLE001 - report, never crash
            errors[entry.name] = f"{type(exc).__name__}: {exc}"
    return found, errors


def get_harness(harnesses_dir: Path, hid: str) -> Harness:
    root = harnesses_dir / hid
    if not root.is_dir():
        found, _ = discover(harnesses_dir)
        for h in found:
            if h.id == hid:
                return h
        raise HarnessError(f"no such harness: {hid}")
    return load_harness(root)
