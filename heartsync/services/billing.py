"""Subscriptions, entitlements and payment webhook fulfilment.

Entitlements are always computed server-side from the subscriptions table,
which is only written by verified webhook events (or admin actions).
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone

from .. import db as dbm
from ..config import get_settings
from ..providers import payments
from ..web import APIError, CurrentUser, bad_request, not_found
from . import audit
from .notifications import notify


def current_subscription(conn: sqlite3.Connection, user_id: str) -> sqlite3.Row | None:
    return dbm.one(
        conn,
        "SELECT * FROM subscriptions WHERE user_id=? AND status IN ('active','past_due','canceled')"
        " AND current_period_end > ? ORDER BY current_period_end DESC LIMIT 1",
        (user_id, dbm.iso()),
    )


def current_plan(conn: sqlite3.Connection, user_id: str) -> str:
    sub = current_subscription(conn, user_id)
    plans = get_settings().plans
    if sub and sub["plan"] in plans:
        return sub["plan"]
    return "free"


def entitlements(conn: sqlite3.Connection, user_id: str) -> dict:
    plan = current_plan(conn, user_id)
    ent = dict(get_settings().plans[plan])
    ent["plan"] = plan
    return ent


def require_entitlement(conn: sqlite3.Connection, user_id: str, feature: str) -> dict:
    ent = entitlements(conn, user_id)
    if not ent.get(feature):
        raise APIError(402, "upgrade_required", "This feature is part of HeartSync Plus. Upgrade to unlock it.",
                       {"feature": feature})
    return ent


def plans_public() -> list[dict]:
    out = []
    for key, p in get_settings().plans.items():
        out.append({"key": key, **p})
    return out


def start_checkout(conn: sqlite3.Connection, user: CurrentUser, plan: str) -> str:
    plans = get_settings().plans
    if plan not in plans or plan == "free":
        raise bad_request("Unknown plan.")
    if not payments.is_configured():
        raise APIError(503, "payments_unconfigured",
                       "Payments are not configured yet. Please try again later.")
    if current_plan(conn, user.id) == plan:
        raise bad_request("You're already on this plan.")
    cid = dbm.new_id()
    with dbm.tx(conn):
        conn.execute("INSERT INTO checkout_sessions (id, user_id, plan, provider, created_at) VALUES (?,?,?,?,?)",
                     (cid, user.id, plan, payments.provider_name(), dbm.iso()))
        audit.log(conn, user.id, "billing.checkout_started", "checkout", cid, {"plan": plan})
    try:
        return payments.create_checkout(cid, user.id, user.email, plan)
    except payments.PaymentError as exc:
        raise APIError(502, "payment_provider_error", str(exc))


def _ts_to_iso(ts: int | None, default_days: int = 30) -> str:
    if ts:
        return dbm.iso(datetime.fromtimestamp(ts, tz=timezone.utc))
    return dbm.iso_in(days=default_days)


def process_event(conn: sqlite3.Connection, provider: str, evt: payments.NormalizedEvent) -> str:
    """Apply a verified webhook event. Idempotent by provider event id."""
    plans = get_settings().plans
    with dbm.tx(conn):
        if dbm.one(conn, "SELECT 1 FROM webhook_events WHERE id=?", (f"{provider}:{evt.id}",)):
            return "duplicate"
        conn.execute("INSERT INTO webhook_events (id, provider, type, received_at) VALUES (?,?,?,?)",
                     (f"{provider}:{evt.id}", provider, evt.type, dbm.iso()))
        result = "ignored"
        if evt.type == "checkout_completed":
            checkout = dbm.one(conn, "SELECT * FROM checkout_sessions WHERE id=?", (evt.checkout_id,))
            if not checkout or checkout["status"] != "open":
                result = "unknown_checkout"
            elif evt.user_id and evt.user_id != checkout["user_id"]:
                result = "user_mismatch"
            else:
                plan = checkout["plan"]
                if plan not in plans:
                    result = "unknown_plan"
                else:
                    uid = checkout["user_id"]
                    conn.execute("UPDATE checkout_sessions SET status='completed', provider_ref=? WHERE id=?",
                                 (evt.subscription_ref, checkout["id"]))
                    # Replace any existing active subscription.
                    conn.execute("UPDATE subscriptions SET status='expired', updated_at=? WHERE user_id=?"
                                 " AND status IN ('active','past_due','canceled')", (dbm.iso(), uid))
                    sid = dbm.new_id()
                    conn.execute(
                        "INSERT INTO subscriptions (id, user_id, plan, status, provider, provider_subscription_id,"
                        " current_period_end, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?)",
                        (sid, uid, plan, "active", provider, evt.subscription_ref or f"{provider}-{sid}",
                         _ts_to_iso(evt.period_end), dbm.iso(), dbm.iso()),
                    )
                    if evt.payment_ref:
                        conn.execute(
                            "INSERT OR IGNORE INTO payments (id, user_id, subscription_id, provider, provider_payment_id,"
                            " amount_minor, currency, status, description, created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                            (dbm.new_id(), uid, sid, provider, evt.payment_ref,
                             evt.amount_minor if evt.amount_minor is not None else plans[plan]["price_minor"],
                             evt.currency or plans[plan]["currency"], "succeeded", f"{plans[plan]['name']} subscription",
                             dbm.iso()),
                        )
                    audit.log(conn, None, "billing.subscription_started", "user", uid, {"plan": plan, "provider": provider})
                    notify(conn, uid, "subscription", f"Welcome to {plans[plan]['name']}!",
                           "Your premium features are now active.", "/billing", dedupe_key=f"sub:{sid}", email=True)
                    result = "activated"
        elif evt.type in ("payment_succeeded", "payment_failed", "subscription_canceled", "subscription_updated"):
            sub = dbm.one(conn, "SELECT * FROM subscriptions WHERE provider_subscription_id=?", (evt.subscription_ref,))
            if not sub:
                result = "unknown_subscription"
            elif evt.type == "payment_succeeded":
                conn.execute("UPDATE subscriptions SET status=CASE WHEN status='past_due' THEN 'active' ELSE status END,"
                             " current_period_end=?, updated_at=? WHERE id=?",
                             (_ts_to_iso(evt.period_end), dbm.iso(), sub["id"]))
                if evt.payment_ref:
                    conn.execute(
                        "INSERT OR IGNORE INTO payments (id, user_id, subscription_id, provider, provider_payment_id,"
                        " amount_minor, currency, status, description, created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                        (dbm.new_id(), sub["user_id"], sub["id"], provider, evt.payment_ref, evt.amount_minor or 0,
                         evt.currency or "INR", "succeeded", "Subscription renewal", dbm.iso()))
                result = "renewed"
            elif evt.type == "payment_failed":
                conn.execute("UPDATE subscriptions SET status='past_due', updated_at=? WHERE id=?", (dbm.iso(), sub["id"]))
                if evt.payment_ref:
                    conn.execute(
                        "INSERT OR IGNORE INTO payments (id, user_id, subscription_id, provider, provider_payment_id,"
                        " amount_minor, currency, status, description, created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                        (dbm.new_id(), sub["user_id"], sub["id"], provider, evt.payment_ref, evt.amount_minor or 0,
                         evt.currency or "INR", "failed", "Renewal payment failed", dbm.iso()))
                notify(conn, sub["user_id"], "subscription", "Payment failed",
                       "We couldn't renew your subscription. Update your payment method to keep premium features.",
                       "/billing", dedupe_key=f"payfail:{evt.id}", email=True)
                result = "past_due"
            elif evt.type == "subscription_canceled":
                conn.execute("UPDATE subscriptions SET status='expired', current_period_end=?, updated_at=? WHERE id=?",
                             (dbm.iso(), dbm.iso(), sub["id"]))
                result = "expired"
            elif evt.type == "subscription_updated":
                conn.execute("UPDATE subscriptions SET cancel_at_period_end=?, current_period_end=COALESCE(?, current_period_end),"
                             " status=CASE WHEN ? THEN 'canceled' ELSE status END, updated_at=? WHERE id=?",
                             (int(bool(evt.cancel_at_period_end)), _ts_to_iso(evt.period_end) if evt.period_end else None,
                              int(bool(evt.cancel_at_period_end)), dbm.iso(), sub["id"]))
                result = "updated"
        conn.execute("UPDATE webhook_events SET processed_at=? WHERE id=?", (dbm.iso(), f"{provider}:{evt.id}"))
    return result


def cancel(conn: sqlite3.Connection, user: CurrentUser) -> None:
    sub = current_subscription(conn, user.id)
    if not sub or sub["status"] == "canceled":
        raise bad_request("You don't have an active subscription to cancel.")
    try:
        payments.cancel_at_period_end(sub["provider_subscription_id"])
    except payments.PaymentError as exc:
        raise APIError(502, "payment_provider_error", str(exc))
    with dbm.tx(conn):
        conn.execute("UPDATE subscriptions SET status='canceled', cancel_at_period_end=1, updated_at=? WHERE id=?",
                     (dbm.iso(), sub["id"]))
        audit.log(conn, user.id, "billing.canceled", "subscription", sub["id"])


def resume(conn: sqlite3.Connection, user: CurrentUser) -> None:
    sub = current_subscription(conn, user.id)
    if not sub or sub["status"] != "canceled":
        raise bad_request("There's no cancelled subscription to resume.")
    try:
        payments.resume(sub["provider_subscription_id"])
    except payments.PaymentError as exc:
        raise APIError(502, "payment_provider_error", str(exc))
    with dbm.tx(conn):
        conn.execute("UPDATE subscriptions SET status='active', cancel_at_period_end=0, updated_at=? WHERE id=?",
                     (dbm.iso(), sub["id"]))
        audit.log(conn, user.id, "billing.resumed", "subscription", sub["id"])


def history(conn: sqlite3.Connection, user_id: str) -> list[dict]:
    return [dict(r) for r in dbm.all_(
        conn, "SELECT provider_payment_id AS reference, amount_minor, currency, status, description, created_at"
              " FROM payments WHERE user_id=? ORDER BY created_at DESC LIMIT 50", (user_id,))]


def summary(conn: sqlite3.Connection, user_id: str) -> dict:
    sub = current_subscription(conn, user_id)
    return {
        "entitlements": entitlements(conn, user_id),
        "subscription": dict(sub) if sub else None,
        "history": history(conn, user_id),
        "provider": payments.provider_name(),
        "configured": payments.is_configured(),
    }


# ---------------------------------------------------------------------------
# Mock provider (development only)
# ---------------------------------------------------------------------------
def mock_complete(conn: sqlite3.Connection, user: CurrentUser, checkout_id: str, succeed: bool) -> str:
    """Simulates the provider: builds a signed webhook payload and feeds it to
    the normal verification path. Only available when PAYMENT_PROVIDER=mock
    and not in production."""
    s = get_settings()
    if s.is_production or s.payment_provider != "mock":
        raise not_found()
    checkout = dbm.one(conn, "SELECT * FROM checkout_sessions WHERE id=? AND user_id=?", (checkout_id, user.id))
    if not checkout or checkout["status"] != "open":
        raise not_found("Checkout session not found.")
    if not succeed:
        with dbm.tx(conn):
            conn.execute("UPDATE checkout_sessions SET status='expired' WHERE id=?", (checkout_id,))
        return "declined"
    plan = s.plans[checkout["plan"]]
    evt = {
        "id": "evt_" + dbm.new_id(), "type": "checkout_completed", "user_id": user.id, "plan": checkout["plan"],
        "subscription_ref": "mocksub_" + dbm.new_id(), "payment_ref": "mockpay_" + dbm.new_id(),
        "amount_minor": plan["price_minor"], "currency": plan["currency"], "checkout_id": checkout_id,
    }
    payload = json.dumps(evt).encode()
    header = payments.build_signature_header(s.mock_webhook_secret, payload)
    event = payments.parse_webhook("mock", payload, {"x-mock-signature": header})
    return process_event(conn, "mock", event)
