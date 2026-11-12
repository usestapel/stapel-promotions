"""Serializers for the stapel-promotions API.

The class names are the codegen contract: drf-spectacular names each
component after its serializer minus the ``Serializer`` suffix, and the
frontend pair generates its client from those names. Every CharField
declares a ``max_length``; money is a ``DecimalField`` rendered as a string.
"""
import datetime

from rest_framework import serializers
from stapel_core.django.api.errors import StapelValidationError

from . import money
from . import models as m
from .errors import ERR_400_GENERATE_COUNT_TOO_LARGE

UTC = datetime.timezone.utc
CURRENCY_RE = r"^[A-Za-z]{3}$"


def _value(**kwargs):
    return serializers.DecimalField(
        max_digits=money.VALUE_MAX_DIGITS, decimal_places=money.DECIMAL_PLACES,
        coerce_to_string=True, **kwargs,
    )


def _amount(**kwargs):
    return serializers.DecimalField(
        max_digits=money.AMOUNT_MAX_DIGITS, decimal_places=money.DECIMAL_PLACES,
        coerce_to_string=True, **kwargs,
    )


def _usage_limit(**kwargs):
    return serializers.IntegerField(min_value=1, max_value=m.USAGE_LIMIT_MAX, allow_null=True, **kwargs)


def _page(item):
    class Page(serializers.Serializer):
        items = item(many=True)
        next_anchor = serializers.CharField(max_length=64, allow_null=True)
        prev_anchor = serializers.CharField(max_length=64, allow_null=True)
        has_next = serializers.BooleanField()
        has_prev = serializers.BooleanField()
        count = serializers.IntegerField()

    return Page


# ── public ────────────────────────────────────────────────────────────────


class PublicPromotionSerializer(serializers.Serializer):
    key = serializers.CharField(max_length=m.KEY_MAX_LENGTH)
    name = serializers.CharField(max_length=m.NAME_MAX_LENGTH)
    description = serializers.CharField(max_length=m.DESCRIPTION_MAX_LENGTH, allow_blank=True)
    kind = serializers.ChoiceField(choices=m.PromotionKind.choices)
    value = _value(help_text="Percent for percent, an amount for fixed, 0.00 for free shipping.")
    currency = serializers.CharField(max_length=m.CURRENCY_MAX_LENGTH, allow_null=True)
    max_discount = _value(allow_null=True)
    min_subtotal = _value(allow_null=True)
    scope = serializers.ChoiceField(choices=m.PromotionScope.choices)
    automatic = serializers.BooleanField()
    ends_at = serializers.DateTimeField(default_timezone=UTC, allow_null=True)


class ActivePromotionListSerializer(serializers.Serializer):
    items = PublicPromotionSerializer(many=True)
    truncated = serializers.BooleanField(help_text="More promotions are live than ACTIVE_LIST_LIMIT.")


class CodeValidateRequestSerializer(serializers.Serializer):
    code = serializers.CharField(max_length=m.CODE_MAX_LENGTH * 2)
    currency = serializers.RegexField(CURRENCY_RE, max_length=3, required=False)
    subtotal = _amount(required=False, min_value=0, help_text="Cart subtotal, for the minimum-amount rule.")


class CodeValidateResponseSerializer(serializers.Serializer):
    code = serializers.CharField(max_length=m.CODE_MAX_LENGTH * 2, help_text="The code, normalised.")
    valid = serializers.BooleanField()
    error = serializers.CharField(max_length=128, allow_null=True, help_text="Error key when not valid.")
    params = serializers.DictField(child=serializers.CharField(max_length=128))
    promotion = PublicPromotionSerializer(allow_null=True)


# ── staff: promotions ─────────────────────────────────────────────────────


