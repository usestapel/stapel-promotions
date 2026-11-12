"""Models of stapel-promotions.

A purchasable is an opaque pair ``(purchasable_type, purchasable_key)`` (the
orders seam); nothing here has a foreign key into a sibling module. Users
are referenced by id strings (``str(user.pk)``): an erased redemption keeps
its economics under a pseudonymized id, which a foreign key cannot hold.

Usage of a promotion is COUNTED from the ledger under the promotion's row
lock (``services/redemption.py``); there is no denormalised counter to drift.
"""
from __future__ import annotations

from django.db import models
from stapel_core.access import access

from . import money

USER_ID_MAX_LENGTH = 64
#: ``auto:<key>`` is an adjustment code, which orders bounds at 64 characters.
KEY_MAX_LENGTH = 58
NAME_MAX_LENGTH = 120
DESCRIPTION_MAX_LENGTH = 500
CODE_MAX_LENGTH = 64
CURRENCY_MAX_LENGTH = 3
PURCHASABLE_TYPE_MAX_LENGTH = 32
PURCHASABLE_KEY_MAX_LENGTH = 255
ATTEMPT_KEY_MAX_LENGTH = 128
ISSUE_KEY_MAX_LENGTH = 128
TOKEN_MAX_LENGTH = 64
ORDER_ID_MAX_LENGTH = 64
BATCH_MAX_LENGTH = 32
REASON_MAX_LENGTH = 64
MAX_SCOPE_KEYS = 500
#: Delivery method codes a free-shipping promotion may be narrowed to.
MAX_FREE_SHIPPING_METHODS = 20
METHOD_CODE_MAX_LENGTH = 32
USAGE_LIMIT_MAX = 10_000_000
PRIORITY_MIN, PRIORITY_MAX = -1000, 1000


def _value_field(**kwargs):
    return models.DecimalField(
        max_digits=money.VALUE_MAX_DIGITS, decimal_places=money.DECIMAL_PLACES, **kwargs
    )


class PromotionKind(models.TextChoices):
    PERCENT = "percent", "Percent off"
    FIXED = "fixed", "Fixed amount off"
    FREE_SHIPPING = "free_shipping", "Free shipping"


class PromotionScope(models.TextChoices):
    CART = "cart", "Whole cart"
    PURCHASABLE_TYPE = "purchasable_type", "One purchasable type"
    KEYS = "keys", "Listed purchasables"


class RedemptionStatus(models.TextChoices):
    RESERVED = "reserved", "Reserved"
    COMMITTED = "committed", "Committed"
    RELEASED = "released", "Released"
    EXPIRED = "expired", "Expired"


