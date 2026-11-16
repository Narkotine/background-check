"""Petite boîte à outils visuelle (canvas + Tk pur, aucune dépendance).

Tout est dessiné à la main pour garder un thème sombre cohérent : ttk ne suit pas proprement un
thème sombre et un Canvas donne les arrondis et le survol exacts. La police vient de
`i18n.font_family()` (le chinois n'a pas de glyphes dans Segoe UI).
"""

from __future__ import annotations

import tkinter as tk
import tkinter.font as tkfont

from . import i18n
from .theme import T, ThemeMap  # noqa: F401  (T réexporté pour les plugins)

# ── palette : voir `theme.py` (objet T) ──────────────────────────────────────

SC = 1.0  # facteur d'échelle DPI, posé par l'app au démarrage
UI_SCALE = 1.0  # agrandissement choisi par l'utilisateur (Réglages → Taille de l'interface)
# Planchers de lisibilité, en pixels écran. Un plancher unique écrasait les tailles 8, 9,
# 10 et 11 sur une seule valeur (13 px) : quatre niveaux de hiérarchie devenus un seul
# (mesuré le 11 nov 2026). Deux paliers réels : aides à 13 px, corps de texte à 15 px.
FONT_MIN = 8.0
FONT_MIN_PX = 13
FONT_BODY_PX = 15


def sc(value: float) -> int:
    return max(1, int(round(value * SC * UI_SCALE)))


def font(size: int, bold: bool = False) -> tuple:
    px = max(sc(max(size, FONT_MIN)), FONT_MIN_PX)
    if size >= 10:  # corps de texte : un cran au-dessus des aides
        px = max(px, FONT_BODY_PX)
    return (i18n.font_family(), -px, "bold") if bold else (i18n.font_family(), -px)


def mono(size: int) -> tuple:
    return ("Consolas", -max(sc(max(size, FONT_MIN)), FONT_MIN_PX))


def round_rect(canvas: tk.Canvas, x1, y1, x2, y2, r, **kw):
    points = [
        x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r, x2, y2 - r, x2, y2,
        x2 - r, y2, x1 + r, y2, x1, y2, x1, y2 - r, x1, y1 + r, x1, y1,
    ]
    return canvas.create_polygon(points, smooth=True, **kw)


def draw_badge(canvas: tk.Canvas, cx: float, cy: float, r: float, state: str,
               ring: str = None, tags=()) -> None:
    """Pastille d'état : pleine et colorée ; « en pause » = grise avec deux barres.

    Remplace l'ancien point gris de 6 px, le même pour « arrêté », « inconnu » et « en pause »
    (revue du 16 nov 2026, b-2).
    """
    ring = T.BG if ring is None else ring
    colour = T.STATE_COLOURS.get(state, T.FAINT)
    canvas.create_oval(cx - r - 2, cy - r - 2, cx + r + 2, cy + r + 2, fill=ring, outline="",
                       tags=tags)
    if state == "paused":
        canvas.create_oval(cx - r, cy - r, cx + r, cy + r, fill=T.CARD_HI, outline=colour,
                           width=max(1, sc(1.5)), tags=tags)
        bar_w = max(1, r * 0.28)
        for dx in (-r * 0.32, r * 0.32):
            canvas.create_rectangle(cx + dx - bar_w / 2, cy - r * 0.5, cx + dx + bar_w / 2,
                                    cy + r * 0.5, fill=T.MUTED, outline="", tags=tags)
        return
    if state == "idle":
        canvas.create_oval(cx - r, cy - r, cx + r, cy + r, fill=T.CARD_HI, outline=colour,
                           width=max(1, sc(1.5)), tags=tags)
        return
    canvas.create_oval(cx - r, cy - r, cx + r, cy + r, fill=colour, outline="", tags=tags)
    if state == "bad":  # un « ! » blanc : lisible même sans distinguer le rouge
        canvas.create_text(cx, cy, text="!", fill=T.ON_ACCENT, font=font(7, True), tags=tags)


# ── infobulles ────────────────────────────────────────────────────────────────
class Tooltip:
    """Infobulle sombre au survol (400 ms). `text` peut être une chaîne ou une fonction.

    Sert aux libellés tronqués du rail, au rail compact (icônes seules) et aux aides ⓘ :
    l'aide longue n'occupe plus l'écran en permanence (revue du 16 nov 2026, b-5).
    """

    DELAY = 400

    def __init__(self, widget, text, wrap: int = 360):
        self.widget, self._text, self._wrap = widget, text, wrap
        self._job = None
        self._tip = None
        widget.bind("<Enter>", self._schedule, add="+")
        widget.bind("<Leave>", self.hide, add="+")
        widget.bind("<ButtonPress>", self.hide, add="+")
        widget.bind("<Destroy>", self.hide, add="+")

    def text(self) -> str:
        value = self._text() if callable(self._text) else self._text
        return str(value or "")

    def _schedule(self, _event=None) -> None:
        self._cancel()
        try:
            self._job = self.widget.after(self.DELAY, self.show)
        except Exception:
            self._job = None

    def _cancel(self) -> None:
        if self._job:
            try:
                self.widget.after_cancel(self._job)
            except Exception:
                pass
            self._job = None

    def show(self) -> None:
        self._job = None
        text = self.text()
        if not text or self._tip is not None:
            return
        try:
            x = self.widget.winfo_rootx() + sc(12)
            y = self.widget.winfo_rooty() + self.widget.winfo_height() + sc(4)
            tip = tk.Toplevel(self.widget)
            tip.wm_overrideredirect(True)
            try:
                tip.attributes("-topmost", True)
            except Exception:
                pass
            frame = tk.Frame(tip, bg=T.BORDER_HI)
            frame.pack()
            tk.Label(frame, text=text, bg=T.CARD_HI, fg=T.TEXT, font=font(9), justify="left",
                     wraplength=sc(self._wrap), padx=sc(8), pady=sc(5)).pack(padx=1, pady=1)
            tip.update_idletasks()
            # Ne pas sortir de l'écran à droite ou en bas.
            width, height = tip.winfo_reqwidth(), tip.winfo_reqheight()
            screen_w, screen_h = tip.winfo_screenwidth(), tip.winfo_screenheight()
            x = min(x, screen_w - width - sc(4))
            if y + height > screen_h:
                y = self.widget.winfo_rooty() - height - sc(4)
            tip.geometry(f"+{max(0, x)}+{max(0, y)}")
            self._tip = tip
        except Exception:
            self._tip = None

    def hide(self, _event=None) -> None:
        self._cancel()
        if self._tip is not None:
            try:
                self._tip.destroy()
            except Exception:
                pass
            self._tip = None


