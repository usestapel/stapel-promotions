"""Presenters for stapel-promotions — the DTO-building layer.

Views never instantiate a ``dto.py`` dataclass (SWAP002) nor import a
concrete presenter (SWAP001); they resolve one through ``get_presenter`` so
a host can swap the presentation via ``STAPEL_SWAP`` without forking.
"""
from __future__ import annotations

from django.db.models import Count, Q
from django.utils import timezone
from stapel_core.django.swappable import declare_swap, get_presenter

from .dto import (
    ActivePromotionListDTO,
    CodeGenerateDTO,
    CodeValidateDTO,
    PromoCodeDTO,
    PromotionDTO,
    PromotionUsageDTO,
    PublicPromotionDTO,
    RedemptionDTO,
)

PROMOTION_PRESENTER_KEY = "STAPEL_PROMOTIONS_PROMOTION_PRESENTER"
PUBLIC_PRESENTER_KEY = "STAPEL_PROMOTIONS_PUBLIC_PRESENTER"
CODE_PRESENTER_KEY = "STAPEL_PROMOTIONS_CODE_PRESENTER"
REDEMPTION_PRESENTER_KEY = "STAPEL_PROMOTIONS_REDEMPTION_PRESENTER"
DEFAULT_PROMOTION_PRESENTER = "stapel_promotions.presenters.PromotionPresenter"
DEFAULT_PUBLIC_PRESENTER = "stapel_promotions.presenters.PublicPromotionPresenter"
DEFAULT_CODE_PRESENTER = "stapel_promotions.presenters.PromoCodePresenter"
DEFAULT_REDEMPTION_PRESENTER = "stapel_promotions.presenters.RedemptionPresenter"

declare_swap(PROMOTION_PRESENTER_KEY, DEFAULT_PROMOTION_PRESENTER)
declare_swap(PUBLIC_PRESENTER_KEY, DEFAULT_PUBLIC_PRESENTER)
declare_swap(CODE_PRESENTER_KEY, DEFAULT_CODE_PRESENTER)
declare_swap(REDEMPTION_PRESENTER_KEY, DEFAULT_REDEMPTION_PRESENTER)


def _or_none(value):
    return value or None


class PublicPromotionPresenter:
    def present(self, p) -> PublicPromotionDTO:
        return PublicPromotionDTO(
            key=p.key, name=p.name, description=p.description, kind=p.kind, value=p.value,
            currency=_or_none(p.currency), max_discount=p.max_discount, min_subtotal=p.min_subtotal,
            scope=p.scope, automatic=p.automatic, ends_at=p.ends_at,
        )

    def present_list(self, rows, truncated: bool) -> ActivePromotionListDTO:
        return ActivePromotionListDTO(items=[self.present(p) for p in rows], truncated=truncated)

    def present_validation(self, outcome: dict) -> CodeValidateDTO:
        promotion = outcome["promotion"]
        return CodeValidateDTO(
            code=outcome["code"], valid=outcome["valid"], error=outcome["error"],
            params={k: str(v) for k, v in outcome["params"].items()},
            promotion=self.present(promotion) if promotion is not None else None,
        )


class PromotionPresenter:
    def present(self, p) -> PromotionDTO:
        from .services.redemption import usage_of
        from .services.staff import live

        now = timezone.now()
        usage = usage_of(p.pk, now)
        return PromotionDTO(
            id=p.pk, key=p.key, name=p.name, description=p.description, kind=p.kind, value=p.value,
            currency=_or_none(p.currency), max_discount=p.max_discount, min_subtotal=p.min_subtotal,
            scope=p.scope, scope_type=_or_none(p.scope_type), scope_keys=list(p.scope_keys or []),
            free_shipping_methods=list(p.free_shipping_methods or []),
            starts_at=p.starts_at, ends_at=p.ends_at, is_active=p.is_active, automatic=p.automatic,
            stackable=p.stackable, priority=p.priority, usage_limit_total=p.usage_limit_total,
            usage_limit_per_user=p.usage_limit_per_user,
            usage=PromotionUsageDTO(committed=usage["committed"], reserved=usage["reserved"]),
            code_count=p.codes.count(), live=live(p, now),
            created_at=p.created_at, updated_at=p.updated_at,
        )


class PromoCodePresenter:
    def present(self, c) -> PromoCodeDTO:
        return self.present_many([c])[0]

    def present_many(self, codes) -> list[PromoCodeDTO]:
        from .models import PromoCode, RedemptionStatus

        ids = [c.pk for c in codes]
        used = dict(
            PromoCode.objects.filter(pk__in=ids)
            .annotate(n=Count("redemptions", filter=Q(redemptions__status=RedemptionStatus.COMMITTED)))
            .values_list("pk", "n")
        )
        return [
            PromoCodeDTO(
                id=c.pk, promotion_id=c.promotion_id, code=c.code, is_active=c.is_active,
                usage_limit=c.usage_limit, used=used.get(c.pk, 0),
                assigned_user_id=_or_none(c.assigned_user_id), batch=_or_none(c.batch),
                revoked_at=c.revoked_at, revoke_reason=_or_none(c.revoke_reason),
                created_at=c.created_at,
            )
            for c in codes
        ]

    def present_batch(self, batch: str, codes: list[str]) -> CodeGenerateDTO:
        return CodeGenerateDTO(batch=batch, created=len(codes), codes=codes)


class RedemptionPresenter:
    def present(self, r) -> RedemptionDTO:
        return RedemptionDTO(
            id=r.pk, promotion_id=r.promotion_id, code=r.code.code if r.code_id else None,
            user_id=_or_none(r.user_id), status=r.status, amount=r.amount, currency=r.currency,
            free_shipping=r.free_shipping, attempt_key=r.attempt.attempt_key,
            order_id=_or_none(r.attempt.order_id), created_at=r.created_at, updated_at=r.updated_at,
        )


def get_promotion_presenter() -> type:
    return get_presenter(PROMOTION_PRESENTER_KEY, default=DEFAULT_PROMOTION_PRESENTER)


def get_public_presenter() -> type:
    return get_presenter(PUBLIC_PRESENTER_KEY, default=DEFAULT_PUBLIC_PRESENTER)


def get_code_presenter() -> type:
    return get_presenter(CODE_PRESENTER_KEY, default=DEFAULT_CODE_PRESENTER)


def get_redemption_presenter() -> type:
    return get_presenter(REDEMPTION_PRESENTER_KEY, default=DEFAULT_REDEMPTION_PRESENTER)


__all__ = [
    "PromoCodePresenter", "PromotionPresenter", "PublicPromotionPresenter", "RedemptionPresenter",
    "get_code_presenter", "get_promotion_presenter", "get_public_presenter", "get_redemption_presenter",
]
