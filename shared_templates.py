"""
shared_templates.py

Responsibility: Provides the single shared Jinja2Templates instance used by
all route handlers, with application-wide globals (e.g. APP_VERSION) pre-set.

Does NOT: define routes, services, or any business logic.
"""

from fastapi.templating import Jinja2Templates

# ---------------------------------------------------------------------------
# Application version — update here on every release
# ---------------------------------------------------------------------------

APP_VERSION = "v2.0.28"

# ---------------------------------------------------------------------------
# Shared templates instance — import this in all route files instead of
# creating a new Jinja2Templates() locally, so env globals are consistent.
# ---------------------------------------------------------------------------

templates = Jinja2Templates(directory="templates")
templates.env.globals["app_version"] = APP_VERSION
