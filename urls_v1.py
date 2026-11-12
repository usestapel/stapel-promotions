"""v1 URL set for stapel-promotions (plain paths, no router)."""
from typing import NamedTuple

from django.urls import path

from .views import (
    ActivePromotionsView,
    CodeValidateView,
    StaffCodeDetailView,
    StaffCodeExportView,
    StaffCodeGenerateView,
    StaffCodeListView,
    StaffPromotionDetailView,
    StaffPromotionListView,
    StaffRedemptionListView,
)

PROMOTION = "staff/promotions/<int:promotion_id>/"

urlpatterns = [
    path("active/", ActivePromotionsView.as_view(), name="promotions-active"),
    path("codes/validate/", CodeValidateView.as_view(), name="promotions-codes-validate"),
    path("staff/promotions/", StaffPromotionListView.as_view(), name="promotions-staff-list"),
    path(PROMOTION, StaffPromotionDetailView.as_view(), name="promotions-staff-detail"),
    path(f"{PROMOTION}codes/", StaffCodeListView.as_view(), name="promotions-staff-codes"),
    path(f"{PROMOTION}codes/export/", StaffCodeExportView.as_view(), name="promotions-staff-codes-export"),
    path(f"{PROMOTION}codes/generate/", StaffCodeGenerateView.as_view(), name="promotions-staff-codes-generate"),
    path(f"{PROMOTION}redemptions/", StaffRedemptionListView.as_view(), name="promotions-staff-redemptions"),
    path("staff/codes/<int:code_id>/", StaffCodeDetailView.as_view(), name="promotions-staff-code"),
]


class GateEntry(NamedTuple):
    """One gated URL block: which flags gate which url patterns."""

    name: str
    flags: tuple
    patterns: tuple


#: No axis unmounts an endpoint, so the surface is one always-on block.
GATE_REGISTRY: dict = {
    "promotions.api": GateEntry("promotions.api", (), tuple(urlpatterns)),
}
