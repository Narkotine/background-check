"""Fermeture du tray : un WM_CLOSE venu de l'extérieur ferme l'app, pas seulement l'icône."""
from bgc import tray


class _Fake:
    """Remplace user32/shell32 : enregistre les appels, renvoie 1."""

    def __init__(self):
        self.calls = []

    def __getattr__(self, name):
        def call(*args):
            self.calls.append(name)
            return 1
        return call


def _icon():
    icon = tray.TrayIcon.__new__(tray.TrayIcon)   # sans Win32 : on teste la logique seule
    icon.actions = []
    icon.on_action = icon.actions.append
    icon._user32, icon._shell32 = _Fake(), _Fake()
    icon._lock = __import__("threading").Lock()
    icon._stopping = False
    icon._taskbar_created = 0xC0DE
    icon._hwnd, icon._hicon, icon.ok, icon.hotkey_ok = 42, None, True, False
    icon.tooltip = "BGC"
    return icon


def test_external_close_quits_the_app_instead_of_orphaning_it():
    icon = _icon()
    assert icon._wndproc(42, tray.WM_CLOSE, 0, 0) == 0
    assert icon.actions == ["quit"]
    assert "DestroyWindow" not in icon._user32.calls   # l'icône reste jusqu'à la vraie sortie
    assert icon.alive


def test_close_after_stop_destroys_window_and_icon():
    icon = _icon()
    icon.stop()
    assert not icon.alive and icon.ok is False
    assert "Shell_NotifyIconW" in icon._shell32.calls   # NIM_DELETE
    icon._wndproc(42, tray.WM_CLOSE, 0, 0)
    assert icon.actions == []
    assert "DestroyWindow" in icon._user32.calls


def test_end_of_session_quits_cleanly():
    icon = _icon()
    assert icon._wndproc(42, tray.WM_QUERYENDSESSION, 0, 0) == 1
    icon._wndproc(42, tray.WM_ENDSESSION, 1, 0)
    assert icon.actions == ["quit"]


def test_explorer_restart_readds_icon():
    icon = _icon()
    icon._wndproc(42, 0xC0DE, 0, 0)
    assert icon._shell32.calls == ["Shell_NotifyIconW"]


def test_destroy_never_leaves_a_ghost_icon():
    icon = _icon()
    icon._wndproc(42, tray.WM_DESTROY, 0, 0)
    assert icon._shell32.calls == ["Shell_NotifyIconW"] and icon.ok is False
    assert "PostQuitMessage" in icon._user32.calls


def test_tick_stops_after_quit_from_tray():
    """« Quitter » du tray arrive par _drain() : _tick ne doit plus peindre une fenêtre
    détruite ni se reprogrammer (« invalid command name » au journal, 16 nov 2026)."""
    import background_check as bc

    class Stub:
        _quitting = False
        painted = scheduled = 0

        def _drain(self):
            self._quitting = True   # le tray a demandé « quitter » → fenêtre détruite

        def _paint(self):
            Stub.painted += 1

        class root:
            @staticmethod
            def after(*_a):
                Stub.scheduled += 1

    bc.App._tick(Stub())
    assert Stub.painted == 0 and Stub.scheduled == 0
    stub = Stub()
    stub._quitting = True
    bc.App._paint(stub)   # garde-fou : ne touche à rien
