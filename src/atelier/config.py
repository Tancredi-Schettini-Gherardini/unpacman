"""Configuration loading: atelier.toml, accounts.toml, pricing.toml.

Everything is resolved once at startup into frozen dataclasses. Paths are
absolute by the time anything else sees them.
"""

from __future__ import annotations

import os
import re
import tomllib
from dataclasses import dataclass, field
from datetime import date, datetime
from functools import lru_cache
from pathlib import Path

# --------------------------------------------------------------------------
# locating the installation
# --------------------------------------------------------------------------


def find_root(start: Path | None = None) -> Path:
    """Locate the atelier root (the directory containing atelier.toml).

    Search order: $ATELIER_ROOT, then upward from `start`/cwd, then the
    package's own grandparent (the repo checkout).
    """
    if env := os.environ.get("ATELIER_ROOT"):
        p = Path(env).expanduser().resolve()
        if (p / "atelier.toml").is_file():
            return p
        raise ConfigError(f"ATELIER_ROOT={env} does not contain atelier.toml")

    here = (start or Path.cwd()).resolve()
    for candidate in [here, *here.parents]:
        if (candidate / "atelier.toml").is_file():
            return candidate

    pkg_root = Path(__file__).resolve().parents[2]
    if (pkg_root / "atelier.toml").is_file():
        return pkg_root

    raise ConfigError(
        "Could not locate atelier.toml. Run from inside the atelier directory "
        "or set ATELIER_ROOT."
    )


class ConfigError(Exception):
    """Raised for any malformed or missing configuration."""


def _resolve(root: Path, value: str) -> Path:
    p = Path(value).expanduser()
    return p if p.is_absolute() else (root / p)


def _merge(base: dict, over: dict) -> dict:
    """Overlay `over` on `base`, section by section."""
    out = dict(base)
    for key, value in over.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _merge(out[key], value)
        else:
            out[key] = value
    return out


def _load_toml(path: Path) -> dict:
    if not path.is_file():
        return {}
    try:
        with path.open("rb") as fh:
            return tomllib.load(fh)
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"{path.name} is not valid TOML: {exc}") from exc


# --------------------------------------------------------------------------
# accounts
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Account:
    id: str
    provider: str                     # "claude" | "codex" | "open"
    auth: str                         # "subscription" | "api_key" | "local"
    label: str = ""
    config_dir: Path | None = None    # CLAUDE_CONFIG_DIR / CODEX_HOME
    key_ref: str | None = None        # "env:NAME" | "keychain:ITEM" | "file:PATH"
    # For an open-weight model (provider "open"): the Anthropic-compatible
    # endpoint Claude Code is pointed at — a local Ollama, or OpenRouter.
    base_url: str = ""
    plan: str = ""
    color: str = "#7C9CF5"
    soft_cap_5h_usd: float | None = None
    soft_cap_7d_usd: float | None = None
    hard_cap_month_usd: float | None = None
    max_concurrent: int = 2

    def env(self) -> dict[str, str]:
        """Environment overlay that selects this account for a child process.

        Not for an open-weight account, whose overlay points Claude Code at
        another endpoint and is made by its adapter (providers/openweight.py).
        """
        out: dict[str, str] = {}
        if self.provider == "open":
            return out
        if self.config_dir:
            var = "CLAUDE_CONFIG_DIR" if self.provider == "claude" else "CODEX_HOME"
            out[var] = str(self.config_dir)
        if self.key_ref:
            secret = resolve_secret(self.key_ref)
            if secret:
                out["ANTHROPIC_API_KEY" if self.provider == "claude" else "OPENAI_API_KEY"] = secret
        return out


