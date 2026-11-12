"""Promo code text: normalisation and bulk generation.

A code is stored upper-cased with whitespace removed, so the unique column
IS the case-insensitivity: ``summer-10``, ``SUMMER-10`` and `` Summer-10 ``
are one code, and a lookup is an exact match.
"""
from __future__ import annotations

import re
import secrets

from django.db import transaction

from ..errors import ERR_400_PROMO_CODE_FORMAT_INVALID, ERR_409_CODE_SPACE_EXHAUSTED
from ..models import CODE_MAX_LENGTH, PromoCode
from . import PromotionsError

CODE_RE = re.compile(r"^[A-Z0-9][A-Z0-9_-]*$")
PREFIX_RE = re.compile(r"^[A-Z0-9][A-Z0-9_-]*$")
MIN_LENGTH, MAX_GENERATED_LENGTH = 6, 32
#: A batch may fill at most this share of the code space (1 / N), so a
#: generated code cannot be guessed by trying neighbours.
SPACE_DENSITY = 1_000_000
_ROUNDS = 8


def normalize(raw) -> str:
    return re.sub(r"\s+", "", str(raw or "")).upper()


def is_well_formed(code: str) -> bool:
    return 0 < len(code) <= CODE_MAX_LENGTH and bool(CODE_RE.match(code))


def require_well_formed(raw) -> str:
    code = normalize(raw)
    if not is_well_formed(code):
        raise PromotionsError(400, ERR_400_PROMO_CODE_FORMAT_INVALID)
    return code


def code_space(alphabet: str, length: int) -> int:
    return len(set(alphabet)) ** length


def generate(promotion, count: int, *, prefix: str = "", length: int | None = None,
             usage_limit: int | None = None, assigned_user_id: str = "") -> tuple[str, list[str]]:
    """Create *count* new unique codes for *promotion*; returns ``(batch, codes)``.

    Candidates are inserted with ``ignore_conflicts`` and the batch is counted
    back from the table, so a collision with an existing code (or with a
    concurrent batch) costs a retry, never a duplicate or a 500.
    """
    from ..conf import promotions_settings

    alphabet = "".join(dict.fromkeys(promotions_settings.CODE_ALPHABET))
    length = int(length or promotions_settings.CODE_LENGTH)
    prefix = normalize(prefix)
    if prefix and not PREFIX_RE.match(prefix):
        raise PromotionsError(400, ERR_400_PROMO_CODE_FORMAT_INVALID)
    if len(prefix) + length > CODE_MAX_LENGTH or not is_well_formed(prefix + alphabet[0] * length):
        raise PromotionsError(400, ERR_400_PROMO_CODE_FORMAT_INVALID)
    if count * SPACE_DENSITY > code_space(alphabet, length):
        raise PromotionsError(409, ERR_409_CODE_SPACE_EXHAUSTED)
    batch = secrets.token_hex(8)
    with transaction.atomic():
        made = 0
        for _ in range(_ROUNDS):
            need = count - made
            if need <= 0:
                break
            fresh = {prefix + "".join(secrets.choice(alphabet) for _ in range(length)) for _ in range(need)}
            PromoCode.objects.bulk_create(
                [PromoCode(promotion=promotion, code=code, batch=batch, usage_limit=usage_limit,
                           assigned_user_id=assigned_user_id) for code in sorted(fresh)],
                ignore_conflicts=True, batch_size=1000,
            )
            made = PromoCode.objects.filter(batch=batch).count()
        if made < count:
            raise PromotionsError(409, ERR_409_CODE_SPACE_EXHAUSTED)
    codes = list(PromoCode.objects.filter(batch=batch).order_by("id").values_list("code", flat=True))
    return batch, codes


__all__ = ["code_space", "generate", "is_well_formed", "normalize", "require_well_formed"]
