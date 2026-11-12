# Changelog

All notable changes to stapel-promotions are documented here.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
Pre-1.0 semver: **minor = breaking**, patch = compatible.

## [Unreleased]

## [0.1.0]

First release: promo codes and automatic discounts over opaque purchasables.

### Added
- Explicit checkout hook order (`preset.py`): inventory 50 (stock first),
  promotions 100, delivery 200 — never a tie broken by name.
- README wiring snippets in the merge forms (the scaffold's
  `_stapel_merge_preset_defaults` list, or a per-key hand merge); pasting
  them never drops the host's `STAPEL_ORDERS` keys — executed against a
  populated settings module by `tests/test_readme_wiring.py`.
- **Promotions**: percent (with `max_discount`), fixed (one currency) and
  free shipping; scope cart / purchasable type / key set; `[starts_at,
  ends_at)` window; `min_subtotal`; stackable flag and priority; usage caps
  total and per user; automatic (code-less) promotions.
- **Promo codes**: many per promotion, stored upper-cased (case-insensitive
  by the unique column), per-code cap (single-use), optional assigned user;
  bulk generation (`codes/generate/`, `manage.py promotions_generate_codes`)
  with collision retry and a code-space guard.
- **Pricing engine** (`pricing.py`, pure): eligibility, stacking contest
  (all stackables vs each exclusive, best money value), sequential
  application, half-up percent and largest-remainder allocation; one
  adjustment per promotion; the free-shipping marker for the shipping
  adjuster. Property-tested: a discount never exceeds its base, totals never
  go negative.
- **Orders checkout seam** by Function name: adjuster `promotions.quote`;
  checkout hook `promotions.reserve/commit/release`, idempotent on the
  attempt key, reserve-after-release reserves anew, caps re-counted under
  `select_for_update()` on the promotion and code rows (real-thread proof on
  Postgres, ordering and interleaving proofs on SQLite);
  `preset.SETTINGS_DEFAULTS` wiring fragment.
- **Ledger** `RedemptionAttempt` + `Redemption` with hold TTL, housekeeping
  beat, facts `promotions.redemption.committed` / `.released` and
  `promotions.promotion.exhausted`; `RETURN_USAGE_ON_RELEASE` axis. Only a
  `cancelled` release undoes a committed attempt; `checkout_failed` /
  `orphaned` on it answer ok and keep the use, and a commit after a cancel
  is a no-op.
- **Ownership by `actor_id`**: the hook payloads may carry `actor_id` (the
  acting account, a guest's own id too) next to `user_id` (null for a
  guest); attempts and ledger rows record it (`user_id` when absent);
  per-user caps still read `user_id` and refuse a guest; `user.merged` and
  GDPR export/erase cover rows keyed by `actor_id`.
- **API** under `/promotions/api/v1/`: `active/`, throttled
  `codes/validate/`, staff CRUD for promotions and codes, ledger pages.
- `promotions.codes.issue` Function (referral rewards), `user.merged`
  consumer, GDPR owner (pseudonymize, keep economics), admin, system checks
  E001/E002/W001/W002 (silent without orders).
