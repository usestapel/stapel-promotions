"""Action subscriptions of stapel-promotions.

Handlers are idempotent: delivery is at-least-once.

- ``user.merged`` (stapel-auth) — a guest was folded into an account on
  sign-in: its ledger rows, attempts and assigned codes follow it, so the
  per-user caps count the guest's uses.
- ``user.deleted`` / ``gdpr.erasure.requested`` are answered by
  ``register_gdpr_owner`` (``apps.py``), not here.
"""
import json
import logging
from pathlib import Path

from django.db import transaction
from stapel_core.comm import on_action

logger = logging.getLogger(__name__)

_CONSUMES = Path(__file__).resolve().parent / "schemas" / "consumes"


def _schema(name: str) -> dict:
    return json.loads((_CONSUMES / f"{name}.json").read_text(encoding="utf-8"))


@on_action("user.merged", schema=_schema("user.merged"))
def handle_user_merged(event):
    """Re-point the guest's promotion history at the surviving account."""
    from .services.redemption import merge_users

    payload = event.payload or {}
    from_user_id = str(payload.get("from_user_id") or "")
    into_user_id = str(payload.get("into_user_id") or "")
    if not from_user_id or not into_user_id:
        logger.error("user.merged without from/into user id: %s", event.event_id)
        return
    if from_user_id == into_user_id:
        return
    with transaction.atomic():
        moved = merge_users(from_user_id, into_user_id)
    if any(moved.values()):
        logger.info("user.merged %s -> %s: %s", from_user_id, into_user_id, moved)
