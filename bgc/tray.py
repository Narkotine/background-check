"""Icône de la zone de notification (Win32 natif via ctypes, sans pystray).

Déplacé tel quel depuis l'ancienne version mono-fichier : la fenêtre de réception et sa boucle
de messages vivent dans un thread dédié ; les actions remontent par un appel de rappel que l'app
remet dans sa file Tk (aucun widget touché hors du fil d'affichage).
"""

from __future__ import annotations

import ctypes
import os
import threading
from ctypes import wintypes

NIM_ADD, NIM_MODIFY, NIM_DELETE = 0, 1, 2
NIF_MESSAGE, NIF_ICON, NIF_TIP = 0x01, 0x02, 0x04
NIF_INFO = 0x10
NIIF_INFO, NIIF_WARNING = 0x00000001, 0x00000002
WM_TRAYICON = 0x8000 + 1
WM_SHOW_REQUEST = 0x8000 + 2   # posté par une seconde instance (voir `instance.py`)
WM_DESTROY, WM_NULL, WM_CLOSE = 0x0002, 0x0000, 0x0010
WM_LBUTTONUP, WM_LBUTTONDBLCLK, WM_RBUTTONUP = 0x0202, 0x0203, 0x0205
WM_CONTEXTMENU = 0x007B
WM_QUERYENDSESSION, WM_ENDSESSION = 0x0011, 0x0016
IMAGE_ICON, LR_LOADFROMFILE, LR_DEFAULTSIZE = 1, 0x10, 0x40
MF_STRING, MF_SEPARATOR, MF_GRAYED, MF_POPUP = 0x0000, 0x0800, 0x0001, 0x0010
WM_HOTKEY = 0x0312
WM_SET_HOTKEY = 0x8000 + 3     # wParam = 1 pour enregistrer, 0 pour libérer (fil du tray)
MOD_ALT, MOD_CONTROL, MOD_NOREPEAT = 0x0001, 0x0002, 0x4000
HOTKEY_ID, HOTKEY_VK = 0x4853, ord("H")   # Ctrl+Alt+H
TPM_RIGHTBUTTON, TPM_RETURNCMD, TPM_NONOTIFY = 0x0002, 0x0100, 0x0080


class _GUID(ctypes.Structure):
    _fields_ = [("Data1", wintypes.DWORD), ("Data2", wintypes.WORD),
                ("Data3", wintypes.WORD), ("Data4", ctypes.c_ubyte * 8)]


class _NOTIFYICONDATAW(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD), ("hWnd", wintypes.HWND), ("uID", wintypes.UINT),
        ("uFlags", wintypes.UINT), ("uCallbackMessage", wintypes.UINT),
        ("hIcon", wintypes.HICON), ("szTip", wintypes.WCHAR * 128),
        ("dwState", wintypes.DWORD), ("dwStateMask", wintypes.DWORD),
        ("szInfo", wintypes.WCHAR * 256), ("uVersion", wintypes.UINT),
        ("szInfoTitle", wintypes.WCHAR * 64), ("dwInfoFlags", wintypes.DWORD),
        ("guidItem", _GUID), ("hBalloonIcon", wintypes.HICON),
    ]


class _WNDCLASSW(ctypes.Structure):
    _fields_ = [
        ("style", wintypes.UINT), ("lpfnWndProc", ctypes.c_void_p),
        ("cbClsExtra", ctypes.c_int), ("cbWndExtra", ctypes.c_int),
        ("hInstance", wintypes.HINSTANCE), ("hIcon", wintypes.HICON),
        ("hCursor", wintypes.HANDLE), ("hbrBackground", wintypes.HBRUSH),
        ("lpszMenuName", wintypes.LPCWSTR), ("lpszClassName", wintypes.LPCWSTR),
    ]


