"""Page routes. The shell is server-rendered; panels hydrate over the API."""

from __future__ import annotations

import secrets

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, PlainTextResponse

from . import security
from .app import state_of

router = APIRouter()

_PAGES = {
    "problems": "Problems",
    "runs": "Runs",
    "harnesses": "Harnesses",
    "accounts": "Accounts",
}


def _shell(request: Request, page: str, **extra) -> HTMLResponse:
    st = state_of(request)
    # The page's own inline script runs by this nonce, made afresh for each
    # page; nothing else inline, and nothing from another website, runs or
    # loads (security.page_policy).
    nonce = secrets.token_urlsafe(16)
    response = st.templates.TemplateResponse(
        request, "index.html",
        {"page": page, "pages": _PAGES, "title": _PAGES.get(page, "Atelier"),
         "csp_nonce": nonce, **extra},
    )
    # The shell carries the asset version, so it must always be fetched fresh —
    # a cached page would keep pointing at the previous version of the app.
    response.headers["Cache-Control"] = "no-store, must-revalidate"
    response.headers["Content-Security-Policy"] = security.page_policy(nonce)
    # A link followed to another website does not tell it the lab's address.
    response.headers["Referrer-Policy"] = "same-origin"
    return response


@router.get("/", response_class=HTMLResponse)
async def page_root(request: Request) -> HTMLResponse:
    return _shell(request, "problems")


@router.get("/problems", response_class=HTMLResponse)
async def page_problems(request: Request) -> HTMLResponse:
    return _shell(request, "problems")


@router.get("/problems/{slug}", response_class=HTMLResponse)
async def page_problem(request: Request, slug: str) -> HTMLResponse:
    return _shell(request, "problems", slug=slug)


@router.get("/runs", response_class=HTMLResponse)
async def page_runs(request: Request) -> HTMLResponse:
    return _shell(request, "runs")


@router.get("/runs/{run_id}", response_class=HTMLResponse)
async def page_run(request: Request, run_id: str) -> HTMLResponse:
    return _shell(request, "runs", run_id=run_id)


@router.get("/harnesses", response_class=HTMLResponse)
async def page_harnesses(request: Request) -> HTMLResponse:
    return _shell(request, "harnesses")


@router.get("/accounts", response_class=HTMLResponse)
async def page_accounts(request: Request) -> HTMLResponse:
    return _shell(request, "accounts")


@router.get("/healthz", response_class=PlainTextResponse)
async def healthz() -> str:
    return "ok"
