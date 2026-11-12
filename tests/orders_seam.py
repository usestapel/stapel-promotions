"""Payloads exactly as stapel-orders sends them, checked against BOTH schemas.

``tests/fixtures/orders-<sha>/`` holds orders' own Function schemas at the
named commit (adjusters, checkout_hooks.reserve/commit/release). Every
payload built here is first validated against orders' schema — so it is a
payload orders may really send, ``actor_id`` and ``user_id`` (null for a
guest) included, with every optional key filled — then against this brick's
registered input schema, then sent through ``comm.call`` with schema
validation on; the answer is validated against orders' ``$defs.result``.
Refresh the fixtures (new sha in the directory name) when orders' seam moves.
"""
import json
import uuid
from pathlib import Path

import jsonschema
from stapel_core.comm import call

TESTS = Path(__file__).resolve().parent
ORDERS = sorted((TESTS / "fixtures").glob("orders-*"))[-1]
OURS = TESTS.parent / "schemas" / "functions"
GUEST = "4242"


def orders_schema(name):
    return json.loads((ORDERS / f"{name}.json").read_text(encoding="utf-8"))


def check_request(orders_name, our_function, payload):
    jsonschema.Draft202012Validator(orders_schema(orders_name),
                                    format_checker=jsonschema.FormatChecker()).validate(payload)
    jsonschema.Draft202012Validator(
        json.loads((OURS / f"{our_function}.json").read_text(encoding="utf-8"))).validate(payload)


def check_result(orders_name, answer):
    schema = orders_schema(orders_name)
    jsonschema.Draft202012Validator({**schema["$defs"]["result"], "$defs": schema["$defs"]}).validate(answer)


def line(index=0, *, key="sku-1", quantity=1, unit_price="20.00", attrs=None):
    return {"index": index, "purchasable_type": "listing", "purchasable_key": key, "quantity": quantity,
            "unit_price": unit_price, "line_total": f"{float(unit_price) * quantity:.2f}",
            "attrs": attrs if attrs is not None else {"weight_grams": 500, "shippable": True}}


def quote_request(lines, context, *, user_id=None, actor_id=GUEST, prior=()):
    subtotal = sum(float(item["line_total"]) for item in lines)
    return {"currency": "USD", "subtotal": f"{subtotal:.2f}", "user_id": user_id, "actor_id": actor_id,
            "lines": lines, "adjustments_so_far": list(prior), "context": context}


def applied(adjustments, adjuster):
    return [{**adj, "adjuster": adjuster} for adj in adjustments]


def run_hook(names, request, *, adjuster_answer, adjuster):
    """reserve -> commit -> release(cancelled), each checked against both sides."""
    key = str(uuid.uuid4())
    reserve = {**request, "adjustments_so_far": [*request["adjustments_so_far"],
                                                 *applied(adjuster_answer, adjuster)],
               "attempt_key": key}
    check_request("checkout_hooks.reserve", names["reserve"], reserve)
    reserved = call(names["reserve"], reserve)
    check_result("checkout_hooks.reserve", reserved)
    assert reserved["ok"] is True, reserved
    commit = {"attempt_key": key, "token": reserved["token"], "order_id": "9001",
              "user_id": request["user_id"], "actor_id": request["actor_id"]}
    check_request("checkout_hooks.commit", names["commit"], commit)
    committed = call(names["commit"], commit)
    check_result("checkout_hooks.commit", committed)
    assert committed["ok"] is True
    release = {"attempt_key": key, "token": None, "order_id": "9001", "reason": "cancelled",
               "user_id": request["user_id"], "actor_id": request["actor_id"]}
    check_request("checkout_hooks.release", names["release"], release)
    released = call(names["release"], release)
    check_result("checkout_hooks.release", released)
    assert released["ok"] is True
    return key


__all__ = ["GUEST", "check_request", "check_result", "line", "quote_request", "run_hook"]
