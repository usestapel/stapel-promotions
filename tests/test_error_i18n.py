"""The error codes this package owns have a string in every language it ships.

``translations/errors.<lang>.json`` (the stapel-listings convention): every
owned key present and non-empty in every target language, nothing but owned
keys, every ``{param}`` slot of the English canon preserved, the shipped
language set exactly the gated one, ``dump_catalog`` byte form, and the
catalogue inside the wheel's package-data.
"""
import json
import tomllib
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
TRANSLATIONS = REPO / "translations"
THIS_PACKAGE = "stapel_promotions"
TARGET_LANGUAGES = ["ru", "es"]


def _canon():
    from stapel_core.i18n import owned_keys, source_owners, source_texts

    return owned_keys(source_texts("errors"), source_owners("errors"), THIS_PACKAGE)


def _shipped(lang):
    path = TRANSLATIONS / f"errors.{lang}.json"
    assert path.is_file(), f"translations/{path.name} is missing"
    return json.loads(path.read_text(encoding="utf-8"))


def test_the_loader_attributes_the_registry_to_this_package():
    from stapel_promotions.errors import STAPEL_PROMOTIONS_ERRORS

    assert set(_canon()) == set(STAPEL_PROMOTIONS_ERRORS)


@pytest.mark.parametrize("lang", TARGET_LANGUAGES)
def test_every_owned_key_is_translated(lang):
    catalog = _shipped(lang)
    missing = sorted(set(_canon()) - set(catalog))
    assert not missing, f"{lang}: missing {missing}"
    assert not [k for k, v in catalog.items() if not str(v).strip()], f"{lang}: empty translation"


@pytest.mark.parametrize("lang", TARGET_LANGUAGES)
def test_nothing_but_owned_keys(lang):
    assert not sorted(set(_shipped(lang)) - set(_canon()))


@pytest.mark.parametrize("lang", TARGET_LANGUAGES)
def test_every_placeholder_is_preserved(lang):
    from stapel_core.i18n.domains import params_of

    canon = _canon()
    for key, text in _shipped(lang).items():
        assert set(params_of(text)) == set(params_of(canon[key])), f"{lang}: {key}"


def test_the_shipped_language_set_is_exactly_the_gated_one():
    shipped = {p.name for p in TRANSLATIONS.glob("errors.*.json")}
    assert shipped == {f"errors.{lang}.json" for lang in TARGET_LANGUAGES}


@pytest.mark.parametrize("lang", TARGET_LANGUAGES)
def test_the_catalogue_is_byte_stable(lang):
    from stapel_core.i18n import dump_catalog

    path = TRANSLATIONS / f"errors.{lang}.json"
    assert path.read_text(encoding="utf-8") == dump_catalog(_shipped(lang))


def test_the_wheel_ships_the_catalogue():
    with open(REPO / "pyproject.toml", "rb") as handle:
        patterns = tomllib.load(handle)["tool"]["setuptools"]["package-data"]
    assert "translations/*.json" in patterns[THIS_PACKAGE]
