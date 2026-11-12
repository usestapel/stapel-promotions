"""Canonical-prefix URLconf for contract emission: the documented host mount."""
from django.urls import include, path

urlpatterns = [
    path("promotions/api/", include("stapel_promotions.urls")),
]
