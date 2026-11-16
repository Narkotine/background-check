"""Interface Tk : rail de tuiles à gauche, une vue par fichier à droite.

Découpage du 16 nov 2026 (ancien `bgc/ui.py` de 1 750 lignes) :

* `bgc/core/` — ce qui ne dépend pas de Tk : `runtime` (une tuile vivante), `poller`
  (sondage), `actions` (marche/arrêt, boutons, menus) partagé par l'UI, le tray et le MCP ;
* `bgc/ui/common.py` — briques communes (boutons, titres, formulaires) ;
* `bgc/ui/rail.py`, `bgc/ui/logpanel.py` — rail et journal ;
* `bgc/ui/views/*.py` — une vue par fichier.

Les anciens noms restent accessibles ici (`ui.ServerView`, `ui.ServerRuntime`…).
"""

from bgc.core.runtime import ServerRuntime  # noqa: F401
from bgc.ui.common import (PHASE_COLOURS, ROW_COLOURS, TYPE_COLOURS, TYPE_GLYPHS,  # noqa: F401
                           TYPE_ICONS, IconField, _apply_preset_to, _collect_actions,
                           _collect_params, _pack_actions_editor, _pack_fields,
                           _pack_preset_buttons, _pick_path, _view_type, ghost_button,
                           section_title)
from bgc.ui.logpanel import LogPanel  # noqa: F401
from bgc.ui.rail import Rail  # noqa: F401
from bgc.ui.views.add import AddServerView  # noqa: F401
from bgc.ui.views.overview import OverviewView  # noqa: F401
from bgc.ui.views.server import ServerView  # noqa: F401
from bgc.ui.views.server_settings import ServerSettingsView  # noqa: F401
from bgc.ui.views.settings import GeneralSettingsView  # noqa: F401
from bgc.ui.views.welcome import WelcomeView  # noqa: F401
