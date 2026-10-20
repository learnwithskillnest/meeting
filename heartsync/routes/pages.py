"""Server-rendered HTML pages. Data is loaded by page scripts from the JSON
API; pages only enforce access and provide the shell."""
from __future__ import annotations

import html
import re
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape
from markupsafe import Markup
from starlette.requests import Request
from starlette.responses import HTMLResponse, PlainTextResponse, Response
from starlette.routing import Route

from .. import catalog
from .. import db as dbm
from ..config import get_settings
from ..services import accounts, admin, billing, safety
from ..web import current_user, get_db, redirect, safe_next

TEMPLATES = Path(__file__).parent.parent / "templates"
env = Environment(loader=FileSystemLoader(str(TEMPLATES)), autoescape=select_autoescape(["html", "xml"]))
routes: list[Route] = []

PUBLIC_PATHS = {"/", "/login", "/signup", "/forgot-password", "/terms", "/privacy", "/cookies", "/guidelines",
                "/safety", "/help", "/pricing", "/refunds"}


def is_public_path(path: str) -> bool:
    return path in PUBLIC_PATHS or path.startswith("/static/") or path in ("/robots.txt", "/sitemap.xml")


def markdown(text: str) -> Markup:
    """Tiny, safe Markdown subset: ## headings, - lists, **bold**, paragraphs, links to local/https."""
    out, in_list = [], False

    def inline(s: str) -> str:
        s = html.escape(s)
        s = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", s)
        s = re.sub(r"\[([^\]]+)\]\(((?:https://|/)[^)\s]+)\)", r'<a href="\2" rel="noopener">\1</a>', s)
        return s

    for line in text.splitlines():
        line = line.rstrip()
        if line.startswith("- "):
            if not in_list:
                out.append("<ul>")
                in_list = True
            out.append(f"<li>{inline(line[2:])}</li>")
            continue
        if in_list:
            out.append("</ul>")
            in_list = False
        if not line:
            continue
        if line.startswith("### "):
            out.append(f"<h3>{inline(line[4:])}</h3>")
        elif line.startswith("## "):
            out.append(f"<h2>{inline(line[3:])}</h2>")
        else:
            out.append(f"<p>{inline(line)}</p>")
    if in_list:
        out.append("</ul>")
    return Markup("\n".join(out))


env.filters["markdown"] = markdown


def money(minor: int | None, currency: str = "INR") -> str:
    if minor is None:
        return ""
    symbol = {"INR": "₹", "USD": "$", "EUR": "€", "GBP": "£"}.get(currency, currency + " ")
    value = minor / 100
    return f"{symbol}{value:,.0f}" if value == int(value) else f"{symbol}{value:,.2f}"


env.filters["money"] = money


def render(request: Request, template: str, status: int = 200, **ctx) -> HTMLResponse:
    s = get_settings()
    user = current_user(request)
    base = {
        "request": request, "settings": s, "user": user, "path": request.url.path,
        "dev_mode": s.is_dev, "catalog": catalog, "public_page": is_public_path(request.url.path),
    }
    base.update(ctx)
    return HTMLResponse(env.get_template(template).render(**base), status_code=status)


def error_page(request: Request, status: int) -> HTMLResponse:
    titles = {403: "You can't view this page", 404: "We couldn't find that page", 500: "Something went wrong"}
    try:
        return render(request, "error.html", status=status, code=status, title=titles.get(status, "Error"))
    except Exception:  # never fail while rendering an error
        return HTMLResponse(f"<h1>{status}</h1>", status_code=status)


def page(path: str, auth: str = "public"):
    """auth: public | user | dating | staff | admin | guest"""
    def deco(fn):
        async def endpoint(request: Request):
            user = current_user(request)
            if auth == "guest" and user:
                return redirect(user.next_step() or "/discover")
            if auth in ("user", "dating", "staff", "admin"):
                if user is None:
                    return redirect(f"/login?next={request.url.path}")
                if user.status == "suspended" and request.url.path not in ("/account/suspended", "/settings/account"):
                    return redirect("/account/suspended")
            if auth == "dating" and not user.can_date:
                return redirect(user.next_step() or "/onboarding")
            if auth == "staff" and not user.is_staff:
                return error_page(request, 404)
            if auth == "admin" and not user.is_admin:
                return error_page(request, 404)
            return await fn(request)
        routes.append(Route(path, endpoint, methods=["GET"]))
        return endpoint
    return deco


