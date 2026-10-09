"""Pydantic settings — the one place environment configuration is read.

Nothing in the app reads `os.environ` directly; everything goes through
`get_settings()` so tests can override via dependency injection / env vars
without monkeypatching globals.
"""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    environment: str = "development"
    log_level: str = "INFO"

    # Migrations / seed script only — never used by request-serving code.
    database_url: str = "postgresql+asyncpg://ecclesia_admin:dev_only_password@localhost:55433/ecclesia_flow"
    # Ordinary requests — RLS-bound role.
    database_url_app: str = "postgresql+asyncpg://app_tenant:change_me_tenant_pw@localhost:55433/ecclesia_flow"
    # Platform-admin service only — bypassrls.
    database_url_platform: str = "postgresql+asyncpg://app_platform:change_me_platform_pw@localhost:55433/ecclesia_flow"

    app_tenant_db_password: str = "change_me_tenant_pw"
    app_platform_db_password: str = "change_me_platform_pw"

    jwt_secret: str = "dev-only-secret-change-in-production"
    jwt_algorithm: str = "HS256"
    access_token_ttl_minutes: int = 15
    refresh_token_ttl_days: int = 30
    step_up_token_ttl_minutes: int = 5
    jwt_issuer: str = "ecclesia-flow-api"
    jwt_audience: str = "ecclesia-flow"

    # Per-account lockout (on top of per-IP rate limiting): after this many
    # consecutive failures the account is locked for lockout_minutes.
    max_failed_logins: int = 5
    lockout_minutes: int = 15

    # The refresh token lives only in this httpOnly cookie for browser
    # clients, so page JavaScript (and therefore an XSS payload) never sees it.
    refresh_cookie_name: str = "ef_refresh"
    refresh_cookie_path: str = "/api/v1/auth"
    invite_ttl_hours: int = 72
    # Where an invitation link points; the token is appended.
    frontend_url: str = "http://localhost:5173"

    mfa_encryption_key: str = "dev-only-32-byte-key-change-me-now"
    # Keys ID-document numbers (see app/core/pii.py). Separate from the MFA key.
    pii_encryption_key: str = "dev-only-pii-key-change-me-now"

    cors_origins: str = "http://localhost:5173"

    rate_limit_storage_url: str = "memory://"

    media_s3_endpoint_url: str = "http://localhost:9000"
    media_s3_bucket: str = "ecclesia-flow-media"
    media_s3_access_key: str = "minioadmin"
    media_s3_secret_key: str = "minioadmin"
    media_s3_region: str = "us-east-1"
    media_s3_public_base_url: str = "http://localhost:9000/ecclesia-flow-media"

    # The S3/MinIO settings above are kept as the documented future swap (a
    # real object store), but nothing stands one up in this environment —
    # local disk storage is the honest stand-in for now, served back via
    # main.py's StaticFiles mount at MEDIA_PUBLIC_URL_PREFIX.
    media_local_dir: str = "media_uploads"
    media_public_url_prefix: str = "/media-files"

    # Files that must never be publicly reachable (government certificates).
    # Served only through permission-checked download endpoints.
    private_files_dir: str = "private_uploads"
    max_document_bytes: int = 10 * 1024 * 1024

    # ── Billing ──────────────────────────────────────────────────────────
    # Leave the Stripe keys empty to use the built-in demo provider (no
    # network calls, test-card checkout page). Fill them in to go live.
    stripe_secret_key: str = ""
    stripe_webhook_secret: str = ""
    stripe_publishable_key: str = ""
    billing_currency: str = "usd"
    # Signs demo webhooks exactly like Stripe signs real ones, so the demo
    # exercises the same verification and processing path.
    demo_webhook_secret: str = "whsec_demo_only_not_secret"

    # ── Email (password resets, ...) ─────────────────────────────────────
    # Leave SMTP_HOST empty in development: messages go to DEV_OUTBOX_DIR.
    # Production refuses to start without it.
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_username: str = ""
    smtp_password: str = ""
    smtp_from: str = "EcclesiaFlow <no-reply@ecclesiaflow.local>"
    smtp_starttls: bool = True
    dev_outbox_dir: str = "dev_outbox"

    # ── Account recovery ─────────────────────────────────────────────────
    password_reset_ttl_minutes: int = 30
    #: Reset emails per account per hour, on top of the per-IP rate limit.
    password_reset_max_per_hour: int = 3

    # ── Request size (DIF-05) ────────────────────────────────────────────
    # Every request body is capped at max_request_body_bytes; multipart file
    # uploads get max_upload_body_bytes. The reverse proxy should enforce the
    # same ceiling in front of this (docs/OPERATIONS.md).
    max_request_body_bytes: int = 1 * 1024 * 1024
    max_upload_body_bytes: int = 11 * 1024 * 1024

    # Automated tests only: new churches start active instead of pending.
    # Refused at startup in production (see main.create_app).
    dev_auto_activate_orgs: bool = False

    @property
    def billing_provider(self) -> str:
        return "stripe" if self.stripe_secret_key else "demo"

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def is_production(self) -> bool:
        return self.environment == "production"

    def production_problems(self) -> list[str]:
        """Settings that must never reach production with their development
        values. app.main refuses to start while any remain."""
        checks = {
            "JWT_SECRET": self.jwt_secret.startswith("dev-only") or len(self.jwt_secret) < 32,
            "MFA_ENCRYPTION_KEY": self.mfa_encryption_key.startswith("dev-only"),
            "PII_ENCRYPTION_KEY": self.pii_encryption_key.startswith("dev-only"),
            "APP_TENANT_DB_PASSWORD": self.app_tenant_db_password.startswith("change_me"),
            "APP_PLATFORM_DB_PASSWORD": self.app_platform_db_password.startswith("change_me"),
            "DATABASE_URL_APP (default password)": "change_me" in self.database_url_app,
            "DATABASE_URL_PLATFORM (default password)": "change_me" in self.database_url_platform,
            "STRIPE_SECRET_KEY / STRIPE_WEBHOOK_SECRET": not (self.stripe_secret_key and self.stripe_webhook_secret),
            "SMTP_HOST": not self.smtp_host,
            "DEV_AUTO_ACTIVATE_ORGS (must be unset)": self.dev_auto_activate_orgs,
        }
        return [name for name, bad in checks.items() if bad]


@lru_cache
def get_settings() -> Settings:
    return Settings()
