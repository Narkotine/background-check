"""Traductions : un fichier JSON par langue, découvert automatiquement.

Ajouter une langue = déposer `locales/<code>.json` (même clés que `fr.json`) : rien à coder,
rien à enregistrer. La langue par défaut est le français ; toute clé manquante retombe sur le
français, puis sur la clé elle-même (jamais d'exception à l'écran).
"""

from __future__ import annotations

import json
import os
import sys

DEFAULT_LANGUAGE = "fr"

_CANDIDATE_DIRS = [
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "locales"),
    os.path.join(getattr(sys, "_MEIPASS", ""), "bgc", "locales") if getattr(sys, "_MEIPASS", "") else "",
]


def locales_dir() -> str:
    for path in _CANDIDATE_DIRS:
        if path and os.path.isdir(path):
            return path
    return _CANDIDATE_DIRS[0]


class Translator:
    """Résolution de clés `section.cle` avec paramètres nommés."""

    def __init__(self, directory: str | None = None):
        self.directory = directory or locales_dir()
        self._sets: dict[str, dict] = {}
        self._meta: dict[str, dict] = {}
        self.language = DEFAULT_LANGUAGE
        self.load()

    # --- chargement -------------------------------------------------------
    def load(self) -> None:
        self._sets.clear()
        self._meta.clear()
        try:
            names = sorted(os.listdir(self.directory))
        except OSError:
            names = []
        for name in names:
            if not name.endswith(".json"):
                continue
            path = os.path.join(self.directory, name)
            try:
                with open(path, encoding="utf-8") as fh:
                    data = json.load(fh)
            except Exception:
                continue
            code = (data.get("meta") or {}).get("code") or os.path.splitext(name)[0]
            strings = data.get("strings") or {}
            if not isinstance(strings, dict):
                continue
            self._sets[code] = strings
            meta = dict(data.get("meta") or {})
            meta.setdefault("code", code)
            meta.setdefault("name", code)
            self._meta[code] = meta
        if self.language not in self._sets:
            self.language = DEFAULT_LANGUAGE if DEFAULT_LANGUAGE in self._sets else next(
                iter(self._sets), DEFAULT_LANGUAGE)

    # --- API --------------------------------------------------------------
    @property
    def available(self) -> list[tuple[str, str]]:
        """[(code, nom natif)] — français d'abord, puis ordre alphabétique du nom."""
        codes = list(self._sets)
        codes.sort(key=lambda c: (c != DEFAULT_LANGUAGE, self._meta.get(c, {}).get("name", c)))
        return [(c, self._meta.get(c, {}).get("name", c)) for c in codes]

    def set_language(self, code: str) -> bool:
        if code in self._sets:
            self.language = code
            return True
        return False

    def name(self, code: str) -> str:
        return self._meta.get(code, {}).get("name", code)

    def font_family(self) -> str:
        """Police qui sait afficher la langue (le chinois n'a pas de glyphes dans Segoe UI)."""
        return self._meta.get(self.language, {}).get("ui_font") or "Segoe UI"

    def t(self, key: str, **params) -> str:
        # Un paramètre peut lui-même être une clé : {"key": "...", "params": {...}} est traduit
        # avant d'être inséré (les instantanés restent indépendants de la langue).
        params = {name: (self.t(v["key"], **(v.get("params") or {}))
                         if isinstance(v, dict) and "key" in v else v)
                  for name, v in params.items()}
        for code in (self.language, DEFAULT_LANGUAGE):
            value = self._sets.get(code, {}).get(key)
            if isinstance(value, str):
                return self._format(value, params)
        return key

    @staticmethod
    def _format(value: str, params: dict) -> str:
        if not params:
            return value
        try:
            return value.format(**params)
        except (KeyError, IndexError, ValueError):
            return value


TRANSLATOR = Translator()


def t(key: str, **params) -> str:
    return TRANSLATOR.t(key, **params)


def set_language(code: str) -> bool:
    return TRANSLATOR.set_language(code)


def font_family() -> str:
    return TRANSLATOR.font_family()
