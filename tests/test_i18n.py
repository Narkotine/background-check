"""Parité des traductions : les 5 langues ont exactement les mêmes clés, et toute clé
citée dans le code existe."""
from __future__ import annotations

import glob
import json
import os
import re

from conftest import ROOT

LOCALES = os.path.join(ROOT, "bgc", "locales")
#: Chaînes qui ressemblent à des clés mais n'en sont pas (noms de fichiers, types de base).
NOT_KEYS = {"output.wav", "settings.json", "config.json", "config.yaml", "type.base.desc", "type.base.label",
            "type.portwatch.desc", "type.portwatch.label", "wsl.exe"}


def _strings(code: str) -> dict:
    with open(os.path.join(LOCALES, f"{code}.json"), encoding="utf-8") as fh:
        return json.load(fh)["strings"]


def _codes() -> list[str]:
    return sorted(os.path.splitext(n)[0] for n in os.listdir(LOCALES) if n.endswith(".json"))


def test_five_locales_present():
    assert set(_codes()) >= {"fr", "en", "es", "pt-BR", "zh"}


def test_key_parity():
    reference = set(_strings("fr"))
    for code in _codes():
        keys = set(_strings(code))
        assert keys == reference, (code, sorted(reference ^ keys)[:20])


def test_no_empty_values():
    for code in _codes():
        empty = [k for k, v in _strings(code).items() if not str(v).strip()]
        assert not empty, (code, empty[:10])


def test_placeholders_match_french():
    """Un `{param}` présent en français doit exister dans chaque traduction."""
    fr = _strings("fr")
    pattern = re.compile(r"\{(\w+)\}")
    for code in _codes():
        other = _strings(code)
        for key, value in fr.items():
            assert set(pattern.findall(value)) == set(pattern.findall(other[key])), (code, key)


def test_keys_used_in_code_exist():
    keys = set(_strings("fr"))
    prefixes = sorted({k.split(".")[0] for k in keys})
    pattern = re.compile(r"[\"']((?:%s)\.[a-z0-9_]+(?:\.[a-z0-9_]+)*)[\"']" % "|".join(prefixes))
    files = [os.path.join(ROOT, "background_check.py")] + glob.glob(
        os.path.join(ROOT, "bgc", "**", "*.py"), recursive=True)
    used = set()
    for path in files:
        with open(path, encoding="utf-8") as fh:
            used |= set(pattern.findall(fh.read()))
    missing = sorted(k for k in used - keys if k not in NOT_KEYS)
    assert not missing, missing
