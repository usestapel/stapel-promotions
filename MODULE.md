# stapel-promotions — MODULE.md

> Agent-facing map of this module: what it provides, where to extend it
> without forking, and what not to do. Kept in the same PR as any change
> to a seam. See also README.md and CHANGELOG.md.

## What this module provides

- `Promotion` (percent / fixed / free shipping; scope cart, purchasable type
  or key set; window; min subtotal; stackable; priority; caps total and per
  user; automatic), `PromoCode` (many per promotion, upper-cased, per-code
  cap, optional assigned user), `RedemptionAttempt` + `Redemption` ledger.
- The pricing engine `pricing.py` — pure, every rule of `docs/SPEC.md` §4
  in one place; `services/quote.py` loads, `services/redemption.py` re-runs
  it under the row locks.
- Public API (`active/`, `codes/validate/`) and staff CRUD under
  `/promotions/api/v1/`.
- The orders checkout seam by Function name: adjuster `promotions.quote`,
  checkout hook `promotions.reserve/commit/release`; the wiring fragment is
  `stapel_promotions.preset.SETTINGS_DEFAULTS`.

## Extension points (fork-free)

### Settings — `STAPEL_PROMOTIONS` namespace (`conf.py`)

Resolution order per key: `settings.STAPEL_PROMOTIONS[key]` -> flat Django
setting of the same name -> environment variable -> default. `CONFIG.MD` is
the registry. No key is a registry or a dotted path (nothing to merge or
replace).

