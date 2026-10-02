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

    # Migrations / seed script only — never used by request-serving code.
    database_url: str = "postgresql+asyncpg://ecclesia_admin:dev_only_password@localhost:55433/ecclesia_flow"
    # Ordinary requests — RLS-bound role.
    database_url_app: str = "postgresql+asyncpg://app_tenant:change_me_tenant_pw@localhost:55433/ecclesia_flow"
    # Platform-admin service only — bypassrls.
    database_url_platform: str = (
        "postgresql+asyncpg://app_platform:change_me_platform_pw@localhost:55433/ecclesia_flow"
    )

    app_tenant_db_password: str = "change_me_tenant_pw"
    app_platform_db_password: str = "change_me_platform_pw"

    jwt_secret: str = "dev-only-secret-change-in-production"
    jwt_algorithm: str = "HS256"
    access_token_ttl_minutes: int = 15
    refresh_token_ttl_days: int = 30
    step_up_token_ttl_minutes: int = 5

    mfa_encryption_key: str = "dev-only-32-byte-key-change-me-now"

    cors_origins: str = "http://localhost:5173"

    rate_limit_storage_url: str = "memory://"

    media_s3_endpoint_url: str = "http://localhost:9000"
    media_s3_bucket: str = "ecclesia-flow-media"
    media_s3_access_key: str = "minioadmin"
    media_s3_secret_key: str = "minioadmin"
    media_s3_region: str = "us-east-1"
    media_s3_public_base_url: str = "http://localhost:9000/ecclesia-flow-media"

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def is_production(self) -> bool:
        return self.environment == "production"


@lru_cache
def get_settings() -> Settings:
    return Settings()
