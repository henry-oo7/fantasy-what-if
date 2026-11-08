"""Register every SQLAlchemy model with the shared metadata.

Any process that inspects or migrates the complete database schema
should import this module before reading ``Base.metadata``.
"""

from app.db import auth_models as auth_models
from app.db import models as models
from app.db import platform_models as platform_models

__all__ = [
    "auth_models",
    "models",
    "platform_models",
]
