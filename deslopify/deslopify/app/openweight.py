"""Models beyond a Claude or ChatGPT plan, for unpacman: open-weight ones on
this Mac with Ollama, DeepSeek's from DeepSeek itself, and any of many through
OpenRouter — Claude's among them.

All are reached through Claude Code (`atelier.providers.openweight`), so a
reading by any of them is the same reading in every way the method cares
about. What this module adds is what the reader needs around it:

- **where they are** — three accounts the reader always has
  (`with_accounts`): "On this Mac" (Ollama, at its usual address),
  "OpenRouter" and "DeepSeek" (each a key the reader pastes — in Advanced
  settings, or for DeepSeek on Getting ready — kept in a file only they can
  read, never sent back to the page);
- **which models** — Ollama's own list of what is downloaded (only models that
  can use tools: a reading writes files), and a short list from OpenRouter's
  public catalogue: the newest models of the best-known open-weight families,
  each able to use tools and to hold a long paper (`suggested`);
- **what they cost** — OpenRouter publishes a price per token for each model,
  which is added to the price table so that a money limit binds; a model on
  this Mac costs nothing and is held to time.

Nothing here runs without the reader asking: Ollama is only looked for at its
address on this computer, and OpenRouter's catalogue (a public list, no
account) is fetched once a day at most, when the AI menu is drawn.

**DeepSeek** (2026-10-13) is offered as an AI of its own, beside Claude and
ChatGPT, at the reader's request. Its own service speaks Anthropic's API
(`api.deepseek.com/anthropic`, documented for Claude Code), so it reads the
same way — and, unlike Ollama and OpenRouter, it answers Claude Code's web
search itself, so its companions' references are searched, not remembered.
It is paid in advance, per token, at DeepSeek's prices (DEEPSEEK_MODELS).

**OpenRouter** (2026-10-15) is an AI of its own too, at the reader's request:
one account, paid per use, for models of many makers. OpenRouter documents
Claude Code as working with it — "only guaranteed to work with the Anthropic
first-party provider" — so Claude's own models come first among its
suggestions (the newest of each kind), then the open-weight ones offered since
2026-10-08. Any other model OpenRouter runs can be named as a custom model.
"""

from __future__ import annotations

import contextlib
import json
import os
import re
import shutil
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from atelier.config import Account, Config, Price

from .places import var_dir

OLLAMA = "ollama"
OPENROUTER = "openrouter"
DEEPSEEK = "deepseek"
OLLAMA_URL = "http://127.0.0.1:11434"         # where Ollama listens unless told otherwise
OPENROUTER_URL = "https://openrouter.ai/api"
CATALOGUE_URL = "https://openrouter.ai/api/v1/models"
KEY_URL = "https://openrouter.ai/api/v1/key"
# DeepSeek's Anthropic-compatible endpoint, which Claude Code is pointed at,
# and its own API, which says what a key may do (balance) and what it offers.
DEEPSEEK_URL = "https://api.deepseek.com/anthropic"
DEEPSEEK_API = "https://api.deepseek.com"
LABELS = {OLLAMA: "On this Mac (Ollama)", OPENROUTER: "OpenRouter", DEEPSEEK: "DeepSeek"}
# Hosts that run Claude Code's web search for it (Anthropic's server tool):
# DeepSeek's API does (api-docs.deepseek.com, Claude Code integration).
SEARCHES = {DEEPSEEK}

# DeepSeek's models: id, name, and its price in USD per million tokens —
# input, output, input read from its cache — at its peak rates (weekdays
# 09–12 and 14–18 Beijing time), which a money limit binds at; every other
# hour, weekends and Chinese public holidays included, costs half.
# Checked on 2026-10-13 at api-docs.deepseek.com/quick_start/pricing. Each is
# offered with Claude Code's "[1m]" suffix: DeepSeek holds a million tokens,
# and Claude Code otherwise assumes 200,000 and condenses the session early
# (it strips the suffix before asking: seen against a stand-in, 2026-10-13).
DEEPSEEK_MODELS = (
    ("deepseek-v4-pro", "DeepSeek V4 Pro", 1.32, 3.96, 0.044),
    ("deepseek-flash", "DeepSeek V4.1 Flash", 0.30, 1.20, 0.006),
)
DEEPSEEK_WINDOW = "[1m]"
# Claude Code's effort levels that DeepSeek is documented to take
# (`output_config.effort`): not "xhigh", Anthropic's own.
DEEPSEEK_EFFORTS = ("low", "medium", "high", "max")

