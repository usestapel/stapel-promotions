"""Django system checks for stapel-promotions.

Each passes silently without stapel-orders installed: the brick's own API
and Functions work alone.

"Orders is present" is asked over comm: its ``orders.order.status``
Function is reachable on this transport.

- W001  orders is installed, the ``promotions.quote`` adjuster is not
        registered in ``STAPEL_ORDERS["ADJUSTERS"]`` (codes apply nowhere).
- E001  the adjuster is registered without the checkout hook: discounts
        would be granted and never counted against a cap.
- W002  the hold TTL is not longer than orders' orphan sweep (a crashed
        checkout's hold would lapse before orders releases it).
- E002  the generated-code space is small enough to enumerate.
"""
from django.conf import settings
from django.core import checks

from .preset import SETTINGS_DEFAULTS

#: A Function stapel-orders provides: reachable here = orders runs with us.
ORDERS_PROBE_FUNCTION = "orders.order.status"
#: orders' default CHECKOUT_HOOK_ORPHAN_SECONDS (contract delta 17).
ORDERS_ORPHAN_SECONDS_DEFAULT = 900
#: Below this many possible codes a generated code can be guessed.
MIN_CODE_SPACE = 10**12

_WIRING = SETTINGS_DEFAULTS["STAPEL_ORDERS"]


def _orders_installed() -> bool:
    """Is stapel-orders reachable on this transport (comm, not an import)?"""
    from stapel_core.comm import function_unreachable_reason

    return function_unreachable_reason(ORDERS_PROBE_FUNCTION) is None


def _orders_registry(name: str) -> dict:
    raw = getattr(settings, "STAPEL_ORDERS", None) or {}
    value = raw.get(name) if isinstance(raw, dict) else None
    return value if isinstance(value, dict) else {}


def _registered(name: str, role: str) -> bool:
    function = next(iter(_WIRING[name].values()))[role]
    return any(isinstance(entry, dict) and entry.get(role) == function
               for entry in _orders_registry(name).values())


@checks.register(checks.Tags.compatibility)
def check_orders_wiring(app_configs, **kwargs):
    findings = []
    adjuster = _registered("ADJUSTERS", "quote")
    hooks = all(_registered("CHECKOUT_HOOKS", role) for role in ("reserve", "commit", "release"))
    if adjuster and not hooks:
        findings.append(checks.Error(
            "STAPEL_ORDERS['ADJUSTERS'] names promotions.quote but STAPEL_ORDERS['CHECKOUT_HOOKS'] "
            "does not name promotions.reserve/commit/release: discounts would be granted and "
            "never counted against a usage cap.",
            hint="Paste stapel_promotions.preset.SETTINGS_DEFAULTS['STAPEL_ORDERS'] (both keys).",
            id="stapel_promotions.E001",
        ))
    elif _orders_installed() and not adjuster:
        findings.append(checks.Warning(
            "stapel-orders is installed but STAPEL_ORDERS['ADJUSTERS'] does not name "
            "promotions.quote: promo codes apply to no order.",
            hint="Paste stapel_promotions.preset.SETTINGS_DEFAULTS['STAPEL_ORDERS'] into STAPEL_ORDERS.",
            id="stapel_promotions.W001",
        ))
    if _orders_installed() and hooks:
        from .conf import promotions_settings

        raw = getattr(settings, "STAPEL_ORDERS", None) or {}
        orphan = raw.get("CHECKOUT_HOOK_ORPHAN_SECONDS", ORDERS_ORPHAN_SECONDS_DEFAULT) if isinstance(raw, dict) \
            else ORDERS_ORPHAN_SECONDS_DEFAULT
        ttl = int(promotions_settings.RESERVATION_TTL_MINUTES) * 60
        if ttl <= int(orphan):
            findings.append(checks.Warning(
                f"STAPEL_PROMOTIONS['RESERVATION_TTL_MINUTES'] ({ttl}s) is not longer than orders' "
                f"orphan sweep ({orphan}s): a crashed checkout's hold lapses before orders releases it.",
                id="stapel_promotions.W002",
            ))
    return findings


@checks.register(checks.Tags.compatibility)
def check_code_space(app_configs, **kwargs):
    from .conf import promotions_settings
    from .services.codes import code_space

    alphabet = str(promotions_settings.CODE_ALPHABET or "")
    length = int(promotions_settings.CODE_LENGTH or 0)
    if code_space(alphabet, length) >= MIN_CODE_SPACE and alphabet.isalnum() and alphabet == alphabet.upper():
        return []
    return [checks.Error(
        f"Generated promo codes ({len(set(alphabet))} symbols ^ {length}) are guessable or not "
        "upper-case alphanumeric.",
        hint=f"Use CODE_LENGTH/CODE_ALPHABET giving at least {MIN_CODE_SPACE:.0e} codes, upper-case A-Z0-9.",
        id="stapel_promotions.E002",
    )]
