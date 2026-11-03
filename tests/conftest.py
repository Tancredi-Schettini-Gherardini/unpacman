from __future__ import annotations

import shutil
from datetime import date
from pathlib import Path

import pytest

from atelier.config import Account, Config, Defaults, GitConfig, LatexConfig, Limits
from atelier.config import Price, PriceTable, ServerConfig, TestingDefaults
from atelier.db import Database

REPO = Path(__file__).resolve().parents[1]


@pytest.fixture
def tmp_cfg(tmp_path: Path) -> Config:
    problems = tmp_path / "problems"
    problems.mkdir()
    runs = tmp_path / "runs"
    runs.mkdir()

    harnesses = tmp_path / "harnesses"
    shutil.copytree(REPO / "harnesses", harnesses)
    shared = tmp_path / "shared"
    shutil.copytree(REPO / "shared", shared)

    prices = PriceTable([
        Price(model="claude-haiku-4-5", input=1.0, output=5.0,
              valid_from=date(2026, 1, 1)),
        Price(model="claude-opus-5", input=5.0, output=25.0,
              valid_from=date(2026, 1, 1)),
        Price(model="claude-sonnet-5", input=2.0, output=10.0,
              valid_from=date(2026, 1, 1), valid_until=date(2026, 8, 31)),
        Price(model="claude-sonnet-5", input=3.0, output=15.0,
              valid_from=date(2026, 9, 1)),
    ])

    return Config(
        root=tmp_path,
        problems_dir=problems,
        harnesses_dir=harnesses,
        shared_dir=shared,
        runs_dir=runs,
        benchmarks_dir=tmp_path / "benchmarks",
        db_path=tmp_path / "var" / "test.db",
        server=ServerConfig(),
        defaults=Defaults(model="claude-haiku-4-5", effort="low"),
        testing=TestingDefaults(),
        git=GitConfig(enabled=False),
        latex=LatexConfig(),
        limits=Limits(),
        accounts={
            "default": Account(id="default", provider="claude", auth="subscription"),
            "codex": Account(id="codex", provider="codex", auth="subscription"),
        },
        prices=prices,
    )


@pytest.fixture
def tmp_db(tmp_cfg: Config):
    db = Database(tmp_cfg.db_path)
    yield db
    db.close()          # or sqlite complains at collection time about the handle


@pytest.fixture
def sample_problem(tmp_cfg: Config):
    from atelier.problems import create_problem

    p = create_problem(tmp_cfg.problems_dir, "widget-conjecture",
                       title="The Widget Conjecture",
                       statement="# Widget\n\nShow every widget is a gadget.\n")
    (p.root / "strategy").mkdir(exist_ok=True)
    (p.root / "strategy" / "01-plan.md").write_text(
        "# Plan\n\nTry induction on the number of sprockets.\n", encoding="utf-8")
    return p
