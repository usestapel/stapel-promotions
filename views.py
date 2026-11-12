"""DRF views for stapel-promotions — thin over ``services``.

Every view declares its guest stance. The two public reads admit anyone
(the validate endpoint is throttled against code enumeration); the staff
surface refuses guests and non-staff.
"""
import csv

from django.http import StreamingHttpResponse
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import permissions
from rest_framework.throttling import SimpleRateThrottle
from stapel_core.django.api.errors import StapelErrorResponse
from stapel_core.django.api.permissions import ANONYMOUS_ALLOWED, ANONYMOUS_DENIED, IsStaffUser
from stapel_core.django.api.views import StapelAPIView
from stapel_core.flows import flow_step

from .flows import MANAGE, REDEEM
from .models import PromotionKind
from .pagination import IdAnchorPagination
from .presenters import (
    get_code_presenter,
    get_promotion_presenter,
    get_public_presenter,
    get_redemption_presenter,
)
from .serializers import (
    ActivePromotionListSerializer,
    CodeGenerateRequestSerializer,
    CodeGenerateResponseSerializer,
    CodeValidateRequestSerializer,
    CodeValidateResponseSerializer,
    PromoCodeCreateRequestSerializer,
    PromoCodePageSerializer,
    PromoCodeResponseSerializer,
    PromoCodeUpdateRequestSerializer,
    PromotionPageSerializer,
    PromotionResponseSerializer,
    PromotionWriteRequestSerializer,
    RedemptionPageSerializer,
    RedemptionResponseSerializer,
)
from .services import PromotionsError
from .services import staff as staff_service

TAGS = ["Promotions"]


class ValidateThrottle(SimpleRateThrottle):
    """``VALIDATE_RATE`` per user (or per address for a request without one)."""

    scope = "promotions_validate"

    def get_rate(self):
        from .conf import promotions_settings

        return promotions_settings.VALIDATE_RATE or None

    def get_cache_key(self, request, view):
        user = getattr(request, "user", None)
        ident = f"u{user.pk}" if user is not None and user.is_authenticated else self.get_ident(request)
        return self.cache_format % {"scope": self.scope, "ident": ident}


class _PromotionsView(StapelAPIView):
    """Renders a service refusal as the fleet error envelope."""

    def handle_exception(self, exc):
        if isinstance(exc, PromotionsError):
            return StapelErrorResponse(exc.status, exc.key, exc.params)
        return super().handle_exception(exc)

    def _page(self, request, qs, present_many):
        paginator = IdAnchorPagination()
        page = paginator.paginate_queryset(qs, request, view=self)
        data = self.get_response_serializer_class()(present_many(page), many=True).data
        return paginator.get_paginated_response(data)


class _StaffView(_PromotionsView):
    permission_classes = [IsStaffUser]
    stapel_anonymous_access = ANONYMOUS_DENIED


BATCH_PARAMETER = OpenApiParameter("batch", str, OpenApiParameter.QUERY, required=False,
                                   description="Only the codes of this generation batch.")

ANCHOR_PARAMETERS = [
    OpenApiParameter("anchor", str, OpenApiParameter.QUERY, required=False,
                     description="Opaque anchor from next_anchor/prev_anchor (exclusive)."),
    OpenApiParameter("limit", int, OpenApiParameter.QUERY, required=False,
                     description=f"Page size, default {IdAnchorPagination.page_size}, "
                                 f"max {IdAnchorPagination.max_page_size}."),
    OpenApiParameter("direction", str, OpenApiParameter.QUERY, required=False,
                     enum=["next", "prev"], description="Walk direction from the anchor."),
]


def _flag(raw):
    if raw in (None, ""):
        return None
    return str(raw).lower() in ("1", "true", "yes")


def _promotions(rows):
    presenter = get_promotion_presenter()()
    return [presenter.present(p) for p in rows]


# ── public ────────────────────────────────────────────────────────────────


@extend_schema(tags=TAGS)
class ActivePromotionsView(_PromotionsView):
    """Automatic promotions live now — for a sale banner."""

    permission_classes = [permissions.AllowAny]
    stapel_anonymous_access = ANONYMOUS_ALLOWED
    response_serializer_class = ActivePromotionListSerializer

    @extend_schema(operation_id="promotions_active_list", responses={200: ActivePromotionListSerializer})
    @flow_step(REDEEM, order=1, note="See the sales running now")
    def get(self, request):
        rows, truncated = staff_service.active_promotions()
        return self.serialized_response(get_public_presenter()().present_list(rows, truncated))


