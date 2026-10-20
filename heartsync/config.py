"""Application settings, loaded from environment variables.

Nothing secret is hard-coded here. In production every secret must come from
the environment (see .env.example). Branding and plan entitlements are
configurable so the product can be re-skinned without code changes.
"""
from __future__ import annotations

import json
import os
import secrets
from dataclasses import dataclass, field
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent


def _load_dotenv(path: Path) -> None:
    """Minimal .env loader (no external dependency). Existing env wins."""
    if not path.exists():
        return
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


_load_dotenv(BASE_DIR / ".env")


def env(key: str, default: str | None = None) -> str | None:
    value = os.environ.get(key)
    return value if value not in (None, "") else default


def env_bool(key: str, default: bool) -> bool:
    value = env(key)
    if value is None:
        return default
    return value.lower() in {"1", "true", "yes", "on"}


def env_int(key: str, default: int) -> int:
    value = env(key)
    return int(value) if value is not None else default


# ---------------------------------------------------------------------------
# Plans & entitlements. Override with PLANS_JSON (same structure) if needed.
# `None` means unlimited (still subject to abuse rate limits).
# ---------------------------------------------------------------------------
DEFAULT_PLANS: dict[str, dict] = {
    "free": {
        "name": "Free",
        "price_minor": 0,
        "currency": "INR",
        "interval": None,
        "daily_likes": 25,
        "daily_superlikes": 1,
        "monthly_boosts": 0,
        "see_incoming_likes": False,
        "undo": False,
        "advanced_filters": False,
        "read_receipts": False,
        "saved_presets": 1,
    },
    "plus": {
        "name": "HeartSync Plus",
        "price_minor": 49900,  # INR 499.00 / month
        "currency": "INR",
        "interval": "month",
        "daily_likes": None,
        "daily_superlikes": 5,
        "monthly_boosts": 1,
        "see_incoming_likes": True,
        "undo": True,
        "advanced_filters": True,
        "read_receipts": True,
        "saved_presets": 10,
    },
    "premium": {
        "name": "HeartSync Premium",
        "price_minor": 99900,  # INR 999.00 / month
        "currency": "INR",
        "interval": "month",
        "daily_likes": None,
        "daily_superlikes": 10,
        "monthly_boosts": 4,
        "see_incoming_likes": True,
        "undo": True,
        "advanced_filters": True,
        "read_receipts": True,
        "saved_presets": 25,
    },
}


