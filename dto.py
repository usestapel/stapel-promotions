"""Dataclass DTOs — the API models of stapel-promotions (never ORM instances).

Built only by the presenters (``presenters.py``); views never instantiate
them. Money is ``Decimal`` here and a string on the wire; instants are UTC.
"""
from __future__ import annotations

import datetime
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Optional


@dataclass
class PublicPromotionDTO:
    """What a shopper may know about a promotion (no caps, no usage)."""

    key: str
    name: str
    description: str
    kind: str
    value: Decimal
    currency: Optional[str]
    max_discount: Optional[Decimal]
    min_subtotal: Optional[Decimal]
    scope: str
    automatic: bool
    ends_at: Optional[datetime.datetime]


@dataclass
class ActivePromotionListDTO:
    """Automatic promotions live now (bounded; ``truncated`` when cut)."""

    items: list[PublicPromotionDTO] = field(default_factory=list)
    truncated: bool = False


@dataclass
class CodeValidateDTO:
    """Would this code apply; ``error`` is the reason when it would not."""

    code: str
    valid: bool
    error: Optional[str]
    params: dict
    promotion: Optional[PublicPromotionDTO]


@dataclass
class PromotionUsageDTO:
    """Holding uses: committed, and reservations still inside their TTL."""

    committed: int
    reserved: int


@dataclass
class PromotionDTO:
    """A promotion as staff sees it."""

    id: int
    key: str
    name: str
    description: str
    kind: str
    value: Decimal
    currency: Optional[str]
    max_discount: Optional[Decimal]
    min_subtotal: Optional[Decimal]
    scope: str
    scope_type: Optional[str]
    scope_keys: list[str]
    free_shipping_methods: list[str]
    starts_at: Optional[datetime.datetime]
    ends_at: Optional[datetime.datetime]
    is_active: bool
    automatic: bool
    stackable: bool
    priority: int
    usage_limit_total: Optional[int]
    usage_limit_per_user: Optional[int]
    usage: PromotionUsageDTO
    code_count: int
    live: bool
    created_at: datetime.datetime
    updated_at: datetime.datetime


@dataclass
class PromoCodeDTO:
    """One code of a promotion."""

    id: int
    promotion_id: int
    code: str
    is_active: bool
    usage_limit: Optional[int]
    used: int
    assigned_user_id: Optional[str]
    batch: Optional[str]
    revoked_at: Optional[datetime.datetime]
    revoke_reason: Optional[str]
    created_at: datetime.datetime


@dataclass
class CodeGenerateDTO:
    """A generated batch."""

    batch: str
    created: int
    codes: list[str]


@dataclass
class RedemptionDTO:
    """One ledger row."""

    id: int
    promotion_id: int
    code: Optional[str]
    user_id: Optional[str]
    status: str
    amount: Decimal
    currency: str
    free_shipping: bool
    attempt_key: str
    order_id: Optional[str]
    created_at: datetime.datetime
    updated_at: datetime.datetime