| Key | Default | What it customizes |
|---|---|---|
| `MAX_CODES_PER_CART` | `5` | codes per cart in `context.codes` |
| `RESERVATION_TTL_MINUTES` | `30` | how long a reserved use counts (> orders' 900 s orphan sweep, W002) |
| `RETURN_USAGE_ON_RELEASE` | `True` | axis: a cancelled order gives the use back |
| `VALIDATE_RATE` | `"30/min"` | throttle of `codes/validate/` |
| `CODE_LENGTH` / `CODE_ALPHABET` | `10` / no 0,O,1,I | generated codes (≥ 1e12 space, E002) |
| `GENERATE_MAX_PER_CALL` | `1000` | codes per API generate call |
| `ACTIVE_LIST_LIMIT` | `50` | bound of `GET active/` |
| `ATTEMPT_TTL_DAYS` | `90` | purge of released/expired attempts |

### Wiring into stapel-orders (`preset.py`)

```python
SETTINGS_DEFAULTS = {"STAPEL_ORDERS": {
    "ADJUSTERS": {"promotions": {"quote": "promotions.quote", "order": 100}},
    "CHECKOUT_HOOKS": {"promotions": {"reserve": "promotions.reserve", "commit": "promotions.commit",
                                      "release": "promotions.release", "order": 100}},
}}
```

The scaffold merges `SETTINGS_DEFAULTS` for composites only today; a
hand-wired host pastes the fragment (README quick start). The adjuster
without the hook is `stapel_promotions.E001` (discounts no cap counts);
orders installed without the adjuster is `W001`. Without orders installed
every check is silent and the brick's API and Functions work alone.

**The interplay with other adjusters** goes through `adjustments_so_far`
only: discounts already given by earlier adjusters reduce the base a
promotion applies to; this package's own adjustments carry
`meta.issuer = "promotions"` and are ignored on the way back in. `meta`
never holds personal data (orders snapshots it past GDPR erasure).

**Free shipping ↔ stapel-delivery.** A free-shipping promotion answers one
marker adjustment: `{"kind": "discount", "amount": "0.00", "line_index":
null, "meta": {"free_shipping": true, "free_shipping_methods": ["pickup"]?,
…}}` — `free_shipping_methods` only when the promotion is narrowed to some
delivery method codes (`Promotion.free_shipping_methods`, empty = every
method). Promotions runs at order **100**, delivery at **200**, so delivery
finds the marker in `adjustments_so_far` and quotes its own shipping line
at `0.00`. Keep that order: a host that moves promotions after delivery
loses free shipping silently. Pinned by `tests/test_delivery_interplay.py`
(a fake delivery adjuster applying exactly that rule).

**Reserve ledgers the snapshot.** The reserve payload's
`adjustments_so_far` is orders' complete final list; reserve records
exactly the adjustments with `meta.issuer == "promotions"` (amount per
promotion), re-checks each under the row lock, and refuses with
`error.409.promo_code_exhausted` / `error.409.promotion_exhausted` when one
no longer holds. Guests (`user_id: null`) are refused a code with a
per-user cap (`error.403.promo_code_requires_account`).

### Serializer seams (`views.py`)

Views inherit `StapelAPIView`; subclass a view, set
`request_serializer_class` / `response_serializer_class`, remount the URL.

| View | Request serializer | Response serializer |
|---|---|---|
| `ActivePromotionsView` | — | `ActivePromotionListSerializer` |
| `CodeValidateView` | `CodeValidateRequestSerializer` | `CodeValidateResponseSerializer` |
| `StaffPromotionListView` / `StaffPromotionDetailView` | `PromotionWriteRequestSerializer` | `PromotionResponseSerializer` |
| `StaffCodeListView` | `PromoCodeCreateRequestSerializer` | `PromoCodeResponseSerializer` |
| `StaffCodeGenerateView` | `CodeGenerateRequestSerializer` | `CodeGenerateResponseSerializer` |
| `StaffCodeDetailView` | `PromoCodeUpdateRequestSerializer` | `PromoCodeResponseSerializer` |
| `StaffRedemptionListView` | — | `RedemptionResponseSerializer` |

Presenter swap keys (`STAPEL_SWAP`): `STAPEL_PROMOTIONS_PROMOTION_PRESENTER`,
`STAPEL_PROMOTIONS_PUBLIC_PRESENTER`, `STAPEL_PROMOTIONS_CODE_PRESENTER`,
`STAPEL_PROMOTIONS_REDEMPTION_PRESENTER`.

### Events & functions (comm surface)

| Kind | Name | Payload | Schema |
|---|---|---|---|
| Function (provides) | `promotions.quote` | orders adjuster request -> `{adjustments, errors}` | `schemas/functions/promotions.quote.json` |
| Function (provides) | `promotions.reserve` | request + `attempt_key` -> `{ok, token, errors}` | `schemas/functions/promotions.reserve.json` |
| Function (provides) | `promotions.commit` | `{attempt_key, token?, order_id, user_id?}` -> `{ok, status}` | `schemas/functions/promotions.commit.json` |
| Function (provides) | `promotions.release` | `{attempt_key, token?, order_id?, reason}` -> `{ok, status}` | `schemas/functions/promotions.release.json` |
| Function (provides) | `promotions.codes.issue` | `{promotion_key, count?, assigned_user_id?, prefix?, usage_limit?, idempotency_key?}` -> `{promotion_key, codes}`; with `idempotency_key` (unique, `CodeIssue` row inserted before minting) a retry answers the first call's codes and mints nothing, the key reused for another request fails the call | `schemas/functions/promotions.codes.issue.json` |
| Function (provides) | `promotions.codes.revoke` | `{code \| code_id, reason}` -> `{code, code_id, revoked, already_revoked, redeemed, revoked_at}`; the code is refused from then on with `error.409.promo_code_revoked` (validate, quote, reserve — reserve re-reads it under the code row lock); committed redemptions untouched; idempotent (first reason kept); unknown code fails the call | `schemas/functions/promotions.codes.revoke.json` |
| Action (emits) | `promotions.redemption.committed` | redemption, promotion, code, user, order, amount | `schemas/emits/…` |
| Action (emits) | `promotions.redemption.released` | the same + `reason` | `schemas/emits/…` |
| Action (emits) | `promotions.promotion.exhausted` | promotion, cap, used | `schemas/emits/…` |
| Action (consumes) | `user.merged` | guest -> account | `schemas/consumes/user.merged.json` |

GDPR: `register_gdpr_owner("promotions", ["account"], erase_subject)` —
ledger keeps amounts under the pseudonym, assigned codes deactivated.

Housekeeping: `beat.get_promotions_beat_schedule()` or
`manage.py promotions_housekeeping`. Bulk codes:
`manage.py promotions_generate_codes <promotion_key> <count> [--prefix] [--length] [--usage-limit] [--out]`.

## Contract emission — the `info.version` convention

When this module grows a contract harness (`_codegen_settings.py` +
`make contract`, geo is the etalon), leave `SPECTACULAR_SETTINGS` **unset**
there: the emitted `docs/schema.json` must stay byte-identical to the
monolith aggregate's slice of this module, and the aggregate runs on the
drf-spectacular defaults — `info.version: "0.0.0"` with an empty
`info.title`. That is the convention, not a bug to fix: `info.version` is
not the contract's version. The version a consumer pins lives in
`pyproject.toml` and in the pair's `manifest.json` (`backend.contract`).
Passing `package=`/`version=` to `get_spectacular_settings` breaks the
byte-identity and is what `stapel-api-lint`'s SCHEMA001 flags.

## Anti-patterns

- **Don't fork to change behavior** — every knob above is a seam; if a
  change is impossible without editing this package, that is an upstream
  bug: open an issue/contribution instead.
- **Don't import other stapel modules** — cross-module communication is
  comm (Actions/Functions) by string name only (`tests/test_architecture.py`).
- **Don't compute a discount outside `pricing.py`** — the quote and the
  reserve must price identically; a second copy of a rule is a drift.
- **Don't count usage before taking the promotion row lock** — the
  concurrency tests (and their inversions) guard exactly that.
- **Don't bypass the settings namespace** with `os.getenv` at import time.

## App-layer override vs upstream contribution — rule of thumb

**App-layer** (host project, no fork) if the change fits a seam above: a
settings key, a subclass + URL remount, a comm subscriber.

**Upstream contribution** if it needs new model fields/migrations, new
endpoints, a new settings key or seam, or changes a committed schema.

Litmus test: if you'd have to monkeypatch or edit code inside
`stapel_promotions/` — it's upstream. If a setting, subclass, receiver or comm
call gets you there — it's app-layer.
