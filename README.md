# Background Check (BGC)

> **Anciennement HermesServ** (renommé le 16 nov 2026 : l'app ne sert plus seulement à Hermes).
> Au premier lancement, les données de `%LOCALAPPDATA%\HermesServ` sont **copiées** dans
> `%LOCALAPPDATA%\BackgroundCheck` (l'ancien dossier reste intact) ; les plugins qui font
> `import hermesserv` marchent toujours.

Tableau de bord local pour **lancer, surveiller et arrêter** les services de cette machine : une
tuile par service dans une barre verticale à gauche, un panneau à droite, et un socle commun
(sondage, arrêt ciblé, journal, icône de zone de notification, serveur MCP pour un agent IA).

Le principe de base : **le statut est une preuve**, pas une supposition — port en écoute, API qui
répond, PID du processus. Un service lancé hors de l'application est adopté et affiché ; l'app
refuse d'arrêter ce qu'elle n'a pas lancé.

Chaque service affiche aussi **ce qu'il consomme** : CPU (part de la machine), RAM et VRAM de son
arbre de processus, mesurés toutes les 4 s — en colonnes dans la vue d'ensemble, avec les courbes
des 5 dernières minutes dans le détail, et via le MCP (`metrics`). La VRAM vient des compteurs
Windows « GPU Process Memory » (toute carte WDDM 2.x : AMD, NVIDIA, Intel) ; « — » s'ils manquent.

Le port de chaque tuile est **nommé** (« 9119 · 8642 » / « dashboard · gateway », « pont STT »,
« API »…). Les **serveurs liés** — ceux dont une tuile a besoin sans les piloter, comme le serveur
whisper d'OpenWhispr derrière le pont STT — apparaissent en sous-lignes sous leur tuile, avec état,
port, temps de marche et CPU / RAM / VRAM comptés à part (jamais deux fois). Le plugin les déclare
lui-même ; pour les autres, champ « Serveurs liés » des réglages de la tuile : `nom = cible ; …`
(cible : port, `hôte:port`, URL ou exécutable comme `ollama.exe`). Fenêtre étroite : la vue passe en
mode compact (ressources sous le nom) au lieu d'écraser la colonne des noms.

## Démarrer

| Voie | Commande |
|---|---|
| Exécutable (rien à installer) | `dist\BackgroundCheck.exe` |
| Depuis les sources | `BackgroundCheck.bat` (lanceur `pyw`, sinon `pythonw` du PATH) ou `py background_check.py` |
| Tests | `py -m pip install -r requirements-dev.txt` puis `py -m pytest` |
| Capture de la fenêtre (contrôle visuel) | `dist\BackgroundCheck.exe --shot out.png` |
| Cycle réel démarrer/vérifier/arrêter | `dist\BackgroundCheck.exe --selftest [id]` (Hermes par défaut) — **refuse** si la tuile ou un élément tourne déjà |

## Réglages

- Configuration : `%LOCALAPPDATA%\BackgroundCheck\config.json` — lisible et modifiable à la main,
  également éditable dans l'interface. Un fichier illisible n'est jamais écrasé : il est renommé
  `config.json.corrupt-<date>` et l'app propose la dernière sauvegarde `.bak`.
- Journal : `%LOCALAPPDATA%\BackgroundCheck\logs\bgc.log` (tournant, 5 fichiers d'1 Mo) —
  bouton « Ouvrir les journaux » dans les Réglages.
- Une seule instance par fichier de configuration : relancer l'app ramène la fenêtre ouverte.
- Variable `BGC_CONFIG=<chemin>` (l'ancien `HERMESSERV_CONFIG` est encore lu) : instance de test sur un autre fichier. Les harnais
  s'en servent pour ne jamais toucher la vraie configuration.
- Thème : Réglages > Thème — Sombre, Clair ou « Comme Windows » (suit le mode des applications
  de Windows, même app ouverte).
- Raccourcis : Ctrl+K (rechercher une tuile), Entrée (bouton principal d'un formulaire),
  Échap (retour) ; Ctrl+Alt+H (afficher la fenêtre de partout) s'active dans les Réglages.
- Plugins utilisateur : `%LOCALAPPDATA%\BackgroundCheck\plugins\<nom>\` (manifeste `plugin.json` +
  module). Un plugin exécute du code : n'installer que ce qu'on a vérifié.

## Brancher un agent IA (serveur MCP)

L'app embarque un serveur MCP sur `http://127.0.0.1:8781/mcp` (Réglages → « Serveur MCP »).
Depuis le 16 novembre 2026, **chaque requête doit présenter le jeton** affiché dans les Réglages
(`Authorization: Bearer <jeton>`) ; les requêtes venant d'un navigateur (en-tête `Origin`), visant
un autre `Host` que `127.0.0.1`/`localhost`, ou qui ne sont pas en `application/json` sont refusées.

Pour Hermes, le bouton **« Copier la config Hermes »** copie le bloc à coller sous `mcp_servers:`
dans `%LOCALAPPDATA%\hermes\config.yaml` :

```yaml
mcp_servers:
  bgc:
    url: http://127.0.0.1:8781/mcp
    headers:
      Authorization: Bearer <jeton>
```

puis `/reload-mcp` (ou redémarrage du gateway). **« Nouveau jeton »** invalide l'ancien
immédiatement : il faut alors mettre à jour le client. Un bloc déjà collé sous l'ancien nom
(`hermesserv:`) continue de marcher : seuls l'URL et le jeton comptent, et ils n'ont pas changé.

## Ce que contient le dossier

| Élément | Rôle |
|---|---|
| `background_check.py` | point d'entrée : fenêtre, rail, sondeur, icône de notification, pont MCP |
| `bgc/` | paquet : configuration, interface, widgets dessinés, plugins, serveur MCP |
| `bgc/plugins/` | types de service : Hermes, WSL, pont STT, « service surveillé » générique |
| `bgc/locales/` | une langue = un fichier JSON (`fr`, `en`, `es`, `pt-BR`, `zh`) |
| `captures/` | captures d'écran de contrôle (`apercu_*.png`) — le dépôt public n'en garde qu'une sélection ; les captures historiques citées dans `NOTES.md` / `ROADMAP.md` montraient des données personnelles et ne sont pas publiées |
| `dist/` | exécutable construit (PyInstaller, recette `BackgroundCheck.spec` ; icône `bgc.ico` générée par `tools/make_icon.py`) |
| `NOTES.md` | journal des constats vérifiés et des décisions (le plus récent en haut) |
| `ROADMAP.md` | vision, plans de travail et étapes cochées |
| `AMELIORATIONS.md` | analyse de novembre 2026 — **historique**, tout est réalisé |
| `IDEA.md` | la demande d'origine |
| `PLUS_TARD.md` | fonctions voisines notées pour plus tard (non développées) |
| `legacy/legacy_hermes_control_mono.py` | ancienne version mono-fichier, conservée comme référence (importée par personne) |
| `tests/` | tests automatiques (`py -m pytest`) — sans interface, sur fichiers temporaires |
| `LICENSE` | licence MIT |
| `requirements.txt` | dépendances d'exécution (`psutil`, `Pillow`) ; `requirements-dev.txt` ajoute `pytest` et PyInstaller |

## Ajouter un service

Trois voies, de la plus simple à la plus technique :

0. **Service détecté** : l'écran d'ajout liste ce qui écoute déjà sur ce PC (regroupé par
   programme, rapproché des modèles connus) ; au premier lancement, l'app le propose d'elle-même.
   « Tester maintenant » vérifie port, adresse, dossier et exécutable **sans rien lancer**.
1. **Modèle rapide** dans l'écran d'ajout (LM Studio, llama.cpp, Ollama, SillyTavern, Open WebUI…).
2. **Service surveillé** : une cible (`127.0.0.1:5451`, `5451`, `hôte:port` ou une URL complète) et,
   si utile, une commande de démarrage et/ou d'arrêt. Sans commande, la tuile surveille seulement.
3. **Types à éléments** : une tuile, plusieurs choses pilotables chacune par ses boutons.
   - **Conteneurs Docker / Podman** : un élément par conteneur (démarrer, arrêter, redémarrer,
     journaux, ouvrir le port publié), filtre par nom/image. Sans Docker ni Podman, ou moteur
     arrêté, la tuile le dit simplement.
   - **WSL** avec la distribution laissée vide : chaque distribution démarrable / arrêtable (une
     distribution déjà lancée hors de l'app n'est arrêtée que si l'option le permet). Avec un nom
     de distribution, la tuile dédiée est inchangée.
   - **Service Windows** : un nom de service (ex. `Spooler`) ou plusieurs séparés par des
     virgules ; démarrer / arrêter, et la fenêtre UAC n'apparaît que si Windows refuse sans droits
     administrateur.
4. **Plugin** : un dossier dans le dossier des plugins, pour un type de service réutilisable.
   `snapshot()` peut publier `items: [{id, label, phase, rows, actions}]` et le contrôleur
   `item_action(item_id, action_id)` ; les actions marquées `"main": True` sont celles du
   menu tray et de la vue d'ensemble (contrat détaillé dans `bgc/service.py`).

## Repères pour intervenir dans le code

- Le fil d'affichage ne fait que peindre : tout sondage vit dans un fil par tuile, avec des caches.
- Aucun libellé n'est codé dans un plugin ni dans le journal : ce sont des clés de traduction
  (`bgc/locales/`), donc l'historique suit la langue choisie.
- Les éléments dessinés (cartes, boutons, tuiles, barres) sont dans `bgc/widgets.py` ; toutes les
  couleurs passent par l'objet `T` de `bgc/theme.py` (palettes sombre et claire) — jamais de
  code hexadécimal dans une vue (un test le vérifie).
- Cœur sans Tk, partagé par l'interface, le tray et le serveur MCP : `bgc/core/` (`runtime.py`
  état d'une tuile, `poller.py` sondeur, `actions.py` exécution des actions et menus).
- Logique d'affichage sans Tk (testable) : `presentation.py` (états, libellés, éléments, services
  détectés, menu tray) et `discovery.py` ; vues : une par fichier dans `bgc/ui/views/`
  (`overview`, `server`, `settings`, `server_settings`, `add`, `welcome`).
- Ressources par service : `bgc/core/metrics.py` (fil d'échantillonnage, 4 s) et `bgc/gpu.py`
  (VRAM par PDH) ; les PID viennent de `meta.pid` / `meta.pids` de l'instantané et du `pid` des
  éléments — un nouveau type n'a rien d'autre à faire pour être mesuré.
- Serveurs liés : `bgc/core/deps.py` (déclaration plugin `Controller.dependencies()` + champ
  `deps` de la tuile, sondage dans le fil de la tuile → `snapshot["deps"]`). Mesures sous la clé
  « tuile/dep:<id> » ; leur arbre de processus est retiré de celui de la tuile. Rôle des ports :
  `meta.ports = [{port, role_key}]` dans l'instantané. MCP : `dependencies` dans `list_items`,
  `get_server` et `list_servers` ; `update_server(deps=…)`.
- Nom affiché de l'app : clé `app.title` (jamais en dur dans un nouveau texte).
- Vérification visuelle : `--shot` photographie **la fenêtre** (pas l'écran), ce qui évite de
  capturer par-dessus.

## Licence

MIT — voir [`LICENSE`](LICENSE). © 2026 Narkotine.
