"""Contrat de plugin (« type de serveur »).

Un plugin déclare :
  * son identité (`id`, clés de traduction du libellé et de la description) ;
  * ses champs de réglages (`Field`) — l'UI génère le formulaire, aucune IHM à écrire par plugin ;
  * ses interrupteurs (`Toggle`) — options propres au serveur, stockées dans `server["options"]` ;
  * un contrôleur (`Controller`) qui sonde et agit.

Le contrôleur ne produit **jamais de texte à afficher** : il renvoie des clés de traduction et
leurs paramètres (`label_key` + `label_params`). C'est ce qui permet de traduire l'app entière
sans jamais toucher au code d'un plugin.

Ajouter un type = écrire un module dans `plugins/`, l'enregistrer dans `plugins/__init__.py`.

Instantané (`Controller.snapshot()`) — clés comprises par l'UI, le tray et le MCP :
  * `phase`, `rows`, `actions`, `toggles`, `url`, `meta`, `extra` (voir les plugins intégrés) ;
  * une action peut porter `"main": True` (bascule principale : tray, vue d'ensemble) et
    `"stops": True/False` (elle arrête quelque chose : pas d'alerte de chute ensuite) ;
  * **éléments** (modèle « liste d'items », 16 nov 2026) — facultatif :
    `items: [{id, label, phase, rows, actions, detail_key?, detail_params?}]`. Une tuile
    Docker a un élément par conteneur, une tuile WSL un par distribution. Les actions d'un
    élément arrivent dans `Controller.item_action(item_id, action_id)`. Un type sans
    `items` est vu comme **un seul élément implicite** (`presentation.items_of`) : les types
    existants n'ont rien à changer ;
  * `extra.chain` (facultatif) : `{"lines": [{title_key, links, used, note_key?}],
    "missing": [...]}` — chaîne de maillons affichée telle quelle (Hermes s'en sert).
"""

from __future__ import annotations

from dataclasses import dataclass, field as dc_field

# phases normalisées, comprises par l'UI et par l'icône de notification
PHASE_RUNNING = "running"
PHASE_STARTING = "starting"
PHASE_STOPPED = "stopped"
PHASE_UNKNOWN = "unknown"

# statuts d'une ligne de diagnostic
ROW_OK, ROW_WARN, ROW_IDLE, ROW_BAD = "ok", "warn", "idle", "bad"


@dataclass
class Field:
    """Un champ de réglage : type, défaut, validation minimale."""
    key: str
    label_key: str
    kind: str = "text"          # text | int | port | path | url | choice
    default: object = ""
    required: bool = False
    hint_key: str | None = None
    choices: list[tuple[str, str]] = dc_field(default_factory=list)  # (valeur, clé de libellé)
    # Regroupement visuel facultatif : les champs consécutifs qui partagent cette clé sont
    # placés sous un titre explicite (`ui` insère le titre + son aide `<clé>_hint`).
    section_key: str | None = None
    # Bouton « Parcourir… » collé au champ : "dir" (dossier) ou "exe" (programme/script).
    browse: str | None = None

    def coerce(self, raw) -> object:
        if self.kind in ("int", "port"):
            try:
                value = int(str(raw).strip())
            except (TypeError, ValueError):
                raise ValueError(self.label_key)
            if self.kind == "port" and not (1 <= value <= 65535):
                raise ValueError(self.label_key)
            return value
        text = "" if raw is None else str(raw).strip()
        if self.required and not text:
            raise ValueError(self.label_key)
        return text


@dataclass
class Toggle:
    """Interrupteur propre au serveur (stocké dans `options`)."""
    key: str
    label_key: str
    default: bool = False


