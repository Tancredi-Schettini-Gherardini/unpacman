"""Which AI reads the paper.

Chosen on the page, just before a reading starts. The choices are the lab's
own — the accounts in `accounts.toml` and the models `atelier.toml` offers for
each provider — narrowed to what can actually run here: a provider the harness
supports, whose program is installed on this machine. Offering anything else
would let someone press a button that can only fail.

The default follows the same order `plan_run` resolves a run in (the command
line, then the harness, then the configuration), so a reading started without
touching the choice runs exactly what it would have run before the choice
existed.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from atelier.config import Config
from atelier.harnesses import Harness

from .openweight import DEEPSEEK, DEEPSEEK_EFFORTS, OPENROUTER, SEARCHES, deepseek_models

PROVIDER_NAMES = {"claude": "Claude", "codex": "ChatGPT", "open": "Open-weight"}

# What is known of each open-weight model offered (openweight.py): its name,
# whether it is small, whether it runs on this Mac at no cost. Kept here, where
# every name a page shows is made, and filled each time the models are looked at.
OPEN_MODELS: dict[str, dict[str, Any]] = {}


def remember_open(models: dict[str, list[Any]]) -> None:
    for listed in models.values():
        for m in listed:
            OPEN_MODELS[m.id] = {"name": m.name, "small": m.small, "free": m.free,
                                 "note": m.note, "context": m.context}


# DeepSeek's own models are known by name from the start: a reading credited
# to one says "DeepSeek V4 Pro" even before its host has been asked anything.
remember_open({DEEPSEEK: deepseek_models()})


def searches(provider: str | None, account: str | None) -> bool:
    """Whether this AI can search the web in a reading here: Claude's and
    ChatGPT's can, and of the open-weight hosts DeepSeek's (openweight.SEARCHES)."""
    return provider != "open" or account in SEARCHES

EFFORT_NAMES = {"low": "low", "medium": "medium", "high": "high",
                "xhigh": "extra high", "max": "maximum"}

# Measured for Haiku (harness notes: it adjudicated all twelve skipped-work
# appeals in a document as routine, where Sonnet recorded eleven gaps). Inferred
# from the name for the others.
_SMALL = re.compile(r"haiku|(?:^|-)(?:mini|nano)(?:-|$)")

# Custom model ids are passed as one argv item to the provider CLI. Restrict
# them to the punctuation model registries conventionally use, both to catch
# pasted prose early and to keep this local escape hatch unambiguous.
_CUSTOM_MODEL = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/+\-\[\]]{0,127}\Z")


# What Advanced settings may set a reading's caps to. Below these a reading of
# even a short paper cannot finish its first pass; above them a mistyped number
# would be an expensive one.
USD_RANGE = (0.1, 200.0)
MINUTES_RANGE = (1, 600)


class Unavailable(ValueError):
    """The page asked for an AI this machine cannot run."""


# The steps of a reading a reader can give to different AIs, as the page names
# them. Every pass of a harness's loop is one of the three: the first, which
# writes the companion; the revision (the loop's reviser); and the check —
# every other pass, the auditor of the Balanced pipeline, the graph builder and
# the two verifiers of the Thorough one.
STEP_NAMES = {
    "draft": ("First reading", "Reads the paper and writes the companion."),
    "check": ("The check", "Reads the companion against the paper, and says what is wrong."),
    "revision": ("The revision", "Answers what the check raised."),
}


def step_roles(harness: Harness) -> dict[str, list[str]]:
    """Each step of a reading by this harness, and the roles it covers; empty
    for a reading of one pass, which has nothing to give to another AI."""
    from atelier.compile import pass_sequence

    sequence = [role for role, _, _ in pass_sequence(harness)]
    if len(sequence) < 2:
        return {}
    out: dict[str, list[str]] = {"draft": ["draft"], "check": [], "revision": []}
    for role in sequence[1:]:
        slot = "revision" if role == harness.loop.reviser else "check"
        if role not in out[slot]:
            out[slot].append(role)
    return {slot: roles for slot, roles in out.items() if roles}


@dataclass(frozen=True, slots=True)
class Agent:
    account: str
    provider: str
    model: str
    effort: str
    custom: bool = False


@dataclass(frozen=True, slots=True)
class Offer:
    """One account and the models it can be asked to run."""
    account: str
    provider: str
    label: str
    models: tuple[str, ...]
    efforts: tuple[str, ...]
    default_model: str


# --------------------------------------------------------------------------
# names
# --------------------------------------------------------------------------


def model_name(model: str) -> str:
    """`claude-opus-4-8` → `Opus 4.8`; `gpt-5.4-mini` → `GPT-5.4 mini`.

    Anything that does not follow either pattern is shown as it is: an id
    nobody recognises is still better than a confident wrong name.
    """
    base, _, tail = model.partition("[")
    extra = f" ({tail.rstrip(']').upper()})" if tail else ""
    parts = base.split("-")
    if parts[0] == "claude" and len(parts) >= 2 and parts[1].isalpha():
        # A trailing snapshot date (`-20251001`) names a build, not a model.
        rest = [p for p in parts[2:] if not (len(p) == 8 and p.isdigit())]
        numbers = [p for p in rest if p.isdigit()]
        words = [p for p in rest if not p.isdigit()]
        name = " ".join([parts[1].capitalize(), ".".join(numbers), *words])
        return " ".join(name.split()) + extra
    if parts[0] == "gpt" and len(parts) >= 2:
        words = [w if w in ("mini", "nano") else w.capitalize() for w in parts[2:]]
        return " ".join([f"GPT-{parts[1]}", *words]) + extra
    return model


def full_name(provider: str, model: str) -> str:
    """The name a reader would use: `Claude Opus 5`, `GPT-5.6 Terra`,
    `DeepSeek V4 Pro`."""
    if provider == "open":
        # unpacman's long-window copy of a local model is that model.
        model = model.removeprefix("unpacman/")
        known = OPEN_MODELS.get(model)
        if known:
            return known["name"]
        # Until the host's own name for it is known: an id reads better without
        # its host's prefix (`deepseek/deepseek-v4-pro`) and the window asked
        # for (`[1m]`) — and Claude's through OpenRouter as Claude's do.
        bare = re.sub(r"\[[^\]]*\]$", "", model.split("/", 1)[-1])
        return f"Claude {model_name(bare)}" if bare.startswith("claude-") else bare
    name = model_name(model)
    if provider == "claude" and not name.lower().startswith("claude"):
        return f"Claude {name}"
    return name


def is_small(model: str) -> bool:
    model = model.removeprefix("unpacman/")
    if model in OPEN_MODELS:
        return bool(OPEN_MODELS[model]["small"])
    return bool(_SMALL.search(model.lower()))


def describe(provider: str | None, account: str | None, model: str | None,
             effort: str | None) -> dict[str, Any] | None:
    """What the page shows about the AI behind a run. A model reached through
    OpenRouter is named with it: "Claude Opus 5.5 through OpenRouter" is not
    the Claude of a Claude plan, nor DeepSeek's model DeepSeek's own service."""
    if not model:
        return None
    provider = provider or ""
    name = full_name(provider, model)
    if provider == "open" and account == OPENROUTER:
        name = f"{name} through OpenRouter"
    return {"provider": provider, "account": account, "model": model,
            "effort": effort, "name": name,
            "small": is_small(model), "web": searches(provider, account)}


# --------------------------------------------------------------------------
# what can run here
# --------------------------------------------------------------------------


def installed(provider: str) -> bool:
    """Whether this machine has the program that provider runs."""
    from atelier.providers import all_adapters

    adapter = all_adapters().get(provider)
    return adapter is not None and adapter.executable() is not None


def _efforts(provider: str) -> tuple[str, ...]:
    from atelier.providers import all_adapters

    adapter = all_adapters().get(provider)
    return tuple(adapter.capabilities.effort_levels) if adapter else ()


def _provider_from_id(model: str) -> str | None:
    """The provider suggested by a conventional model-id prefix."""
    if model.startswith("claude"):
        return "claude"
    if model.startswith(("gpt", "codex")) or re.match(r"o\d", model):
        return "codex"
    return None


def provider_of(cfg: Config, model: str) -> str | None:
    """The provider a model id belongs to, if it can be told."""
    for name, models in cfg.models.by_provider:
        if model in models:
            return name
    return _provider_from_id(model)


def offered(cfg: Config, harness: Harness, *, extra: str | None = None,
            open_models: dict[str, list[Any]] | None = None,
            catalog: dict[str, list[str]] | None = None) -> list[Offer]:
    """Every account a reading can run on here, with its models.

    `extra` is a model named on the command line; it is offered under its own
    provider even if the configuration does not list it, so the choice the
    server was started with is always one the page can show. `open_models` are
    the models each open-weight account can run now (openweight.py); an
    open-weight account with none is not offered. `catalog` is what each
    Claude or Codex account's own program says it offers (catalog.py): Claude
    Code names the newest model of each kind, and those come first, before the
    older ones the configuration lists; Codex lists every model the account
    can use, and those are the account's models. An account whose program
    could not say has the configuration's, as before.
    """
    accounts = [a for a in cfg.accounts.values()
                if harness.supports(a.provider) and installed(a.provider)]
    shared = {a.provider for a in accounts
              if sum(b.provider == a.provider for b in accounts) > 1}

    out: list[Offer] = []
    for acct in accounts:
        p = acct.provider
        if p == "open":
            listed = [m.id for m in (open_models or {}).get(acct.id, [])]
            if listed:
                out.append(Offer(account=acct.id, provider=p, label=acct.label or acct.id,
                                 models=tuple(listed),
                                 # DeepSeek is documented to take these, not "xhigh".
                                 efforts=DEEPSEEK_EFFORTS if acct.id == DEEPSEEK else _efforts(p),
                                 default_model=listed[0]))
            continue
        models = list(cfg.models.for_provider(p))
        # Never a Claude id for ChatGPT, or the reverse, whatever a program says.
        said = [m for m in (catalog or {}).get(acct.id, []) if provider_of(cfg, m) in (None, p)]
        if said and p == "codex":
            models = said
        elif said:
            models = [*said, *(m for m in models if m not in said)]
        if not models:
            fallback = cfg.defaults.model_for(p)
            # Without a list, the configured default is all there is — and a
            # Claude id must never be offered to ChatGPT, or the reverse.
            if provider_of(cfg, fallback) in (None, p):
                models = [fallback]
        if extra and extra not in models and provider_of(cfg, extra) == p:
            models.append(extra)
        if not models:
            continue
        preferred = cfg.defaults.model_for(p)
        out.append(Offer(
            account=acct.id, provider=p,
            # Two accounts on one provider are told apart by their own labels.
            label=(acct.label or acct.id) if p in shared else PROVIDER_NAMES.get(p, p),
            models=tuple(models), efforts=_efforts(p),
            default_model=preferred if preferred in models else models[0]))
    return out


def effort_for(offer: Offer, wanted: str | None) -> str:
    """The effort asked for, if this AI has it; else "high", else its first."""
    if not offer.efforts or (wanted and wanted in offer.efforts):
        return wanted or "high"
    return "high" if "high" in offer.efforts else offer.efforts[0]


_effort_for = effort_for


def default_agent(cfg: Config, harness: Harness, offers: list[Offer], *,
                  model: str | None = None, effort: str | None = None) -> Agent | None:
    """What a reading runs on when nobody chooses — `plan_run`'s own order."""
    if not offers:
        return None
    chosen = cfg.account(harness.defaults.account)
    provider = ((provider_of(cfg, model) if model else None)
                or (chosen.provider if chosen else None)
                or harness.defaults.provider or cfg.defaults.provider)
    if chosen and chosen.provider == provider:
        wanted = chosen.id
    else:
        fallback = cfg.default_account(provider)
        wanted = fallback.id if fallback else None
    offer = (next((o for o in offers if o.account == wanted), None)
             or next((o for o in offers if o.provider == provider), None)
             or offers[0])

    if model and offer.provider == provider and model in offer.models:
        pick = model
    elif harness.defaults.model and harness.defaults.model in offer.models:
        pick = harness.defaults.model
    else:
        pick = offer.default_model
    return Agent(account=offer.account, provider=offer.provider, model=pick,
                 effort=_effort_for(offer, effort or harness.defaults.effort
                                    or cfg.defaults.effort))