CATALOGUE_TTL = 12 * 3600          # seconds a fetched catalogue is used for
OLLAMA_TTL = 15.0                  # seconds Ollama's list of models is trusted for
# A reading holds the paper, the method and Claude Code's own instructions at
# once; a model that cannot hold this much would lose the paper mid-reading.
MIN_CONTEXT = 100_000
# Claude's kinds, offered first from OpenRouter, the newest of each — in the
# order Claude Code itself lists them.
CLAUDE_KINDS = ("opus", "fable", "sonnet", "haiku")
# A window this large is asked for with Claude Code's "[1m]", as OpenRouter's
# guide to Claude Code says to ("Claude Code assumes a 200k context window
# unless the model name ends in [1m]"; "OpenRouter strips the marker before
# routing, so it only changes what Claude Code assumes about the window"):
# otherwise a session is condensed at a fifth of what the model holds.
LONG_WINDOW = 1_000_000
WINDOW_1M = "[1m]"
# The open-weight families offered from OpenRouter, in this order, and how many
# of each (the newest). Others can still be named as a custom model.
FAMILIES = ("deepseek", "qwen", "moonshotai", "z-ai", "minimax", "openai",
            "meta-llama", "mistralai", "google", "nvidia")
PER_FAMILY = 2
# Models that judge other models' text rather than read: a safety classifier
# ("gpt-oss-safeguard-20b", "llama-guard-…") can use tools, and cannot read.
_NOT_A_READER = re.compile(r"(?:^|[-/])(?:safe)?guard(?:$|[-:])")
# At or below this many billions of parameters a model is called small: fine
# for trying unpacman out, likely to miss what a reading exists to find.
SMALL_BILLIONS = 20.0

_UA = "unpacman/1 (a reader for mathematics papers)"
_KEY_FORMAT = re.compile(r"^sk-or-[A-Za-z0-9_-]{16,200}$")
# DeepSeek's keys are "sk-" and letters and digits only: no "sk-or-" (an
# OpenRouter key), "sk-ant-" (Anthropic's) or "sk-proj-" (OpenAI's).
_DEEPSEEK_KEY = re.compile(r"^sk-[A-Za-z0-9]{16,128}$")
_BILLIONS = re.compile(r"(?<![\d.])(\d+(?:\.\d+)?)\s*b(?![a-z])", re.IGNORECASE)


class OpenError(RuntimeError):
    """Something about an open-weight model, said so a reader can act on it."""


# --------------------------------------------------------------------------
# the two accounts
# --------------------------------------------------------------------------


def ollama_url() -> str:
    """Where Ollama is: its own OLLAMA_HOST setting, if there is one (an Ollama
    told to listen on another port), else its usual address."""
    host = os.environ.get("OLLAMA_HOST", "").strip().rstrip("/")
    if not host:
        return OLLAMA_URL
    if "://" not in host:
        host = f"http://{host}"
    scheme, _, rest = host.partition("://")
    # "Listen everywhere" is reached, from here, on this computer's own address.
    rest = rest.replace("0.0.0.0", "127.0.0.1")
    if ":" not in rest.split("/", 1)[0]:
        rest = rest.replace("/", ":11434/", 1) if "/" in rest else f"{rest}:11434"
    return f"{scheme}://{rest}"


def key_path(cfg: Config, host: str = OPENROUTER) -> Path:
    return var_dir(cfg) / "keys" / host


def accounts(cfg: Config) -> dict[str, Account]:
    return {
        OLLAMA: Account(id=OLLAMA, provider="open", auth="local", label=LABELS[OLLAMA],
                        base_url=ollama_url(), max_concurrent=1, color="#6B7280"),
        OPENROUTER: Account(id=OPENROUTER, provider="open", auth="api_key",
                            label=LABELS[OPENROUTER], base_url=OPENROUTER_URL,
                            key_ref=f"file:{key_path(cfg)}", max_concurrent=2,
                            color="#7C5CFC"),
        DEEPSEEK: Account(id=DEEPSEEK, provider="open", auth="api_key",
                          label=LABELS[DEEPSEEK], base_url=DEEPSEEK_URL,
                          key_ref=f"file:{key_path(cfg, DEEPSEEK)}", max_concurrent=3,
                          color="#4D6BFE"),
    }