def resolve_secret(ref: str) -> str | None:
    """Resolve a `env:NAME` or `keychain:ITEM` reference to its value.

    Secrets are never stored in accounts.toml — only referenced.
    """
    scheme, _, name = ref.partition(":")
    if scheme == "env":
        return os.environ.get(name)
    if scheme == "file":
        # A file only its owner can read, as the reader keeps a key it was given.
        try:
            return Path(name).expanduser().read_text(encoding="utf-8").strip() or None
        except OSError:
            return None
    if scheme == "keychain":
        import subprocess

        try:
            r = subprocess.run(
                ["security", "find-generic-password", "-s", name, "-w"],
                capture_output=True, text=True, timeout=10,
            )
            return r.stdout.strip() or None
        except (OSError, subprocess.SubprocessError):
            return None
    raise ConfigError(f"Unknown secret reference scheme {scheme!r} in {ref!r}")


# --------------------------------------------------------------------------
# pricing
# --------------------------------------------------------------------------


# A 1-hour cache write bills at 2x input; the 5-minute default is 1.25x and
# lives on each Price so it can be overridden per model.
CACHE_WRITE_1H_MULT = 2.00

# What may follow a priced model's id and still name that model: a context
# window asked for (`[1m]`), or a dated snapshot (`-20251001`). Anything else
# names another model — `claude-opus-5-5` is not `claude-opus-5` — whose price
# is its own, and which is unpriced until it is listed.
_SAME_MODEL = re.compile(r"(?:\[[^\]]*\]|-\d{8})+")


@dataclass(frozen=True, slots=True)
class Price:
    model: str
    input: float                      # USD per million tokens
    output: float
    cache_read_mult: float = 0.10
    cache_write_mult: float = 1.25
    valid_from: date = date(2000, 1, 1)
    valid_until: date | None = None

    def covers(self, when: date) -> bool:
        return self.valid_from <= when and (self.valid_until is None or when <= self.valid_until)


class PriceTable:
    """Date-versioned price lookup, so historical runs cost at the rate in force."""

    def __init__(self, prices: list[Price]) -> None:
        self._by_model: dict[str, list[Price]] = {}
        for p in prices:
            self._by_model.setdefault(p.model, []).append(p)
        for lst in self._by_model.values():
            lst.sort(key=lambda p: p.valid_from, reverse=True)

    def lookup(self, model: str, when: date | datetime | None = None) -> Price | None:
        if isinstance(when, datetime):
            when = when.date()
        when = when or date.today()
        for p in self._by_model.get(model, []):
            if p.covers(when):
                return p
        # Fall back to the model this id is a variant of, so `claude-opus-5[1m]`
        # finds `claude-opus-5` — the longest such, so `claude-opus-5-5[1m]`
        # finds `claude-opus-5-5`, never `claude-opus-5`.
        variants = [known for known in self._by_model
                    if model.startswith(known) and _SAME_MODEL.fullmatch(model[len(known):])]
        for known in sorted(variants, key=len, reverse=True):
            for p in self._by_model[known]:
                if p.covers(when):
                    return p
        return None

    def cost(
        self,
        model: str,
        *,
        tok_in: int = 0,
        tok_out: int = 0,
        cache_read: int = 0,
        cache_write: int = 0,
        cache_write_5m: int = 0,
        cache_write_1h: int = 0,
        when: date | datetime | None = None,
    ) -> float | None:
        """Compute USD cost. Returns None when the model is not in the table.

        When the 5-minute / 1-hour split is known, each is billed at its own
        multiplier (1.25x and 2x input respectively); otherwise the whole
        cache-write figure falls back to the 5-minute rate.
        """
        p = self.lookup(model, when)
        if p is None:
            return None
        m = 1_000_000
        split = cache_write_5m + cache_write_1h
        if split:
            write_cost = (cache_write_5m * p.cache_write_mult
                          + cache_write_1h * CACHE_WRITE_1H_MULT) * p.input / m
            leftover = max(0, cache_write - split)
            write_cost += leftover * p.input * p.cache_write_mult / m
        else:
            write_cost = cache_write * p.input * p.cache_write_mult / m
        return (
            tok_in * p.input / m
            + tok_out * p.output / m
            + cache_read * p.input * p.cache_read_mult / m
            + write_cost
        )

    def add(self, prices: list[Price]) -> None:
        """Prices learned while running — an open-weight model's, as its host
        publishes them. A model's newest price for a date replaces the one
        known before; nothing priced from pricing.toml is changed."""
        for p in prices:
            current = [q for q in self._by_model.get(p.model, [])
                       if q.valid_from != p.valid_from]
            self._by_model[p.model] = sorted([*current, p], key=lambda q: q.valid_from,
                                             reverse=True)

    @property
    def models(self) -> list[str]:
        return sorted(self._by_model)