# ------------------------------------------------------------------ public
@page("/")
async def home(request: Request):
    user = current_user(request)
    if user and user.can_date:
        return redirect("/discover")
    return render(request, "landing.html", plans=billing.plans_public())


@page("/login", auth="guest")
async def login(request: Request):
    return render(request, "auth/login.html", next=safe_next(request.query_params.get("next"), ""),
                  reset=request.query_params.get("reset") == "1")


@page("/signup", auth="guest")
async def signup(request: Request):
    return render(request, "auth/signup.html")


@page("/forgot-password")
async def forgot(request: Request):
    return render(request, "auth/forgot.html")


@page("/reset-password")
async def reset(request: Request):
    return render(request, "auth/reset.html", token=request.query_params.get("token", ""))


@page("/verify-email/confirm")
async def confirm_email(request: Request):
    uid = accounts.confirm_email(get_db(request), request.query_params.get("token", ""))
    user = current_user(request)
    if uid and user and user.id == uid:
        user.email_verified = True
        return redirect(user.next_step() or "/discover")
    return render(request, "auth/email_confirmed.html", success=bool(uid))


@page("/pricing")
async def pricing(request: Request):
    return render(request, "pricing.html", plans=billing.plans_public())


def _legal(slug: str, template: str):
    @page(slug)
    async def legal(request: Request):
        return render(request, template)
    return legal


for _slug, _tpl in (("/terms", "legal/terms.html"), ("/privacy", "legal/privacy.html"), ("/cookies", "legal/cookies.html"),
                    ("/guidelines", "legal/guidelines.html"), ("/refunds", "legal/refunds.html")):
    _legal(_slug, _tpl)


@page("/help")
async def help_page(request: Request):
    return render(request, "help.html", content=admin.get_content(get_db(request), "faq"))


@page("/safety")
async def safety_center(request: Request):
    return render(request, "safety.html", content=admin.get_content(get_db(request), "safety-tips"),
                  categories=safety.REPORT_CATEGORIES)


async def robots(request: Request):
    s = get_settings()
    lines = ["User-agent: *", "Allow: /$", "Allow: /pricing", "Allow: /help", "Allow: /safety", "Allow: /terms",
             "Allow: /privacy", "Allow: /guidelines", "Disallow: /", f"Sitemap: {s.base_url}/sitemap.xml"]
    return PlainTextResponse("\n".join(lines) + "\n")


async def sitemap(request: Request):
    s = get_settings()
    urls = "".join(f"<url><loc>{s.base_url}{p}</loc></url>" for p in
                   ("/", "/pricing", "/help", "/safety", "/terms", "/privacy", "/guidelines", "/cookies", "/refunds"))
    return Response(f'<?xml version="1.0" encoding="UTF-8"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">{urls}</urlset>',
                    media_type="application/xml")


async def healthz(request: Request):
    conn = get_db(request)
    dbm.scalar(conn, "SELECT 1")
    return PlainTextResponse("ok")


async def brand_css(request: Request):
    s = get_settings()
    ok = re.compile(r"^#[0-9a-fA-F]{3,8}$")
    primary = s.brand_primary if ok.match(s.brand_primary) else "#4a1d3f"
    accent = s.brand_accent if ok.match(s.brand_accent) else "#d9577a"
    css = (f":root:not([data-theme=dark]){{--brand:{primary};--accent:{accent};}}"
           f"@media (prefers-color-scheme: dark){{:root:not([data-theme=light]){{--brand:#e39ac0;--accent:#ec7a99;}}}}")
    return Response(css, media_type="text/css", headers={"Cache-Control": "public, max-age=300"})


routes += [Route("/robots.txt", robots), Route("/sitemap.xml", sitemap), Route("/healthz", healthz),
           Route("/brand.css", brand_css)]


# --------------------------------------------------------------- onboarding
@page("/verify-email", auth="user")
async def verify_email(request: Request):
    user = current_user(request)
    if user.email_verified:
        return redirect(user.next_step() or "/discover")
    return render(request, "auth/verify_email.html")


@page("/verify-phone", auth="user")
async def verify_phone(request: Request):
    user = current_user(request)
    if not user.email_verified:
        return redirect("/verify-email")
    return render(request, "auth/verify_phone.html", change=request.query_params.get("change") == "1")


@page("/onboarding", auth="user")
async def onboarding(request: Request):
    user = current_user(request)
    step = user.next_step()
    if step and step != "/onboarding":
        return redirect(step)
    if user.onboarded:
        return redirect("/discover")
    return render(request, "app/profile_edit.html", onboarding=True)


