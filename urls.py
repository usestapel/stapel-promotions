"""Root URLconf for stapel-promotions — v1 canon mount.

The host mounts the module under ``promotions/api/``; this module contributes
the ``v1/`` segment:

    path("promotions/api/", include("stapel_promotions.urls"))   # -> /promotions/api/v1/...
"""
from django.urls import include, path

from stapel_promotions.urls_v1 import GATE_REGISTRY  # noqa: F401  (re-export)

urlpatterns = [
    path("v1/", include("stapel_promotions.urls_v1")),
]