def with_accounts(cfg: Config) -> Config:
    """`cfg` with the open-weight accounts, unless it declares its own; and
    DeepSeek's prices, which are known before any model is listed."""
    cfg.prices.add(deepseek_prices())
    if any(a.provider == "open" for a in cfg.accounts.values()):
        return cfg
    return replace(cfg, accounts={**cfg.accounts, **accounts(cfg)})


# --------------------------------------------------------------------------
# the OpenRouter key
# --------------------------------------------------------------------------


def has_key(cfg: Config, host: str = OPENROUTER) -> bool:
    try:
        return bool(key_path(cfg, host).read_text(encoding="utf-8").strip())
    except OSError:
        return False


def key_hint(cfg: Config, host: str = OPENROUTER) -> str:
    """The last four characters, which is all the page is ever shown."""
    try:
        key = key_path(cfg, host).read_text(encoding="utf-8").strip()
    except OSError:
        return ""
    return f"…{key[-4:]}" if len(key) > 8 else ""


def looks_like_key(key: str, host: str = OPENROUTER) -> bool:
    """Whether `key` has the form of the host's keys — checked before it is
    sent anywhere, so that nothing pasted by mistake leaves this computer."""
    pattern = _DEEPSEEK_KEY if host == DEEPSEEK else _KEY_FORMAT
    return bool(pattern.fullmatch(key.strip()))


def check_key(key: str, timeout: float = 10.0) -> dict[str, Any]:
    """What OpenRouter says of a key (`/api/v1/key`): its label, what it has
    spent, the limit set on it and what is left of that (none: no limit), and
    whether its account has ever bought credit — or why it cannot say."""
    request = urllib.request.Request(KEY_URL, headers={
        "Authorization": f"Bearer {key}", "User-Agent": _UA})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as r:
            data = json.loads(r.read().decode("utf-8")).get("data") or {}
    except urllib.error.HTTPError as exc:
        if exc.code in (401, 403):
            raise OpenError("OpenRouter does not recognise that key. Copy it again from "
                            "openrouter.ai/keys.") from exc
        raise OpenError(f"OpenRouter could not check the key (it answered {exc.code}).") from exc
    except (OSError, ValueError) as exc:
        raise OpenError("OpenRouter could not be reached to check the key. Check the "
                        "internet connection, and try again.") from exc
    data = data if isinstance(data, dict) else {}
    return {"label": str(data.get("label") or ""), "usage": _dollars(data.get("usage")),
            "limit": _dollars(data.get("limit")),
            "remaining": _dollars(data.get("limit_remaining")),
            "free_tier": bool(data.get("is_free_tier"))}


def _dollars(value: Any) -> float | None:
    """A sum OpenRouter reports, in dollars; None when it reports none."""
    try:
        return None if value is None else round(float(value), 4)
    except (TypeError, ValueError):
        return None


