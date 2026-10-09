"""Rate limiting via slowapi/limits.

Keyed by client IP for anonymous endpoints (login, register, forgot-password
— the ones credential-stuffing and enumeration attacks target) and
additionally by user id once authenticated, so one compromised account can't
hide behind a shared office IP's higher aggregate limit.

``RATE_LIMIT_STORAGE_URL=memory://`` (the default) is per-process — fine for
a single dev instance, but production with >1 worker needs a real Redis URL
here so limits are enforced across processes, not reset per-worker.
"""

from slowapi import Limiter
from slowapi.util import get_remote_address

from app.core.config import get_settings

limiter = Limiter(key_func=get_remote_address, storage_uri=get_settings().rate_limit_storage_url)