def fit_text(text: str, fnt, max_px: int) -> tuple[str, bool]:
    """(texte, tronqué ?) : coupe avec « … » pour tenir dans `max_px` pixels à cette police."""
    from tkinter import font as tkfont
    try:
        measure = (fnt if isinstance(fnt, tkfont.Font) else tkfont.Font(font=fnt)).measure
    except Exception:
        return text, False
    if max_px <= 0 or measure(text) <= max_px:
        return text, False
    lo, hi = 0, len(text)
    while lo < hi:   # plus long préfixe qui tient avec « … »
        mid = (lo + hi + 1) // 2
        if measure(text[:mid].rstrip() + "…") <= max_px:
            lo = mid
        else:
            hi = mid - 1
    return text[:lo].rstrip() + "…", True


class InfoIcon(tk.Label):
    """Petit « ⓘ » : l'aide s'affiche au survol (ou au clic, pour qui ne survole pas)."""

    def __init__(self, master, text, bg=None):
        bg = T.BG if bg is None else bg
        super().__init__(master, text="ⓘ", bg=bg, fg=T.FAINT, font=font(9), cursor="question_arrow")
        self.tooltip = Tooltip(self, text)
        self.bind("<Button-1>", lambda _e: self.after(1, self.tooltip.show), add="+")
        self.bind("<Enter>", lambda _e: self.configure(fg=T.ACCENT), add="+")
        self.bind("<Leave>", lambda _e: self.configure(fg=T.FAINT), add="+")


def label_with_info(master, text: str, hint: str = "", bg=None, fg=None, size: int = 9,
                    bold: bool = False) -> tk.Frame:
    """Libellé + ⓘ facultatif sur une ligne (le cadre renvoyé est à packer par l'appelant)."""
    bg = T.BG if bg is None else bg
    fg = T.MUTED if fg is None else fg
    row = tk.Frame(master, bg=bg)
    tk.Label(row, text=text, bg=bg, fg=fg, font=font(size, bold), anchor="w").pack(side="left")
    if hint:
        InfoIcon(row, hint, bg=bg).pack(side="left", padx=(sc(5), 0))
    return row


def hint_for(key: str) -> str:
    """Aide associée à une clé par convention (`<clé>_hint`), ou chaîne vide."""
    if not key:
        return ""
    hint_key = key + "_hint"
    text = i18n.t(hint_key)
    return "" if text == hint_key else text


# ── icônes du rail ────────────────────────────────────────────────────────────
def draw_icon(canvas: tk.Canvas, kind: str, cx: float, cy: float, size: float, colour: str) -> bool:
    """Pictogramme vectoriel du rail, dessiné à la main comme le reste du thème.

    `size` est le côté du carré utile (déjà scalé). Renvoie False si le type est
    inconnu — l'appelant garde alors la lettre de repli. L'icône ne remplace
    jamais le point d'état : c'est un ornement de repérage, pas une information.
    """
    s = size
    w = max(1, sc(2))
    if kind == "hermes":  # bulle de discussion (l'agent qui répond)
        x1, y1 = cx - s * 0.46, cy - s * 0.46
        x2, y2 = cx + s * 0.46, cy + s * 0.16
        round_rect(canvas, x1, y1, x2, y2, s * 0.26, fill="", outline=colour, width=w)
        canvas.create_line(x1 + s * 0.22, y2 - 1, x1 + s * 0.13, y2 + s * 0.30,
                           x1 + s * 0.45, y2 - 1, fill=colour, width=w,
                           joinstyle="round", capstyle="round")
        for dx in (-0.17, 0.0, 0.17):
            canvas.create_oval(cx + s * dx - s * 0.05, cy - s * 0.15 - s * 0.05,
                               cx + s * dx + s * 0.05, cy - s * 0.15 + s * 0.05,
                               fill=colour, outline="")
        return True
    if kind == "wsl":  # terminal
        x1, y1 = cx - s * 0.46, cy - s * 0.38
        x2, y2 = cx + s * 0.46, cy + s * 0.38
        round_rect(canvas, x1, y1, x2, y2, s * 0.16, fill="", outline=colour, width=w)
        canvas.create_line(x1 + s * 0.18, cy - s * 0.15, x1 + s * 0.36, cy,
                           x1 + s * 0.18, cy + s * 0.15, fill=colour, width=w,
                           joinstyle="round", capstyle="round")
        canvas.create_line(cx + s * 0.04, cy + s * 0.15, cx + s * 0.32, cy + s * 0.15,
                           fill=colour, width=w, capstyle="round")
        return True
    if kind == "stt":  # microphone
        round_rect(canvas, cx - s * 0.16, cy - s * 0.46, cx + s * 0.16, cy + s * 0.10,
                   s * 0.16, fill="", outline=colour, width=w)
        canvas.create_line(cx, cy + s * 0.10, cx, cy + s * 0.38, fill=colour, width=w,
                           capstyle="round")
        canvas.create_line(cx - s * 0.24, cy + s * 0.38, cx + s * 0.24, cy + s * 0.38,
                           fill=colour, width=w, capstyle="round")
        return True
    if kind == "command":  # ligne de commande : boîte + triangle « exécuter »
        x1, y1 = cx - s * 0.46, cy - s * 0.40
        x2, y2 = cx + s * 0.46, cy + s * 0.40
        round_rect(canvas, x1, y1, x2, y2, s * 0.18, fill="", outline=colour, width=w)
        canvas.create_polygon(cx - s * 0.13, cy - s * 0.19, cx - s * 0.13, cy + s * 0.19,
                              cx + s * 0.21, cy, fill=colour, outline="")
        return True
    if kind == "docker":  # conteneurs empilés : trois caisses sur une palette
        bw, bh = s * 0.26, s * 0.22
        for col, row in ((-1, 0), (0, 0), (1, 0), (-0.5, -1), (0.5, -1)):
            x = cx + col * (bw + s * 0.04)
            y = cy + s * 0.06 + row * (bh + s * 0.04)
            canvas.create_rectangle(x - bw / 2, y - bh / 2, x + bw / 2, y + bh / 2,
                                    outline=colour, width=w)
        canvas.create_line(cx - s * 0.48, cy + s * 0.30, cx + s * 0.48, cy + s * 0.30,
                           fill=colour, width=w, capstyle="round")
        return True
    if kind == "winsvc":  # engrenage : huit dents autour d'un moyeu
        import math
        for i in range(8):
            ang = i * math.pi / 4
            canvas.create_line(cx + math.cos(ang) * s * 0.26, cy + math.sin(ang) * s * 0.26,
                               cx + math.cos(ang) * s * 0.44, cy + math.sin(ang) * s * 0.44,
                               fill=colour, width=max(2, sc(4)), capstyle="butt")
        r = s * 0.30
        canvas.create_oval(cx - r, cy - r, cx + r, cy + r, outline=colour, width=w)
        r = s * 0.11
        canvas.create_oval(cx - r, cy - r, cx + r, cy + r, outline=colour, width=w)
        return True
    if kind == "portwatch":  # cible
        for radius, filled in ((0.42, False), (0.22, False), (0.08, True)):
            r = s * radius
            if filled:
                canvas.create_oval(cx - r, cy - r, cx + r, cy + r, fill=colour, outline="")
            else:
                canvas.create_oval(cx - r, cy - r, cx + r, cy + r, outline=colour, width=w)
        return True
    return False


