"""i18n error keys of stapel-promotions.

Only ``error.<status>.<slug>`` keys leave this package — human-readable
strings are translations, never literals in responses. The ``promo_code_*``
keys also travel inside the orders adjuster answer (``errors[].code``), and
orders refuses the checkout with exactly that key.
"""
from stapel_core.django.api.errors import register_service_errors

ERR_400_PROMO_CODE_UNKNOWN = "error.400.promo_code_unknown"
ERR_400_PROMO_CODES_TOO_MANY = "error.400.promo_codes_too_many"
ERR_400_PROMO_CODES_INVALID = "error.400.promo_codes_invalid"
ERR_400_PROMOTION_VALUE_INVALID = "error.400.promotion_value_invalid"
ERR_400_PROMOTION_CURRENCY_REQUIRED = "error.400.promotion_currency_required"
ERR_400_PROMOTION_SCOPE_INVALID = "error.400.promotion_scope_invalid"
ERR_400_PROMOTION_WINDOW_INVALID = "error.400.promotion_window_invalid"
ERR_400_PROMO_CODE_FORMAT_INVALID = "error.400.promo_code_format_invalid"
ERR_400_GENERATE_COUNT_TOO_LARGE = "error.400.generate_count_too_large"
ERR_403_PROMO_CODE_REQUIRES_ACCOUNT = "error.403.promo_code_requires_account"
ERR_404_PROMOTION_NOT_FOUND = "error.404.promotion_not_found"
ERR_404_PROMO_CODE_NOT_FOUND = "error.404.promo_code_not_found"
ERR_409_PROMO_CODE_NOT_ACTIVE = "error.409.promo_code_not_active"
ERR_409_PROMO_CODE_EXHAUSTED = "error.409.promo_code_exhausted"
ERR_409_PROMO_CODE_REVOKED = "error.409.promo_code_revoked"
ERR_409_PROMO_CODE_USER_LIMIT_REACHED = "error.409.promo_code_user_limit_reached"
ERR_409_PROMO_CODE_MIN_SUBTOTAL = "error.409.promo_code_min_subtotal"
ERR_409_PROMO_CODE_NOT_APPLICABLE = "error.409.promo_code_not_applicable"
#: params ``label``; also ``code`` when the grant came from a promo code (optional:
#: the template cannot name it, so errors.json lists ``label`` only).
ERR_409_PROMOTION_EXHAUSTED = "error.409.promotion_exhausted"
ERR_409_PROMOTION_HAS_REDEMPTIONS = "error.409.promotion_has_redemptions"
ERR_409_PROMO_CODE_HAS_REDEMPTIONS = "error.409.promo_code_has_redemptions"
ERR_409_PROMO_CODE_TAKEN = "error.409.promo_code_taken"
ERR_409_PROMOTION_KEY_TAKEN = "error.409.promotion_key_taken"
ERR_409_CODE_SPACE_EXHAUSTED = "error.409.code_space_exhausted"

STAPEL_PROMOTIONS_ERRORS = {
    ERR_400_PROMO_CODE_UNKNOWN: "Promo code {code} does not exist",
    ERR_400_PROMO_CODES_TOO_MANY: "At most {max} promo codes per order",
    ERR_400_PROMO_CODES_INVALID: "Promo codes must be a list of short texts",
    ERR_400_PROMOTION_VALUE_INVALID: "The discount value is out of range for this kind of promotion",
    ERR_400_PROMOTION_CURRENCY_REQUIRED: "A fixed discount or a minimum order amount needs a currency",
    ERR_400_PROMOTION_SCOPE_INVALID: "The promotion scope is incomplete",
    ERR_400_PROMOTION_WINDOW_INVALID: "The promotion must end after it starts",
    ERR_400_PROMO_CODE_FORMAT_INVALID: "A promo code may contain only Latin letters, digits, dashes and underscores, and starts with a letter or digit",
    ERR_400_GENERATE_COUNT_TOO_LARGE: "At most {max} codes per request",
    ERR_403_PROMO_CODE_REQUIRES_ACCOUNT: "Sign in to use promo code {code}",
    ERR_404_PROMOTION_NOT_FOUND: "Promotion not found",
    ERR_404_PROMO_CODE_NOT_FOUND: "Promo code not found",
    ERR_409_PROMO_CODE_NOT_ACTIVE: "Promo code {code} is not active",
    ERR_409_PROMO_CODE_EXHAUSTED: "Promo code {code} has been used up",
    ERR_409_PROMO_CODE_REVOKED: "Promo code {code} has been withdrawn",
    ERR_409_PROMO_CODE_USER_LIMIT_REACHED: "You have already used promo code {code}",
    ERR_409_PROMO_CODE_MIN_SUBTOTAL: "Promo code {code} needs an order of at least {min_subtotal} {currency}",
    ERR_409_PROMO_CODE_NOT_APPLICABLE: "Promo code {code} does not apply to this order",
    ERR_409_PROMOTION_EXHAUSTED: "The promotion {label} has just ended; review your order",
    ERR_409_PROMOTION_HAS_REDEMPTIONS: "This promotion has been used; deactivate it instead",
    ERR_409_PROMO_CODE_HAS_REDEMPTIONS: "This promo code has been used; deactivate it instead",
    ERR_409_PROMO_CODE_TAKEN: "Promo code {code} already exists",
    ERR_409_PROMOTION_KEY_TAKEN: "A promotion with key {key} already exists",
    ERR_409_CODE_SPACE_EXHAUSTED: "No more unique codes of this length; use a longer code or a prefix",
}

#: Explicit remediation where the status heuristic would mislead.
STAPEL_PROMOTIONS_REMEDIATION = {
    ERR_403_PROMO_CODE_REQUIRES_ACCOUNT: "reauthenticate",
    ERR_409_PROMO_CODE_NOT_ACTIVE: "fix_input",
    ERR_409_PROMO_CODE_EXHAUSTED: "fix_input",
    ERR_409_PROMO_CODE_REVOKED: "fix_input",
    ERR_409_PROMO_CODE_USER_LIMIT_REACHED: "fix_input",
    ERR_409_PROMO_CODE_MIN_SUBTOTAL: "fix_input",
    ERR_409_PROMO_CODE_NOT_APPLICABLE: "fix_input",
    ERR_409_PROMOTION_EXHAUSTED: "retry",
}

register_service_errors(STAPEL_PROMOTIONS_ERRORS, remediation=STAPEL_PROMOTIONS_REMEDIATION)

__all__ = ["STAPEL_PROMOTIONS_ERRORS", *sorted(n for n in tuple(globals()) if n.startswith("ERR_"))]
