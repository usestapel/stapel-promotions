# SPEC — stapel-promotions 0.1.0 (promo codes and automatic discounts)

Status: **decided, built locally** (brigade PR, 2026-11-12). Queue: brick wave
2026-11-12, row `stapel-promotions`. Consumers: the stapel-orders checkout
(adjuster `kind=discount` + checkout hook, contract of the wave spec §2), the
react pair `@stapel/promotions-react` (generated from `docs/schema.json`),
stapel-referrals (`promotions.codes.issue`). Gates: `tests/` (named below),
`stapel-verify`, `make contract-check`. House style: `SPEC-STAPEL-ORDERS.md`.

## 1. Scope boundary

| Decision | Reason |
|---|---|
| **In:** `Promotion` (percent / fixed / free shipping), `PromoCode` (many per promotion, case-insensitive), `Redemption` ledger + `RedemptionAttempt` (one per checkout attempt), automatic (code-less) promotions, staff CRUD, bulk code generation, public "validate code" and "active promotions" reads. | The row of the wave spec. |
| **In:** `promotions.quote` (the orders adjuster) and `promotions.reserve / commit / release` (the orders checkout hook). | Bricks never import each other; orders calls these by name. |
| **Out:** tiered/BOGO/"buy X get Y", gift cards, loyalty points, price lists, per-segment targeting, A/B. | Each is a product; v1 covers "promo codes, a sale, free shipping over N". |
| **Out:** computing the shipping price. Free shipping is a **marker** the shipping adjuster honours (§4). | Delivery owns shipping; discounts run before shipping (order 100 < 200). |
| **Out:** currency conversion. A fixed amount or a minimum subtotal is in one currency; another cart currency is "not applicable". | A converted threshold is a quote that drifts (orders' rule). |
| **Out:** currencies with exponent ≠ 2 (orders' named gap). | |

## 2. Models

All user references are `CharField(64)` id strings (pseudonyms must fit); no
foreign key leaves the app.

- **`Promotion`** — `key` (slug ≤64, unique, generated from the name when
  blank), `name` ≤120, `description` ≤500, `kind` (`percent` | `fixed` |
  `free_shipping`), `value` Decimal(12,2) (percent: 0 < v ≤ 100; fixed: > 0;
  free shipping: 0), `currency` (ISO, blank = any; required for `fixed` and for
  a `min_subtotal`), `max_discount` (percent cap, nullable), `min_subtotal`
  (nullable), `scope` (`cart` | `purchasable_type` | `keys`), `scope_type`
  ≤32, `scope_keys` (JSON list ≤ 500 keys ≤255), `starts_at` / `ends_at`
  (nullable window, half-open `[starts_at, ends_at)`), `is_active`,
  `automatic` (applies without a code), `stackable`, `priority` (int),
  `usage_limit_total` / `usage_limit_per_user` (nullable), `created_by`,
  `free_shipping_methods` (free shipping only: delivery method codes it
  waives, empty = all), timestamps.
- **`PromoCode`** — `promotion` FK, `code` ≤64 stored **upper-cased and
  stripped** (unique: case-insensitivity is a property of the column, not of
  every query), `is_active`, `usage_limit` (per code, nullable; 1 = single-use),
  `assigned_user_id` (blank = anyone; set = only that user, e.g. a referral
  reward), `batch` (generation batch id), timestamps.
- **`RedemptionAttempt`** — `attempt_key` ≤128 unique (the orders checkout
  idempotency key), `token`, `user_id` (blank for a guest), `actor_id`
  (the acting account, a guest's own id too), `status` (`reserved` | `committed` |
  `released` | `expired`), `currency`, `result` (the adjustments reserved),
  `order_id`, `expires_at`, timestamps.
- **`Redemption`** (the ledger) — `attempt` FK, `promotion` FK (PROTECT),
  `code` FK nullable, `user_id` (blank for a guest; what per-user caps
  count), `actor_id` (who owns the row), `status` (same four), `amount` Decimal(18,2)
  (positive: the discount granted), `currency`, `free_shipping`, timestamps.
  Unique `(attempt, promotion)`.

**Usage** = ledger rows of a promotion (or code, or promotion + user) whose
status is `committed`, or `reserved` with `attempt.expires_at > now`. No
denormalised counter: the count is taken under the row lock (§5).

## 3. Ledger FSM

```
reserve ─► reserved ─► committed ─► released   (release reason cancelled only, RETURN_USAGE_ON_RELEASE)
              │  └──► released                  (any release reason)
              └──► expired                      (TTL passed, housekeeping)
expired/released ─► committed                   (a late commit wins: the order already holds the discount —
                                                 unless a cancelled release reached the attempt first)
```

All transitions are on the attempt and its rows together. `reserve` on a
`reserved`/`committed` attempt replays the stored answer; on a
`released`/`expired` one it re-evaluates and re-reserves (the same checkout
key retried after a failure).

## 4. Pricing rules (stated once — `pricing.py`)

Input = the orders adjuster request. Money is `Decimal` parsed from strings
(floats refused), quantum `0.01`.

1. **Candidates**: every `automatic` promotion that is active and in its
   window, plus the promotion of every code in `context.codes` (≤
   `MAX_CODES_PER_CART`, normalised, duplicates collapsed).
2. **Eligibility** (in this order; the first failure is the reason):
   active and in window → currency (`fixed`/`min_subtotal` need the cart
   currency) → assignment (a code assigned to someone else reads as unknown)
   → per-user cap needs a `user_id` → caps (total, per user, per code) not
   reached → `min_subtotal ≤ subtotal` (the cart `subtotal` before any
   adjustment) → at least one line in scope with a base > 0.
   A **code** that fails is an `errors[]` entry (refuses checkout: the shopper
   must remove it); an **automatic** promotion that fails is silently skipped.
3. **Scope base** per line = `line_total` minus discount adjustments already
   on that line (`adjustments_so_far` with that `line_index`, negative
   amounts); cart-level discounts already present are spread over lines by
   rule 5 first. Never below 0.
4. **Stacking**: candidate sets are *all eligible stackable promotions
   together* and *each eligible non-stackable promotion alone*. The set with
   the largest money discount wins; ties → the set that carries free shipping
   → the higher summed `priority` → the lower promotion ids. Free shipping is
   worth 0 in this contest (its value is not known before the shipping
   adjuster runs); a merchant who wants it always applied marks it stackable.
   A valid code that loses the contest is **not** an error.
5. **Amount and allocation**: inside a set, promotions apply in
   `(-priority, id)` order, each on the base left by the previous ones.
   percent: `round_half_up(base × value / 100)`, capped by `max_discount`;
   fixed: `value`; both capped at the base. The amount is allocated across the
   in-scope lines proportionally to their remaining base, rounding each share
   **down** to the cent and handing the leftover cents one by one to the lines
   with the largest remainders (ties → lower index), never above a line's
   remaining base. Hence: **a discount never exceeds what it applies to, and
   the cart total never goes negative** (property-tested).
6. **Output adjustments** (`kind="discount"`, amount negative):
   scope `cart` → one cart-level adjustment (`line_index: null`,
   `meta.allocation` = per-line shares); scope `purchasable_type`/`keys` → one
   adjustment per touched line. `code` = the promo code, or `auto:<key>` for an
   automatic promotion; `label` = promotion name; `meta` = `{promotion_id,
   promotion_key, source: code|automatic, kind, free_shipping}`.
7. **Free shipping** = one marker adjustment `{"kind": "discount", "amount":
   "0.00", "line_index": null, "meta": {"free_shipping": true,
   "free_shipping_methods"?: [codes], ...}}` (the delivery brick's
   contract). The delivery adjuster (order 200, after discounts at 100) sees
   it in `adjustments_so_far` and quotes its shipping line at `0.00` — for
   every method, or only the listed ones. Promotions never emit
   `kind="shipping"`.
8. **Bounds** (orders contract deltas): one adjustment per applied
   promotion (cart scope or several lines → `line_index: null` +
   `meta.allocation`; exactly one line → that `line_index`), at most 5
   promotions per cart (orders accepts 20 adjustments in total); adjustment
   `code` ≤ 64 (`auto:<key>`, key ≤ 58); `meta` holds no personal data.

## 5. Redemption: reserve / commit / release

- `reserve` (payload = adjuster request + `attempt_key`; its
  `adjustments_so_far` is orders' complete final list): the adjustments with
  `meta.issuer == "promotions"` are what the order snapshot grants. In one
  transaction `select_for_update()` those `Promotion` rows (ordered by id —
  no deadlock between two checkouts) and their `PromoCode` rows, **re-count
  usage under the lock**, re-check each grant (rule 2, scope excepted),
  write the attempt + one ledger row per granted promotion with the
  snapshot's amount. A grant that no longer holds refuses the checkout.
  Two shoppers racing for the last use end as one `ok` and one
  `error.409.promo_code_exhausted` (Postgres row locks; SQLite proves the
  ordering and the interleaving, `tests/test_concurrency.py`).
  Returns `{ok, token, errors}`.
- `commit` (`{attempt_key, token|null, order_id, user_id|null}`, after the
  order commits, retried by orders until ok): rows → `committed`; emits
  `promotions.redemption.committed` per row and `promotions.promotion.exhausted`
  when the total cap is now reached. Idempotent.
- `release` (`{attempt_key, token|null, order_id|null, reason:
  checkout_failed|cancelled|orphaned}`): `reserved` → `released` for any
  reason. **Only `cancelled` undoes a commit:** `committed` → `released` when
  `RETURN_USAGE_ON_RELEASE` (default true: a cancelled order gives the use
  back) and emits `promotions.redemption.released`; `checkout_failed` /
  `orphaned` on a committed attempt is a no-op answering `{ok: true,
  status: "committed"}`. A `cancelled` release on a released/expired attempt
  is recorded (`release_reason`) so a late commit stays a no-op. Idempotent.
  `tests/test_release_semantics.py` is the full (state, event) table.
- Unknown `attempt_key` on commit/release = `{ok: true, status: "none"}` (a
  checkout with no promotion reserved nothing).
- A reservation older than `RESERVATION_TTL_MINUTES` stops counting toward
  caps; housekeeping marks it `expired`. orders' own `CheckoutHookState` beat
  releases crashed checkouts as well; the TTL is the backstop when orders is
  gone.

## 6. API surface (`/promotions/api/v1/`)

| Method/path | Who | Request → Response |
|---|---|---|
| `GET active/` | anyone | → `ActivePromotionList {items: [PublicPromotion], truncated}` (automatic promotions live now, for banners) |
| `POST codes/validate/` | anyone (throttled `VALIDATE_RATE`) | `CodeValidateRequest {code, currency?, subtotal?}` → `CodeValidateResponse {code, valid, error, params, promotion: PublicPromotion\|null}` |
| `GET/POST staff/promotions/` | staff | `?kind=&active=&q=` anchor page → `PromotionPage`; `PromotionWriteRequest` → `201 PromotionResponse` |
| `GET/PATCH/DELETE staff/promotions/{id}/` | staff | `PromotionResponse`; delete refused `409 promotion_has_redemptions` once used (deactivate instead) |
| `GET/POST staff/promotions/{id}/codes/` | staff | `PromoCodePage`; `PromoCodeCreateRequest` → `201 PromoCodeResponse` |
| `GET staff/promotions/{id}/codes/export/` | staff | `?batch=` → streamed `text/csv` (`code, batch, is_active, usage_limit, assigned_user_id, created_at, revoked_at, revoke_reason`; a cell starting with `= + - @` TAB CR is prefixed with `'`) |
| `POST staff/promotions/{id}/codes/generate/` | staff | `CodeGenerateRequest {count ≤ GENERATE_MAX_PER_CALL, prefix?, length?, usage_limit?}` → `201 CodeGenerateResponse {batch, created, codes}` |
| `PATCH/DELETE staff/codes/{id}/` | staff | `PromoCodeUpdateRequest {is_active?, usage_limit?}` → `PromoCodeResponse`; delete refused once used |
| `GET staff/promotions/{id}/redemptions/` | staff | anchor page → `RedemptionPage` |

## 7. Functions, facts, settings, GDPR

Functions (schemas in `schemas/functions/`, input validated; outputs
documented in each schema's description): `promotions.quote`,
`promotions.reserve`, `promotions.commit`, `promotions.release`,
`promotions.codes.issue` (`{promotion_key, count?, assigned_user_id?,
prefix?}` → `{codes}` — the referrals reward strategy).

Facts (schemas in `schemas/emits/`, emitted inside `mutate_and_emit`):
`promotions.redemption.committed`, `promotions.redemption.released`,
`promotions.promotion.exhausted`. Consumes `user.merged` (ledger and assigned
codes follow the account).

Settings `STAPEL_PROMOTIONS` — see `CONFIG.MD` (the registry):
`MAX_CODES_PER_CART` 5, `RESERVATION_TTL_MINUTES` 30,
`RETURN_USAGE_ON_RELEASE` True, `VALIDATE_RATE` "30/min",
`CODE_LENGTH` 10, `CODE_ALPHABET` (no 0/O/1/I), `GENERATE_MAX_PER_CALL` 1000,
`ACTIVE_LIST_LIMIT` 50, `ATTEMPT_TTL_DAYS` 90.

Orders wiring: `stapel_promotions.preset.SETTINGS_DEFAULTS` carries the
`ADJUSTERS["promotions"]` and `CHECKOUT_HOOKS["promotions"]` entries. Checks:
`W001` orders installed, adjuster not registered; `E001` adjuster registered
without the checkout hook (caps would never be enforced); `E002` code space
too small. Without orders installed every check is silent.

**Ownership (`actor_id`).** Orders sends `actor_id` (the acting account,
a guest's own id included, always present) next to `user_id` (null for a
guest on the adjuster and hook payloads; the `orders.order.placed` fact keeps
`user_id` = the acting account). Per-user caps read `user_id` only, so they still
refuse a guest; reserve records `actor_id` on the attempt and its ledger
rows (payloads without it record `user_id`; a commit fills a blank one).
`user.merged` moves rows by `user_id` and by `actor_id` — a guest's rows
keep a blank `user_id`, so they never count toward the account's per-user
caps.

GDPR (`register_gdpr_owner("promotions", ["account"], erase)`): ledger and
attempts keep amounts, `user_id` and `actor_id` become the fleet pseudonym
(per-user caps keep counting); assigned codes are pseudonymized and
deactivated; `created_by` pseudonymized. Export = ledger rows (by `user_id`
or `actor_id`) and assigned codes.

## 8. Switches parked for the foreman

| Switch | Default | The fork |
|---|---|---|
| `RETURN_USAGE_ON_RELEASE` | `True` | Does a cancelled order give its code use back? Customer's favour by default; a merchant burning single-use codes on cancel flips it. |
| stacking contest | best money value | Alternative "a typed code always wins" was not built: it can charge the shopper more than an automatic sale. |
