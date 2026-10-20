"""Application factory: middleware, security headers, error handling."""
from __future__ import annotations

import logging
import secrets
import traceback
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlparse

from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, Response
from starlette.routing import Mount
from starlette.staticfiles import StaticFiles

from . import db as dbm
from .config import get_settings
from .routes import api, pages
from .services import accounts
from .seed_data import ensure_reference_data
from .web import get_db

log = logging.getLogger("heartsync")
STATIC_DIR = Path(__file__).parent / "static"

CSRF_COOKIE = "hs_csrf"
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}
CSRF_EXEMPT_PREFIXES = ("/api/webhooks/",)


def _csp() -> str:
    return ("default-src 'self'; img-src 'self' data: blob:; style-src 'self'; script-src 'self'; "
            "connect-src 'self'; font-src 'self'; object-src 'none'; base-uri 'self'; form-action 'self'; "
            "frame-ancestors 'none'")


class CoreMiddleware(BaseHTTPMiddleware):
    """Per-request DB connection, session loading, CSRF and security headers."""

    async def dispatch(self, request: Request, call_next):
        s = get_settings()
        path = request.url.path
        request.state.user = None
        try:
            if not path.startswith("/static/"):
                conn = get_db(request)
                request.state.user = accounts.load_session(conn, request.cookies.get("hs_session"))

            if request.method not in SAFE_METHODS and path.startswith("/api/") \
                    and not path.startswith(CSRF_EXEMPT_PREFIXES):
                failure = self._csrf_failure(request)
                if failure:
                    return JSONResponse({"ok": False, "error": {"code": "csrf_failed", "message": failure}},
                                        status_code=403)
            response = await call_next(request)
        finally:
            conn = getattr(request.state, "db", None)
            if conn is not None:
                try:
                    if conn.in_transaction:
                        conn.execute("ROLLBACK")
                finally:
                    conn.close()
                request.state.db = None

        if not request.cookies.get(CSRF_COOKIE):
            response.set_cookie(CSRF_COOKIE, secrets.token_urlsafe(24), httponly=False, secure=s.cookie_secure,
                                samesite="strict", path="/")
        h = response.headers
        h.setdefault("X-Content-Type-Options", "nosniff")
        h.setdefault("X-Frame-Options", "DENY")
        h.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        h.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=(), payment=()")
        h.setdefault("Content-Security-Policy", _csp())
        h.setdefault("Cross-Origin-Opener-Policy", "same-origin")
        if s.cookie_secure:
            h.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
        if path.startswith("/api/") or request.state.user is not None:
            h.setdefault("Cache-Control", "no-store")
        if not pages.is_public_path(path):
            # Private pages must never be indexed.
            h.setdefault("X-Robots-Tag", "noindex, nofollow")
        return response

    @staticmethod
    def _csrf_failure(request: Request) -> str | None:
        cookie = request.cookies.get(CSRF_COOKIE)
        header = request.headers.get("x-csrf-token")
        if not cookie or not header or not secrets.compare_digest(cookie, header):
            return "Security check failed. Refresh the page and try again."
        origin = request.headers.get("origin")
        if origin:
            host = request.headers.get("host", "")
            if urlparse(origin).netloc != host:
                return "Cross-site request blocked."
        return None


@asynccontextmanager
async def lifespan(app: Starlette):
    conn = dbm.connect()
    try:
        ran = dbm.migrate(conn)
        if ran:
            log.info("Applied migrations: %s", ", ".join(ran))
        ensure_reference_data(conn)
    finally:
        conn.close()
    yield


async def http_error(request: Request, exc: Exception):
    status = getattr(exc, "status_code", 500)
    if request.url.path.startswith("/api/"):
        msg = {404: "Not found.", 405: "Method not allowed."}.get(status, "Request failed.")
        return JSONResponse({"ok": False, "error": {"code": f"http_{status}", "message": msg}}, status_code=status)
    return pages.error_page(request, status)


async def server_error(request: Request, exc: Exception):
    # Log the stack trace server-side only; never leak internals to clients.
    log.error("Unhandled error on %s %s: %s", request.method, request.url.path,
              "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))[-4000:])
    if request.url.path.startswith("/api/"):
        return JSONResponse({"ok": False, "error": {"code": "server_error",
                                                    "message": "Something went wrong on our side. Please try again."}},
                            status_code=500)
    return pages.error_page(request, 500)


def create_app() -> Starlette:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    get_settings()  # validate configuration early (fails fast in production)
    app = Starlette(
        debug=False,
        routes=[*api.routes, *pages.routes, Mount("/static", StaticFiles(directory=STATIC_DIR), name="static")],
        middleware=[Middleware(CoreMiddleware)],
        exception_handlers={404: http_error, 405: http_error, 500: server_error, Exception: server_error},
        lifespan=lifespan,
    )
    return app


app = create_app()


def healthcheck() -> Response:  # pragma: no cover - convenience for scripts
    return HTMLResponse("ok")
