"""Admin for stapel-promotions.

Promotions and codes are edited here like in the staff API (the save path
re-validates through the service); the ledger is read-only — a use moves
only through reserve / commit / release.
"""
from django import forms
from django.contrib import admin

from .models import PromoCode, Promotion, Redemption, RedemptionAttempt
from .services import PromotionsError
from .services.staff import PROMOTION_FIELDS, validate_promotion


class PromotionForm(forms.ModelForm):
    class Meta:
        model = Promotion
        fields = [name for name in PROMOTION_FIELDS]

    def clean(self):
        data = super().clean()
        if self.errors:
            return data
        try:
            return validate_promotion(dict(data))
        except PromotionsError as exc:
            raise forms.ValidationError(exc.key) from None


class PromoCodeInline(admin.TabularInline):
    model = PromoCode
    extra = 0
    fields = ("code", "is_active", "usage_limit", "assigned_user_id", "batch")
    readonly_fields = ("batch",)
    show_change_link = True


@admin.register(Promotion)
class PromotionAdmin(admin.ModelAdmin):
    form = PromotionForm
    list_display = ("key", "name", "kind", "value", "currency", "automatic", "stackable", "is_active",
                    "starts_at", "ends_at")
    list_filter = ("kind", "automatic", "is_active", "stackable", "scope")
    search_fields = ("key", "name")
    readonly_fields = ("created_by", "created_at", "updated_at")
    inlines = [PromoCodeInline]


@admin.register(PromoCode)
class PromoCodeAdmin(admin.ModelAdmin):
    list_display = ("code", "promotion", "is_active", "usage_limit", "assigned_user_id", "batch")
    list_filter = ("is_active",)
    search_fields = ("code", "batch", "assigned_user_id")
    raw_id_fields = ("promotion",)


class _ReadOnly(admin.ModelAdmin):
    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(Redemption)
class RedemptionAdmin(_ReadOnly):
    list_display = ("id", "promotion", "code", "user_id", "status", "amount", "currency", "created_at")
    list_filter = ("status", "free_shipping")
    search_fields = ("user_id", "attempt__attempt_key", "attempt__order_id")


@admin.register(RedemptionAttempt)
class RedemptionAttemptAdmin(_ReadOnly):
    list_display = ("attempt_key", "user_id", "status", "order_id", "expires_at", "created_at")
    list_filter = ("status",)
    search_fields = ("attempt_key", "order_id", "user_id")