class Controller:
    """Base des contrôleurs : un par serveur vivant, créé par le plugin."""

    def __init__(self, server: dict, log):
        self.server = server
        self.params = dict(server.get("params") or {})
        self.options = dict(server.get("options") or {})
        self._log = log  # log(cle_traduction, **parametres)

    # --- sondage ----------------------------------------------------------
    def snapshot(self) -> dict:
        """Vue prête à peindre : phase, entête, lignes, actions, méta."""
        raise NotImplementedError

    # --- actions ----------------------------------------------------------
    def start(self) -> str:
        return "log.not_running"

    def stop(self) -> str:
        return "log.not_running"

    def action(self, action_id: str) -> str:
        return "log.not_running"

    def item_action(self, item_id: str, action_id: str) -> str:
        """Action sur un élément (conteneur, distribution…) ; voir le modèle « items »."""
        return "log.not_running"

    def dispose(self) -> None:
        """Libère ce qui doit l'être à la suppression du serveur."""
        return None

    # --- aides ------------------------------------------------------------
    def set_option(self, key: str, value: bool) -> None:
        self.options[key] = bool(value)

    @staticmethod
    def row(status: str, label_key: str, value_key: str,
            label_params: dict | None = None, value_params: dict | None = None) -> dict:
        return {"status": status, "label_key": label_key, "label_params": label_params or {},
                "value_key": value_key, "value_params": value_params or {}}


class ServerType:
    """Description d'un type de serveur (une instance par plugin)."""

    id = "base"
    label_key = "type.base.label"
    desc_key = "type.base.desc"
    fields: list[Field] = []
    toggles: list[Toggle] = []
    supports_start_stop = True

    # Facultatif : un type qui n'est pas un process (distro WSL, conteneur…) fournit ses
    # propres libellés d'état, sinon l'UI utilise les génériques (« En ligne », « Arrêté »).
    phase_labels: dict = {}                      # {phase: clé de traduction}
    external_label_key: str = "status.external"  # état d'un service lancé hors de l'app
    phase_keys: dict = {}                        # {phase: clé de journal des transitions}

    def default_params(self) -> dict:
        return {f.key: f.default for f in self.fields}

    def summary(self, params: dict) -> tuple[str, dict] | None:
        """Phrase « que fait cet onglet ? » : (clé de traduction, paramètres) ou None.

        C'est le type qui parle — il connaît le sens réel de la combinaison de ses champs.
        L'UI affiche une ligne discrète sous le titre du serveur (chantier 2, 9 nov 2026).
        """
        return None

    #: Champs fusionnés en **une seule ligne « cible »** dans les formulaires (facultatif).
    #: Le fichier de configuration, lui, ne change pas : on écrit toujours host/port/check_url.
    TARGET_FIELDS: tuple[str, ...] | None = None

    def target_text(self, params: dict) -> str:
        """La cible en une ligne (« hôte:port/chemin »), pour les types qui en déclarent une."""
        return ""

    def parse_target(self, text: str, base: dict) -> dict:
        """Décompose la ligne de cible en champs réels ; `base` porte les autres champs."""
        return dict(base)

    def validate(self, raw: dict) -> dict:
        """Coerce et valide les paramètres ; lève ValueError(label_key) si invalide."""
        cleaned = {}
        for f in self.fields:
            value = raw.get(f.key, f.default)
            if value == "" and not f.required:
                cleaned[f.key] = "" if f.kind not in ("int", "port") else f.default
                continue
            cleaned[f.key] = f.coerce(value)
        return cleaned

    def default_options(self) -> dict:
        return {t.key: t.default for t in self.toggles}

    def controller(self, server: dict, log) -> Controller:
        raise NotImplementedError


_REGISTRY: dict[str, ServerType] = {}
#: Propriétaire de chaque type : None = intégré à l'app, sinon le dossier du plugin.
_OWNERS: dict[str, str | None] = {}
#: Plugin en cours de chargement (posé par `plugin_loader`) — None hors chargement.
CURRENT_OWNER: str | None = None


def register(server_type: ServerType) -> ServerType:
    """Enregistre un type. Un plugin ne remplace **jamais** un type intégré ni celui d'un
    autre plugin (revue du 16 nov 2026 : `register()` contournait le contrôle) ; il peut
    seulement se remplacer lui-même (bouton « Recharger les plugins »)."""
    owner = CURRENT_OWNER
    type_id = server_type.id
    if type_id in _REGISTRY and owner is not None and _OWNERS.get(type_id) != owner:
        taken_by = _OWNERS.get(type_id)
        raise ValueError(f"identifiant déjà pris : {type_id} "
                         f"({'type intégré' if taken_by is None else 'plugin ' + taken_by})")
    _REGISTRY[type_id] = server_type
    _OWNERS[type_id] = owner
    return server_type


def get(type_id: str) -> ServerType | None:
    return _REGISTRY.get(type_id)


def all_types() -> list[ServerType]:
    return list(_REGISTRY.values())
