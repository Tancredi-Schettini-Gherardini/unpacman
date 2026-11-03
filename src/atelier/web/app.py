"""FastAPI application: lifespan, shared state, static mounts, route wiring."""

from __future__ import annotations

import contextlib
from collections.abc import Sequence
from dataclasses import dataclass
from html import escape
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.responses import Response as StarletteResponse

from ..config import Config, load_config
from ..db import Database, get_db
from ..events import EventHub, get_hub
from ..runner import RunManager
from . import security

WEB_DIR = Path(__file__).resolve().parents[3] / "web"


class AppStatic(StaticFiles):
    """Static files that always revalidate for app code.

    Browsers hold ES modules in a per-page module map and will happily reuse a
    stale one across reloads. For a local tool where you edit `harnesses/` and
    `web/js/` and expect the change to show up, silently serving yesterday's
    JavaScript is the worst possible default. ETags make revalidation cheap;
    vendored libraries (KaTeX) never change, so they stay cacheable.
    """

    async def get_response(self, path: str, scope) -> StarletteResponse:
        response = await super().get_response(path, scope)
        if path.startswith(("js/", "css/")):
            response.headers["Cache-Control"] = "no-cache, must-revalidate"
        else:
            response.headers["Cache-Control"] = "public, max-age=604800"
        if path.endswith(".html"):
            # A page found here loads only the lab's own files (security.py).
            response.headers["Content-Security-Policy"] = security.page_policy()
        return response


@dataclass(slots=True)
class AppState:
    cfg: Config
    db: Database
    hub: EventHub
    manager: RunManager
    templates: Jinja2Templates
    startup_report: dict[str, Any]


def create_app(cfg: Config | None = None, *, control_token: str | None = None,
               allowed_hosts: Sequence[str] | None = None) -> FastAPI:
    """The lab's application. `atelier serve` always passes `control_token` —
    the key it made for this start — and the Host names it listens by; with
    them, /api/ answers only the page that holds the key (security.py). Without
    them it is the bare application, for tests and embedding: never serve that
    on a port a browser can reach."""
    cfg = cfg or load_config()
    db = get_db(cfg.db_path)
    hub = get_hub()
    manager = RunManager(cfg, hub, db)

    templates = Jinja2Templates(directory=str(WEB_DIR / "templates"))
    # Evaluated per render, not once at startup: assets are served `immutable`,
    # so a stamp frozen at boot pins every browser to the code as it was when
    # the server started, and no later edit can ever reach them.
    templates.env.globals["asset_version"] = lambda: _asset_version(WEB_DIR)
    templates.env.filters["human_size"] = _human_size
    templates.env.filters["usd"] = _usd
    templates.env.filters["duration"] = _duration

    @contextlib.asynccontextmanager
    async def lifespan(app: FastAPI):
        from ..doctor import run_checks

        orphans = manager.reap_orphans()
        report = await run_checks(cfg, db, live=False)
        report["orphans_reaped"] = orphans
        app.state.atelier.startup_report = report
        yield
        await manager.shutdown()

    app = FastAPI(title="Atelier", docs_url=None, redoc_url=None, lifespan=lifespan)
    app.state.atelier = AppState(cfg=cfg, db=db, hub=hub, manager=manager,
                                 templates=templates, startup_report={})

    # The static mount shadows dynamic routes under /static, so the generated
    # Pygments theme is materialised to disk instead of served from a handler.
    if (WEB_DIR / "css").is_dir():
        from ..render import pygments_css

        try:
            (WEB_DIR / "css" / "pygments.css").write_text(pygments_css(), encoding="utf-8")
        except OSError:
            pass
        app.mount("/static", AppStatic(directory=str(WEB_DIR)), name="static")

    @app.get("/asset/{version}/{path:path}")
    async def versioned_asset(version: str, path: str) -> FileResponse:
        """Version-stamped app code.

        Static files previously carried no Cache-Control, so browsers applied
        *heuristic* freshness from Last-Modified and could skip revalidation
        entirely — serving yesterday's module after an edit. Because ES modules
        import each other by relative path, versioning only the entry point was
        not enough: `app.js?v=N` still pulled an unversioned `launch.js` from
        cache. Putting the whole tree under /asset/<version>/ means every module
        URL changes together.
        """
        target = (WEB_DIR / path).resolve()
        if not str(target).startswith(str(WEB_DIR.resolve())) or not target.is_file():
            raise HTTPException(404, "no such asset")
        media = {"js": "text/javascript", "css": "text/css"}.get(
            target.suffix.lstrip("."), None)
        return FileResponse(target, media_type=media,
                            headers={"Cache-Control": "public, max-age=31536000, immutable"})

    @app.post("/api/session")
    async def api_session(request: Request) -> JSONResponse:
        """The launch key (checked by security.LocalControl) traded for a
        cookie that lets this browser read: the event stream, and files shown
        in a frame or a tab of their own."""
        response = JSONResponse({"ok": True}, headers={"Cache-Control": "no-store"})
        if control_token:
            response.set_cookie(security.cookie_name(request.scope),
                                security.read_cookie(control_token),
                                httponly=True, samesite="strict", path="/api")
        return response

    from . import api, views

    app.include_router(api.router, prefix="/api")
    app.include_router(views.router)

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception) -> JSONResponse:
        import traceback

        detail = f"{type(exc).__name__}: {exc}"
        if request.url.path.startswith("/api"):
            return JSONResponse({"error": detail,
                                 "trace": traceback.format_exc()[-4000:]}, status_code=500)
        # Escaped: an exception's message may quote the address that caused it.
        return HTMLResponse(
            f"<pre style='padding:2rem;font:13px ui-monospace;color:#D96A6A'>{escape(detail)}\n\n"
            f"{escape(traceback.format_exc())}</pre>", status_code=500)

    app.state.control_token = control_token
    if control_token:
        # The last added runs first: a foreign Host is refused before anything
        # else looks at the request (DNS rebinding), then the key is checked.
        app.add_middleware(security.LocalControl, token=control_token)
        app.add_middleware(TrustedHostMiddleware,
                           allowed_hosts=list(allowed_hosts or security.LOOPBACK_HOSTS),
                           www_redirect=False)
    return app


def _asset_version(web_dir: Path) -> str:
    """Newest mtime across app code — changes whenever the frontend changes."""
    newest = 0.0
    for sub in ("js", "css"):
        for f in (web_dir / sub).rglob("*"):
            if f.is_file():
                newest = max(newest, f.stat().st_mtime)
    return str(int(newest))


def state_of(request: Request) -> AppState:
    return request.app.state.atelier


# -- jinja filters ---------------------------------------------------------


def _human_size(n: Any) -> str:
    from ..render import human_size

    try:
        return human_size(int(n))
    except (TypeError, ValueError):
        return "—"


def _usd(v: Any) -> str:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return "—"
    if f == 0:
        return "$0.00"
    return f"${f:.4f}" if f < 0.01 else f"${f:,.2f}"


def _duration(seconds: Any) -> str:
    try:
        s = int(float(seconds))
    except (TypeError, ValueError):
        return "—"
    h, rem = divmod(s, 3600)
    m, sec = divmod(rem, 60)
    if h:
        return f"{h}h {m:02d}m"
    if m:
        return f"{m}m {sec:02d}s"
    return f"{sec}s"
