"""Thème : les couleurs passent par `T`, relu au dessin ; aucune vue n'écrit de code hexadécimal."""
import glob
import os
import re

from bgc import theme
from conftest import ROOT

VIEW_FILES = (["bgc/widgets.py", "background_check.py"]
              + [os.path.relpath(p, ROOT) for p in glob.glob(os.path.join(ROOT, "bgc", "ui", "**", "*.py"),
                                                       recursive=True)])
#: Couleurs de marque des types (pictogrammes) : identiques dans tous les thèmes.
BRAND = {"#8b7cf6", "#e0b341", "#2dd4bf", "#38bdf8", "#f472b6"}


def test_no_hardcoded_colours_in_views():
    offenders = []
    for rel in VIEW_FILES:
        path = os.path.join(ROOT, rel)
        if not os.path.isfile(path):
            continue
        for number, line in enumerate(open(path, encoding="utf-8"), 1):
            for colour in re.findall(r'"(#[0-9a-fA-F]{6})"', line):
                if colour.lower() not in BRAND:
                    offenders.append(f"{rel}:{number} {colour}")
    assert offenders == []


def test_theme_map_follows_the_current_palette():
    rows = theme.ThemeMap({"ok": "GREEN", "x": "#123456"})
    before = rows.get("ok")
    custom = theme.replace(theme.DARK, name="test", GREEN="#00ff00")
    try:
        theme.apply(custom)
        assert rows.get("ok") == "#00ff00" and rows["x"] == "#123456"
        assert theme.T.STATE_COLOURS.get("ok") == "#00ff00"
    finally:
        theme.apply("dark")
    assert rows.get("ok") == before


def test_every_palette_defines_every_colour():
    names = theme.colour_names()
    for palette in theme.PALETTES.values():
        for name in names:
            assert re.fullmatch(r"#[0-9a-f]{6}", getattr(palette, name)), (palette.name, name)


def _luminance(hex_colour: str) -> float:
    rgb = [int(hex_colour[i:i + 2], 16) / 255 for i in (1, 3, 5)]
    lin = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in rgb]
    return 0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2]


def _contrast(a: str, b: str) -> float:
    la, lb = sorted((_luminance(a), _luminance(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


def test_light_palette_meets_aa_contrast():
    from bgc.theme import LIGHT
    for fg in ("TEXT", "MUTED", "FAINT", "LOG_TEXT", "ACCENT", "GREEN", "AMBER", "RED"):
        for bg in ("BG", "CARD", "RAIL"):
            ratio = _contrast(getattr(LIGHT, fg), getattr(LIGHT, bg))
            assert ratio >= 4.5, (fg, bg, round(ratio, 2))
    for bg in ("ACCENT_DARK", "DANGER", "DANGER_ARMED"):   # texte blanc des boutons
        assert _contrast(LIGHT.ON_ACCENT, getattr(LIGHT, bg)) >= 4.5, bg


def test_theme_choice_resolution():
    from bgc import theme
    assert theme.resolve("light") == "light" and theme.resolve("nope") == "dark"
    assert theme.resolve("system", prefers_light=lambda: True) == "light"
    assert theme.resolve("system", prefers_light=lambda: False) == "dark"
    assert theme.resolve("system", prefers_light=lambda: None) == "dark"   # hors Windows
    assert theme.windows_prefers_light() in (None, True, False)


def test_theme_choice_survives_config_reload(tmp_path, monkeypatch):
    from bgc import config as config_mod
    path = tmp_path / "config.json"
    monkeypatch.setenv("BGC_CONFIG", str(path))
    cfg = config_mod.Config().load()
    assert cfg.general["theme"] == "dark"
    cfg.general["theme"] = "system"
    cfg.save()
    assert config_mod.Config().load().general["theme"] == "system"