# --------------------------------------------------------------------------
# top-level config
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ServerConfig:
    host: str = "127.0.0.1"
    port: int = 7717
    open_browser: bool = True


@dataclass(frozen=True, slots=True)
class Defaults:
    provider: str = "claude"
    model: str = "claude-opus-5"
    effort: str = "xhigh"
    account: str = "default"
    harness: str = "solo"
    # Per-provider default model, so switching provider does not hand a Claude
    # model id to Codex.
    models: tuple[tuple[str, str], ...] = ()

    def model_for(self, provider: str) -> str:
        for name, model in self.models:
            if name == provider:
                return model
        return self.model


@dataclass(frozen=True, slots=True)
class ProviderModels:
    """Models offered in the UI, per provider.

    Kept separate from pricing.toml: a model can be perfectly usable without a
    published per-token price, which is the normal case on a subscription.
    """
    by_provider: tuple[tuple[str, tuple[str, ...]], ...] = ()

    def for_provider(self, provider: str) -> list[str]:
        for name, models in self.by_provider:
            if name == provider:
                return list(models)
        return []

    def all(self) -> list[str]:
        return [m for _, models in self.by_provider for m in models]


@dataclass(frozen=True, slots=True)
class TestingDefaults:
    model: str = "claude-haiku-4-5"
    effort: str = "low"


@dataclass(frozen=True, slots=True)
class GitConfig:
    enabled: bool = True
    author_name: str = "atelier"
    author_email: str = "atelier@localhost"
    commit_prefix: str = "session"


@dataclass(frozen=True, slots=True)
class LatexConfig:
    engine: str = "latexmk"
    args: tuple[str, ...] = ("-pdf", "-interaction=nonstopmode", "-halt-on-error", "-file-line-error")
    debounce_ms: int = 2000
    timeout_s: int = 180


@dataclass(frozen=True, slots=True)
class Limits:
    max_concurrent_runs: int = 6
    max_event_line_bytes: int = 32 * 1024 * 1024


@dataclass(frozen=True, slots=True)
class Config:
    root: Path
    problems_dir: Path
    harnesses_dir: Path
    shared_dir: Path
    runs_dir: Path
    benchmarks_dir: Path
    db_path: Path
    server: ServerConfig
    defaults: Defaults
    testing: TestingDefaults
    git: GitConfig
    latex: LatexConfig
    limits: Limits
    accounts: dict[str, Account] = field(default_factory=dict)
    prices: PriceTable = field(default_factory=lambda: PriceTable([]))
    models: ProviderModels = field(default_factory=ProviderModels)

    def account(self, ident: str | None) -> Account | None:
        if ident is None:
            return None
        if ident in self.accounts:
            return self.accounts[ident]
        return None

    def accounts_for(self, provider: str) -> list[Account]:
        return [a for a in self.accounts.values() if a.provider == provider]

    def default_account(self, provider: str) -> Account | None:
        named = self.account(self.defaults.account)
        if named and named.provider == provider:
            return named
        pool = self.accounts_for(provider)
        return pool[0] if pool else None


