"""Thème : toutes les couleurs de l'interface passent par l'objet `T`.

Avant, chaque module importait des constantes (`BG`, `CARD`…) figées à l'import et une
quarantaine de couleurs étaient écrites en dur (« #252a3a » pour un survol, « #ffffff » sur un
bouton…). Impossible, dans ces conditions, d'offrir un thème clair. Ici :

* `T.BG`, `T.CARD`… se lisent **au moment du dessin** : changer de palette puis reconstruire
  la fenêtre suffit (`App.rebuild_all`) ;
* `ThemeMap` remplace les dictionnaires de couleurs par état, qui sont relus à chaque appel ;
* une couleur nouvelle = un attribut de `Palette`, jamais un code hexadécimal dans une vue.
"""

from __future__ import annotations

from dataclasses import dataclass, fields, replace


@dataclass(frozen=True)
class Palette:
    name: str
    dark: bool
    BG: str
    RAIL: str
    CARD: str
    CARD_HI: str
    CARD_HOVER: str        # survol d'un bouton secondaire / d'une ligne
    BORDER: str
    BORDER_HI: str
    TEXT: str
    MUTED: str
    FAINT: str
    LOG_TEXT: str          # texte du journal (un cran sous TEXT)
    ACCENT: str            # texte d'accent, bordures de sélection
    ACCENT_DARK: str       # FOND des boutons primaires
    ACCENT_HI: str
    ON_ACCENT: str         # texte posé sur un fond coloré (bouton primaire, danger, « ! »)
    GREEN: str
    AMBER: str
    RED: str
    GREY: str
    DANGER: str            # bouton « Supprimer »
    DANGER_HI: str
    DANGER_ARMED: str      # « Supprimer » armé (second clic)
    DANGER_ARMED_HI: str
    DISABLED: str          # fond d'un bouton grisé
    TOGGLE_OFF: str
    SCROLL: str
    SCROLL_HI: str


DARK = Palette(
    name="dark", dark=True,
    BG="#0f1116", RAIL="#12141b", CARD="#171a22", CARD_HI="#1d2130", CARD_HOVER="#252a3a",
    BORDER="#262b3a", BORDER_HI="#33507f",
    TEXT="#e9ebf2", MUTED="#9aa3b8",
    # FAINT : aides, horodatages — 4,72:1 sur carte et 5,12:1 sur le fond (seuil AA 4,5:1).
    FAINT="#7d859a", LOG_TEXT="#a9b2c6",
    ACCENT="#5b8cff", ACCENT_DARK="#3f66d4", ACCENT_HI="#4a6fdb", ON_ACCENT="#ffffff",
    GREEN="#34d399", AMBER="#fbbf24", RED="#f87171", GREY="#6b7280",
    DANGER="#8f3f3f", DANGER_HI="#a84c4c", DANGER_ARMED="#b91c1c", DANGER_ARMED_HI="#dc2626",
    DISABLED="#2a2f3d", TOGGLE_OFF="#333a4c", SCROLL="#333a49", SCROLL_HI="#4a5266",
)

# Thème clair (16 nov 2026) : mêmes rôles, contrastes AA vérifiés par tests/test_theme.py
# (texte, aides, accent et couleurs d'état ≥ 4,5:1 sur le fond comme sur les cartes).
LIGHT = Palette(
    name="light", dark=False,
    BG="#f4f5f9", RAIL="#e9ecf3", CARD="#ffffff", CARD_HI="#edf1fb", CARD_HOVER="#e2e7f2",
    BORDER="#d3d8e4", BORDER_HI="#7d9fe6",
    TEXT="#151924", MUTED="#465064", FAINT="#5a6377", LOG_TEXT="#2b3142",
    ACCENT="#2b59c9", ACCENT_DARK="#2f5fd0", ACCENT_HI="#264fb3", ON_ACCENT="#ffffff",
    GREEN="#0b7a52", AMBER="#975700", RED="#c0262d", GREY="#646b78",
    DANGER="#b5363a", DANGER_HI="#9e2d31", DANGER_ARMED="#b91c1c", DANGER_ARMED_HI="#991b1b",
    DISABLED="#e1e4ec", TOGGLE_OFF="#bfc6d4", SCROLL="#c7cdd9", SCROLL_HI="#a7afbf",
)

PALETTES: dict[str, Palette] = {"dark": DARK, "light": LIGHT}

#: Choix proposés dans les Réglages (« system » suit le thème des applis Windows).
THEME_CHOICES = ("dark", "light", "system")


def windows_prefers_light() -> bool | None:
    """Thème des applications Windows (Paramètres > Personnalisation > Couleurs).

    None hors Windows ou si la valeur n'existe pas (Windows ancien) : on reste en sombre.
    """
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize") as key:
            value, _kind = winreg.QueryValueEx(key, "AppsUseLightTheme")
        return bool(value)
    except Exception:
        return None


def resolve(choice, prefers_light=windows_prefers_light) -> str:
    """Nom de palette effectif pour un choix des Réglages (inconnu → sombre)."""
    choice = str(choice or "dark")
    if choice == "system":
        return "light" if prefers_light() else "dark"
    return choice if choice in PALETTES else "dark"

#: Couleur par état d'affichage (`presentation.badge_state`) : noms d'attributs de palette.
STATE_NAMES = {"ok": "GREEN", "warn": "AMBER", "bad": "RED", "idle": "GREY", "paused": "GREY",
               "unknown": "FAINT"}


class _Theme:
    """La palette courante ; ses attributs sont ceux de `Palette`."""

    def __init__(self, palette: Palette):
        object.__setattr__(self, "_palette", palette)

    def __getattr__(self, name: str):
        return getattr(object.__getattribute__(self, "_palette"), name)

    def __setattr__(self, name, value):  # pragma: no cover
        raise AttributeError("T est en lecture seule : utiliser theme.apply()")

    @property
    def palette(self) -> Palette:
        return object.__getattribute__(self, "_palette")

    @property
    def STATE_COLOURS(self) -> "ThemeMap":  # noqa: N802 (nom historique des widgets)
        return ThemeMap(STATE_NAMES)


T = _Theme(DARK)


def apply(name_or_palette) -> Palette:
    """Change la palette courante (la fenêtre doit ensuite être reconstruite)."""
    palette = (name_or_palette if isinstance(name_or_palette, Palette)
               else PALETTES.get(str(name_or_palette), DARK))
    object.__setattr__(T, "_palette", palette)
    return palette


def colour_names() -> list[str]:
    return [f.name for f in fields(Palette) if f.name.isupper()]


class ThemeMap:
    """Dictionnaire {clé: nom d'attribut de palette ou couleur} relu à chaque accès."""

    def __init__(self, mapping: dict):
        self._mapping = dict(mapping)

    def _resolve(self, value):
        return getattr(T, value) if isinstance(value, str) and value.isupper() else value

    def get(self, key, default=None):
        if key not in self._mapping:
            return default
        return self._resolve(self._mapping[key])

    def __getitem__(self, key):
        return self._resolve(self._mapping[key])

    def __contains__(self, key) -> bool:
        return key in self._mapping

    def items(self):
        return [(key, self._resolve(value)) for key, value in self._mapping.items()]


__all__ = ["T", "Palette", "DARK", "LIGHT", "PALETTES", "THEME_CHOICES", "ThemeMap", "apply",
           "colour_names", "replace", "resolve", "windows_prefers_light"]
