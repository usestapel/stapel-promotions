"""A fake orders caller: sends exactly the §2 payloads through comm ``call``.

It plays stapel-orders' side of the checkout contract (and its contract
deltas): quote with ``adjustments_so_far`` from earlier adjusters, then
reserve with the adjuster request + ``attempt_key`` whose
``adjustments_so_far`` is the complete final list (each item normalised and
tagged with ``"adjuster"``), commit ``{attempt_key, token, order_id,
user_id}`` and release ``{attempt_key, token, order_id, reason}``. It also
applies orders' acceptance rules to every answer (≤ 20 adjustments, amounts
≤ 2 dp, codes ≤ 64), so a malformed answer fails the test like it would
fail closed in orders.
"""
from __future__ import annotations

import re
import uuid
from decimal import Decimal

from stapel_core.comm import call

from stapel_promotions.preset import SETTINGS_DEFAULTS

ADJUSTER = SETTINGS_DEFAULTS["STAPEL_ORDERS"]["ADJUSTERS"]["promotions"]
HOOK = SETTINGS_DEFAULTS["STAPEL_ORDERS"]["CHECKOUT_HOOKS"]["promotions"]
AMOUNT_RE = re.compile(r"^-?[0-9]+(\.[0-9]{1,2})?$")
MAX_ADJUSTMENTS = 20


def line(index, total, *, ptype="listing", key=None, quantity=1):
    total = Decimal(str(total))
    return {
        "index": index, "purchasable_type": ptype, "purchasable_key": key or f"k{index}",
        "quantity": quantity, "unit_price": str((total / quantity).quantize(Decimal("0.01"))),
        "line_total": str(total), "attrs": {},
    }


def request(lines, *, currency="USD", user=None, codes=None, prior=None):
    subtotal = sum((Decimal(item["line_total"]) for item in lines), Decimal("0.00"))
    context = {"codes": list(codes)} if codes is not None else {}
    return {
        "currency": currency,
        "subtotal": str(subtotal),
        "user_id": None if user is None else str(getattr(user, "pk", user)),
        "lines": lines,
        "adjustments_so_far": list(prior or []),
        "context": context,
    }


def _accept(answer: dict, lines: list) -> None:
    """orders' fail-closed rules (contract delta 5)."""
    assert isinstance(answer, dict) and set(answer) >= {"adjustments", "errors"}
    assert len(answer["adjustments"]) <= MAX_ADJUSTMENTS
    indexes = {item["index"] for item in lines}
    for adj in answer["adjustments"]:
        assert adj["kind"] in ("discount", "shipping", "tax", "fee")
        assert isinstance(adj["amount"], str) and AMOUNT_RE.match(adj["amount"]), adj
        assert abs(Decimal(adj["amount"])) < Decimal(10) ** 14
        assert adj["line_index"] is None or adj["line_index"] in indexes
        assert len(adj["code"]) <= 64
        assert isinstance(adj["meta"], dict)
    for err in answer["errors"]:
        assert re.match(r"^error\.[0-9]{3}\.[a-z0-9_]+$", err["code"]), err


class FakeOrders:
    def __init__(self, name="promotions"):
        self.name = name

    def quote(self, payload: dict) -> dict:
        answer = call(ADJUSTER["quote"], payload)
        _accept(answer, payload["lines"])
        return answer

    def total(self, payload: dict, answer: dict) -> Decimal:
        subtotal = Decimal(payload["subtotal"])
        return max(Decimal("0.00"), subtotal + sum((Decimal(a["amount"]) for a in answer["adjustments"]),
                                                    Decimal("0.00")))

    def final_list(self, payload: dict, answer: dict) -> list:
        prior = list(payload.get("adjustments_so_far") or [])
        return prior + [{**adj, "adjuster": self.name} for adj in answer["adjustments"]]

    def reserve(self, payload: dict, answer: dict, attempt_key: str | None = None) -> tuple[str, dict]:
        key = attempt_key or str(uuid.uuid4())
        body = {**payload, "adjustments_so_far": self.final_list(payload, answer), "attempt_key": key}
        return key, call(HOOK["reserve"], body)

    def commit(self, key, token=None, order_id="101", user_id=None) -> dict:
        return call(HOOK["commit"], {"attempt_key": key, "token": token, "order_id": order_id, "user_id": user_id})

    def release(self, key, token=None, order_id=None, reason="checkout_failed") -> dict:
        return call(HOOK["release"], {"attempt_key": key, "token": token, "order_id": order_id, "reason": reason})

    def checkout(self, payload: dict, *, order_id="101", attempt_key=None) -> dict:
        """quote → refuse on errors → reserve → refuse unless ok → commit."""
        answer = self.quote(payload)
        if answer["errors"]:
            return {"placed": False, "errors": answer["errors"], "answer": answer}
        key, reserved = self.reserve(payload, answer, attempt_key)
        if reserved.get("ok") is not True or reserved.get("errors"):
            self.release(key, reserved.get("token"))
            return {"placed": False, "errors": reserved.get("errors") or [], "answer": answer, "key": key}
        self.commit(key, reserved.get("token"), order_id, payload.get("user_id"))
        return {"placed": True, "answer": answer, "key": key, "token": reserved["token"],
                "total": self.total(payload, answer)}