def load_config(root: Path | None = None) -> Config:
    root = root or find_root()
    # atelier.toml is shared and committed; atelier.local.toml is this machine's
    # own settings and is never committed, so a checkout works unedited and
    # nobody's local paths end up in anybody else's copy.
    raw = _merge(_load_toml(root / "atelier.toml"),
                 _load_toml(root / "atelier.local.toml"))

    paths = raw.get("paths", {})
    server = ServerConfig(**{k: v for k, v in raw.get("server", {}).items()
                             if k in ServerConfig.__slots__})
    raw_defaults = dict(raw.get("defaults", {}))
    per_provider = raw_defaults.pop("models", {}) or {}
    defaults = Defaults(
        **{k: v for k, v in raw_defaults.items() if k in Defaults.__slots__},
        models=tuple(sorted((str(k), str(v)) for k, v in per_provider.items())),
    )
    models = ProviderModels(by_provider=tuple(
        (str(name), tuple(str(m) for m in listed))
        for name, listed in (raw.get("models", {}) or {}).items()))
    testing = TestingDefaults(**{k: v for k, v in raw.get("testing", {}).items()
                                 if k in TestingDefaults.__slots__})
    git = GitConfig(**{k: v for k, v in raw.get("git", {}).items()
                       if k in GitConfig.__slots__})
    latex_raw = dict(raw.get("latex", {}))
    if "args" in latex_raw:
        latex_raw["args"] = tuple(latex_raw["args"])
    latex = LatexConfig(**{k: v for k, v in latex_raw.items() if k in LatexConfig.__slots__})
    limits = Limits(**{k: v for k, v in raw.get("limits", {}).items()
                       if k in Limits.__slots__})

    accounts = _load_accounts(root)
    prices = _load_prices(root)

    return Config(
        root=root,
        problems_dir=_resolve(root, paths.get("problems", "problems")),
        harnesses_dir=_resolve(root, paths.get("harnesses", "harnesses")),
        shared_dir=_resolve(root, paths.get("shared", "shared")),
        runs_dir=_resolve(root, paths.get("runs", "runs")),
        benchmarks_dir=_resolve(root, paths.get("benchmarks", "benchmarks")),
        db_path=_resolve(root, paths.get("db", "var/atelier.db")),
        server=server, defaults=defaults, testing=testing,
        git=git, latex=latex, limits=limits,
        accounts=accounts, prices=prices, models=models,
    )


def _load_accounts(root: Path) -> dict[str, Account]:
    local = root / "accounts.local.toml"
    raw = _load_toml(local if local.is_file() else root / "accounts.toml")
    out: dict[str, Account] = {}
    for entry in raw.get("account", []):
        if "id" not in entry:
            raise ConfigError("accounts.toml: every [[account]] needs an `id`")
        data = dict(entry)
        if cd := data.get("config_dir"):
            data["config_dir"] = Path(cd).expanduser()
        data.setdefault("label", data["id"])
        known = {f for f in Account.__slots__}
        unknown = set(data) - known
        if unknown:
            raise ConfigError(
                f"accounts.toml: account {data['id']!r} has unknown keys: {sorted(unknown)}"
            )
        acct = Account(**data)
        if acct.provider not in ("claude", "codex", "open"):
            raise ConfigError(f"account {acct.id!r}: unknown provider {acct.provider!r}")
        if acct.auth not in ("subscription", "api_key", "local"):
            raise ConfigError(f"account {acct.id!r}: auth must be subscription|api_key|local")
        if acct.provider == "open" and not acct.base_url:
            raise ConfigError(f"account {acct.id!r}: an open-weight account needs a base_url")
        out[acct.id] = acct
    return out


def _load_prices(root: Path) -> PriceTable:
    raw = _load_toml(root / "pricing.toml")
    prices: list[Price] = []
    for entry in raw.get("price", []):
        data = dict(entry)
        data["valid_from"] = data.pop("from", date(2000, 1, 1))
        data["valid_until"] = data.pop("until", None)
        for key in ("valid_from", "valid_until"):
            v = data.get(key)
            if isinstance(v, str):
                data[key] = date.fromisoformat(v)
        known = set(Price.__slots__)
        prices.append(Price(**{k: v for k, v in data.items() if k in known}))
    return PriceTable(prices)


@lru_cache(maxsize=1)
def get_config() -> Config:
    """Process-wide cached config. Call `get_config.cache_clear()` to reload."""
    return load_config()