@extend_schema(tags=TAGS)
class CodeValidateView(_PromotionsView):
    """Would this code apply? The real discount comes from the orders quote."""

    permission_classes = [permissions.AllowAny]
    stapel_anonymous_access = ANONYMOUS_ALLOWED
    throttle_classes = [ValidateThrottle]
    request_serializer_class = CodeValidateRequestSerializer
    response_serializer_class = CodeValidateResponseSerializer

    @extend_schema(operation_id="promotions_codes_validate", request=CodeValidateRequestSerializer,
                   responses={200: CodeValidateResponseSerializer})
    @flow_step(REDEEM, order=2, note="Check a promo code before adding it to the order")
    def post(self, request):
        data = self.validated_request_data(request)
        user = request.user
        outcome = staff_service.validate_code(
            data["code"],
            user_id=str(user.pk) if user is not None and user.is_authenticated else None,
            currency=data.get("currency"),
            subtotal=data.get("subtotal"),
        )
        return self.serialized_response(get_public_presenter()().present_validation(outcome))


# ── staff: promotions ─────────────────────────────────────────────────────


@extend_schema(tags=TAGS)
class StaffPromotionListView(_StaffView):
    request_serializer_class = PromotionWriteRequestSerializer
    response_serializer_class = PromotionResponseSerializer

    @extend_schema(
        operation_id="promotions_staff_promotions_list",
        parameters=[
            OpenApiParameter("kind", str, OpenApiParameter.QUERY, required=False,
                             enum=[k for k, _ in PromotionKind.choices]),
            OpenApiParameter("active", bool, OpenApiParameter.QUERY, required=False),
            OpenApiParameter("q", str, OpenApiParameter.QUERY, required=False,
                             description="Name, key or an exact code."),
            *ANCHOR_PARAMETERS,
        ],
        responses={200: PromotionPageSerializer},
    )
    @flow_step(MANAGE, order=1, note="Page through the promotions")
    def get(self, request):
        qs = staff_service.list_promotions(
            kind=request.query_params.get("kind"),
            active=_flag(request.query_params.get("active")),
            q=(request.query_params.get("q") or "")[:64],
        )
        return self._page(request, qs, _promotions)

    @extend_schema(operation_id="promotions_staff_promotions_create",
                   request=PromotionWriteRequestSerializer, responses={201: PromotionResponseSerializer})
    @flow_step(MANAGE, order=2, note="Create a promotion")
    def post(self, request):
        promotion = staff_service.create_promotion(request.user, self.validated_request_data(request))
        return self.serialized_response(get_promotion_presenter()().present(promotion), status=201)


@extend_schema(tags=TAGS)
class StaffPromotionDetailView(_StaffView):
    request_serializer_class = PromotionWriteRequestSerializer
    response_serializer_class = PromotionResponseSerializer

    @extend_schema(operation_id="promotions_staff_promotions_retrieve",
                   responses={200: PromotionResponseSerializer})
    @flow_step(MANAGE, order=3, note="Open a promotion with its usage")
    def get(self, request, promotion_id):
        return self.serialized_response(
            get_promotion_presenter()().present(staff_service.get_promotion(promotion_id)))

    @extend_schema(operation_id="promotions_staff_promotions_update",
                   request=PromotionWriteRequestSerializer, responses={200: PromotionResponseSerializer})
    @flow_step(MANAGE, order=4, note="Change or deactivate a promotion")
    def patch(self, request, promotion_id):
        data = self.validated_request_data(request, partial=True)
        promotion = staff_service.update_promotion(promotion_id, data)
        return self.serialized_response(get_promotion_presenter()().present(promotion))

    @extend_schema(operation_id="promotions_staff_promotions_destroy", responses={204: None})
    @flow_step(MANAGE, order=5, note="Delete a promotion nobody has used")
    def delete(self, request, promotion_id):
        staff_service.delete_promotion(promotion_id)
        return self.serialized_response(None, status=204)


# ── staff: codes ──────────────────────────────────────────────────────────