@access.standard
class Promotion(models.Model):
    """One discount rule; codes (or nothing, when automatic) unlock it."""

    key = models.SlugField(
        max_length=KEY_MAX_LENGTH, unique=True,
        help_text="Stable identifier for other services (codes.issue, facts).",
    )
    name = models.CharField(max_length=NAME_MAX_LENGTH, help_text="Shown to the shopper as the label.")
    description = models.CharField(max_length=DESCRIPTION_MAX_LENGTH, blank=True, default="",
                                   help_text="Longer text for a banner or the staff list.")
    kind = models.CharField(max_length=16, choices=PromotionKind.choices, help_text="What the discount is.")
    value = _value_field(
        default=money.ZERO,
        help_text="Percent (0-100] for percent, an amount for fixed, 0 for free shipping.",
    )
    currency = models.CharField(
        max_length=CURRENCY_MAX_LENGTH, blank=True, default="",
        help_text="ISO 4217; blank = any cart currency (percent and free shipping only).",
    )
    max_discount = _value_field(null=True, blank=True, help_text="Cap of a percent discount.")
    min_subtotal = _value_field(null=True, blank=True, help_text="Cart subtotal needed, in currency.")
    scope = models.CharField(
        max_length=16, choices=PromotionScope.choices, default=PromotionScope.CART,
        help_text="Which cart lines the discount applies to.",
    )
    scope_type = models.CharField(
        max_length=PURCHASABLE_TYPE_MAX_LENGTH, blank=True, default="",
        help_text="purchasable_type for the purchasable_type and keys scopes.",
    )
    scope_keys = models.JSONField(default=list, blank=True, help_text="purchasable keys for the keys scope.")
    free_shipping_methods = models.JSONField(
        default=list, blank=True,
        help_text="free_shipping only: delivery method codes it waives; empty = every method.",
    )
    starts_at = models.DateTimeField(null=True, blank=True, help_text="Inclusive start; null = now.")
    ends_at = models.DateTimeField(null=True, blank=True, help_text="Exclusive end; null = open.")
    is_active = models.BooleanField(default=True, help_text="Off = no code or automatic use applies.")
    automatic = models.BooleanField(default=False, help_text="Applies without a code.")
    stackable = models.BooleanField(default=True, help_text="Combines with other stackable promotions.")
    priority = models.IntegerField(default=0, help_text="Higher applies first inside a stack.")
    usage_limit_total = models.PositiveIntegerField(null=True, blank=True, help_text="Uses over all shoppers.")
    usage_limit_per_user = models.PositiveIntegerField(null=True, blank=True, help_text="Uses per shopper.")
    created_by = models.CharField(max_length=USER_ID_MAX_LENGTH, blank=True, default="",
                                  help_text="Staff user id that created it (pseudonymized on erasure).")
    created_at = models.DateTimeField(auto_now_add=True, help_text="When the row was first written.")
    updated_at = models.DateTimeField(auto_now=True, help_text="Last save.")

    class Meta:
        app_label = "promotions"
        ordering = ["-id"]
        indexes = [models.Index(fields=["automatic", "is_active"], name="promotions_auto_active")]

    def __str__(self) -> str:
        return f"Promotion({self.key})"


@access.standard
class PromoCode(models.Model):
    """A code that unlocks its promotion. Stored upper-cased: lookups are exact."""

    promotion = models.ForeignKey(Promotion, on_delete=models.CASCADE, related_name="codes",
                                  help_text="The promotion this code unlocks.")
    code = models.CharField(max_length=CODE_MAX_LENGTH, unique=True, help_text="Upper-cased, stripped.")
    is_active = models.BooleanField(default=True, help_text="Off = the code reads as not active.")
    usage_limit = models.PositiveIntegerField(null=True, blank=True, help_text="Uses of this code; 1 = single-use.")
    assigned_user_id = models.CharField(
        max_length=USER_ID_MAX_LENGTH, blank=True, default="", db_index=True,
        help_text="Only this user may use the code; blank = anyone.",
    )
    batch = models.CharField(max_length=BATCH_MAX_LENGTH, blank=True, default="", db_index=True,
                             help_text="Generation batch id; blank for a hand-made code.")
    revoked_at = models.DateTimeField(
        null=True, blank=True,
        help_text="Set once by promotions.codes.revoke; a revoked code never applies again.",
    )
    revoke_reason = models.CharField(max_length=REASON_MAX_LENGTH, blank=True, default="",
                                     help_text="Why the issuer took the code back (first revoke wins).")
    created_at = models.DateTimeField(auto_now_add=True, help_text="When the row was first written.")
    updated_at = models.DateTimeField(auto_now=True, help_text="Last save.")

    class Meta:
        app_label = "promotions"
        ordering = ["-id"]

    def __str__(self) -> str:
        return self.code


@access.ops
class CodeIssue(models.Model):
    """One ``promotions.codes.issue`` call keyed by the caller's idempotency key.

    The unique key is the replay guard: a retry after a lost answer finds this
    row and answers with the codes of its batch instead of minting again.
    """

    idempotency_key = models.CharField(max_length=ISSUE_KEY_MAX_LENGTH, unique=True,
                                       help_text="The caller's retry key (namespaced by the caller).")
    promotion = models.ForeignKey(Promotion, on_delete=models.CASCADE, related_name="issues",
                                  help_text="The promotion the codes were minted for.")
    fingerprint = models.CharField(max_length=64, help_text="sha256 of the request; a reused key with "
                                                            "another request is refused.")
    batch = models.CharField(max_length=BATCH_MAX_LENGTH, blank=True, default="",
                             help_text="Batch of the minted codes.")
    created_at = models.DateTimeField(auto_now_add=True, help_text="When the row was first written.")

    class Meta:
        app_label = "promotions"

    def __str__(self) -> str:
        return f"CodeIssue({self.idempotency_key})"


