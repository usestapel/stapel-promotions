"""Settings namespace for stapel-promotions.

All configuration is read through ``promotions_settings`` (lazily, at call
time). Resolution order per key: ``settings.STAPEL_PROMOTIONS`` dict -> flat
Django setting of the same name -> environment variable -> default below.

The orders wiring (``STAPEL_ORDERS["ADJUSTERS"]`` / ``["CHECKOUT_HOOKS"]``)
is NOT a key here: it lives in orders' namespace and this package ships it
as ``stapel_promotions.preset.SETTINGS_DEFAULTS``.
"""
from stapel_core.conf import AppSettings

#: Code characters: upper-case letters and digits without the look-alikes
#: 0/O and 1/I, so a code read aloud or retyped from paper survives.
DEFAULT_CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"

#: AppSettings-shaped literal dict: a top-level DEFAULTS lets the
#: capabilities.json emitter introspect axis keys/kinds.
DEFAULTS = {
    # How many codes one cart may carry in context.codes.
    "MAX_CODES_PER_CART": 5,
    # A reserved redemption stops counting toward caps after this many
    # minutes; housekeeping marks it expired.
    "RESERVATION_TTL_MINUTES": 30,
    # Axis: does a `cancelled` release after commit (a cancelled order) give the use
    # back? True = the shopper may use the code again.
    "RETURN_USAGE_ON_RELEASE": True,
    # DRF rate for the public validate endpoint (code enumeration guard),
    # per user or per address. Empty string disables the throttle.
    "VALIDATE_RATE": "30/min",
    # Generated codes: length (without prefix) and alphabet.
    "CODE_LENGTH": 10,
    "CODE_ALPHABET": DEFAULT_CODE_ALPHABET,
    # Most codes one API call may generate (the management command is not
    # bound by it).
    "GENERATE_MAX_PER_CALL": 1000,
    # GET active/ answers at most this many promotions.
    "ACTIVE_LIST_LIMIT": 50,
    # Released and expired attempts older than this many days are purged.
    "ATTEMPT_TTL_DAYS": 90,
}

promotions_settings = AppSettings("STAPEL_PROMOTIONS", defaults=DEFAULTS)

__all__ = ["promotions_settings", "DEFAULTS", "DEFAULT_CODE_ALPHABET"]
