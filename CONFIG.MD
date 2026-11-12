# CONFIG.MD — stapel-promotions

Config registry for **stapel-promotions**. One row per configuration key
the module reads, its **source** (`env` = the process environment / the
`STAPEL_PROMOTIONS` settings dict), what it is for, whether it is required,
and its default.

Every key is read through `promotions_settings` (`stapel_promotions.conf`,
namespace `STAPEL_PROMOTIONS`). Resolution order per key:
`settings.STAPEL_PROMOTIONS` dict → a flat Django setting of the same name →
environment variable → the default below. `RETURN_USAGE_ON_RELEASE` is the
CTO-facing config axis.

The orders wiring is not a key here: it lives in `STAPEL_ORDERS`
(`ADJUSTERS["promotions"]`, `CHECKOUT_HOOKS["promotions"]`) and ships as
`stapel_promotions.preset.SETTINGS_DEFAULTS`.

## stapel-promotions

| Key | Source | Purpose | Required | Default |
|-----|--------|---------|----------|---------|
| MAX_CODES_PER_CART | env | most promo codes one cart may carry in `context.codes`; over is `error.400.promo_codes_too_many`. | no | 5 |
| RESERVATION_TTL_MINUTES | env | a reserved use stops counting toward caps after this long; housekeeping marks it expired. Keep it above orders' `CHECKOUT_HOOK_ORPHAN_SECONDS` (check W002). | no | 30 |
| RETURN_USAGE_ON_RELEASE | env | axis: a `cancelled` release after commit (a cancelled order) gives the use back; other release reasons never touch a committed use. | no | true |
| VALIDATE_RATE | env | DRF rate of `POST codes/validate/` per user or address (code enumeration guard); empty disables. | no | 30/min |
| CODE_LENGTH | env | length of a generated code without its prefix; with the alphabet it must give ≥ 1e12 codes (check E002). | no | 10 |
| CODE_ALPHABET | env | characters of generated codes, upper-case A-Z0-9 (default leaves out 0/O and 1/I). | no | ABCDEFGHJKLMNPQRSTUVWXYZ23456789 |
| GENERATE_MAX_PER_CALL | env | most codes one `codes/generate/` call creates (the management command is not bound). | no | 1000 |
| ACTIVE_LIST_LIMIT | env | most promotions `GET active/` answers (`truncated` beyond). | no | 50 |
| ATTEMPT_TTL_DAYS | env | released and expired checkout attempts older than this are purged by the beat. | no | 90 |