@extend_schema(tags=TAGS)
class StaffCodeListView(_StaffView):
    request_serializer_class = PromoCodeCreateRequestSerializer
    response_serializer_class = PromoCodeResponseSerializer

    @extend_schema(operation_id="promotions_staff_codes_list", parameters=[*ANCHOR_PARAMETERS, BATCH_PARAMETER],
                   responses={200: PromoCodePageSerializer})
    @flow_step(MANAGE, order=6, note="Page through a promotion's codes")
    def get(self, request, promotion_id):
        qs = staff_service.list_codes(promotion_id, request.query_params.get("batch") or None)
        return self._page(request, qs, get_code_presenter()().present_many)

    @extend_schema(operation_id="promotions_staff_codes_create", request=PromoCodeCreateRequestSerializer,
                   responses={201: PromoCodeResponseSerializer})
    @flow_step(MANAGE, order=7, note="Attach a code")
    def post(self, request, promotion_id):
        code = staff_service.create_code(promotion_id, self.validated_request_data(request))
        return self.serialized_response(get_code_presenter()().present(code), status=201)


class _EchoBuffer:
    def write(self, value):
        return value


@extend_schema(tags=TAGS)
class StaffCodeExportView(_StaffView):
    """A promotion's codes as CSV, streamed; cells are formula-injection safe (staff)."""

    @extend_schema(operation_id="promotions_staff_codes_export", parameters=[BATCH_PARAMETER],
                   responses={(200, "text/csv"): OpenApiTypes.STR})
    def get(self, request, promotion_id):
        rows = staff_service.export_code_rows(promotion_id, request.query_params.get("batch") or None)
        writer = csv.writer(_EchoBuffer())
        response = StreamingHttpResponse(
            (writer.writerow(row) for row in rows),
            content_type="text/csv; charset=utf-8",
        )
        response["Content-Disposition"] = f'attachment; filename="promotion-{int(promotion_id)}-codes.csv"'
        return response


@extend_schema(tags=TAGS)
class StaffCodeGenerateView(_StaffView):
    request_serializer_class = CodeGenerateRequestSerializer
    response_serializer_class = CodeGenerateResponseSerializer

    @extend_schema(operation_id="promotions_staff_codes_generate", request=CodeGenerateRequestSerializer,
                   responses={201: CodeGenerateResponseSerializer})
    @flow_step(MANAGE, order=8, note="Generate a batch of unique codes")
    def post(self, request, promotion_id):
        batch, codes = staff_service.generate_codes(promotion_id, self.validated_request_data(request))
        return self.serialized_response(get_code_presenter()().present_batch(batch, codes), status=201)


@extend_schema(tags=TAGS)
class StaffCodeDetailView(_StaffView):
    request_serializer_class = PromoCodeUpdateRequestSerializer
    response_serializer_class = PromoCodeResponseSerializer

    @extend_schema(operation_id="promotions_staff_code_update", request=PromoCodeUpdateRequestSerializer,
                   responses={200: PromoCodeResponseSerializer})
    @flow_step(MANAGE, order=9, note="Deactivate a code or change its cap")
    def patch(self, request, code_id):
        code = staff_service.update_code(code_id, self.validated_request_data(request, partial=True))
        return self.serialized_response(get_code_presenter()().present(code))

    @extend_schema(operation_id="promotions_staff_code_destroy", responses={204: None})
    @flow_step(MANAGE, order=10, note="Delete an unused code")
    def delete(self, request, code_id):
        staff_service.delete_code(code_id)
        return self.serialized_response(None, status=204)


# ── staff: ledger ─────────────────────────────────────────────────────────


@extend_schema(tags=TAGS)
class StaffRedemptionListView(_StaffView):
    response_serializer_class = RedemptionResponseSerializer

    @extend_schema(operation_id="promotions_staff_redemptions_list", parameters=ANCHOR_PARAMETERS,
                   responses={200: RedemptionPageSerializer})
    @flow_step(MANAGE, order=11, note="Read the redemption ledger")
    def get(self, request, promotion_id):
        presenter = get_redemption_presenter()()
        qs = staff_service.list_redemptions(promotion_id)
        return self._page(request, qs, lambda rows: [presenter.present(r) for r in rows])
