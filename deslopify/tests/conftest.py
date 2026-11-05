"""Fixtures shared with Atelier's own tests.

The reading interface drives the Atelier pipeline, so its tests need the same
temporary configuration Atelier's tests use. It is loaded from there rather
than copied: two copies of a fixture drift, and the one that drifts is the one
nobody is looking at. When Deslopify becomes its own project, this is the file
that gets a real body.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

_path = Path(__file__).resolve().parents[2] / "tests" / "conftest.py"
_spec = importlib.util.spec_from_file_location("_atelier_conftest", _path)
_atelier = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_atelier)

tmp_cfg = _atelier.tmp_cfg


# --------------------------------------------------------------------------
# readiness, as a test decides it
# --------------------------------------------------------------------------

import time  # noqa: E402

import pytest  # noqa: E402


class ReadyStub:
    """Stands in for `ready.Checker`: whether an AI is signed in and LaTeX is
    ready is a fact about the machine running the tests, not about the code
    under test. `signed_in` and `tex` can be changed by a test."""

    def __init__(self, cfg, *, signed_in=True, tex=True):
        from deslopify.app import ready

        self.cfg = cfg
        self.signed_in = signed_in        # True, False, or a set of providers
        self.tex = tex
        self.jobs = {}
        self.started = []
        self._ready = ready

    def get(self, *, fresh=False):
        r = self._ready
        def on(provider):
            return self.signed_in is True or (isinstance(self.signed_in, set)
                                              and provider in self.signed_in)
        ais = [r.SignIn(account=a.id, provider=a.provider, installed=True,
                        signed_in=on(a.provider))
               for a in self.cfg.accounts.values()]
        tex = (r.Tex(bindir="/tex/bin", label="TeX Live", trial="ok") if self.tex
               else r.Tex())
        return r.Readiness(ais=ais, tex=tex, pdf="PDFium", checked=time.time())

    async def aget(self, *, fresh=False):
        return self.get(fresh=fresh)

    def forget(self, **_):
        pass

    def job(self, ident):
        return self.jobs.get(ident)

    def start(self, kind):
        self.started.append(kind)
        if kind not in ("install-claude", "install-codex", "install-latex",
                        "signin-claude", "signin-codex", "switch-claude", "switch-codex",
                        "revert-claude", "revert-codex"):
            raise ValueError(f"unknown job {kind!r}")
        job = self._ready.Job(id=f"job{len(self.started)}", kind=kind, title=kind)
        self.jobs[job.id] = job
        return job


@pytest.fixture
def ready_stub():
    return ReadyStub