@page("/account/suspended", auth="user")
async def suspended(request: Request):
    user = current_user(request)
    if user.status != "suspended":
        return redirect("/discover")
    conn = get_db(request)
    reason = dbm.scalar(conn, "SELECT suspended_reason FROM users WHERE id=?", (user.id,))
    return render(request, "app/suspended.html", reason=reason, appeal=safety.appeal_status(conn, user.id))


# ------------------------------------------------------------------- app
@page("/discover", auth="dating")
async def discover(request: Request):
    return render(request, "app/discover.html", nav="discover")


@page("/search", auth="dating")
async def search(request: Request):
    return render(request, "app/search.html", nav="discover")


@page("/likes", auth="dating")
async def likes(request: Request):
    return render(request, "app/likes.html", nav="likes")


@page("/matches", auth="dating")
async def matches(request: Request):
    return render(request, "app/matches.html", nav="chat")


@page("/saved", auth="dating")
async def saved(request: Request):
    return render(request, "app/saved.html", nav="likes")


@page("/chat", auth="dating")
async def chat_index(request: Request):
    return render(request, "app/chat.html", nav="chat", conversation_id="")


@page("/chat/{cid}", auth="dating")
async def chat_conversation(request: Request):
    cid = request.path_params["cid"]
    if not re.fullmatch(r"[0-9a-f]{32}", cid):
        return error_page(request, 404)
    return render(request, "app/chat.html", nav="chat", conversation_id=cid)


@page("/u/{uid}", auth="dating")
async def profile_view(request: Request):
    uid = request.path_params["uid"]
    if not re.fullmatch(r"[0-9a-f]{32}", uid):
        return error_page(request, 404)
    return render(request, "app/profile_view.html", nav="discover", target_id=uid)


@page("/profile", auth="dating")
async def profile_edit(request: Request):
    return render(request, "app/profile_edit.html", nav="profile", onboarding=False)


@page("/notifications", auth="user")
async def notifications(request: Request):
    return render(request, "app/notifications.html", nav="notifications")


@page("/billing", auth="user")
async def billing_page(request: Request):
    return render(request, "app/billing.html", nav="profile", checkout=request.query_params.get("checkout"))


@page("/billing/mock-checkout/{cid}", auth="dating")
async def mock_checkout(request: Request):
    s = get_settings()
    if s.is_production or s.payment_provider != "mock":
        return error_page(request, 404)
    user = current_user(request)
    row = dbm.one(get_db(request), "SELECT * FROM checkout_sessions WHERE id=? AND user_id=? AND status='open'",
                  (request.path_params["cid"], user.id))
    if not row:
        return error_page(request, 404)
    return render(request, "app/mock_checkout.html", checkout=row, plan=s.plans[row["plan"]])


@page("/settings", auth="user")
async def settings_root(request: Request):
    return redirect("/settings/account")


for _section in ("account", "privacy", "discovery", "notifications", "security"):
    def _make(section: str):
        @page(f"/settings/{section}", auth="user")
        async def settings_page(request: Request):
            user = current_user(request)
            if section in ("discovery", "privacy") and not user.can_date:
                return redirect(user.next_step() or "/onboarding")
            return render(request, f"settings/{section}.html", nav="profile", section=section)
        return settings_page
    _make(_section)


# ------------------------------------------------------------------ admin
for _name in ("", "users", "reports", "verifications", "appeals", "subscriptions", "audit", "content"):
    def _make_admin(name: str):
        auth = "admin" if name in ("subscriptions", "audit") else "staff"

        @page(f"/admin/{name}".rstrip("/") or "/admin", auth=auth)
        async def admin_page(request: Request):
            return render(request, "admin/index.html", section=name or "overview")
        return admin_page
    _make_admin(_name)


@page("/admin/users/{uid}", auth="staff")
async def admin_user(request: Request):
    return render(request, "admin/index.html", section="user", item_id=request.path_params["uid"])


@page("/admin/reports/{rid}", auth="staff")
async def admin_report(request: Request):
    return render(request, "admin/index.html", section="report", item_id=request.path_params["rid"])


# ------------------------------------------------------------- dev tools
@page("/dev/outbox")
async def dev_outbox(request: Request):
    s = get_settings()
    if not s.is_dev:
        return error_page(request, 404)
    rows = dbm.all_(get_db(request), "SELECT * FROM dev_outbox ORDER BY id DESC LIMIT 50")
    return render(request, "dev_outbox.html", items=rows)
