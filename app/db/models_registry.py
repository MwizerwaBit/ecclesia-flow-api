"""Import every module's models so SQLAlchemy's declarative registry is
complete before any ORM operation runs — a model that's never imported
can't have its foreign keys resolved by name, even though the table exists
in the database from migrations. Imported once, at process startup, by
app/main.py and by anything else (seed script, tests) that touches the ORM
without going through main.py first.
"""
from app.modules.activity import models as _activity_models  # noqa: F401
from app.modules.audit import models as _audit_models  # noqa: F401
from app.modules.certificates import models as _certificates_models  # noqa: F401
from app.modules.finance import models as _finance_models  # noqa: F401
from app.modules.hierarchy import models as _hierarchy_models  # noqa: F401
from app.modules.identity import models as _identity_models  # noqa: F401
from app.modules.media import models as _media_models  # noqa: F401
from app.modules.messaging import models as _messaging_models  # noqa: F401
from app.modules.people import models as _people_models  # noqa: F401
from app.modules.rbac import models as _rbac_models  # noqa: F401
from app.modules.tenant import models as _tenant_models  # noqa: F401
