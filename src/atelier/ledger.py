"""Cost ledger and account health.

Four independent signals, kept separate on purpose rather than mashed into one
number that would imply knowledge nobody has:

  1. measured   — exact spend computed from token counts we observed
  2. calibrated — rolling 5h / 7d utilisation against caps the user sets
  3. reported   — whatever the provider volunteered, timestamped
  4. observed   — actual rate-limit events, with the cooldown they implied

No public API exposes "credits remaining" for a subscription plan. Pretending
otherwise would be worse than useless, so the UI shows all four with their
provenance.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from .config import Account, Config
from .db import Database

_RESET_PATTERNS = [
    re.compile(r"resets?\s+at\s+([0-9]{1,2}:[0-9]{2}\s*(?:am|pm)?)", re.I),
    re.compile(r"try again (?:at|after)\s+([0-9]{1,2}:[0-9]{2}\s*(?:am|pm)?)", re.I),
    re.compile(r"resets?\s+in\s+(\d+)\s*(minute|hour|second)s?", re.I),
]


@dataclass(slots=True)
class Window:
    label: str
    hours: float
    spend: float
    cap: float | None
    runs: int

    @property
    def fraction(self) -> float | None:
        if not self.cap:
            return None
        return self.spend / self.cap


@dataclass(slots=True)
class AccountHealth:
    account: Account
    windows: list[Window] = field(default_factory=list)
    total_30d: float = 0.0
    limited_until: str | None = None
    last_limit_message: str = ""
    last_probe: str = ""
    last_probe_at: str = ""
    provider_window: dict[str, Any] | None = None

    @property
    def limited(self) -> bool:
        if not self.limited_until:
            return False
        try:
            return datetime.fromisoformat(self.limited_until) > datetime.now(UTC)
        except ValueError:
            return False

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.account.id,
            "label": self.account.label or self.account.id,
            "provider": self.account.provider,
            "auth": self.account.auth,
            "plan": self.account.plan,
            "color": self.account.color,
            "limited": self.limited,
            "limited_until": self.limited_until,
            "last_limit_message": self.last_limit_message,
            "last_probe": self.last_probe,
            "last_probe_at": self.last_probe_at,
            "provider_window": self.provider_window,
            "total_30d": round(self.total_30d, 4),
            "windows": [
                {"label": w.label, "hours": w.hours, "spend": round(w.spend, 4),
                 "cap": w.cap, "runs": w.runs,
                 "fraction": None if w.fraction is None else round(w.fraction, 4)}
                for w in self.windows
            ],
        }


def _spend_since(db: Database, account_id: str, since: datetime) -> tuple[float, int]:
    row = db.one(
        "SELECT COALESCE(SUM(cost), 0) AS spend, COUNT(DISTINCT run_id) AS runs "
        "FROM usage_samples WHERE account = ? AND ts >= ?",
        (account_id, since.isoformat()),
    )
    return (float(row["spend"]) if row else 0.0, int(row["runs"]) if row else 0)


def account_health(cfg: Config, db: Database, account: Account) -> AccountHealth:
    now = datetime.now(UTC)
    health = AccountHealth(account=account)

    for label, hours, cap in (
        ("5h", 5.0, account.soft_cap_5h_usd),
        ("7d", 24.0 * 7, account.soft_cap_7d_usd),
        ("30d", 24.0 * 30, account.hard_cap_month_usd),
    ):
        spend, runs = _spend_since(db, account.id, now - timedelta(hours=hours))
        health.windows.append(Window(label=label, hours=hours, spend=spend,
                                     cap=cap, runs=runs))
        if label == "30d":
            health.total_30d = spend

    for row in db.query(
        "SELECT ts, kind, payload FROM account_events WHERE account = ? "
        "ORDER BY ts DESC LIMIT 40", (account.id,)
    ):
        payload = {}
        if row["payload"]:
            try:
                payload = json.loads(row["payload"])
            except json.JSONDecodeError:
                payload = {}
        if row["kind"] == "window" and not health.provider_window:
            health.provider_window = {
                "status": payload.get("status"), "window": payload.get("window"),
                "resets_at": payload.get("resets_at"), "at": row["ts"],
            }
        elif row["kind"] == "rate_limited" and not health.limited_until:
            if payload.get("resets_at"):
                health.limited_until = payload["resets_at"]
                health.last_limit_message = (
                    f"{payload.get('window', 'window')} limit reached "
                    f"(reported by provider)")
                continue
            msg = str(payload.get("message", ""))
            health.last_limit_message = msg
            health.limited_until = _guess_reset(msg, row["ts"])
        elif row["kind"] == "probe" and not health.last_probe:
            health.last_probe = str(payload.get("status", ""))[:200]
            health.last_probe_at = row["ts"]
    return health


def _guess_reset(message: str, event_ts: str) -> str | None:
    """Best-effort parse of a provider's cooldown wording. None when unclear."""
    try:
        base = datetime.fromisoformat(event_ts)
    except ValueError:
        base = datetime.now(UTC)
    for pat in _RESET_PATTERNS:
        m = pat.search(message)
        if not m:
            continue
        token = m.group(1)
        if token.isdigit() and len(m.groups()) > 1:
            unit = m.group(2).lower()
            delta = {"second": timedelta(seconds=int(token)),
                     "minute": timedelta(minutes=int(token)),
                     "hour": timedelta(hours=int(token))}[unit]
            return (base + delta).isoformat()
        try:
            hh, _, rest = token.partition(":")
            mm = int(rest[:2])
            hour = int(hh)
            if "pm" in token.lower() and hour < 12:
                hour += 12
            if "am" in token.lower() and hour == 12:
                hour = 0
            candidate = base.replace(hour=hour % 24, minute=mm, second=0, microsecond=0)
            if candidate <= base:
                candidate += timedelta(days=1)
            return candidate.isoformat()
        except (ValueError, IndexError):
            continue
    # A limit with no parseable reset: assume the common 5-hour window, and say so.
    return (base + timedelta(hours=5)).isoformat()


def all_health(cfg: Config, db: Database) -> list[AccountHealth]:
    return [account_health(cfg, db, a) for a in cfg.accounts.values()]


# --------------------------------------------------------------------------
# aggregates for the usage views
# --------------------------------------------------------------------------


def usage_by(db: Database, group: str, *, days: int = 30) -> list[dict[str, Any]]:
    since = (datetime.now(UTC) - timedelta(days=days)).isoformat()
    column = {
        "day": "substr(started_at, 1, 10)",
        "problem": "problem",
        "harness": "harness",
        "model": "model",
        "account": "account",
        "provider": "provider",
    }.get(group)
    if column is None:
        raise ValueError(f"cannot group usage by {group!r}")
    rows = db.query(
        f"SELECT {column} AS key, COUNT(*) AS runs, "
        "COALESCE(SUM(cost_computed), 0) AS cost, "
        "COALESCE(SUM(tok_in), 0) AS tok_in, COALESCE(SUM(tok_out), 0) AS tok_out, "
        "COALESCE(SUM(wall_seconds), 0) AS seconds "
        "FROM runs WHERE started_at >= ? GROUP BY key ORDER BY cost DESC",
        (since,),
    )
    return [dict(r) for r in rows]


def totals(db: Database) -> dict[str, Any]:
    row = db.one(
        "SELECT COUNT(*) AS runs, COALESCE(SUM(cost_computed), 0) AS cost, "
        "COALESCE(SUM(wall_seconds), 0) AS seconds FROM runs"
    )
    return dict(row) if row else {"runs": 0, "cost": 0.0, "seconds": 0.0}