@dataclass
class Settings:
    app_env: str = field(default_factory=lambda: env("APP_ENV", "development"))
    base_url: str = field(default_factory=lambda: env("BASE_URL", "http://localhost:8000"))
    secret_key: str = field(default_factory=lambda: env("SECRET_KEY", ""))
    database_path: str = field(
        default_factory=lambda: env("DATABASE_PATH", str(BASE_DIR / "var" / "heartsync.db"))
    )
    media_root: str = field(default_factory=lambda: env("MEDIA_ROOT", str(BASE_DIR / "var" / "media")))

    # Branding (configurable)
    brand_name: str = field(default_factory=lambda: env("BRAND_NAME", "HeartSync"))
    brand_tagline: str = field(
        default_factory=lambda: env("BRAND_TAGLINE", "Meaningful connections, made with care.")
    )
    brand_primary: str = field(default_factory=lambda: env("BRAND_PRIMARY", "#4a1d3f"))
    brand_accent: str = field(default_factory=lambda: env("BRAND_ACCENT", "#d9577a"))
    support_email: str = field(default_factory=lambda: env("SUPPORT_EMAIL", "support@example.com"))

    # Providers: "console" (dev only) or a real provider name
    sms_provider: str = field(default_factory=lambda: env("SMS_PROVIDER", "console"))
    email_provider: str = field(default_factory=lambda: env("EMAIL_PROVIDER", "console"))
    payment_provider: str = field(default_factory=lambda: env("PAYMENT_PROVIDER", "mock"))
    ai_provider: str = field(default_factory=lambda: env("AI_PROVIDER", "rules"))

    twilio_account_sid: str | None = field(default_factory=lambda: env("TWILIO_ACCOUNT_SID"))
    twilio_auth_token: str | None = field(default_factory=lambda: env("TWILIO_AUTH_TOKEN"))
    twilio_from_number: str | None = field(default_factory=lambda: env("TWILIO_FROM_NUMBER"))

    smtp_host: str | None = field(default_factory=lambda: env("SMTP_HOST"))
    smtp_port: int = field(default_factory=lambda: env_int("SMTP_PORT", 587))
    smtp_user: str | None = field(default_factory=lambda: env("SMTP_USER"))
    smtp_password: str | None = field(default_factory=lambda: env("SMTP_PASSWORD"))
    smtp_from: str = field(default_factory=lambda: env("SMTP_FROM", "HeartSync <no-reply@example.com>"))

    stripe_secret_key: str | None = field(default_factory=lambda: env("STRIPE_SECRET_KEY"))
    stripe_webhook_secret: str | None = field(default_factory=lambda: env("STRIPE_WEBHOOK_SECRET"))
    stripe_price_plus: str | None = field(default_factory=lambda: env("STRIPE_PRICE_PLUS"))
    stripe_price_premium: str | None = field(default_factory=lambda: env("STRIPE_PRICE_PREMIUM"))
    mock_webhook_secret: str = field(default_factory=lambda: env("MOCK_PAYMENT_WEBHOOK_SECRET", ""))

    # Security / policy
    session_days: int = field(default_factory=lambda: env_int("SESSION_DAYS", 14))
    cookie_secure: bool = field(default_factory=lambda: env_bool("COOKIE_SECURE", False))
    require_phone_verification: bool = field(
        default_factory=lambda: env_bool("REQUIRE_PHONE_VERIFICATION", True)
    )
    min_age: int = 18
    max_upload_bytes: int = field(default_factory=lambda: env_int("MAX_UPLOAD_BYTES", 8 * 1024 * 1024))
    max_photos: int = 6
    max_message_chars: int = 2000
    signed_url_ttl: int = field(default_factory=lambda: env_int("SIGNED_URL_TTL", 600))

    plans: dict = field(default_factory=lambda: json.loads(env("PLANS_JSON")) if env("PLANS_JSON") else DEFAULT_PLANS)

    def __post_init__(self) -> None:
        if self.is_production:
            missing = [k for k in ("secret_key",) if not getattr(self, k)]
            if missing or len(self.secret_key) < 32:
                raise RuntimeError("SECRET_KEY (32+ chars) must be set in production")
            if self.sms_provider == "console" or self.email_provider == "console":
                raise RuntimeError(
                    "Console SMS/email providers are development-only. Configure real providers "
                    "(SMS_PROVIDER=twilio, EMAIL_PROVIDER=smtp) before running in production."
                )
            if self.payment_provider == "mock":
                raise RuntimeError("PAYMENT_PROVIDER=mock is development-only. Use PAYMENT_PROVIDER=stripe.")
        else:
            if not self.secret_key:
                # Development convenience: persist a random key under var/ so sessions
                # survive restarts. Never used in production (SECRET_KEY is required there).
                key_file = Path(self.database_path).parent / ".dev_secret_key"
                try:
                    key_file.parent.mkdir(parents=True, exist_ok=True)
                    if not key_file.exists():
                        key_file.write_text(secrets.token_urlsafe(48))
                        key_file.chmod(0o600)
                    self.secret_key = key_file.read_text().strip()
                except OSError:
                    self.secret_key = secrets.token_urlsafe(48)
            if not self.mock_webhook_secret:
                self.mock_webhook_secret = "dev-" + secrets.token_urlsafe(24)

    @property
    def is_production(self) -> bool:
        return self.app_env == "production"

    @property
    def is_dev(self) -> bool:
        return self.app_env in {"development", "test"}


_settings: Settings | None = None


def get_settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings


def override_settings(**kwargs) -> Settings:
    """Used by tests to build fresh settings."""
    global _settings
    _settings = Settings(**kwargs)
    return _settings