def choose(offers: list[Offer], default: Agent | None, *, account: str | None = None,
           model: str | None = None, effort: str | None = None,
           custom: bool = False) -> Agent:
    """The AI the page asked for, checked against what can run here.

    Whatever is left out comes from the default, so an older page — or a test —
    that sends only the level still gets a sensible reading.
    """
    if not offers or default is None:
        raise Unavailable(
            "No AI is available to read with here. Install Claude Code or "
            "Codex, then start unpacman again.")

    # A companion written by a custom model should, by default, be explained by
    # that same model. There is no explicit page choice in this path, so carry
    # the custom marker from the credited reading into the normal validation.
    if not custom and not account and not model and default.custom:
        custom, account, model = True, default.account, default.model

    if custom:
        if not model:
            raise Unavailable("Enter a custom model ID.")
        if not _CUSTOM_MODEL.fullmatch(model):
            raise Unavailable(
                "A custom model ID may contain letters, numbers, dots, dashes, "
                "underscores, slashes, colons, plus signs, and brackets.")
        if account:
            offer = next((o for o in offers if o.account == account), None)
            if offer is None:
                raise Unavailable("That AI is not available on this computer.")
        else:
            provider = _provider_from_id(model)
            offer = (next((o for o in offers if o.provider == provider), None)
                     if provider else None)
            if offer is None:
                raise Unavailable(
                    "Choose which installed AI account should run this custom model.")
        inferred = _provider_from_id(model)
        if inferred and inferred != offer.provider:
            name = PROVIDER_NAMES.get(offer.provider, offer.provider)
            raise Unavailable(f"{model} cannot be run with the selected {name} account.")
        if effort:
            if offer.efforts and effort not in offer.efforts:
                raise Unavailable(
                    f"Effort must be one of: {', '.join(offer.efforts)}.")
        else:
            effort = _effort_for(offer, default.effort)
        return Agent(account=offer.account, provider=offer.provider, model=model,
                     effort=effort, custom=True)

    if account:
        offer = next((o for o in offers if o.account == account), None)
        if offer is None:
            raise Unavailable("That AI is not available on this computer.")
    elif model:
        offer = next((o for o in offers if model in o.models), None)
        if offer is None:
            raise Unavailable(f"{model_name(model)} is not offered here.")
    else:
        offer = next((o for o in offers if o.account == default.account), offers[0])

    same = offer.account == default.account
    model = model or (default.model if same else offer.default_model)
    if model not in offer.models:
        raise Unavailable(f"{model_name(model)} is not offered for {offer.label}.")
    if effort:
        if offer.efforts and effort not in offer.efforts:
            raise Unavailable(
                f"Effort must be one of: {', '.join(offer.efforts)}.")
    else:
        effort = _effort_for(offer, default.effort)
    return Agent(account=offer.account, provider=offer.provider, model=model,
                 effort=effort)


