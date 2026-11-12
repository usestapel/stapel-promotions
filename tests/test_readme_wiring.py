"""The README's orders wiring snippets keep what the host already has.

Both snippets are executed exactly as printed in README.md against a
settings module that already holds ``STAPEL_ORDERS`` (the shop preset's
``PURCHASABLE_TYPES`` and another companion's adjuster and hook): nothing
may be lost, and this brick's entries must land. The scaffold form runs
through stapel-tools' own merge helper.
"""
import copy
import re
from pathlib import Path

import pytest

from stapel_promotions.preset import SETTINGS_DEFAULTS

README = Path(__file__).resolve().parent.parent / "README.md"
OURS = SETTINGS_DEFAULTS["STAPEL_ORDERS"]

HOST = {
    "PURCHASABLE_TYPES": {"listing": "listings.purchasables"},
    "ADJUSTERS": {"other": {"quote": "other.quote", "order": 300}},
    "CHECKOUT_HOOKS": {"other": {"reserve": "o.r", "commit": "o.c", "release": "o.x", "order": 300}},
}


def snippet(name: str) -> str:
    match = re.search(rf"<!-- orders-wiring:{name} -->\s*```python\n(.*?)```", README.read_text(), re.S)
    assert match, f"README.md has no orders-wiring:{name} snippet"
    return match.group(1)


def merge_helper() -> dict:
    from stapel_tools._wiring import _PRESET_MERGE

    source = _PRESET_MERGE.format(modules="", composites_doc="")
    namespace: dict = {}
    exec(source.rsplit("_stapel_merge_preset_defaults(globals()", 1)[0], namespace)
    return namespace


def assert_kept_and_added(result: dict) -> None:
    assert result["PURCHASABLE_TYPES"] == HOST["PURCHASABLE_TYPES"]
    for registry, entries in OURS.items():
        assert result[registry]["other"] == HOST[registry]["other"]
        for name, entry in entries.items():
            assert result[registry][name] == entry


@pytest.mark.parametrize("form", ["scaffold", "hand"])
def test_snippet_keeps_the_host_settings(form):
    namespace = merge_helper() if form == "scaffold" else {}
    namespace["STAPEL_ORDERS"] = copy.deepcopy(HOST)
    exec(snippet(form), namespace)
    assert_kept_and_added(namespace["STAPEL_ORDERS"])


@pytest.mark.parametrize("form", ["scaffold", "hand"])
def test_snippet_pasted_twice_or_first_is_harmless(form):
    namespace = merge_helper() if form == "scaffold" else {}
    exec(snippet(form), namespace)
    exec(snippet(form), namespace)
    for registry, entries in OURS.items():
        assert namespace["STAPEL_ORDERS"][registry] == entries


def test_a_host_override_wins_over_the_preset():
    name = next(iter(OURS["CHECKOUT_HOOKS"]))
    namespace = {"STAPEL_ORDERS": {"CHECKOUT_HOOKS": {name: None}}}
    exec(snippet("hand"), namespace)
    assert namespace["STAPEL_ORDERS"]["CHECKOUT_HOOKS"][name] is None


def test_the_readme_never_assigns_a_bare_stapel_orders():
    for block in re.findall(r"```python\n(.*?)```", README.read_text(), re.S):
        for statement in re.findall(r"^STAPEL_ORDERS = \{\n(.*?)^\}", block, re.S | re.M):
            assert statement.lstrip().startswith("**STAPEL_ORDERS"), statement