def save_key(cfg: Config, key: str, host: str = OPENROUTER) -> None:
    """Keep a key where only this user can read it (var/ is never shared)."""
    key = key.strip()
    if not looks_like_key(key, host):
        raise OpenError(KEY_FORMS.get(host, KEY_FORMS[OPENROUTER]))
    path = key_path(cfg, host)
    path.parent.mkdir(parents=True, exist_ok=True)
    with contextlib.suppress(OSError):
        os.chmod(path.parent, 0o700)
    tmp = path.with_suffix(".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(key + "\n")
    tmp.replace(path)


def forget_key(cfg: Config, host: str = OPENROUTER) -> None:
    with contextlib.suppress(FileNotFoundError):
        key_path(cfg, host).unlink()


# What a key of each host looks like, said when what was pasted is not one.
KEY_FORMS = {
    OPENROUTER: "That does not look like an OpenRouter key, which begins with “sk-or-”.",
    DEEPSEEK: "That does not look like a DeepSeek key, which begins with “sk-” followed by "
              "letters and digits. Copy it again from platform.deepseek.com/api_keys.",
}


def check_deepseek_key(key: str, timeout: float = 10.0) -> dict[str, Any]:
    """What DeepSeek says of a key: whether it may be used now, and the
    balance it has (DeepSeek is paid in advance), or why not."""
    request = urllib.request.Request(f"{DEEPSEEK_API}/user/balance", headers={
        "Authorization": f"Bearer {key}", "Accept": "application/json", "User-Agent": _UA})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as r:
            data = json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        if exc.code in (401, 403):
            raise OpenError("DeepSeek does not recognise that key. Copy it again from "
                            "platform.deepseek.com/api_keys.") from exc
        raise OpenError(f"DeepSeek could not check the key (it answered {exc.code}).") from exc
    except (OSError, ValueError) as exc:
        raise OpenError("DeepSeek could not be reached to check the key. Check the "
                        "internet connection, and try again.") from exc
    return deepseek_balance(data)


def deepseek_balance(data: Any) -> dict[str, Any]:
    """DeepSeek's answer about a key, as the page says it: usable or not, and
    the balance in each currency it holds ("4.20 USD")."""
    data = data if isinstance(data, dict) else {}
    balances = []
    for info in data.get("balance_infos") or []:
        if isinstance(info, dict) and info.get("total_balance") is not None:
            balances.append(f"{info.get('total_balance')} {info.get('currency') or ''}".strip())
    return {"available": bool(data.get("is_available")), "balance": ", ".join(balances)}


# --------------------------------------------------------------------------
# models
# --------------------------------------------------------------------------


@dataclass(slots=True)
class Model:
    id: str
    name: str
    small: bool = False
    free: bool = False              # on this Mac: no price, held to time
    context: int = 0
    note: str = ""                  # "2.0B, 1.4 GB" — what the menu may add
    price: Price | None = None      # per million tokens, from the host


def _billions(text: str) -> float | None:
    found = [float(x) for x in _BILLIONS.findall(text or "")]
    return max(found) if found else None


def _small(text: str) -> bool:
    n = _billions(text)
    return (n is not None and n <= SMALL_BILLIONS) or bool(
        re.search(r"(?:^|[-_/:])(?:mini|nano|tiny)(?:$|[-_:])", text.lower()))


def _opener() -> urllib.request.OpenerDirector:
    # This computer's own Ollama: never through a proxy (instance.py has why).
    return urllib.request.build_opener(urllib.request.ProxyHandler({}))


def _get_json(url: str, timeout: float, *, local: bool = False) -> Any:
    request = urllib.request.Request(url, headers={"User-Agent": _UA})
    opener = _opener() if local else urllib.request.build_opener()
    with opener.open(request, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def ollama_installed() -> bool:
    return bool(shutil.which("ollama") or Path("/Applications/Ollama.app").is_dir()
                or (Path.home() / "Applications" / "Ollama.app").is_dir()
                or Path("/opt/homebrew/bin/ollama").is_file()
                or Path("/usr/local/bin/ollama").is_file())


def ollama_status(base: str | None = None, timeout: float = 1.5) -> dict[str, Any]:
    """Whether Ollama is running here, its version, and the models it has that
    can read — those that can use tools (a reading writes files)."""
    base = (base or ollama_url()).rstrip("/")
    out: dict[str, Any] = {"installed": ollama_installed(), "running": False,
                           "version": "", "models": [], "other": []}
    try:
        out["version"] = str(_get_json(f"{base}/api/version", timeout, local=True)
                             .get("version") or "")
        tags = _get_json(f"{base}/api/tags", timeout, local=True)
    except (OSError, ValueError):
        return out
    out["running"] = out["installed"] = True
    for m in tags.get("models") or []:
        ident = str(m.get("model") or m.get("name") or "")
        # unpacman's own long-window copies are the reader's models already.
        if not ident or ident.startswith(VARIANT):
            continue
        details = m.get("details") or {}
        caps = m.get("capabilities")
        size = f"{(m.get('size') or 0) / 1e9:.1f} GB" if m.get("size") else ""
        params = str(details.get("parameter_size") or "")
        model = Model(
            id=ident, name=ident, free=True,
            small=_small(params) or _small(ident),
            context=int(details.get("context_length") or 0),
            note=", ".join(x for x in (params, size) if x))
        # An Ollama too old to say what a model can do is given the benefit of
        # the doubt; one that says a model cannot use tools is believed.
        (out["models"] if caps is None or "tools" in caps else out["other"]).append(model)
    return out


def _price(entry: dict[str, Any]) -> Price | None:
    """A model's price from OpenRouter's catalogue, per million tokens — the
    highest a request can be billed at. Some prices change with the request:
    above so many tokens of prompt (`overrides` with `min_prompt_tokens`:
    Claude Haiku 5.5 costs five times as much above 100,000) or at some hours
    of the day. A pass's cost is counted from its totals, which cannot tell
    one request from another, and a money limit must never let more be spent
    than it says."""
    pricing = entry.get("pricing") or {}
    if not isinstance(pricing, dict):
        return None
    try:
        float(pricing.get("prompt") or 0)
        float(pricing.get("completion") or 0)
    except (TypeError, ValueError):
        return None
    tiers = [pricing, *(o for o in pricing.get("overrides") or [] if isinstance(o, dict))]

    def most(key: str) -> float:
        best = 0.0
        for tier in tiers:
            try:
                best = max(best, float(tier.get(key) or 0) * 1e6)
            except (TypeError, ValueError):
                continue
        return best

    prompt, completion = most("prompt"), most("completion")
    if prompt <= 0 and completion <= 0:
        return None

    def ratio(key: str) -> float:
        value = most(key)
        return value / prompt if value > 0 and prompt > 0 else 1.0

    return Price(model=str(entry["id"]), input=prompt, output=completion,
                 cache_read_mult=ratio("input_cache_read"),
                 cache_write_mult=ratio("input_cache_write"),
                 valid_from=datetime.now(UTC).date())


def _display(entry: dict[str, Any]) -> str:
    name = str(entry.get("name") or entry.get("id") or "")
    return name.split(": ", 1)[1] if ": " in name else name


def _can_read(entry: dict[str, Any]) -> bool:
    """A model a reading can run on: one that can use tools (a reading writes
    files) and hold a long paper, at a price (the free and batch variants are
    left out: rate-limited, or not for a live reading)."""
    ident = str(entry.get("id") or "")
    try:
        context = int(entry.get("context_length") or 0)
    except (TypeError, ValueError):
        return False
    return (bool(ident) and ":" not in ident and not _NOT_A_READER.search(ident.lower())
            and "tools" in (entry.get("supported_parameters") or [])
            and context >= MIN_CONTEXT and _price(entry) is not None)


def _claude_version(ident: str, kind: str) -> tuple[int, ...] | None:
    """`anthropic/claude-opus-5.5` → (5, 5) for kind "opus"; None for any
    other id — a dated or otherwise decorated one included."""
    found = re.fullmatch(rf"anthropic/claude-{kind}-(\d+(?:\.\d+)*)", ident)
    return tuple(int(n) for n in found.group(1).split(".")) if found else None


def claude_suggested(catalogue: list[dict[str, Any]]) -> list[Model]:
    """Claude's own models through OpenRouter: the newest of each kind that can
    read (CLAUDE_KINDS), each asked for with its whole window (LONG_WINDOW)."""
    out: list[Model] = []
    for kind in CLAUDE_KINDS:
        ranked = [((_claude_version(str(e.get("id") or ""), kind) or ()), e.get("created") or 0, e)
                  for e in catalogue if isinstance(e, dict) and _can_read(e)
                  and _claude_version(str(e.get("id") or ""), kind) is not None]
        if not ranked:
            continue
        _, _, entry = max(ranked, key=lambda r: (r[0], r[1]))
        ident = str(entry["id"])
        context = int(entry.get("context_length") or 0)
        out.append(Model(id=ident + (WINDOW_1M if context >= LONG_WINDOW else ""),
                         name=_display(entry), small=kind == "haiku", context=context,
                         price=_price(entry)))
    return out


def suggested(catalogue: list[dict[str, Any]]) -> list[Model]:
    """From OpenRouter's catalogue: Claude's newest models first
    (claude_suggested), then open-weight models (those with published weights)
    that can read, the newest few of each well-known family — each holding a
    million tokens or more asked for with its whole window."""
    by_family: dict[str, list[dict[str, Any]]] = {f: [] for f in FAMILIES}
    for entry in catalogue:
        if not isinstance(entry, dict):
            continue
        family = str(entry.get("id") or "").split("/", 1)[0]
        if family in by_family and entry.get("hugging_face_id") and _can_read(entry):
            by_family[family].append(entry)
    out = claude_suggested([e for e in catalogue if isinstance(e, dict)])
    for family in FAMILIES:
        newest = sorted(by_family[family], key=lambda e: e.get("created") or 0, reverse=True)
        for entry in newest[:PER_FAMILY]:
            ident = str(entry["id"])
            context = int(entry.get("context_length") or 0)
            out.append(Model(id=ident + (WINDOW_1M if context >= LONG_WINDOW else ""),
                             name=_display(entry), small=_small(ident), context=context,
                             price=_price(entry)))
    return out


def deepseek_prices() -> list[Price]:
    """DeepSeek's models' prices, for the price table (DEEPSEEK_MODELS)."""
    return [Price(model=ident, input=i, output=o,
                  cache_read_mult=hit / i, cache_write_mult=1.0,
                  valid_from=datetime(2026, 10, 13, tzinfo=UTC).date())
            for ident, _, i, o, hit in DEEPSEEK_MODELS]


def deepseek_models(listed: list[str] | None = None) -> list[Model]:
    """What DeepSeek offers: its models as known here, in this order (the
    strongest first), and any other its API lists — without a price, so held
    to time. Each with the suffix that gives it its whole window."""
    known = {ident: (name, i, o, hit) for ident, name, i, o, hit in DEEPSEEK_MODELS}
    out = [Model(id=ident + DEEPSEEK_WINDOW, name=name, context=1_048_576,
                 price=next(p for p in deepseek_prices() if p.model == ident))
           for ident, (name, _, _, _) in known.items()]
    for ident in listed or []:
        if ident and ident not in known and re.fullmatch(r"[a-z0-9][a-z0-9._-]{0,63}", ident):
            name = " ".join(w.upper() if re.fullmatch(r"v\d[\w.]*", w) else w.capitalize()
                            for w in ident.split("-"))
            out.append(Model(id=ident + DEEPSEEK_WINDOW, name=name.replace("Deepseek", "DeepSeek"),
                             context=1_048_576))
    return out


def deepseek_listed(key: str, timeout: float = 8.0) -> list[str]:
    """The model ids DeepSeek's API lists for this key; [] if it cannot say."""
    request = urllib.request.Request(f"{DEEPSEEK_API}/models", headers={
        "Authorization": f"Bearer {key}", "Accept": "application/json", "User-Agent": _UA})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as r:
            data = json.loads(r.read().decode("utf-8"))
    except (OSError, ValueError):
        return []
    return [str(m.get("id") or "") for m in (data.get("data") or []) if isinstance(m, dict)]


def memory_gb() -> int:
    """This computer's memory, in GB: what decides which models fit on it."""
    try:
        # In the units a Mac is sold in: 48 GB is 48 × 2^30 bytes.
        return round(os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES") / 2**30)
    except (ValueError, OSError, AttributeError):
        return 0


# Models to download in Ollama, by the memory a Mac needs to run one and still
# be usable: about twice the download, since a whole paper (a 128k-token
# window) needs several GB more and the Mac itself needs room. Measured on
# 2026-10-08: gpt-oss:20b with its long window took 13 GB of GPU memory on a
# 48 GB Mac, and the Mac, with other work open, went 17 GB into swap. All can
# use tools. Checked against Ollama's library on 2026-10-08.
LOCAL_MODELS = (
    (128, "gpt-oss:120b", "65 GB — the strongest that runs on a large Mac"),
    (64, "qwen3:30b", "19 GB — strong, and quick for its size"),
    (32, "gpt-oss:20b", "14 GB — a good first choice"),
    (16, "qwen3:8b", "5 GB — small, and holds only a short paper"),
)


def local_suggestions(memory: int | None = None) -> list[dict[str, str]]:
    """The models this Mac has room for, the largest first."""
    memory = memory_gb() if memory is None else memory
    return [{"id": ident, "about": about} for need, ident, about in LOCAL_MODELS
            if memory >= need]


# --------------------------------------------------------------------------
# a whole paper at once, on this Mac
# --------------------------------------------------------------------------

# Ollama gives a model a window of its own choosing (32,768 tokens on a 48 GB
# Mac, measured 2026-10-08) and silently drops what does not fit — most of a
# forty-page paper. A request cannot ask for more. So unpacman reads with a
# copy of the model made to hold more, `unpacman/<model>`: same weights, no
# extra space, its window set to the most the model can take, up to this.
LONG_CONTEXT = 131_072
VARIANT = "unpacman/"
_variants: dict[str, int] = {}              # copy → its window, made this session


def _post_json(url: str, body: dict[str, Any], timeout: float) -> Any:
    request = urllib.request.Request(url, data=json.dumps(body).encode("utf-8"), headers={
        "Content-Type": "application/json", "User-Agent": _UA})
    with _opener().open(request, timeout=timeout) as r:
        raw = r.read().decode("utf-8")
    # /api/create answers with one line per step; the last says how it ended.
    lines = [line for line in raw.splitlines() if line.strip()]
    return json.loads(lines[-1]) if lines else {}


def base_model(model: str) -> str:
    return model[len(VARIANT):] if model.startswith(VARIANT) else model


def model_context(model: str, base: str | None = None) -> int:
    """The most a local model can hold, in tokens, as Ollama reports it."""
    base = (base or ollama_url()).rstrip("/")
    try:
        info = _post_json(f"{base}/api/show", {"model": base_model(model)}, 10.0)
    except (OSError, ValueError):
        return 0
    for key, value in (info.get("model_info") or {}).items():
        if key.endswith(".context_length") and isinstance(value, int):
            return value
    return 0


def long_context(model: str, base: str | None = None) -> tuple[str, int]:
    """The copy of a local model that holds the most it can (made if need be),
    and how much that is. Blocking: run it in a thread."""
    base = (base or ollama_url()).rstrip("/")
    original = base_model(model)
    window = min(model_context(original, base) or LONG_CONTEXT, LONG_CONTEXT)
    variant = f"{VARIANT}{original}"
    if _variants.get(variant) == window:
        return variant, window
    try:
        done = _post_json(f"{base}/api/create", {"model": variant, "from": original,
                                                 "parameters": {"num_ctx": window}}, 120.0)
    except urllib.error.HTTPError as exc:
        raise OpenError(f"Ollama could not prepare {original} for a whole paper "
                        f"(it answered {exc.code}).") from exc
    except (OSError, ValueError) as exc:
        raise OpenError("Ollama did not answer. Is it still running?") from exc
    if done.get("status") != "success":
        raise OpenError(f"Ollama could not prepare {original} for a whole paper: "
                        f"{done.get('error') or done.get('status') or 'no reason given'}.")
    _variants[variant] = window
    return variant, window


# Measured on 2026-10-08 (a reading of a two-page paper through Ollama): Claude
# Code's own instructions and tools come to some 18,000 tokens before anything
# of ours; and a later pass holds the companion it checks as well, and answers.
CLAUDE_CODE_TOKENS = 18_000
LATER_PASS_TOKENS = 12_000
CHARS_PER_TOKEN = 3.6


def tokens_for(chars: int, *, session: bool = True) -> int:
    """About how many tokens a model must hold at once for `chars` characters
    of prompt: a reading's session (its prompt and method, as planned, and
    room for what a later pass adds), or one call (an explanation), which
    replaces Claude Code's instructions with its own and uses no tools."""
    extra = CLAUDE_CODE_TOKENS + LATER_PASS_TOKENS if session else 2_000
    return int(chars / CHARS_PER_TOKEN) + extra


# --------------------------------------------------------------------------
# downloading a model, from the page
# --------------------------------------------------------------------------

_MODEL_NAME = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}(?::[a-z0-9][a-z0-9._-]{0,63})?$")


@dataclass
class Pull:
    """One model being downloaded by Ollama, as the page shows it."""
    model: str
    state: str = "running"          # running | done | failed | cancelled
    completed: int = 0
    total: int = 0
    status: str = "Starting…"
    error: str = ""
    _stop: threading.Event = field(default_factory=threading.Event)

    def to_dict(self) -> dict[str, Any]:
        return {"model": self.model, "state": self.state, "completed": self.completed,
                "total": self.total, "status": self.status, "error": self.error}

    def cancel(self) -> None:
        self._stop.set()

    def run(self, base: str | None = None) -> None:
        """Ask Ollama for the model and follow its progress. Blocking."""
        base = (base or ollama_url()).rstrip("/")
        request = urllib.request.Request(f"{base}/api/pull", data=json.dumps(
            {"model": self.model, "stream": True}).encode("utf-8"), headers={
            "Content-Type": "application/json", "User-Agent": _UA})
        try:
            with _opener().open(request, timeout=60) as r:
                for raw in r:
                    if self._stop.is_set():
                        self.state, self.status = "cancelled", "Stopped."
                        return
                    with contextlib.suppress(ValueError):
                        step = json.loads(raw)
                        if step.get("error"):
                            raise OpenError(str(step["error"]))
                        if step.get("total"):
                            self.total, self.completed = int(step["total"]), int(
                                step.get("completed") or 0)
                        self.status = str(step.get("status") or self.status)
                        if step.get("status") == "success":
                            self.state, self.status = "done", "Downloaded."
                            return
            self.state = "failed"
            self.error = "Ollama stopped before the model was downloaded."
        except OpenError as exc:
            self.state, self.error = "failed", f"Ollama could not download {self.model}: {exc}"
        except (OSError, ValueError) as exc:
            self.state = "failed"
            self.error = f"Ollama could not be reached ({exc}). Is it still running?"


def can_pull(model: str) -> bool:
    """A name Ollama's library could have — nothing else is sent to it."""
    return bool(_MODEL_NAME.fullmatch(model))


# --------------------------------------------------------------------------
# kept for the server
# --------------------------------------------------------------------------


@dataclass
class Discovery:
    """What can run, looked at no more often than it changes: Ollama's list
    every few seconds at most, OpenRouter's catalogue twice a day."""
    cfg: Config
    _ollama: dict[str, Any] = field(default_factory=dict)
    _ollama_at: float = 0.0
    _catalogue: list[dict[str, Any]] = field(default_factory=list)
    _catalogue_at: float = 0.0
    _deepseek: list[str] = field(default_factory=list)
    _deepseek_at: float = 0.0
    _last: dict[str, list[Model]] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    @property
    def cache(self) -> Path:
        return var_dir(self.cfg) / "openrouter-models.json"

    def ollama(self, *, fresh: bool = False) -> dict[str, Any]:
        with self._lock:
            if fresh or time.monotonic() - self._ollama_at > OLLAMA_TTL:
                self._ollama = ollama_status()
                self._ollama_at = time.monotonic()
            return self._ollama

    def catalogue(self, *, fetch: bool = True) -> list[dict[str, Any]]:
        """OpenRouter's public list of models: kept on disk, fetched again
        when older than CATALOGUE_TTL (and `fetch` allows); the copy on disk
        if the internet is not there."""
        with self._lock:
            if self._catalogue and time.time() - self._catalogue_at < CATALOGUE_TTL:
                return self._catalogue
            kept: dict[str, Any] = {}
            with contextlib.suppress(OSError, ValueError):
                kept = json.loads(self.cache.read_text(encoding="utf-8"))
            at = float(kept.get("fetched") or 0)
            models = kept.get("models") if isinstance(kept.get("models"), list) else []
            if fetch and (not models or time.time() - at > CATALOGUE_TTL):
                try:
                    got = _get_json(CATALOGUE_URL, 8.0).get("data")
                    if isinstance(got, list) and got:
                        models, at = got, time.time()
                        self.cache.parent.mkdir(parents=True, exist_ok=True)
                        self.cache.write_text(json.dumps({"fetched": at, "models": got}),
                                              encoding="utf-8")
                except (OSError, ValueError):
                    pass
            self._catalogue, self._catalogue_at = models, at or time.time()
            return self._catalogue

    def models(self, *, fetch: bool = True) -> dict[str, list[Model]]:
        """The models each open-weight account can run now: Ollama's, if it is
        running; OpenRouter's suggestions, if a key is saved. Blocking (it may
        ask Ollama, and fetch the catalogue): run it in a thread."""
        out: dict[str, list[Model]] = {OLLAMA: [], OPENROUTER: [], DEEPSEEK: []}
        status = self.ollama()
        if status.get("running"):
            out[OLLAMA] = list(status["models"])
        if has_key(self.cfg):
            out[OPENROUTER] = suggested(self.catalogue(fetch=fetch))
        if has_key(self.cfg, DEEPSEEK):
            out[DEEPSEEK] = deepseek_models(self.deepseek_listed(fetch=fetch))
        prices = [m.price for ms in out.values() for m in ms if m.price is not None]
        if prices:
            self.cfg.prices.add(prices)
        self._last = out
        return out

    def snapshot(self) -> dict[str, list[Model]]:
        """The models as last looked at, without looking again: for the many
        requests that need the list but must not wait on the network."""
        return self._last or {OLLAMA: [], OPENROUTER: [], DEEPSEEK: []}

    def deepseek_listed(self, *, fetch: bool = True) -> list[str]:
        """DeepSeek's own list of models for the saved key, asked for at most
        twice a day (and never without a key)."""
        with self._lock:
            if self._deepseek and time.time() - self._deepseek_at < CATALOGUE_TTL:
                return self._deepseek
            if not fetch:
                return self._deepseek
            try:
                key = key_path(self.cfg, DEEPSEEK).read_text(encoding="utf-8").strip()
            except OSError:
                return []
            listed = deepseek_listed(key)
            if listed:
                self._deepseek, self._deepseek_at = listed, time.time()
            return listed

    def price_of(self, model: str) -> Price | None:
        """An OpenRouter model's price, even one not in the suggestions (named
        as a custom model) — so that a money limit binds on it too."""
        for entry in self.catalogue(fetch=False):
            if entry.get("id") == model:
                price = _price(entry)
                if price is not None:
                    self.cfg.prices.add([price])
                return price
        return None
