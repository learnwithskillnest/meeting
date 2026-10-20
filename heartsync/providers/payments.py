"""Payment provider adapters.

* `mock`   - DEVELOPMENT ONLY. A local checkout page that, when confirmed,
             produces an HMAC-signed webhook event that goes through exactly
             the same verification and fulfilment path as a real provider.
             Refuses to run in production (see config.py).
* `stripe` - Stripe Checkout (subscriptions) via its REST API + webhook
             signature verification (Stripe-Signature, HMAC-SHA256).
             Awaiting configuration: needs STRIPE_* env vars.

Premium status is ONLY granted from a verified webhook event, never from the
browser redirect or client state.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any

from ..config import get_settings

SIGNATURE_TOLERANCE = 300  # seconds


class PaymentError(Exception):
    pass


class WebhookRejected(Exception):
    pass


@dataclass
class NormalizedEvent:
    id: str
    type: str  # checkout_completed | payment_succeeded | payment_failed | subscription_canceled | subscription_updated
    user_id: str | None = None
    plan: str | None = None
    subscription_ref: str | None = None
    payment_ref: str | None = None
    amount_minor: int | None = None
    currency: str | None = None
    period_end: int | None = None  # unix ts
    cancel_at_period_end: bool | None = None
    checkout_id: str | None = None


# ---------------------------------------------------------------------------
# Signatures (same scheme for mock and Stripe: "t=<ts>,v1=<hex hmac>")
# ---------------------------------------------------------------------------
def compute_signature(secret: str, payload: bytes, timestamp: int) -> str:
    signed = f"{timestamp}.".encode() + payload
    return hmac.new(secret.encode(), signed, hashlib.sha256).hexdigest()


def build_signature_header(secret: str, payload: bytes, timestamp: int | None = None) -> str:
    ts = timestamp or int(time.time())
    return f"t={ts},v1={compute_signature(secret, payload, ts)}"


def verify_signature_header(secret: str, payload: bytes, header: str | None) -> None:
    if not secret:
        raise WebhookRejected("Webhook secret not configured.")
    if not header:
        raise WebhookRejected("Missing signature.")
    parts: dict[str, list[str]] = {}
    for item in header.split(","):
        if "=" in item:
            k, v = item.split("=", 1)
            parts.setdefault(k.strip(), []).append(v.strip())
    try:
        ts = int(parts.get("t", ["0"])[0])
    except ValueError:
        raise WebhookRejected("Bad timestamp.")
    if abs(time.time() - ts) > SIGNATURE_TOLERANCE:
        raise WebhookRejected("Signature timestamp outside tolerance.")
    expected = compute_signature(secret, payload, ts)
    if not any(hmac.compare_digest(expected, sig) for sig in parts.get("v1", [])):
        raise WebhookRejected("Signature mismatch.")


# ---------------------------------------------------------------------------
# Provider interface
# ---------------------------------------------------------------------------
def provider_name() -> str:
    return get_settings().payment_provider


def is_configured() -> bool:
    s = get_settings()
    if s.payment_provider == "mock":
        return not s.is_production
    if s.payment_provider == "stripe":
        return bool(s.stripe_secret_key and s.stripe_webhook_secret and s.stripe_price_plus)
    return False


def create_checkout(checkout_id: str, user_id: str, email: str, plan: str) -> str:
    """Return the URL the browser should be sent to."""
    s = get_settings()
    if s.payment_provider == "mock":
        return f"/billing/mock-checkout/{checkout_id}"
    if s.payment_provider == "stripe":
        price = {"plus": s.stripe_price_plus, "premium": s.stripe_price_premium}.get(plan)
        if not (s.stripe_secret_key and price):
            raise PaymentError("Stripe is not configured for this plan.")
        resp = _stripe_post(
            "/v1/checkout/sessions",
            {
                "mode": "subscription",
                "line_items[0][price]": price,
                "line_items[0][quantity]": "1",
                "customer_email": email,
                "client_reference_id": checkout_id,
                "metadata[user_id]": user_id,
                "metadata[plan]": plan,
                "subscription_data[metadata][user_id]": user_id,
                "subscription_data[metadata][plan]": plan,
                "success_url": f"{s.base_url}/billing?checkout=success",
                "cancel_url": f"{s.base_url}/billing?checkout=cancelled",
            },
        )
        return resp["url"]
    raise PaymentError("No payment provider configured.")


def cancel_at_period_end(subscription_ref: str) -> None:
    s = get_settings()
    if s.payment_provider == "stripe":
        _stripe_post(f"/v1/subscriptions/{urllib.parse.quote(subscription_ref)}", {"cancel_at_period_end": "true"})
    # mock: nothing remote to do; local state is updated by the caller.


def resume(subscription_ref: str) -> None:
    s = get_settings()
    if s.payment_provider == "stripe":
        _stripe_post(f"/v1/subscriptions/{urllib.parse.quote(subscription_ref)}", {"cancel_at_period_end": "false"})


def _stripe_post(path: str, fields: dict[str, str]) -> dict[str, Any]:
    s = get_settings()
    req = urllib.request.Request(
        "https://api.stripe.com" + path, data=urllib.parse.urlencode(fields).encode(), method="POST"
    )
    req.add_header("Authorization", f"Bearer {s.stripe_secret_key}")
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read())
    except Exception as exc:
        raise PaymentError("Payment provider request failed.") from exc


# ---------------------------------------------------------------------------
# Webhooks
# ---------------------------------------------------------------------------
def parse_webhook(provider: str, payload: bytes, headers: dict[str, str]) -> NormalizedEvent | None:
    s = get_settings()
    if provider == "mock":
        if s.is_production or s.payment_provider != "mock":
            raise WebhookRejected("Mock provider disabled.")
        verify_signature_header(s.mock_webhook_secret, payload, headers.get("x-mock-signature"))
        data = json.loads(payload)
        return NormalizedEvent(**data)
    if provider == "stripe":
        verify_signature_header(s.stripe_webhook_secret or "", payload, headers.get("stripe-signature"))
        return _normalize_stripe(json.loads(payload))
    raise WebhookRejected("Unknown provider.")


def _normalize_stripe(evt: dict[str, Any]) -> NormalizedEvent | None:
    etype = evt.get("type")
    obj = (evt.get("data") or {}).get("object") or {}
    meta = obj.get("metadata") or {}
    if etype == "checkout.session.completed":
        return NormalizedEvent(
            id=evt["id"], type="checkout_completed", user_id=meta.get("user_id"), plan=meta.get("plan"),
            subscription_ref=obj.get("subscription"), payment_ref=obj.get("payment_intent") or obj.get("id"),
            amount_minor=obj.get("amount_total"), currency=(obj.get("currency") or "").upper() or None,
            checkout_id=obj.get("client_reference_id"),
        )
    if etype in ("invoice.paid", "invoice.payment_succeeded"):
        lines = ((obj.get("lines") or {}).get("data") or [{}])
        period_end = ((lines[0] or {}).get("period") or {}).get("end")
        return NormalizedEvent(
            id=evt["id"], type="payment_succeeded", subscription_ref=obj.get("subscription"),
            payment_ref=obj.get("id"), amount_minor=obj.get("amount_paid"),
            currency=(obj.get("currency") or "").upper() or None, period_end=period_end,
        )
    if etype == "invoice.payment_failed":
        return NormalizedEvent(id=evt["id"], type="payment_failed", subscription_ref=obj.get("subscription"),
                               payment_ref=obj.get("id"), amount_minor=obj.get("amount_due"),
                               currency=(obj.get("currency") or "").upper() or None)
    if etype == "customer.subscription.deleted":
        return NormalizedEvent(id=evt["id"], type="subscription_canceled", subscription_ref=obj.get("id"))
    if etype == "customer.subscription.updated":
        return NormalizedEvent(id=evt["id"], type="subscription_updated", subscription_ref=obj.get("id"),
                               period_end=obj.get("current_period_end"),
                               cancel_at_period_end=bool(obj.get("cancel_at_period_end")))
    return None  # ignored event types