def load_icon(path: str, px: int):
    """Charge une icône personnalisée en carré px×px, ou None.

    Sources acceptées : une image (.png/.ico/.jpg/…) ou l'icône **embarquée d'un
    .exe/.dll** (`_pe_icon`, API Windows, zéro dépendance). Repli en cascade :
    PIL → tk.PhotoImage (PNG) → icône PE. Un fichier absent ou illisible renvoie
    None et la tuile retombe sur le pictogramme du type — jamais sur une case vide.
    """
    if not path:
        return None
    try:
        from PIL import Image, ImageTk
        img = Image.open(path).convert("RGBA")
        side = max(img.size)
        square = Image.new("RGBA", (side, side), (0, 0, 0, 0))
        square.paste(img, ((side - img.width) // 2, (side - img.height) // 2))
        return ImageTk.PhotoImage(square.resize((px, px), Image.LANCZOS))
    except Exception:
        pass
    try:
        img = tk.PhotoImage(file=path)
        factor = max(1, int(round(max(img.width(), img.height()) / max(1, px))))
        return img.subsample(factor, factor) if factor > 1 else img
    except Exception:
        pass
    try:
        from PIL import ImageTk
        img = _pe_icon(path, px)
        if img is not None:
            return ImageTk.PhotoImage(img)
    except Exception:
        pass
    return None


def _hicon_to_image(hicon, px: int):
    """HICON → image PIL RGBA (px×px). Les .exe sans alpha réel utilisent le masque AND."""
    import ctypes
    from ctypes import wintypes

    class ICONINFO(ctypes.Structure):
        _fields_ = [("fIcon", wintypes.BOOL), ("xHotspot", wintypes.DWORD),
                    ("yHotspot", wintypes.DWORD), ("hbmMask", wintypes.HBITMAP),
                    ("hbmColor", wintypes.HBITMAP)]

    class BITMAPINFOHEADER(ctypes.Structure):
        _fields_ = [("biSize", wintypes.DWORD), ("biWidth", wintypes.LONG),
                    ("biHeight", wintypes.LONG), ("biPlanes", wintypes.WORD),
                    ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
                    ("biSizeImage", wintypes.DWORD), ("biXPelsPerMeter", wintypes.LONG),
                    ("biYPelsPerMeter", wintypes.LONG), ("biClrUsed", wintypes.DWORD),
                    ("biClrImportant", wintypes.DWORD)]

    from PIL import Image

    user32, gdi32 = ctypes.windll.user32, ctypes.windll.gdi32
    # Signatures obligatoires : sans argtypes, ctypes passe les handles en int 32 bits
    # et déborde sur les handles 64 bits (OverflowError constaté sur shell32.dll).
    user32.GetDC.argtypes = [wintypes.HWND]
    user32.GetDC.restype = wintypes.HDC
    user32.ReleaseDC.argtypes = [wintypes.HWND, wintypes.HDC]
    user32.ReleaseDC.restype = ctypes.c_int
    user32.GetIconInfo.argtypes = [wintypes.HICON, ctypes.POINTER(ICONINFO)]
    user32.GetIconInfo.restype = wintypes.BOOL
    gdi32.GetDIBits.argtypes = [wintypes.HDC, wintypes.HBITMAP, wintypes.UINT, wintypes.UINT,
                                ctypes.c_void_p, ctypes.POINTER(BITMAPINFOHEADER), wintypes.UINT]
    gdi32.GetDIBits.restype = ctypes.c_int
    gdi32.DeleteObject.argtypes = [wintypes.HGDIOBJ]
    gdi32.DeleteObject.restype = wintypes.BOOL

    ii = ICONINFO()
    if not user32.GetIconInfo(hicon, ctypes.byref(ii)):
        return None
    hdc = user32.GetDC(0)
    try:
        def read_bmp(hbmp):
            bi = BITMAPINFOHEADER()
            bi.biSize = ctypes.sizeof(BITMAPINFOHEADER)
            gdi32.GetDIBits(hdc, hbmp, 0, 0, None, ctypes.byref(bi), 0)
            w, h = bi.biWidth, abs(bi.biHeight)
            if not (0 < w <= 1024 and 0 < h <= 1024):
                return None
            bi.biBitCount, bi.biCompression, bi.biSizeImage = 32, 0, w * h * 4
            buf = ctypes.create_string_buffer(w * h * 4)
            gdi32.GetDIBits(hdc, hbmp, 0, h, buf, ctypes.byref(bi), 0)
            img = Image.frombytes("RGBA", (w, h), buf.raw)  # octets BGRA
            b, g, r, a = img.split()
            img = Image.merge("RGBA", (r, g, b, a))
            # GetDIBits avec biHeight > 0 = lignes bas vers haut : il faut retourner l'image
            return img.transpose(Image.FLIP_TOP_BOTTOM) if bi.biHeight > 0 else img

        img = read_bmp(ii.hbmColor) if ii.hbmColor else None
        if img is not None:
            alpha = img.getchannel("A")
            if alpha.getextrema() == (0, 0):
                # alpha absent (tout à 0) → transparence portée par le masque AND
                mask = read_bmp(ii.hbmMask)
                if mask is not None:
                    img.putalpha(mask.convert("L").point(lambda v: 0 if v > 127 else 255))
            else:
                # Les octets d'icône sont prémultipliés : sans conversion, les bords
                # semi-transparents deviennent sombres et « déchiquetés » (constaté à 256 px).
                r, g, b, a = img.split()

                def un_premul(channel, al):
                    out = [min(255, cv * 255 // av) if av else 0
                           for cv, av in zip(channel.getdata(), al.getdata())]
                    channel.putdata(out)
                    return channel

                img = Image.merge("RGBA", (un_premul(r, a), un_premul(g, a), un_premul(b, a), a))
        if img is not None and img.size != (px, px):
            img = img.resize((px, px), Image.LANCZOS)
        return img
    finally:
        user32.ReleaseDC(0, hdc)
        if ii.hbmColor:
            gdi32.DeleteObject(ii.hbmColor)
        if ii.hbmMask:
            gdi32.DeleteObject(ii.hbmMask)


def _pe_icon(path: str, px: int):
    """Icône embarquée d'un .exe/.dll, à la taille demandée si possible, ou None.

    `PrivateExtractIconsW` sert d'abord (taille exacte, donc nette) puis repli sur
    `ExtractIconExW` (32 px). Seule la première icône (index 0) est retenue —
    d'habitude l'icône de l'application. Un fichier sans icône renvoie None.
    """
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    user32.DestroyIcon.argtypes = [wintypes.HICON]
    user32.DestroyIcon.restype = wintypes.BOOL

    hicon = None
    try:
        private = ctypes.windll.shell32.PrivateExtractIconsW
        private.argtypes = [wintypes.LPCWSTR, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                            ctypes.POINTER(wintypes.HICON), ctypes.POINTER(wintypes.UINT),
                            wintypes.UINT, wintypes.UINT]
        private.restype = wintypes.UINT
        buf = (wintypes.HICON * 1)()
        ids = (wintypes.UINT * 1)()
        if private(str(path), 0, px, px, buf, ids, 1, 0) and buf[0]:
            hicon = buf[0]
    except Exception:
        hicon = None
    if hicon is None:
        try:
            extract = ctypes.windll.shell32.ExtractIconExW
            extract.argtypes = [wintypes.LPCWSTR, ctypes.c_int, ctypes.POINTER(wintypes.HICON),
                                ctypes.POINTER(wintypes.HICON), wintypes.UINT]
            extract.restype = wintypes.UINT
            large = (wintypes.HICON * 1)()
            small = (wintypes.HICON * 1)()
            if extract(str(path), 0, large, small, 1):
                hicon = large[0] or small[0]
        except Exception:
            return None
    if not hicon:
        return None
    try:
        return _hicon_to_image(hicon, px)
    finally:
        user32.DestroyIcon(hicon)


# ── conteneurs ────────────────────────────────────────────────────────────────
class Card(tk.Canvas):
    """Panneau arrondi sur fond sombre ; le contenu se place dans `.inner` (Frame)."""

    def __init__(self, master, height=None, bg=None, border=None, radius=12, **kw):
        bg = T.CARD if bg is None else bg
        border = T.BORDER if border is None else border
        super().__init__(master, bg=kw.pop("outer_bg", T.BG), highlightthickness=0, bd=0, **kw)
        if height:
            self.configure(height=sc(height))
        self._bg, self._border, self._radius = bg, border, radius
        self.bind("<Configure>", self._redraw)

    def _redraw(self, _evt=None):
        self.delete("card")
        w, h = self.winfo_width(), self.winfo_height()
        if w < 4 or h < 4:
            return
        round_rect(self, sc(1), sc(1), w - sc(1), h - sc(1), sc(self._radius),
                   fill=self._bg, outline=self._border, width=1, tags="card")
        self.tag_lower("card")


class ScrollFrame(tk.Frame):
    """Zone défilante verticale (la molette marche dès que la souris est dessus)."""

    def __init__(self, master, bg=None):
        bg = T.BG if bg is None else bg
        super().__init__(master, bg=bg)
        self.canvas = tk.Canvas(self, bg=bg, highlightthickness=0, bd=0)
        self.canvas.pack(side="left", fill="both", expand=True)
        self.inner = tk.Frame(self.canvas, bg=bg)
        self._window = self.canvas.create_window((0, 0), window=self.inner, anchor="nw")
        self.inner.bind("<Configure>", self._on_inner)
        self.canvas.bind("<Configure>", self._on_canvas)
        #: Appelé après chaque défilement/redimensionnement (une barre fine s'y accroche).
        self.on_scroll = None
        _SCROLLERS.append(self)
        _bind_wheel_once(self)

    def _on_inner(self, _evt=None):
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))
        self._notify()

    def _on_canvas(self, evt):
        self.canvas.itemconfigure(self._window, width=evt.width)
        self._notify()

    def _notify(self) -> None:
        callback = self.on_scroll
        if callback:
            try:
                callback()
            except Exception:
                pass

    def scroll_by(self, steps: int) -> None:
        self.canvas.yview_scroll(int(steps), "units")
        self._notify()


# ── molette : un seul répartiteur pour toutes les zones défilantes ────────────
# `bind_all` n'accepte qu'UN gestionnaire par séquence : chaque ScrollFrame qui en posait
# un écrasait le précédent (constaté : la molette cessait de marcher sur le rail dès qu'une
# vue de réglages, elle aussi défilante, avait été ouverte). On garde donc la liste des
# zones vivantes et on envoie l'événement à celle qui est SOUS LE POINTEUR.
_SCROLLERS: list["ScrollFrame"] = []
_WHEEL_BOUND = False


def _bind_wheel_once(widget) -> None:
    global _WHEEL_BOUND
    if _WHEEL_BOUND:
        return
    try:
        widget.bind_all("<MouseWheel>", _wheel_dispatch)
        _WHEEL_BOUND = True
    except Exception:
        pass


def _wheel_dispatch(event):
    step = int(-event.delta / 120) or (-1 if event.delta > 0 else 1)
    for frame in list(_SCROLLERS):
        try:
            if not frame.winfo_exists():
                _SCROLLERS.remove(frame)
                continue
            under = frame.winfo_containing(event.x_root, event.y_root)
            if under is None or not str(under).startswith(str(frame)):
                continue
            frame.scroll_by(step)
            return
        except Exception:
            continue


class ThinBar(tk.Canvas):
    """Barre de défilement fine et sombre, dessinée à la main (un pouce, glisser pour défiler).

    Packée à droite d'une `ScrollFrame` ; elle ne se dessine que si le contenu dépasse.
    """

    def __init__(self, master, target: tk.Canvas, bg=None, width=None):
        bg = T.RAIL if bg is None else bg
        super().__init__(master, width=width or sc(7), bg=bg, highlightthickness=0, bd=0)
        self.target = target
        self._dragging = False
        self._hover = False
        self.bind("<Configure>", lambda _e: self.redraw())
        self.bind("<Button-1>", self._press)
        self.bind("<B1-Motion>", self._drag)
        self.bind("<ButtonRelease-1>", self._release)
        self.bind("<Enter>", self._enter)
        self.bind("<Leave>", self._leave)

    # --- peinture ---------------------------------------------------------
    def redraw(self) -> None:
        try:
            if not self.winfo_exists() or not self.target.winfo_exists():
                return
            first, last = self.target.yview()
            height = self.winfo_height()
            width = self.winfo_width()
        except Exception:
            return
        self.delete("all")
        if height <= 1 or (first <= 0.0 and last >= 1.0):
            return  # tout tient : rien à faire défiler, pas de barre
        top = int(first * height)
        bottom = max(top + sc(20), int(last * height))
        colour = T.SCROLL_HI if (self._hover or self._dragging) else T.SCROLL
        round_rect(self, 1, top, max(2, width - 1), min(height, bottom), sc(3),
                   fill=colour, outline="")

    def _enter(self, _event=None) -> None:
        self._hover = True
        self.redraw()

    def _leave(self, _event=None) -> None:
        self._hover = False
        self.redraw()

    # --- glisser ----------------------------------------------------------
    def _press(self, event) -> None:
        self._dragging = True
        self._move_to(event.y)

    def _drag(self, event) -> None:
        if self._dragging:
            self._move_to(event.y)

    def _release(self, _event=None) -> None:
        self._dragging = False

    def _move_to(self, y: int) -> None:
        height = max(1, self.winfo_height())
        first, last = self.target.yview()
        span = max(0.0, last - first)
        fraction = (y / height) - span / 2
        self.target.yview_moveto(min(max(fraction, 0.0), 1.0))
        self.redraw()



# ── boutons ───────────────────────────────────────────────────────────────────
class Button(tk.Canvas):
    """Bouton dessiné. `width=None` = largeur **mesurée** sur le libellé.

    Les largeurs fixes en dur débordaient sur les libellés longs et dans les autres
    langues (« Português (Brasil) », « Servicio vigilado ») : Tk ne coupe pas un texte
    dessiné au Canvas, il dépasse du rectangle. La mesure par défaut règle ça (11 nov 2026).
    """

    def __init__(self, master, text, command, width=None, height=38,
                 fill=None, hover=None, fg=None, radius=10, bold=True,
                 size=11, outline="", parent_bg=None):
        fill = T.ACCENT_DARK if fill is None else fill
        hover = T.ACCENT_HI if hover is None else hover
        fg = T.ON_ACCENT if fg is None else fg
        parent_bg = T.BG if parent_bg is None else parent_bg
        spec = font(size, bold)
        if width is None:
            metrics = tkfont.Font(font=spec)
            px_width = max(sc(90), metrics.measure(text) + sc(28))
        else:
            px_width = sc(width)  # le constructeur applique l'échelle : passer des unités de base
        super().__init__(master, width=px_width, height=sc(height), bg=parent_bg,
                         highlightthickness=0, bd=0, cursor="hand2", takefocus=1)
        self._text, self._command = text, command
        self._fill, self._hover, self._fg, self._outline = fill, hover, fg, outline
        self._radius, self._bold, self._size = sc(radius), bold, size
        self._enabled = True
        self._hovering = False
        self._focused = False
        self.bind("<Configure>", lambda e: self._draw())
        self.bind("<Enter>", self._on_enter)
        self.bind("<Leave>", self._on_leave)
        self.bind("<Button-1>", self._on_click)
        # Clavier : Tab atteint le bouton, Espace/Entrée l'active, et le focus se voit.
        self.bind("<FocusIn>", self._on_focus_in)
        self.bind("<FocusOut>", self._on_focus_out)
        self.bind("<Return>", self._on_key)
        self.bind("<space>", self._on_key)

    def set_text(self, text):
        if text != self._text:
            self._text = text
            self._draw()

    def set_style(self, fill, hover, fg=None, outline=None):
        self._fill, self._hover = fill, hover
        if fg is not None:
            self._fg = fg
        if outline is not None:
            self._outline = outline
        self._draw()

    def set_enabled(self, enabled: bool):
        if enabled != self._enabled:
            self._enabled = enabled
            self.configure(cursor="hand2" if enabled else "arrow")
            self._draw()

    def _on_enter(self, _e):
        self._hovering = True
        self._draw()

    def _on_leave(self, _e):
        self._hovering = False
        self._draw()

    def _on_focus_in(self, _e):
        self._focused = True
        self._draw()

    def _on_focus_out(self, _e):
        self._focused = False
        self._draw()

    def _on_key(self, _event=None):
        self._on_click(_event)
        return "break"

    def _on_click(self, _e):
        if self._enabled and self._command:
            self._command()

    def _draw(self):
        self.delete("all")
        w, h = self.winfo_width(), self.winfo_height()
        if w < 4 or h < 4:
            w, h = self.winfo_reqwidth(), self.winfo_reqheight()
        base = self._fill if self._enabled else T.DISABLED
        colour = self._hover if (self._hovering and self._enabled) else base
        border = (self._outline if self._enabled else T.BORDER) or ""
        round_rect(self, sc(1), sc(1), w - sc(1), h - sc(1), self._radius, fill=colour,
                   outline=border, width=1 if border else 0)
        if self._focused and self._enabled:  # anneau de focus visible au clavier
            round_rect(self, sc(2), sc(2), w - sc(2), h - sc(2), max(1, self._radius - sc(2)),
                       fill="", outline=T.ACCENT, width=2)
        self.create_text(w / 2, h / 2, text=self._text,
                         fill=self._fg if self._enabled else T.FAINT,
                         font=font(self._size, self._bold))


class Toggle(tk.Frame):
    """Interrupteur dessiné + libellé, et une aide ⓘ facultative.

    L'état se lit à la position et à la couleur du curseur (et au libellé, plus clair quand
    c'est activé) : le suffixe « · désactivé » répétait l'interrupteur (revue b-5). L'aide
    longue passe dans l'infobulle `hint`.
    """

    def __init__(self, master, label, value=False, command=None, parent_bg=None, hint: str = ""):
        parent_bg = T.BG if parent_bg is None else parent_bg
        super().__init__(master, bg=parent_bg)
        self.label, self.value, self.command = label, value, command
        self._bg = parent_bg
        self.switch = tk.Canvas(self, width=sc(34), height=sc(22), bg=parent_bg,
                                highlightthickness=0, bd=0, cursor="hand2", takefocus=1)
        self.switch.pack(side="left")
        self.text = tk.Label(self, text=label, bg=parent_bg, font=font(10), anchor="w",
                             cursor="hand2")
        self.text.pack(side="left", padx=(sc(8), 0))
        if hint:
            InfoIcon(self, hint, bg=parent_bg).pack(side="left", padx=(sc(6), 0))
        for widget in (self.switch, self.text):
            widget.bind("<Button-1>", self._click)
        self.switch.bind("<space>", self._click)
        self.switch.bind("<Return>", self._click)
        self.switch.bind("<FocusIn>", lambda _e: self._draw(True))
        self.switch.bind("<FocusOut>", lambda _e: self._draw(False))
        self._draw()

    def set_label(self, label):
        self.label = label
        self.text.configure(text=label)

    def set_value(self, value: bool) -> None:
        if bool(value) != bool(self.value):
            self.value = bool(value)
            self._draw()

    def _click(self, _e=None):
        self.value = not self.value
        self._draw()
        if self.command:
            self.command(self.value)
        return "break"

    def _draw(self, focused: bool = False):
        canvas = self.switch
        canvas.delete("all")
        h = sc(22)
        tw, th = sc(30), sc(16)
        y = h / 2
        round_rect(canvas, sc(2), y - th / 2, sc(2) + tw, y + th / 2, th / 2,
                   fill=T.ACCENT if self.value else T.TOGGLE_OFF,
                   outline=T.ACCENT_HI if focused else "", width=2 if focused else 0)
        cx = sc(2) + tw - th / 2 if self.value else sc(2) + th / 2
        canvas.create_oval(cx - th / 2 + sc(2), y - th / 2 + sc(2), cx + th / 2 - sc(2),
                           y + th / 2 - sc(2), fill=T.ON_ACCENT, outline="")
        self.text.configure(fg=T.TEXT if self.value else T.MUTED)


class Tile(tk.Canvas):
    """Tuile du rail : icône (image, pictogramme ou lettre) + libellé, état par une pastille.

    * `state` : état d'affichage (`presentation.badge_state`) dessiné en pastille colorée
      dans le coin ; None = pas de pastille (tuiles Ajouter / Réglages) ;
    * `compact` : icône seule (48 px), le libellé passe dans l'infobulle ;
    * le libellé trop long est **tronqué proprement** (« … ») et l'infobulle montre le nom
      complet — avant, Tk le coupait au bord (« lamaGUI (test », revue b-1).

    Un glisser-déposer facultatif (`drag`) est délégué au rail : la tuile ne fait que signaler
    le geste (seuil de 6 px pour ne pas confondre un clic avec un déplacement).
    """

    def __init__(self, master, glyph, label, command, selected=False, accent=None,
                 size=78, state=None, parent_bg=None, icon_kind=None, icon_img=None, drag=None,
                 compact=False, tip=None):
        accent = T.ACCENT if accent is None else accent
        parent_bg = T.RAIL if parent_bg is None else parent_bg
        self._compact = bool(compact)
        if self._compact:
            size = 56
        height = size if self._compact else size + 30
        super().__init__(master, width=sc(size), height=sc(height), bg=parent_bg,
                         highlightthickness=0, bd=0, cursor="hand2")
        self._side = sc(size)  # la peinture doit suivre le même côté que la géométrie
        self._glyph, self._label, self._command = glyph, label, command
        self._selected, self._accent, self._state = selected, accent, state
        self._icon_kind, self._icon_img = icon_kind, icon_img
        self._tip_extra = tip
        self._hovering = False
        self._ghost = False       # tuile en cours de déplacement
        self._pressed = False
        self._moved = False
        self._press_xy = (0, 0)
        self._drag = drag         # objet avec .start/.motion/.end(tuile, event), facultatif
        self._truncated = False
        self.bind("<ButtonPress-1>", self._press)
        self.bind("<B1-Motion>", self._motion)
        self.bind("<ButtonRelease-1>", self._release)
        self.bind("<Enter>", self._enter)
        self.bind("<Leave>", self._leave)
        self.bind("<Configure>", lambda e: self._draw())
        self.tooltip = Tooltip(self, self._tooltip_text)

    def _tooltip_text(self) -> str:
        """Nom complet (si tronqué ou en mode compact) + état, sur deux lignes."""
        extra = self._tip_extra() if callable(self._tip_extra) else (self._tip_extra or "")
        if self._compact or self._truncated:
            return f"{self._label}\n{extra}".strip() if extra else self._label
        return extra

    def set_state(self, selected=None, state=None, label=None, dot=None):
        changed = False
        if selected is not None and selected != self._selected:
            self._selected, changed = selected, True
        if state != self._state:
            self._state, changed = state, True
        if label is not None and label != self._label:
            self._label, changed = label, True
        if changed:
            self._draw()

    def set_dragging(self, flag: bool) -> None:
        if bool(flag) != self._ghost:
            self._ghost = bool(flag)
            self._draw()

    # --- gestes -----------------------------------------------------------
    def _press(self, event):
        self.tooltip.hide()
        self._pressed = True
        self._moved = False
        self._press_xy = (event.x_root, event.y_root)
        if self._drag:
            try:
                self._drag.start(self, event)
            except Exception:
                pass

    def _motion(self, event):
        if not self._pressed:
            return
        if not self._moved:
            x0, y0 = self._press_xy
            if abs(event.x_root - x0) + abs(event.y_root - y0) < 6:
                return
            self._moved = True
            self.set_dragging(True)
        if self._drag:
            try:
                self._drag.motion(self, event)
            except Exception:
                pass

    def _release(self, _event):
        moved = self._moved
        self._pressed = False
        self._moved = False
        if moved:
            self.set_dragging(False)
            if self._drag:
                try:
                    self._drag.end(self, _event)
                except Exception:
                    pass
            return
        if self._command:  # clic simple : sélection
            self._command()

    def _enter(self, _e):
        self._hovering = True
        self._draw()

    def _leave(self, _e):
        self._hovering = False
        self._draw()

    def _fit_label(self, width: int) -> str:
        """Libellé sur une ligne, tronqué avec « … » s'il dépasse la largeur."""
        metrics = tkfont.Font(font=font(8))
        text = self._label or ""
        self._truncated = False
        if metrics.measure(text) <= width:
            return text
        self._truncated = True
        while text and metrics.measure(text + "…") > width:
            text = text[:-1]
        return text.rstrip() + "…"

    def _draw(self):
        self.delete("all")
        size = self._side
        pad = sc(5) if self._compact else sc(6)
        box_y = pad
        box = size - 2 * pad
        highlight = self._selected or self._ghost
        fill = T.CARD_HI if (self._selected or self._hovering) else T.CARD
        outline = self._accent if highlight else T.BORDER
        extra = {"dash": (4, 3)} if self._ghost else {}
        round_rect(self, pad, box_y, size - pad, box_y + box, sc(10 if self._compact else 12),
                   fill=fill, outline=outline, width=2 if highlight else 1, **extra)
        cx, cy = size / 2, box_y + box / 2
        icon_px = sc(30) if self._compact else sc(42)
        if self._icon_img is not None:
            self.create_image(cx, cy, image=self._icon_img)
        elif self._icon_kind and draw_icon(self, self._icon_kind, cx, cy, icon_px, self._accent):
            pass
        else:
            self.create_text(cx, cy, text=self._glyph, fill=self._accent,
                             font=font(15 if self._compact else 19, True))
        if self._state:
            r = sc(5) if self._compact else sc(6)
            draw_badge(self, size - pad - r, box_y + r, r, self._state, ring=T.RAIL)
        if self._label and not self._compact:
            self.create_text(size / 2, size + sc(2), text=self._fit_label(size - sc(4)),
                             fill=T.TEXT if self._selected else T.MUTED, font=font(8), anchor="n")


# ── champs de formulaire ──────────────────────────────────────────────────────
class TextField(tk.Frame):
    """Champ texte sombre avec libellé au-dessus et aide en dessous."""

    def __init__(self, master, label, value="", hint="", width=40, bg=None, show=None,
                 action: tuple[str, object] | None = None):
        bg = T.BG if bg is None else bg
        super().__init__(master, bg=bg)
        # L'aide passe dans un ⓘ à côté du libellé (revue b-5) : le formulaire reste dense.
        label_with_info(self, label, hint, bg=bg).pack(fill="x")
        wrap = tk.Frame(self, bg=T.BORDER)
        wrap.pack(fill="x", pady=(sc(3), 0))
        self.var = tk.StringVar(value=str(value))
        self.entry = tk.Entry(wrap, textvariable=self.var, bg=T.CARD, fg=T.TEXT, bd=0,
                              highlightthickness=0, insertbackground=T.TEXT, font=font(11),
                              width=width, show=show)
        self.entry.pack(side="left", fill="x", expand=True, padx=1, pady=1, ipady=sc(5),
                        ipadx=sc(6))
        if action is not None:  # bouton « Parcourir… » collé au champ (chemin, exécutable)
            text, callback = action
            Button(wrap, text, callback, height=28, fill=T.CARD_HI,
                   hover=T.CARD_HOVER, fg=T.TEXT, radius=6, bold=False, size=9, outline="",
                   parent_bg=T.CARD).pack(side="right", padx=sc(3), pady=sc(3))

    def get(self) -> str:
        return self.var.get()

    def set(self, value) -> None:
        self.var.set(str(value))

    def focus_entry(self) -> None:
        try:
            self.entry.focus_set()
        except Exception:
            pass


class ChoiceField(tk.Frame):
    """Champ « choix » d'un formulaire : libellé + liste déroulante (même interface que
    TextField : get / set / focus_entry). Avant, un champ `choice` s'affichait en texte libre."""

    def __init__(self, master, label, choices, value="", hint="", bg=None):
        bg = T.BG if bg is None else bg
        super().__init__(master, bg=bg)
        label_with_info(self, label, hint, bg=bg).pack(fill="x")
        self.dropdown = Dropdown(self, choices, value=value, width=320, bg=bg)
        self.dropdown.pack(anchor="w", pady=(sc(3), 0))

    def get(self) -> str:
        return "" if self.dropdown.value is None else str(self.dropdown.value)

    def set(self, value) -> None:
        if any(key == value for key, _ in self.dropdown._choices):
            self.dropdown.value = value
            self.dropdown._draw()

    def focus_entry(self) -> None:
        try:
            self.dropdown.focus_set()
        except Exception:
            pass


class SelectRow(tk.Frame):
    """Choix unique présenté en rangée de boutons (langues, types de serveur)."""

    def __init__(self, master, options, value=None, command=None, bg=None, per_row=5):
        bg = T.BG if bg is None else bg
        super().__init__(master, bg=bg)
        self._command = command
        self._buttons: dict[str, Button] = {}
        self.value = value or (options[0][0] if options else None)
        for index, (key, label) in enumerate(options):
            btn = Button(self, label, lambda k=key: self.select(k), height=32,
                         fill=T.CARD_HI, hover=T.CARD_HOVER, fg=T.TEXT, radius=9, bold=False,
                         size=10, outline=T.BORDER, parent_bg=bg)
            btn.grid(row=index // per_row, column=index % per_row, padx=(0, sc(6)),
                     pady=(0, sc(6)), sticky="w")
            self._buttons[key] = btn
        self._paint()

    def select(self, key: str) -> None:
        self.value = key
        self._paint()
        if self._command:
            self._command(key)

    def _paint(self) -> None:
        for key, btn in self._buttons.items():
            if key == self.value:
                btn.set_style(T.CARD, T.CARD, fg=T.ACCENT, outline=T.BORDER_HI)
            else:
                btn.set_style(T.CARD_HI, T.CARD_HOVER, fg=T.TEXT, outline=T.BORDER)


class Dropdown(tk.Canvas):
    """Liste déroulante sombre : la valeur choisie + « ▾ », un menu au clic.

    Remplace les rangées de 4 à 5 gros boutons (langue, taille) des réglages (revue b-10).
    """

    def __init__(self, master, options, value=None, command=None, width=200, bg=None):
        bg = T.BG if bg is None else bg
        super().__init__(master, width=sc(width), height=sc(32), bg=bg, highlightthickness=0,
                         bd=0, cursor="hand2", takefocus=1)
        self._choices = list(options)
        self._command = command
        self.value = value if any(k == value for k, _ in self._choices) else (
            self._choices[0][0] if self._choices else None)
        self._hover = False
        self.bind("<Configure>", lambda _e: self._draw())
        self.bind("<Button-1>", self._open)
        self.bind("<Return>", self._open)
        self.bind("<space>", self._open)
        self.bind("<Enter>", lambda _e: self._set_hover(True))
        self.bind("<Leave>", lambda _e: self._set_hover(False))

    def _set_hover(self, flag: bool) -> None:
        self._hover = flag
        self._draw()

    def label(self) -> str:
        return next((label for key, label in self._choices if key == self.value), "")

    def _draw(self) -> None:
        self.delete("all")
        w, h = self.winfo_width(), self.winfo_height()
        if w < 4 or h < 4:
            w, h = self.winfo_reqwidth(), self.winfo_reqheight()
        round_rect(self, sc(1), sc(1), w - sc(1), h - sc(1), sc(8),
                   fill=T.CARD_HOVER if self._hover else T.CARD_HI, outline=T.BORDER, width=1)
        self.create_text(sc(12), h / 2, text=self.label(), anchor="w", fill=T.TEXT, font=font(10))
        self.create_text(w - sc(14), h / 2, text="▾", fill=T.MUTED, font=font(10))

    def _open(self, _event=None) -> str:
        menu = tk.Menu(self, tearoff=0, bg=T.CARD_HI, fg=T.TEXT, activebackground=T.ACCENT_DARK,
                       activeforeground=T.ON_ACCENT, bd=0, font=font(10))
        for key, label in self._choices:
            prefix = "✓  " if key == self.value else "     "
            menu.add_command(label=prefix + label, command=lambda k=key: self.select(k))
        try:
            menu.tk_popup(self.winfo_rootx(), self.winfo_rooty() + self.winfo_height())
        finally:
            menu.grab_release()
        return "break"

    def select(self, key) -> None:
        if key == self.value:
            return
        self.value = key
        self._draw()
        if self._command:
            self._command(key)


# ── journal ───────────────────────────────────────────────────────────────────
class LogView(tk.Text):
    def __init__(self, master, height=6):
        super().__init__(master, bg=T.CARD, fg=T.LOG_TEXT, bd=0, highlightthickness=0,
                         font=mono(9), wrap="word", height=height, insertbackground=T.MUTED,
                         state="disabled", padx=sc(8), pady=sc(6),
                         spacing1=sc(1), spacing3=sc(1))
        self.tag_configure("ts", foreground=T.FAINT)
        self.tag_configure("msg", foreground=T.LOG_TEXT)

    def append(self, stamp: str, message: str) -> None:
        # Quelqu'un qui a remonté lire une ligne ne doit pas être renvoyé au bas par le
        # tick suivant. Tk suit le curseur d'insertion : on mémorise la première ligne
        # visible (index, pas fraction) et on la remet en haut après l'insertion.
        at_bottom = self.yview()[1] >= 0.999
        top_index = self.index("@0,0")
        self.configure(state="normal")
        self.insert("end", stamp + "  ", "ts")
        self.insert("end", message + "\n", "msg")
        if at_bottom:
            self.see("end")
        else:
            self.yview(top_index)
        self.configure(state="disabled")


def __getattr__(name):
    """Compatibilité : `widgets.BG` (ancienne constante) lit la palette courante."""
    if name == "STATE_COLOURS" or name in T.palette.__dataclass_fields__ and name.isupper():
        return getattr(T, name)
    raise AttributeError(name)