class TrayIcon:
    """Clic gauche = on_action("show"), clic droit = menu construit par `menu_provider`.

    `menu_provider()` renvoie des entrées `(libellé, action, actif)` ; `("-", None, False)`
    est un séparateur et une entrée dont l'action est une **liste** d'entrées devient un
    sous-menu (un par service, plus « Autres (n) » au-delà de la limite).
    """

    MENU_BASE = 1001

    def __init__(self, icon_path: str, tooltip: str, on_action, menu_provider,
                 window_title: str = "BackgroundCheckTray"):
        self.icon_path = icon_path
        self.window_title = window_title
        self.tooltip = (tooltip or "BGC")[:120]
        self.on_action = on_action
        self.menu_provider = menu_provider
        self.ok = False
        self.error = ""
        self.hotkey_ok = False
        self._hotkey_wanted = False
        # Posé par stop() : le WM_CLOSE qui suit vient de l'app elle-même. Sans lui, un
        # WM_CLOSE venu d'ailleurs (fin de session, script, Explorateur) détruisait la seule
        # fenêtre du tray et laissait l'app tourner sans icône (bug du 16 nov 2026).
        self._stopping = False
        self._taskbar_created = 0
        self._hwnd = None
        self._hicon = None
        self._ready = threading.Event()
        self._lock = threading.Lock()
        self._thread = threading.Thread(target=self._run, name="tray", daemon=True)
        self._user32 = ctypes.WinDLL("user32", use_last_error=True)
        self._shell32 = ctypes.WinDLL("shell32", use_last_error=True)
        self._kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        self._declare()

    def _declare(self) -> None:
        u = self._user32
        u.DefWindowProcW.restype = ctypes.c_ssize_t
        u.DefWindowProcW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
        u.CreateWindowExW.restype = wintypes.HWND
        u.CreateWindowExW.argtypes = [wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR,
                                      wintypes.DWORD, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                      ctypes.c_int, wintypes.HWND, wintypes.HMENU,
                                      wintypes.HINSTANCE, ctypes.c_void_p]
        u.RegisterClassW.restype = wintypes.ATOM
        u.RegisterClassW.argtypes = [ctypes.POINTER(_WNDCLASSW)]
        u.LoadImageW.restype = wintypes.HANDLE
        u.LoadImageW.argtypes = [wintypes.HINSTANCE, wintypes.LPCWSTR, wintypes.UINT,
                                 ctypes.c_int, ctypes.c_int, wintypes.UINT]
        u.CreatePopupMenu.restype = wintypes.HMENU
        u.AppendMenuW.restype = wintypes.BOOL
        u.AppendMenuW.argtypes = [wintypes.HMENU, wintypes.UINT, ctypes.c_size_t, wintypes.LPCWSTR]
        u.TrackPopupMenu.restype = ctypes.c_int
        u.TrackPopupMenu.argtypes = [wintypes.HMENU, wintypes.UINT, ctypes.c_int, ctypes.c_int,
                                     ctypes.c_int, wintypes.HWND, ctypes.c_void_p]
        u.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
        u.DestroyWindow.restype = wintypes.BOOL
        u.DestroyWindow.argtypes = [wintypes.HWND]
        u.SetForegroundWindow.restype = wintypes.BOOL
        u.SetForegroundWindow.argtypes = [wintypes.HWND]
        u.GetCursorPos.restype = wintypes.BOOL
        u.GetCursorPos.argtypes = [ctypes.POINTER(wintypes.POINT)]
        u.DestroyMenu.restype = wintypes.BOOL
        u.DestroyMenu.argtypes = [wintypes.HMENU]
        u.LoadIconW.restype = wintypes.HICON
        u.LoadIconW.argtypes = [wintypes.HINSTANCE, wintypes.LPCWSTR]
        u.GetMessageW.restype = ctypes.c_int
        u.GetMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND,
                                  wintypes.UINT, wintypes.UINT]
        u.TranslateMessage.argtypes = [ctypes.POINTER(wintypes.MSG)]
        u.DispatchMessageW.restype = ctypes.c_ssize_t
        u.DispatchMessageW.argtypes = [ctypes.POINTER(wintypes.MSG)]
        u.RegisterHotKey.restype = wintypes.BOOL
        u.RegisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int, wintypes.UINT, wintypes.UINT]
        u.UnregisterHotKey.restype = wintypes.BOOL
        u.UnregisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int]
        u.RegisterWindowMessageW.restype = wintypes.UINT
        u.RegisterWindowMessageW.argtypes = [wintypes.LPCWSTR]
        self._shell32.Shell_NotifyIconW.restype = wintypes.BOOL
        self._shell32.Shell_NotifyIconW.argtypes = [wintypes.DWORD,
                                                    ctypes.POINTER(_NOTIFYICONDATAW)]

    # --- cycle de vie ------------------------------------------------------
    def start(self, wait: float = 4.0) -> bool:
        self._thread.start()
        self._ready.wait(wait)
        return self.ok

    def stop(self) -> None:
        with self._lock:
            self._stopping = True
            if self._hwnd:
                self._notify(NIM_DELETE)
                self.ok = False
                self._user32.PostMessageW(self._hwnd, WM_CLOSE, 0, 0)

    @property
    def alive(self) -> bool:
        """L'icône est en place et sa fenêtre existe (sinon « cacher » perdrait la fenêtre)."""
        return bool(self.ok and self._hwnd and not self._stopping)

    def set_hotkey(self, enabled: bool) -> None:
        """Active/désactive Ctrl+Alt+H ; l'enregistrement se fait dans le fil du tray."""
        self._hotkey_wanted = bool(enabled)
        with self._lock:
            if self._hwnd:
                self._user32.PostMessageW(self._hwnd, WM_SET_HOTKEY, 1 if enabled else 0, 0)

    def _apply_hotkey(self, hwnd, enabled: bool) -> None:
        if self.hotkey_ok:
            self._user32.UnregisterHotKey(hwnd, HOTKEY_ID)
            self.hotkey_ok = False
        if enabled:
            self.hotkey_ok = bool(self._user32.RegisterHotKey(
                hwnd, HOTKEY_ID, MOD_CONTROL | MOD_ALT | MOD_NOREPEAT, HOTKEY_VK))

    # --- shell / icône -----------------------------------------------------
    def _notify(self, message: int, flags: int = NIF_MESSAGE | NIF_ICON | NIF_TIP) -> bool:
        data = _NOTIFYICONDATAW()
        data.cbSize = ctypes.sizeof(_NOTIFYICONDATAW)
        data.hWnd = self._hwnd
        data.uID = 1
        data.uFlags = flags
        data.uCallbackMessage = WM_TRAYICON
        data.hIcon = self._hicon
        data.szTip = self.tooltip
        return bool(self._shell32.Shell_NotifyIconW(message, ctypes.byref(data)))

    def balloon(self, title: str, text: str, warning: bool = False) -> bool:
        with self._lock:
            if not self.ok:
                return False
            data = _NOTIFYICONDATAW()
            data.cbSize = ctypes.sizeof(_NOTIFYICONDATAW)
            data.hWnd = self._hwnd
            data.uID = 1
            data.uFlags = NIF_INFO
            data.szInfoTitle = (title or "")[:60]
            data.szInfo = (text or "")[:250]
            data.dwInfoFlags = NIIF_WARNING if warning else NIIF_INFO
            return bool(self._shell32.Shell_NotifyIconW(NIM_MODIFY, ctypes.byref(data)))

    def set_tooltip(self, text: str) -> None:
        text = (text or "")[:120]
        with self._lock:
            if text == self.tooltip or not self.ok:
                return
            self.tooltip = text
            self._notify(NIM_MODIFY, NIF_TIP)

    def _load_icon(self):
        icon = self._user32.LoadImageW(None, self.icon_path, IMAGE_ICON, 0, 0,
                                       LR_LOADFROMFILE | LR_DEFAULTSIZE)
        if not icon:
            icon = self._user32.LoadIconW(None, ctypes.cast(32512, wintypes.LPCWSTR))
        return icon

    # --- thread de messages ------------------------------------------------
    def _run(self) -> None:
        try:
            self._hicon = self._load_icon()
            class_name = f"BackgroundCheckTray{os.getpid()}_{id(self)}"
            self._wndproc_ref = ctypes.WINFUNCTYPE(
                ctypes.c_ssize_t, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM
            )(self._wndproc)
            wc = _WNDCLASSW()
            wc.lpfnWndProc = ctypes.cast(self._wndproc_ref, ctypes.c_void_p)
            wc.hInstance = self._kernel32.GetModuleHandleW(None)
            wc.lpszClassName = class_name
            if not self._user32.RegisterClassW(ctypes.byref(wc)):
                raise OSError(f"RegisterClass a échoué (erreur {ctypes.get_last_error()})")
            self._hwnd = self._user32.CreateWindowExW(0, class_name, self.window_title, 0, 0, 0, 0, 0,
                                                      None, None, wc.hInstance, None)
            if not self._hwnd:
                raise OSError(f"CreateWindowEx a échoué (erreur {ctypes.get_last_error()})")
            # Explorateur redémarré : il diffuse « TaskbarCreated », on remet l'icône.
            self._taskbar_created = self._user32.RegisterWindowMessageW("TaskbarCreated")
            self.ok = self._notify(NIM_ADD)
            if not self.ok:
                raise OSError(f"Shell_NotifyIcon a échoué (erreur {ctypes.get_last_error()})")
        except Exception as exc:  # pragma: no cover
            self.error = str(exc)
            self.ok = False
            self._ready.set()
            return
        self._ready.set()
        if self._hotkey_wanted:
            self._apply_hotkey(self._hwnd, True)

        msg = wintypes.MSG()
        while self._user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            self._user32.TranslateMessage(ctypes.byref(msg))
            self._user32.DispatchMessageW(ctypes.byref(msg))

    def _wndproc(self, hwnd, message, wparam, lparam):
        if message == WM_TRAYICON:
            event = lparam & 0xFFFF
            if event in (WM_LBUTTONUP, WM_LBUTTONDBLCLK):
                self.on_action("show")
            elif event in (WM_RBUTTONUP, WM_CONTEXTMENU):
                self._popup()
            return 0
        if message == WM_SHOW_REQUEST:
            self.on_action("show")
            return 0
        if message == WM_HOTKEY and wparam == HOTKEY_ID:
            self.on_action("show")
            return 0
        if message == WM_SET_HOTKEY:
            self._apply_hotkey(hwnd, bool(wparam))
            return 0
        if self._taskbar_created and message == self._taskbar_created:
            if not self._stopping:
                self.ok = self._notify(NIM_ADD)
            return 0
        if message == WM_QUERYENDSESSION:
            return 1  # fin de session : on accepte, la fermeture propre suit
        if message == WM_ENDSESSION:
            if wparam and not self._stopping:
                self.on_action("quit")
            return 0
        if message == WM_CLOSE:
            if not self._stopping:
                # Fermeture demandée de l'extérieur : c'est l'app entière qu'on ferme
                # proprement (elle rappellera stop(), et ce WM_CLOSE-là détruira la fenêtre).
                self.on_action("quit")
                return 0
            if self.hotkey_ok:
                self._user32.UnregisterHotKey(hwnd, HOTKEY_ID)
                self.hotkey_ok = False
            self._user32.DestroyWindow(hwnd)
            return 0
        if message == WM_DESTROY:
            if self.ok:  # jamais d'icône fantôme dans la zone de notification
                self._notify(NIM_DELETE)
                self.ok = False
            self._user32.PostQuitMessage(0)
            return 0
        return self._user32.DefWindowProcW(hwnd, message, wparam, lparam)

    def _fill_menu(self, menu, entries, mapping: dict, counter: list) -> None:
        for label, action, enabled in entries or []:
            if label == "-":
                self._user32.AppendMenuW(menu, MF_SEPARATOR, 0, None)
                continue
            if isinstance(action, (list, tuple)):
                sub = self._user32.CreatePopupMenu()
                if not sub:
                    continue
                self._fill_menu(sub, action, mapping, counter)
                flags = MF_POPUP if enabled else MF_POPUP | MF_GRAYED
                self._user32.AppendMenuW(menu, flags, sub, label)
                continue  # le sous-menu est détruit avec son parent
            ident = self.MENU_BASE + counter[0]
            counter[0] += 1
            flags = MF_STRING if action and enabled else MF_STRING | MF_GRAYED
            self._user32.AppendMenuW(menu, flags, ident, label)
            if action and enabled:
                mapping[ident] = action

    def _popup(self) -> None:
        menu = self._user32.CreatePopupMenu()
        if not menu:
            return
        mapping: dict = {}
        self._fill_menu(menu, self.menu_provider() or [], mapping, [0])
        pt = wintypes.POINT()
        self._user32.GetCursorPos(ctypes.byref(pt))
        self._user32.SetForegroundWindow(self._hwnd)
        chosen = self._user32.TrackPopupMenu(
            menu, TPM_RIGHTBUTTON | TPM_RETURNCMD | TPM_NONOTIFY, pt.x, pt.y, 0, self._hwnd, None)
        self._user32.DestroyMenu(menu)
        self._user32.PostMessageW(self._hwnd, WM_NULL, 0, 0)
        action = mapping.get(chosen)
        if action:
            self.on_action(action)