def caps(cfg: Config, harness: Harness, model: str, *, max_usd: float | None,
         max_minutes: float | None) -> dict[str, float | None]:
    """The limits a reading on `model` will run under, for the page to state.

    The same rule as `plan_run`: a model with no per-token price cannot be held
    to a dollar cap — it would report a cost of zero and never trip it — so it
    is held to time instead. A test keeps the two in step.
    """
    b = harness.budget
    usd = max_usd if max_usd is not None else b.max_usd
    minutes = max_minutes if max_minutes is not None else b.max_minutes
    if cfg.prices.lookup(model) is None:
        usd = None
        if not (minutes or b.max_tokens_out or b.max_turns):
            minutes = b.max_minutes or 30.0
    return {"usd": usd, "minutes": minutes}


def menu(cfg: Config, harness: Harness, offers: list[Offer], default: Agent | None, *,
         pinned: bool, max_usd: float | None, max_minutes: float | None) -> dict[str, Any]:
    """Everything the page needs to draw the choice."""
    return {
        "groups": [{
            "account": o.account, "provider": o.provider, "label": o.label,
            "max_concurrent": (cfg.account(o.account).max_concurrent
                               if cfg.account(o.account) else None),
            # A model through OpenRouter says so in its own name: a closed menu
            # shows the model, not its group, and "Claude Opus 5.5" from a
            # Claude plan and from OpenRouter's credit are billed differently.
            "models": [{"id": m, "name": full_name(o.provider, m)
                        + (f" ({o.label})" if o.account == OPENROUTER else ""),
                        "small": is_small(m),
                        # Whether a dollar cap can bind at all: an unpriced
                        # model reports a cost of zero and is held to time.
                        "priced": cfg.prices.lookup(m) is not None,
                        # On this Mac: no cost at all, so time is the limit.
                        "free": bool(OPEN_MODELS.get(m, {}).get("free"))
                                if o.provider == "open" else False,
                        "note": OPEN_MODELS.get(m, {}).get("note", "")
                                if o.provider == "open" else "",
                        "cap": caps(cfg, harness, m, max_usd=max_usd,
                                    max_minutes=max_minutes)}
                       for m in o.models],
            "efforts": [{"id": e, "name": EFFORT_NAMES.get(e, e)} for e in o.efforts],
            # Whether a reading on it can search the web for its references.
            "web": searches(o.provider, o.account),
        } for o in offers],
        "default": ({"account": default.account, "model": default.model,
                     "effort": default.effort} if default else None),
        # Set on the command line: whoever typed it meant it for this session,
        # so it outranks whatever this browser remembers choosing last time.
        "pinned": pinned,
        # The caps a reading has unless Advanced settings says otherwise, and
        # the range the server will accept there.
        "limits": {
            "max_usd": max_usd if max_usd is not None else harness.budget.max_usd,
            "max_minutes": (max_minutes if max_minutes is not None
                            else harness.budget.max_minutes),
            "usd_range": list(USD_RANGE), "minutes_range": list(MINUTES_RANGE),
            "max_concurrent_runs": cfg.limits.max_concurrent_runs,
            "concurrent_range": [1, cfg.limits.max_concurrent_runs],
        },
    }