class PromotionWriteRequestSerializer(serializers.Serializer):
    key = serializers.SlugField(max_length=m.KEY_MAX_LENGTH, required=False, allow_blank=True,
                                help_text="Generated from the name when blank.")
    name = serializers.CharField(max_length=m.NAME_MAX_LENGTH)
    description = serializers.CharField(max_length=m.DESCRIPTION_MAX_LENGTH, required=False, allow_blank=True)
    kind = serializers.ChoiceField(choices=m.PromotionKind.choices)
    value = _value(required=False, min_value=0)
    currency = serializers.RegexField(CURRENCY_RE, max_length=3, required=False, allow_blank=True)
    max_discount = _value(required=False, allow_null=True, min_value=0)
    min_subtotal = _value(required=False, allow_null=True, min_value=0)
    scope = serializers.ChoiceField(choices=m.PromotionScope.choices, required=False)
    scope_type = serializers.CharField(max_length=m.PURCHASABLE_TYPE_MAX_LENGTH, required=False, allow_blank=True)
    scope_keys = serializers.ListField(
        child=serializers.CharField(max_length=m.PURCHASABLE_KEY_MAX_LENGTH),
        max_length=m.MAX_SCOPE_KEYS, required=False,
    )
    free_shipping_methods = serializers.ListField(
        child=serializers.CharField(max_length=m.METHOD_CODE_MAX_LENGTH),
        max_length=m.MAX_FREE_SHIPPING_METHODS, required=False,
        help_text="free_shipping only: delivery method codes it waives; empty = every method.",
    )
    starts_at = serializers.DateTimeField(default_timezone=UTC, required=False, allow_null=True)
    ends_at = serializers.DateTimeField(default_timezone=UTC, required=False, allow_null=True)
    is_active = serializers.BooleanField(required=False)
    automatic = serializers.BooleanField(required=False)
    stackable = serializers.BooleanField(required=False)
    priority = serializers.IntegerField(min_value=m.PRIORITY_MIN, max_value=m.PRIORITY_MAX, required=False)
    usage_limit_total = _usage_limit(required=False)
    usage_limit_per_user = _usage_limit(required=False)


class PromotionUsageSerializer(serializers.Serializer):
    committed = serializers.IntegerField(min_value=0)
    reserved = serializers.IntegerField(min_value=0)


class PromotionResponseSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    key = serializers.CharField(max_length=m.KEY_MAX_LENGTH)
    name = serializers.CharField(max_length=m.NAME_MAX_LENGTH)
    description = serializers.CharField(max_length=m.DESCRIPTION_MAX_LENGTH, allow_blank=True)
    kind = serializers.ChoiceField(choices=m.PromotionKind.choices)
    value = _value()
    currency = serializers.CharField(max_length=m.CURRENCY_MAX_LENGTH, allow_null=True)
    max_discount = _value(allow_null=True)
    min_subtotal = _value(allow_null=True)
    scope = serializers.ChoiceField(choices=m.PromotionScope.choices)
    scope_type = serializers.CharField(max_length=m.PURCHASABLE_TYPE_MAX_LENGTH, allow_null=True)
    scope_keys = serializers.ListField(child=serializers.CharField(max_length=m.PURCHASABLE_KEY_MAX_LENGTH),
                                       max_length=m.MAX_SCOPE_KEYS)
    free_shipping_methods = serializers.ListField(child=serializers.CharField(max_length=m.METHOD_CODE_MAX_LENGTH),
                                                  max_length=m.MAX_FREE_SHIPPING_METHODS)
    starts_at = serializers.DateTimeField(default_timezone=UTC, allow_null=True)
    ends_at = serializers.DateTimeField(default_timezone=UTC, allow_null=True)
    is_active = serializers.BooleanField()
    automatic = serializers.BooleanField()
    stackable = serializers.BooleanField()
    priority = serializers.IntegerField(min_value=m.PRIORITY_MIN, max_value=m.PRIORITY_MAX)
    usage_limit_total = serializers.IntegerField(allow_null=True)
    usage_limit_per_user = serializers.IntegerField(allow_null=True)
    usage = PromotionUsageSerializer()
    code_count = serializers.IntegerField(min_value=0)
    live = serializers.BooleanField(help_text="Active and inside its window now.")
    created_at = serializers.DateTimeField(default_timezone=UTC)
    updated_at = serializers.DateTimeField(default_timezone=UTC)


