"""Every response body the committed schema declares is the body the views send.

The operation list is DERIVED from ``docs/schema.json``: an operation with a
declared JSON 2xx body and no recipe here fails loudly. Every body is
validated against its declared schema (OAS ``nullable`` honoured), and each
read is driven in a populated and an empty state.
"""
import copy
import json
from pathlib import Path

import jsonschema
import pytest

from .factories import make_code, make_promotion
from .orders import line, request

REPO = Path(__file__).resolve().parent.parent
SCHEMA = json.loads((REPO / "docs" / "schema.json").read_text())
V1 = "/promotions/api/v1"

pytestmark = pytest.mark.django_db


def _json_schema(node):
    if isinstance(node, list):
        return [_json_schema(item) for item in node]
    if not isinstance(node, dict):
        return node
    rebuilt = {k: _json_schema(v) for k, v in node.items() if k != "nullable"}
    if node.get("nullable"):
        return {"anyOf": [rebuilt, {"type": "null"}]}
    return rebuilt


def _validator(body_schema):
    root = copy.deepcopy(body_schema)
    root["components"] = copy.deepcopy(SCHEMA["components"])
    return jsonschema.Draft202012Validator(_json_schema(root))


def _operations():
    ops = []
    for path, methods in SCHEMA["paths"].items():
        for method, op in methods.items():
            for code, response in op.get("responses", {}).items():
                body = response.get("content", {}).get("application/json", {}).get("schema")
                if body is not None and code.startswith("2"):
                    ops.append((method.upper(), path, int(code), body))
    return sorted(ops, key=lambda o: (o[1], o[0], o[2]))


OPERATIONS = _operations()


def _url(path, **params):
    for name, value in params.items():
        path = path.replace("{%s}" % name, str(value))
    assert "{" not in path, f"unfilled path parameter in {path}"
    return path


P = V1 + "/staff/promotions/{promotion_id}/"


def _used(env, promotion, code):
    assert env.orders.checkout(request([line(0, "30.00")], user=env.user, codes=[code]))["placed"]


def r_active(env):
    empty = env.anon.get(V1 + "/active/")
    make_promotion(kind="free_shipping", automatic=True, min_subtotal="50.00", currency="USD")
    make_promotion(kind="fixed", value="5.00", currency="USD", automatic=True)
    return [env.anon.get(V1 + "/active/")], [empty]


def r_validate(env):
    make_promotion(value="10", code="GOOD")
    return [env.anon.post(V1 + "/codes/validate/", {"code": "good"}, format="json"),
            env.anon.post(V1 + "/codes/validate/", {"code": "BAD"}, format="json")], []


def r_list(env):
    empty = env.staff.get(V1 + "/staff/promotions/")
    for _ in range(3):
        make_promotion(code=None)
    return [env.staff.get(V1 + "/staff/promotions/?limit=2")], [empty]


def r_create(env):
    return [env.staff.post(V1 + "/staff/promotions/", {
        "name": "Sale", "kind": "fixed", "value": "5.00", "currency": "USD", "min_subtotal": "20.00",
        "scope": "keys", "scope_type": "listing", "scope_keys": ["a", "b"],
        "starts_at": "2026-01-01T00:00:00Z", "ends_at": "2030-01-01T00:00:00Z"}, format="json")], []


def r_retrieve(env):
    p = make_promotion(code="R")
    _used(env, p, "R")
    plain = make_promotion(kind="free_shipping")
    return [env.staff.get(_url(P, promotion_id=p.pk)), env.staff.get(_url(P, promotion_id=plain.pk))], []


def r_update(env):
    p = make_promotion()
    return [env.staff.patch(_url(P, promotion_id=p.pk), {"priority": 3}, format="json")], []


def r_codes(env):
    p = make_promotion()
    empty = env.staff.get(_url(P + "codes/", promotion_id=p.pk))
    make_code(p, "ONE")
    make_code(p, "TWO", usage_limit=1, assigned_user_id="7")
    return [env.staff.get(_url(P + "codes/", promotion_id=p.pk))], [empty]


def r_code_create(env):
    p = make_promotion()
    return [env.staff.post(_url(P + "codes/", promotion_id=p.pk), {"code": "new-one"}, format="json")], []


def r_generate(env):
    p = make_promotion()
    return [env.staff.post(_url(P + "codes/generate/", promotion_id=p.pk), {"count": 3}, format="json")], []


def r_redemptions(env):
    p = make_promotion(code="LED")
    empty = env.staff.get(_url(P + "redemptions/", promotion_id=p.pk))
    _used(env, p, "LED")
    return [env.staff.get(_url(P + "redemptions/", promotion_id=p.pk))], [empty]


def r_code_update(env):
    code = make_code(make_promotion(), "UPD")
    return [env.staff.patch(_url(V1 + "/staff/codes/{code_id}/", code_id=code.pk), {"is_active": False},
                            format="json")], []


RECIPES = {
    ("GET", V1 + "/active/", 200): r_active,
    ("POST", V1 + "/codes/validate/", 200): r_validate,
    ("GET", V1 + "/staff/promotions/", 200): r_list,
    ("POST", V1 + "/staff/promotions/", 201): r_create,
    ("GET", P, 200): r_retrieve,
    ("PATCH", P, 200): r_update,
    ("GET", P + "codes/", 200): r_codes,
    ("POST", P + "codes/", 201): r_code_create,
    ("POST", P + "codes/generate/", 201): r_generate,
    ("GET", P + "redemptions/", 200): r_redemptions,
    ("PATCH", V1 + "/staff/codes/{code_id}/", 200): r_code_update,
}


class Env:
    def __init__(self, anon, staff, user, orders):
        self.anon, self.staff, self.user, self.orders = anon, staff, user, orders


def test_every_declared_operation_has_a_recipe():
    declared = {(m, p, c) for m, p, c, _ in OPERATIONS}
    assert declared == set(RECIPES), {
        "missing": sorted(declared - set(RECIPES)),
        "stale": sorted(set(RECIPES) - declared),
    }


@pytest.mark.parametrize("method,path,code,body_schema", OPERATIONS,
                         ids=[f"{m} {p} {c}" for m, p, c, _ in OPERATIONS])
def test_wire_matches_the_declared_body(method, path, code, body_schema, anon_client, staff_client, user, orders):
    populated, empty = RECIPES[(method, path, code)](Env(anon_client, staff_client, user, orders))
    validator = _validator(body_schema)
    for resp in populated + empty:
        assert resp.status_code == code, (method, path, resp.status_code, resp.content)
        errors = sorted(validator.iter_errors(resp.json()), key=str)
        assert not errors, [e.message for e in errors[:5]]
    for resp in populated:
        data = resp.json()
        for key in ("items", "codes"):
            if key in data:
                assert data[key], f"{method} {path}: populated '{key}' came back empty"
    for resp in empty:
        data = resp.json()
        if "items" in data:
            assert not data["items"], f"{method} {path}: the empty state is not empty"


def test_the_gate_is_not_blind(staff_client):
    """Inversion: a body validated against a wrong schema must fail."""
    p = make_promotion()
    body = staff_client.get(f"{V1}/staff/promotions/{p.pk}/").json()
    assert list(jsonschema.Draft202012Validator({"type": "string"}).iter_errors(body))
    broken = dict(body, value=12.5)
    schema = SCHEMA["paths"][P]["get"]["responses"]["200"]["content"]["application/json"]["schema"]
    assert list(_validator(schema).iter_errors(broken))