@access.ops
class RedemptionAttempt(models.Model):
    """One checkout attempt (orders' idempotency key) and what it reserved."""

    attempt_key = models.CharField(max_length=ATTEMPT_KEY_MAX_LENGTH, unique=True,
                                   help_text="The orders checkout idempotency key.")
    token = models.CharField(max_length=TOKEN_MAX_LENGTH, unique=True, help_text="Opaque token handed to orders.")
    user_id = models.CharField(max_length=USER_ID_MAX_LENGTH, blank=True, default="", db_index=True,
                               help_text="Shopper id; blank for a guest.")
    status = models.CharField(max_length=16, choices=RedemptionStatus.choices, default=RedemptionStatus.RESERVED,
                              help_text="Ledger state of every row of this attempt.")
    currency = models.CharField(max_length=CURRENCY_MAX_LENGTH, blank=True, default="", help_text="Cart currency.")
    result = models.JSONField(default=dict, blank=True, help_text="The adjustments reserved (replay answer).")
    order_id = models.CharField(max_length=ORDER_ID_MAX_LENGTH, blank=True, default="",
                                help_text="The order id commit reported.")
    release_reason = models.CharField(max_length=REASON_MAX_LENGTH, blank=True, default="",
                                      help_text="checkout_failed, cancelled or orphaned.")
    expires_at = models.DateTimeField(help_text="A reservation stops counting after this instant.")
    created_at = models.DateTimeField(auto_now_add=True, help_text="When the row was first written.")
    updated_at = models.DateTimeField(auto_now=True, help_text="Last save.")

    class Meta:
        app_label = "promotions"
        indexes = [models.Index(fields=["status", "expires_at"], name="promotions_attempt_status")]

    def __str__(self) -> str:
        return f"RedemptionAttempt({self.attempt_key}, {self.status})"


@access.sensitive
class Redemption(models.Model):
    """The ledger: one promotion granted (or held) on one checkout attempt."""

    attempt = models.ForeignKey(RedemptionAttempt, on_delete=models.CASCADE, related_name="redemptions",
                                help_text="The checkout attempt that holds this use.")
    promotion = models.ForeignKey(Promotion, on_delete=models.PROTECT, related_name="redemptions",
                                  help_text="The promotion used.")
    code = models.ForeignKey(PromoCode, null=True, blank=True, on_delete=models.PROTECT, related_name="redemptions",
                             help_text="The code used; null for an automatic promotion.")
    user_id = models.CharField(max_length=USER_ID_MAX_LENGTH, blank=True, default="",
                               help_text="Shopper id (pseudonym after erasure); blank for a guest.")
    status = models.CharField(max_length=16, choices=RedemptionStatus.choices, default=RedemptionStatus.RESERVED,
                              help_text="reserved, committed, released or expired.")
    amount = models.DecimalField(
        max_digits=money.AMOUNT_MAX_DIGITS, decimal_places=money.DECIMAL_PLACES,
        help_text="The discount granted, positive, in currency.",
    )
    currency = models.CharField(max_length=CURRENCY_MAX_LENGTH, help_text="Cart currency of the amount.")
    free_shipping = models.BooleanField(default=False, help_text="The use granted free shipping.")
    created_at = models.DateTimeField(auto_now_add=True, help_text="When the row was first written.")
    updated_at = models.DateTimeField(auto_now=True, help_text="Last save.")

    class Meta:
        app_label = "promotions"
        ordering = ["-id"]
        constraints = [
            models.UniqueConstraint(fields=["attempt", "promotion"], name="promotions_one_row_per_attempt"),
        ]
        indexes = [
            models.Index(fields=["promotion", "status"], name="promotions_red_promo_status"),
            models.Index(fields=["promotion", "user_id", "status"], name="promotions_red_promo_user"),
            models.Index(fields=["user_id"], name="promotions_red_user"),
        ]

    def __str__(self) -> str:
        return f"Redemption({self.promotion_id}, {self.status}, {self.amount})"


#: Ledger states that may count toward a cap (reserved ones only while live).
HOLDING_STATUSES = (RedemptionStatus.RESERVED, RedemptionStatus.COMMITTED)