class PromotionPageSerializer(_page(PromotionResponseSerializer)):
    """Anchor envelope of the staff promotion list (schema only)."""


# ── staff: codes ──────────────────────────────────────────────────────────


class PromoCodeCreateRequestSerializer(serializers.Serializer):
    code = serializers.CharField(max_length=m.CODE_MAX_LENGTH, help_text="Stored upper-cased.")
    usage_limit = _usage_limit(required=False)
    assigned_user_id = serializers.CharField(max_length=m.USER_ID_MAX_LENGTH, required=False, allow_blank=True)
    is_active = serializers.BooleanField(required=False, default=True)


class PromoCodeUpdateRequestSerializer(serializers.Serializer):
    is_active = serializers.BooleanField(required=False)
    usage_limit = _usage_limit(required=False)


class PromoCodeResponseSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    promotion_id = serializers.IntegerField()
    code = serializers.CharField(max_length=m.CODE_MAX_LENGTH)
    is_active = serializers.BooleanField()
    usage_limit = serializers.IntegerField(allow_null=True)
    used = serializers.IntegerField(min_value=0, help_text="Committed uses.")
    assigned_user_id = serializers.CharField(max_length=m.USER_ID_MAX_LENGTH, allow_null=True)
    batch = serializers.CharField(max_length=m.BATCH_MAX_LENGTH, allow_null=True)
    created_at = serializers.DateTimeField(default_timezone=UTC)


class PromoCodePageSerializer(_page(PromoCodeResponseSerializer)):
    """Anchor envelope of a promotion's codes (schema only)."""


class CodeGenerateRequestSerializer(serializers.Serializer):
    count = serializers.IntegerField(min_value=1, help_text="At most GENERATE_MAX_PER_CALL.")
    prefix = serializers.CharField(max_length=16, required=False, allow_blank=True)
    length = serializers.IntegerField(min_value=6, max_value=32, required=False)
    usage_limit = _usage_limit(required=False)

    def validate_count(self, value):
        from .conf import promotions_settings

        limit = int(promotions_settings.GENERATE_MAX_PER_CALL)
        if value > limit:
            raise StapelValidationError(ERR_400_GENERATE_COUNT_TOO_LARGE, {"max": limit})
        return value


class CodeGenerateResponseSerializer(serializers.Serializer):
    batch = serializers.CharField(max_length=m.BATCH_MAX_LENGTH)
    created = serializers.IntegerField(min_value=0)
    codes = serializers.ListField(child=serializers.CharField(max_length=m.CODE_MAX_LENGTH))


# ── staff: ledger ─────────────────────────────────────────────────────────


class RedemptionResponseSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    promotion_id = serializers.IntegerField()
    code = serializers.CharField(max_length=m.CODE_MAX_LENGTH, allow_null=True)
    user_id = serializers.CharField(max_length=m.USER_ID_MAX_LENGTH, allow_null=True)
    status = serializers.ChoiceField(choices=m.RedemptionStatus.choices)
    amount = _amount()
    currency = serializers.CharField(max_length=m.CURRENCY_MAX_LENGTH)
    free_shipping = serializers.BooleanField()
    attempt_key = serializers.CharField(max_length=m.ATTEMPT_KEY_MAX_LENGTH)
    order_id = serializers.CharField(max_length=m.ORDER_ID_MAX_LENGTH, allow_null=True)
    created_at = serializers.DateTimeField(default_timezone=UTC)
    updated_at = serializers.DateTimeField(default_timezone=UTC)


class RedemptionPageSerializer(_page(RedemptionResponseSerializer)):
    """Anchor envelope of a promotion's ledger (schema only)."""
