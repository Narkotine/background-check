# Background Check (ex-HermesServ) — notes de projet

Journal des constats techniques et des décisions, pour ne pas refaire le raisonnement
deux fois. Dernière mise à jour : **16 novembre 2026** (v2.4.0).

Les captures citées dans ce journal (`apercu_*.png`) sont rangées dans `captures/` depuis le
11 novembre 2026 ; les chemins historiques ci-dessous s'entendent donc relativement à ce dossier.

## Ressources par service : CPU / RAM / VRAM (16 nov 2026, après-midi)

**Demande** : « VRAM et RAM par service, voire CPU » (item 2 de `PLUS_TARD.md`).

- **Où** : `bgc/core/metrics.py` (échantillonneur), `bgc/gpu.py` (VRAM). Un fil `bgc-metrics`
  mesure toutes les **4 s** (jamais le fil Tk) et publie un instantané partagé ; l'interface et le
  MCP ne font que lire. Historique : 75 points (5 min) par tuile / élément.
- **Quels processus** : ceux que les sondes trouvent déjà — `meta.pid` (et `meta.pids` pour une
  tuile multi-éléments), `pid` d'un élément (services Windows) — **plus tous leurs enfants**
  (`children(recursive=True)`). Exception : un PID égal à celui de BGC (pont STT intégré) est
  mesuré seul, sans ses enfants (ce sont les autres services). Docker : pas de PID côté Windows
  (les conteneurs vivent dans la VM WSL) → « — ». Un service Windows partagé (`svchost`) compte
  tout le `svchost`.
- **CPU** : `psutil.Process.cpu_percent(None)` ; le premier appel rend 0, donc les objets
  `Process` sont **gardés** d'un passage à l'autre (cache par PID, vérifié par l'heure de
  création) et chaque PID n'est mesuré **qu'une fois par passage** (un PID compté par la tuile et
  par son élément ne doit pas relancer le compteur : constaté, l'élément affichait 0 %). Divisé par
  le nombre de cœurs logiques : 100 % = machine pleine.
- **VRAM** : compteurs Windows `\GPU Process Memory(*)\Dedicated Usage` (instances
  `pid_<n>_luid_…_phys_0`, additionnées par PID) et `\GPU Adapter Memory(*)\Dedicated Usage`
  (total utilisé = l'adaptateur le plus chargé), lus par **PDH en ctypes** (requête ouverte une
  fois ; ni typeperf ni PowerShell). Lus un passage sur deux (~8 s). Capacité : registre du pilote
  d'affichage (`HardwareInformation.qwMemorySize`, la plus grande carte). Indépendant du
  constructeur (WDDM 2.x) : marche sur la RX 6950 XT. Si les compteurs manquent : « — ».
- **Mesuré sur le PC** : ouverture PDH 0,27 s une fois, un échantillon < 1 ms ; total lu
  16 368 Mo, ~4 Go utilisés ; `whisper-server-win32-x64-vulkan.exe` 1,8 Go dédiés (cohérent avec le
  Gestionnaire des tâches). Processus factice de test (150 Mo + enfant 40 Mo, ~0,4 cœur) :
  233 Mo, 2 processus, 2,6 % ; arrêt propre, rien de résiduel.
- **Interface** : vue d'ensemble = colonnes CPU / RAM / VRAM + « VRAM x / y » dans l'en-tête ;
  détail = carte « Ressources » (valeurs + courbes des 5 dernières minutes) et une ligne
  « CPU · RAM · VRAM » par élément. Captures : `apercu_metrics.png`, `apercu_metrics_detail.png`.
- **MCP** : `metrics {cpu_percent, ram_mb, vram_mb, processes, sampled_at}` dans `list_servers`,
  `get_server` (statut et éléments), `list_items`, `item_action` ; `gpu_memory_mb {used_mb,
  total_mb}` dans `get_machine_info`. `null` quand rien n'est mesuré.
- **Piège de test** : une tuile « commande » adopte tout processus dont l'exe **et** les arguments
  correspondent — un script de capture qui recevait le chemin du factice en argument a été adopté
  à sa place (comportement voulu de l'adoption, pas un bug).

## Priorité 5 du 16 nov 2026 : modèle « liste d'items », cœur/vues, nouveaux types, thème clair — version 2.4.0

**Demande** : priorité 5 de la revue + restes (arrêt propre depuis le tray, thème clair).

- **Arrêt depuis le tray** (cause racine) : un `WM_CLOSE` venu d'ailleurs que `stop()` détruisait la
  fenêtre de l'icône sans prévenir l'app (orpheline, plus quittable). Désormais : `WM_CLOSE` externe
  = « Quitter », `WM_QUERYENDSESSION`/`WM_ENDSESSION` gérés, icône recréée après un redémarrage de
  l'Explorateur (`TaskbarCreated`), `NIM_DELETE` à la destruction. Deux suites trouvées en vérifiant
  sur le PC : rappels `after()` annulés avant `destroy()`, et `_tick()` qui repeignait après un
  « Quitter » traité dans `_drain()` (« invalid command name … » au journal). Tests dans
  `tests/test_tray.py`.
- **Cœur / interface** : `bgc/core/runtime.py` (état d'une tuile), `poller.py` (sondeur, chutes),
  `actions.py` (exécution des actions, menus tray) — une seule implémentation pour l'UI, le tray et
  le MCP. Vues : un fichier par vue dans `bgc/ui/views/` (anciens modules gardés en alias).
  Couleurs : objet `T` de `bgc/theme.py` ; un test refuse tout code hexadécimal dans une vue.
- **Éléments** : `snapshot()` peut publier `items: [{id, label, phase, detail_key, rows, actions}]`,
  le contrôleur reçoit `item_action(item_id, action_id)`. Un type classique = un élément implicite
  (`presentation.items_of`). Vue détail : une ligne par élément ; vue d'ensemble : puces (8 max) et
  « n/m en marche » ; tray : sous-menu par élément ; MCP : `list_items`, `item_action` (les actions
  purement d'interface sont refusées). Les plugins marquent leurs actions principales
  (`"main": True`) : le cœur ne connaît plus aucun identifiant Hermes. `phone_node` n'a plus de
  valeur par défaut personnelle. Un paramètre de traduction peut être une clé (`{"key": …}`).
- **Nouveaux types** : Docker/Podman (un élément par conteneur ; absent ou moteur arrêté : dit
  clairement ; identifiants vérifiés contre une liste fraîche avant la CLI), service Windows
  (psutil, repli `sc query` ; `sc start/stop`, élévation UAC **seulement** sur « accès refusé » ;
  refus UAC journalisé, rien d'autre tenté), WSL « toutes les distributions » pilotable par
  distribution (même règle de sûreté qu'une tuile nommée).
- **Tuile WSL existante** (`distro: "Ubuntu"`) : mode nommé inchangé → **aucune migration** de
  config nécessaire. Seul changement visible : « "Ubuntu" introuvable » au lieu de « Lecture de
  l'état… » quand la distribution n'est pas visible (phase toujours `unknown`).
- **Thème** : palette claire (contrastes AA testés), Réglages > Thème : Sombre / Clair / Comme
  Windows (`general.theme`, défaut `dark`) ; « Comme Windows » lit `AppsUseLightTheme` et suit un
  changement à chaud (toutes les 5 s) ; barre de titre DWM accordée.
- **Formulaires** : un champ `choice` (ex. moteur Docker) est une liste déroulante (`ChoiceField`).

### Vérifié sur le PC (16 nov 2026)

- Tests verts sur le PC et la box. Exe reconstruit (`dist\BackgroundCheck.exe`, v2.4.0) ; précédent
  gardé en `BackgroundCheck.exe.bak-20261116T105517-avant-items`. Sauvegarde : `Documents\
  HermServ_backup_2026-11-16_phase4\` (config inchangée après les essais : même empreinte).
- Exe réel, vraie config : 5 tuiles lues, `list_items` MCP sur chacune (élément implicite, actions
  Hermes intactes), fermeture par le tray en 1–2 s sans erreur au journal.
- Lecture seule sur ce PC : service `Spooler` (en marche, automatique), `wuauserv` (arrêté), service
  inconnu (« introuvable », aucun bouton) ; Docker/Podman absents ; aucune distribution WSL visible
  depuis la session de l'agent. **Aucun service, conteneur ni distribution n'a été démarré ou arrêté.**
- Captures : `captures/apercu_items_accueil.png`, `apercu_items_services_windows.png`,
  `apercu_items_docker_absent.png`, `apercu_items_wsl_toutes.png`, `apercu_items_reglages_theme.png`,
  et en clair `apercu_items_accueil_clair.png`, `apercu_items_hermes_clair.png`,
  `apercu_items_services_windows_clair.png`, `apercu_items_reglages_theme_clair.png`.

### Suite du 16 nov 2026 (après-midi) : essai réel du spouleur, restes

- **Spouleur d'impression** (accord de l'utilisateur) : tuile service Windows → arrêt puis relance,
  deux fois (avant et après correctif). `sc` sans droits → « accès refusé » → élévation → OK en
  ~2 s ; état final vérifié par `sc query` : **RUNNING**. Aucune fenêtre UAC visible : avec
  `ConsentPromptBehaviorAdmin = 5` (réglage par défaut), Windows élève sans demander ses propres
  binaires signés comme `sc.exe`.
- **Correctif** : l'élévation passe par `ShellExecuteExW` et attend `sc.exe` (60 s max) ; son code
  de sortie est lu (vérifié : 1056 « déjà démarré ») et traduit (1058 désactivé, 1068 dépendance…)
  au lieu d'un « délai dépassé » 20 s plus tard.
- **WSL** : sur ce PC, aucune distribution n'est enregistrée pour le compte (clé
  `HKCU\…\Lxss` vide, pas de paquet Ubuntu, pas de disque `.vhdx`) — ce n'est ni la session de
  l'agent ni l'encodage. Défaut réel corrigé quand même : `wsl.exe` traduit les états (« En cours
  d'exécution »…), le lecteur ne reconnaissait que l'anglais ; repli `-l -q` / `-l --running -q`,
  `WSL_UTF8=1`.
- « Arrêt du serveur avant de quitter » n'apparaît plus que s'il y a quelque chose à arrêter.
- `--selftest [id]` générique (Hermes par défaut) ; essayé sur une tuile de test en bac à sable.
- Exe reconstruit ; précédent : `BackgroundCheck.exe.bak-20261116T131450-avant-spouleur`.

### Limites connues

- Docker et WSL par distribution n'ont été essayés qu'avec des CLI simulées (tests + captures box) :
  ni Docker ni distribution WSL installés sur ce PC.

## Renommage du 16 nov 2026 : HermesServ → Background Check (BGC) — version 2.3.0

**Demande** : « au lieu de l'appeler HermesServ (au début ça ne servait qu'à gérer Hermès), on va
l'appeler Background Check, BGC pour faire court ». Les mentions plus bas gardent l'ancien nom :
c'est l'historique. Hermes **le service** (tuile, plugin `hermes`, icône de sa tuile) ne change pas.

- **Nom affiché** : `APP_NAME = "Background Check"` (`bgc/__init__.py`), clé `app.title` ; « BGC »
  dans l'infobulle du tray (`tray.summary`). 14 chaînes × 5 langues reprises.
- **Code** : paquet `hermesserv/` → `bgc/` (git mv), `hermes_control.py` → `background_check.py`,
  `HermesServ.spec/.bat` → `BackgroundCheck.spec/.bat`. **Shim** `bgc/compat.py` : `import
  hermesserv.X` renvoie le **même** module que `bgc.X` (même registre de types) — les plugins
  utilisateur écrits avant marchent sans retouche.
- **Données** : `bgc/migration.py` copie `%LOCALAPPDATA%\HermesServ` → `%LOCALAPPDATA%\BackgroundCheck`
  au premier lancement si le nouveau dossier n'existe pas (config + jeton MCP, `.bak`, journaux,
  plugins), via un dossier temporaire renommé à la fin ; l'ancien reste intact. Journal : `bgc.log`.
- **Démarrage avec Windows** : valeur `Run` → `BackgroundCheck` ; `autostart.migrate()` ne fait
  quelque chose **que si** l'ancienne valeur `HermesServ` existait (recréée sous le nouveau nom,
  puis l'ancienne effacée). Mutex `Local\BackgroundCheck-<clé>`, fenêtre tray
  `BackgroundCheckTray:<clé>`.
- **MCP** : `serverInfo.name = "background-check"`, bloc « Copier la config Hermes » sous la clé
  `bgc:`. URL et jeton inchangés : un bloc `hermesserv:` déjà collé continue de marcher.
- **Tuiles exportées** : format `bgc-tile` ; l'ancien `hermesserv-tile` s'importe toujours.
- **Variables** : `BGC_CONFIG`, `BGC_FFMPEG` (les anciennes `HERMESSERV_*` sont encore lues).
- **Icône** : `bgc.ico` (tracé de pouls vert sur carré sombre, 16→256 px), générée par
  `tools/make_icon.py` ; l'ancienne `hermes_serv.ico` est retirée du dépôt.
- **Exe** : `dist\BackgroundCheck.exe` ; l'ancien `HermesServ.exe` gardé en `.bak`. Raccourci du
  bureau « Background Check ».
- Le dossier du projet (`Documents\HermServ`) n'est **pas** renommé.

## Refonte UX du 16 nov 2026 (priorité 4) — version 2.2.0

**Demande** : suite de la revue (« tu peux tout faire, petit à petit »). Les fonctions voisines
(`PLUS_TARD.md`, QR code compris) et le modèle « liste d'items » restent **hors champ**.
Renommage prévu en « Background Check » (BGC) **après** cette phase : tout nouveau texte passe par
la clé `app.title` (en-tête, à propos, accueil, rapport de journal), aucun « HermesServ » ajouté.

### Ce qui change à l'écran
- **Accueil = vue d'ensemble** (`overview.py`) : une ligne par service — pastille + mot d'état,
  port, temps de marche, bouton marche/arrêt, bouton « Interface web » (action navigateur, sinon
  le premier bouton sur mesure qui ouvre une adresse). Tuile « Accueil » en tête du rail.
- **Rail** : pastilles colorées (vert / ambre / rouge « tombé » / anneau gris = arrêté normal /
  gris à deux barres = en pause), noms tronqués avec « … » + infobulle, mode **icônes seules**
  (Réglages), champ de **recherche** au-delà de 8 tuiles (Ctrl+K).
- **Hermes honnête** : la `phase` ne bouge pas (ligne rouge), on ajoute `snapshot["health"]` :
  vert seulement si la chaîne utilisée par le desktop répond en entier ; tunnel vers une cible
  muette = ambre (`value.tunnel_dead`) ; l'URL publique s'affiche « inactive » tant que 9119 est
  arrêté ; carte « chaîne » linéaire (Téléphone → Tunnel → Dashboard → API / Desktop → Gateway)
  avec les maillons manquants en clair. `chain_status()` est testé sans réseau.
- **Libellés** : « libre » → « personne n'écoute » ; « Copier l'URL » / « Copier l'URL publique »
  selon l'adresse, grisé à l'arrêt (règle commune `presentation.adjust_actions`) ; « Desktop ✓ »
  → « Ouvrir Hermes Desktop » + ligne « App Hermes Desktop : ouverte/fermée » ; interrupteurs sans
  « · on/off », textes courts, explications dans des ⓘ (convention de clé `<clé>_hint`).
- **Journal repliable** à onglets Événements | Sortie console (la fenêtre séparée a disparu),
  filtre, Copier, Exporter (.txt, en-tête nom + version).
- **Ajout guidé** (`addflow.py`) en 3 étapes : quoi (services détectés filtrés — déjà suivis,
  l'app et son port MCP, processus système, plage dynamique — regroupés par processus et
  rapprochés des modèles ; modèles ; autres types) → réglages pré-remplis + **Tester maintenant**
  (sonde à blanc, rien n'est lancé) → nom et icône (icône reprise de l'exécutable).
- **Réglages de tuile** : les modèles sont derrière « Réinitialiser depuis un modèle… » avec la
  liste des différences et confirmation (formulaire seulement, il faut encore Enregistrer).
- **Réglages généraux** plus denses : langue et taille en listes, port MCP modifiable à chaud,
  démarrage avec Windows en tête, rail compact, raccourci global, version (« À propos »).
- **Premier lancement** (`onboarding.py`) sans tuile : balayage (Hermes, WSL, programmes
  installés, services en écoute), cases à cocher (les inconnus jamais cochés d'office), création
  en un clic, explication MCP + « Copier la config Hermes ».
- **Tray** : un sous-menu par service (marche/arrêt, interface web, copier l'URL, journal) ; plus
  de plafond muet à 14 lignes — au-delà de 10 services, sous-menu « Autres (n) ».
- **Raccourci global** Ctrl+Alt+H (désactivé par défaut) : `RegisterHotKey` dans le fil du tray ;
  un raccourci déjà pris est signalé dans le journal.
- **Version** visible en haut à droite.

### Non fait, volontairement
- **Thème clair** : la palette est importée par nom dans tous les modules (`from .widgets import
  BG, CARD…`) ; suivre le thème Windows demanderait de passer par un objet thème partout. À faire
  avec le découpage core/ui (priorité 5).

### Code
- Logique d'affichage pure et testée : `presentation.py` (états, libellés, URL, services
  détectés, différences de modèle, menu tray) et `discovery.py` (détection, sonde à blanc).
- `tests/test_presentation.py` (21 tests) ; harnais visuel sur le box : Xvfb + Python avec Tk.

## Revue du 16 nov 2026 : hygiène, sécurité, robustesse (priorités 1 à 3)

**Demande** : « tu peux tout faire, petit à petit » après la revue critique (rapport complet hors
dépôt). Fonctions voisines notées dans `PLUS_TARD.md`, **non développées**. Sauvegardes faites
avant toute modification : `Documents\HermServ_backup_2026-11-16` (dossier entier, dist compris)
et `Documents\HermServ_backup_2026-11-16_appdata` (+ `config.json.bak-20261116T-avant-revue`).

### Dépôt
- **git** initialisé (`main`) ; premier commit = état d'origine ; un commit par chantier.
- `requirements.txt` / `requirements-dev.txt` / `pyproject.toml` ; `tests/` (pytest, sans Tk sauf
  deux tests `ui_call` qui sautent sans tkinter) ; `legacy/` pour l'ancien mono-fichier ;
  `HermesServ.bat` passe par `pyw` (sinon `pythonw` du PATH).

### Sécurité
- **MCP** : une page web pouvait créer et démarrer une tuile (POST `text/plain` sans preflight
  CORS vers 127.0.0.1). Maintenant : `Origin` ⇒ 403, `Host` non local ⇒ 403, POST hors
  `application/json` ⇒ 415, **jeton bearer** obligatoire ⇒ 401. Jeton dans `general.mcp_token`,
  créé au premier lancement ; Réglages : copier le jeton / copier la config Hermes / nouveau jeton.
  Hermes n'avait **pas** HermesServ dans ses `mcp_servers` (vérifié dans `config.yaml`) : rien à
  mettre à jour de son côté. Pour le brancher : bloc `headers: Authorization: Bearer …` (README).
- **Import de tuile** : fenêtre de confirmation (commandes, boutons, noms des variables) ;
  `start_on_app_start` / `auto_restart` / `auto_launch_desktop` toujours coupés. Piège trouvé :
  l'import appelait `refresh()` ⇒ `maybe_auto_start` ⇒ la commande partait sans clic.
- **Export de tuile** : `env` et paramètres « secrets » retirés sauf « oui » explicite.
- **Plugins** : `service.register` connaît le propriétaire de chaque type ; un plugin ne remplace
  plus un type intégré ni celui d'un autre plugin, même via `register()` (le contrôle ne valait
  que pour `TYPE`). Effet de bord utile : « Recharger les plugins » ne journalise plus d'erreur
  « identifiant déjà pris » pour un plugin déjà chargé.

### Robustesse
- `config.json` illisible ⇒ renommé `config.json.corrupt-<date>`, alerte, restauration proposée
  de la `.bak` lisible la plus récente (avant : remplacé en silence, puis écrasé).
- Instance unique par fichier de config (mutex `Local\HermesServ-<sha1[:12] du chemin>`) ; la
  seconde instance poste `WM_SHOW_REQUEST` à la fenêtre du tray (`HermesServTray:<clé>`).
  Une instance de test (`HERMESSERV_CONFIG`) reste possible à côté de la vraie.
- Journal fichier `%LOCALAPPDATA%\HermesServ\logs\hermesserv.log` (1 Mo × 5) + crochets
  d'exception (sys, threading, Tk).
- Arrêt des commandes **lancées par l'app** : CTRL_BREAK au groupe (l'app sans console s'attache
  à la console cachée de l'enfant le temps de l'envoi), 5 s, puis arrêt forcé. Rien de changé pour
  un service adopté ni pour le plugin Hermes (ligne rouge).
- `ui_call` annulable ; `quit()` sans gel (arrêts en parallèle, plafond 25 s) ; sondeur à pool
  fixe + instantané TCP partagé (`sysinfo.listeners_snapshot`, TTL 1 s).

## Granularité Hermes (Local 8642 + Distant 9119) & nettoyage LlamaGUI (11 nov 2026, suite)

**Demande** :
1. Supprimer le doublon « Browser » / « Open LlamaGUI » sur LlamaGUI, afficher clairement la sonde
   du port `9931` (`llama-server` + état `/health`), et **laisser LM Studio tel quel**.
2. Sur la tuile **Hermes**, avoir **les deux boutons côte à côte sur la même tuile** (`Start/Stop
   local gateway` 8642 + `Start/Stop remote access` 9119), et rendre le monitoring granulaire.
3. Corriger le faux positif « running outside the app » sur LlamaGUI (chaîne `.venv\Scripts\python.exe`
   → enfant Python).

### Modifications vérifiées

1. **Déduplication automatique du bouton `Browser`** (`plugins/command.py`) : si la tuile possède déjà
   un bouton sur mesure (`actions`) qui ouvre la même URL que `effective_url()`, le bouton générique
   `Browser` n'est pas ajouté. Sur LlamaGUI, il ne reste que `Open LlamaGUI` (5240) et `Open the
   llama.cpp web UI` (9931). LM Studio (dont les boutons sur mesure sont des commandes `.exe` /
   `taskkill`) reste strictement inchangé.
2. **Sonde `llama-server (9931)` explicite + `/health`** (`plugins/command.py`) : toute sonde
   supplémentaire (`extra_checks`) affiche désormais le numéro de port dans son libellé (`llama-server
   (9931)`), et lorsque le port d'une sonde `llama` écoute, interroge `/health` pour distinguer
   `loading model (503) · pid …` (ambre) de `ready (200) · pid …` (vert).
3. **Arbre de processus `.venv` Windows** (`plugins/command.py`) : `_real_pid()` descend jusqu'au
   processus qui écoute sur le port (ou la feuille de l'arbre) au lieu de s'arrêter dès que `cmd.exe`
   a plus d'un descendant ; `stop()` respecte aussi `stop_external` pour un processus sans
   `stop_command` et tue la racine de l'arbre lancé.
4. **Tuile Hermes à double commande Local (8642) + Distant (9119)** (`plugins/hermes.py`,
   `sysinfo.py`, `hermes_control.py`, `ui.py`) :
   - Deux boutons côte à côte : **`Start / Stop local gateway`** (`toggle_gateway`, port 8642) et
     **`Start / Stop remote access`** (`toggle`, port 9119), suivis de **`Desktop ✓`**,
     **`Web UI (9119)`** (grisé tant que 9119 est arrêté) et **`Copy public URL`**.
   - Bandeau et point d'état de la tuile : `Online (local gateway :8642)`, `Online (remote access
     :9119)` ou `Online (local :8642 + remote :9119)` — la tuile n'affiche plus « Stopped » quand
     seul le gateway local tourne.
   - Lignes d'état nettoyées : `Local gateway` (avec version et PID), `Port 9119`, `API`,
     `Tailscale tunnel`, `Tailscale device`, `Desktop connects via` (la ligne floue « Hermes backend
     processes : 1 » est retirée).
   - Menu clic droit du Tray : expose les deux actions Hermes séparément (`toggle_gateway` et
     `toggle`).
   - Parité i18n : **457 clés × 5 langues** (`fr`, `en`, `es`, `pt-BR`, `zh`).
   - `dist/HermesServ.exe` reconstruit et relancé (sauvegarde `.bak-20261111T120035`), captures
     `captures/apercu_hermes_dual.png` et `captures/apercu_llamagui_clean.png` vérifiées.

## Revue critique : logique puis esthétique (11 nov 2026)

**Demande** : « commence par le logique puis l'esthétique » — carte blanche après une revue critique
du projet (défauts trouvés par lecture du code, calculs et sondes). Plan et cases cochées :
`ROADMAP.md`.

### Logique

1. **Détection « Détecté sur cette machine » réparée.** Depuis la fusion hôte/port/URL en une ligne
   « cible » (9 nov), `_apply_suggestion` écrivait dans `fields["port"]`, clé disparue du formulaire :
   le clic ne remplissait plus que le nom (prouvé par sonde : ligne cible vide). Il recompose
   maintenant la cible avec le type, comme un modèle rapide. Preuve : `127.0.0.1:5240`.
2. **Sondeur : un fil par tuile** (`App._start_poller`). Une sonde lente ne retarde plus les autres ;
   une tuile dont la sonde précédente traîne est sautée (`in_flight` par id), donc aucun empilement.
   Mesuré : sonde de 4 s sur une tuile, les deux autres sondées pendant ce temps, un seul appel sur
   la lente.
3. **Scan de détection hors du fil Tk** (`AddServerView._build_suggestions`) : psutil / `wsl -l -v`
   dans un fil, remplissage par `after()`. Ouverture mesurée : **13 ms** avec un scan artificiel
   de 1 s ; les boutons arrivent ensuite.
4. **Un clic d'action ne reconstruit plus la vue** : `run_action` appelle `_paint()` (les boutons se
   grisent par l'état `busy`) au lieu de `_render_view()` — le filtre du journal et le défilement
   survivent. Preuve harnais : `render=0 paint=1`.
5. **Portée du MCP dite franchement** : clé `settings.mcp_scope` (5 langues) sous l'aide des
   Réglages, en ambre — « tout programme de cet ordinateur peut piloter les tuiles ».
6. **`--selftest` : garde dans le code.** Si la tuile est déjà en marche, refus (code 2) avant tout
   `adopt()` — la protection n'est plus seulement une consigne de ce fichier.
7. `Tile._draw` respecte son paramètre `size` (78 était codé en dur).

### Esthétique

1. **Palette** (contrastes WCAG recalculés) : FAINT `#7d859a` → 4,72:1 sur carte (2,98 avant),
   MUTED `#9aa3b8` → 6,88:1, fond de bouton primaire `ACCENT_DARK #3f66d4` → blanc 5,17:1 (3,16
   avant). ACCENT reste la couleur de texte/accent (5,97:1).
2. **Échelle typographique dé-flattenée.** Le plancher unique (11 unités de base + 13 px) rendait
   8, 9, 10 et 11 **tous à 13 px**. Deux paliers : aides 13 px, corps 15 px. Mesure :
   `font(8/9)=13`, `font(10/11)=15`, titres 15/17/20.
3. **Boutons mesurés** : `Button(width=None)` mesure son libellé (`measure + 28 px`, plancher 90 px).
   `SelectRow`, suggestions, réglages et pieds de page s'en servent. Corrige au passage un **double
   `sc()`** sur « Parcourir » (`width=sc(96)` dans un constructeur qui applique déjà `sc()`) :
   invisible à 100 %, ×1,69 à 130 %.
4. **Pied de page fixe** : Enregistrer/Supprimer (réglages d'une tuile) et Ajouter/Annuler (ajout)
   sont packés `side="bottom"`, **hors** du `ScrollFrame`. Preuve : pixels du bleu primaire dans la
   bande basse des captures (1 552 et 1 380).
5. **Journal** : ne force plus le bas. Piège mesuré : conditionner `see("end")` ne suffit pas —
   **Tk suit le curseur d'insertion** et la vue saute quand même (0,86). Correctif : mémoriser
   `index("@0,0")` avant l'insertion et restaurer par `yview(index)`.
6. **Fenêtre « Sortie console »** : barre de titre sombre via `apply_dark_titlebar()` (partagée avec
   la fenêtre principale ; `DwmGetWindowAttribute` relu = 1) + rafraîchissement auto (1 s) et bouton
   « Fermer ».
7. **Clavier** : boutons `takefocus=1`, anneau de focus, Espace/Entrée ; Échap revient en arrière,
   Entrée enregistre/ajoute (bindings sur la fenêtre, pas sur les vues).
8. **Infobulle du tray** : « en marche : 1 · arrêtés : 1 » (tuiles en pause exclues) au lieu du seul
   serveur sélectionné.

### Vérifications et pièges de méthode

- Harnais en processus neuf, config isolée (`HERMESSERV_CONFIG`, MCP coupé) : **14/14** logique,
  **19/19** esthétique, **9/9** clavier. Captures `apercu_revue_serveur/reglages/ajout/hermes.png`.
- Le panneau Hermes a été photographié sur une **copie** de la vraie config (auto-lancement du
  desktop forcé à `false`) : phase `stopped`, 7 lignes, 4 actions (`toggle`, `desktop`, `browser`,
  `copy_url`).
- `dist/HermesServ.exe` reconstruit (11 nov 09:53, 32,8 Mo), vérifié par `--shot`, puis relancé
  (fenêtre « HermesServ », pid 23788 au moment du bilan).
- Piège de harnais : `isinstance(view, ui.AddServerView)` échoue sur un `SimpleNamespace` — pour
  tester un handler qui teste le type, prendre une vraie instance (ou `object.__new__` quand
  l'`__init__` Tk est lourd).
- `cmd //c start` depuis ce shell n'a pas lancé l'exe (console interactive) : relancer par
  `Popen(..., creationflags=DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP, env=` purgé des `_PYI*`).

## Audit des tuiles & interfaces web (10 nov 2026)

**Demande** : configurer HermesServ « comme il se doit » — savoir si un serveur tourne sans aucun
visuel Windows, **piloter les interfaces web** (cas des moteurs LLM qui séparent backend et
frontend), contrôler gateway puis desktop pour Hermes, et créer des modèles de tuiles.

### Relevé tuile par tuile (état réel de la machine, lecture seule)

| Tuile | Ce que la sonde prouve | Verdict |
|---|---|---|
| **Hermes** | port 9119 libre · API muette · tunnel tailnet → 9119 **OK** · téléphone **hors ligne (2 j)** · gateway local **on 8642 v0.23.0** (pid 50992) · desktop en mode local · 1 backend | complet — un seul défaut, corrigé ci-dessous |
| **STT Hermès** | pont arrêté · amont `127.0.0.1:8178` répond **200** | correct, rien à toucher |
| **LlamaGUI** | arrêté · port 5240 libre · `llama-server` 9931 fermé | correct ; `check_url` à poser (proposé) |
| **WSL** | `wsl -l -v` → **aucune distribution installée** | tuile obsolète → proposée en pause |
| **LM Studio** | `lms` présent (`~/.lmstudio/bin/lms.EXE`) · 1234 libre | correct ; pas d'UI web (voir plus bas) |

**Défaut trouvé sur Hermes** : la ligne « processus serve/dashboard » affichait « aucun » alors que
le gateway local tournait. Il ne s'appelle pas `hermes.exe serve` mais
`python -m hermes_cli.main --profile default gateway run --quiet`. `sysinfo._argv_is_hermes_backend`
reconnaît maintenant le module `-m hermes_cli…` + le jeton `gateway` (jamais l'app Electron), et la
ligne est renommée « Processus backend Hermes » dans les 5 langues — elle nomme ce qu'elle mesure.

**LM Studio n'a aucune interface web** : son serveur local n'expose que `/v1` (documentation LM
Studio, 10 nov 2026). L'interface, c'est l'application — d'où le bouton « Ouvrir LM Studio » dans le
modèle rapide, plutôt qu'un bouton « Navigateur » qui ouvrirait une page inexistante.

### Corrections de code

1. **Boutons d'interface web « vivants »** (`ui.ServerRuntime._probe_custom_actions`) : chaque bouton
   sur mesure dont la cible est une adresse web est sondé (cache 5 s) et **grisé quand l'interface ne
   répond pas** ; une cible qui est une commande reste toujours active. C'est ce qui rend le couple
   backend `llama.cpp` (9931) ↔ frontend `LlamaGUI` (5240) lisible : deux boutons, deux états.
2. **« Navigateur » grisé hors marche** (`ui.ServerView._paint_actions`) : la page n'existe pas tant
   que la tuile n'est pas `running` — même règle que ci-dessus, un bouton ne doit pas avoir l'air prêt.
3. **Modèles de tuiles groupés** (`presets.LAYOUT`) : *Surveillance* (port/réseau, Home Assistant),
   *Moteurs locaux* (script local, LlamaGUI, LM Studio, Ollama, llama.cpp, vLLM,
   text-generation-webui, KoboldCpp, ComfyUI), *Interfaces web* (SillyTavern, Open WebUI).
   Ports par défaut vérifiés : llama-server **8080** (`/health`), text-generation-webui **7860**,
   KoboldCpp **5001**, vLLM **8000** (`/v1/models`), ComfyUI **8188**, SillyTavern **8000**,
   Open WebUI **3000** (`/health`), Home Assistant **8123**, LM Studio 1234, Ollama 11434.
   Aucun preset ne lance rien : il remplit.
4. **Sonde explicite d'un bouton sur mesure** (`check`, facultatif) : `proc:LM Studio.exe`,
   `port:1234`, `hôte:port` ou une URL. Sans elle, la cible web est sondée elle-même et une
   commande reste toujours active. `config._normalise_actions` la conserve (sinon le chargeur
   l'éplucherait), et `ui._collect_actions` la reporte à l'enregistrement d'une tuile — **sauf si
   la cible a changé** (une autre cible demande une autre sonde). C'est ce qui permet un couple
   « Ouvrir / Fermer » : *Fermer* est grisé quand l'application ne tourne pas.

**Fermer une application, sans `taskkill /F`** : `taskkill /IM "LM Studio.exe"` (sans `/F`) envoie
un **WM_CLOSE** — la fermeture propre. Vérifié sur un vrai process fenêtré portant ce nom (copie de
`pythonw.exe` + boucle Tk) : « un signal de fin a été envoyé », process disparu. **Piège** : la même
commande **échoue** (« ne peut être arrêté que de force ») sur un process **console** — donc jamais
de `/F` par défaut, et ne pas conclure d'un échec sur `ping.exe` que la commande est mauvaise.

### Tuile LM Studio de l'utilisateur (appliqué le 10 nov 2026, avec son accord)

Deux boutons ajoutés à `command-1` (sauvegarde `config.json.bak-20261110T093713` faite avant, app
fermée pendant l'écriture) : « Open LM Studio » (le `.exe`, toujours actif) et « Close LM Studio »
(`taskkill /IM "LM Studio.exe"`, sonde `proc:LM Studio.exe`). Refusé par l'utilisateur : `check_url`
sur LlamaGUI (il n'en veut pas) et mise en pause de la tuile WSL (il la garde telle quelle).

**Vérifié** : `harness_web.py` **49/49** (ports par défaut, 13 presets exploitables, libellés
résolus dans les 5 langues, un serveur HTTP jetable qui répond et un port fermé → bouton actif /
grisé, cache 5 s compté, `check proc:`/`port:` présents et absents), captures
`apercu_web_actions.png`, `apercu_presets_groupes.png`, `apercu_reel_llamagui.png`,
`apercu_reel_hermes.png`, `apercu_lmstudio_boutons.png`. Parité i18n : **443 clés × 5**.

## Tuiles du rail centrées sur la colonne (9 nov 2026)

**Symptôme** (vu par l'utilisateur) : les tuiles des serveurs étaient décalées de ~5 px vers la
gauche par rapport à la tuile « Réglages » du bas, qui est centrée sur toute la largeur du rail.

**Cause** : la barre de défilement fine était **packée à côté** de la zone défilante ; elle lui
prenait ses `sc(7)` px, donc le canvas — et les tuiles centrées dedans — avaient un centre à 43,5 px
au lieu de 48 (mesuré au pixel sur `--shot` : cadre des tuiles 10→77 contre 15→81 pour Réglages).

**Correctif** : la barre est désormais **posée par-dessus** la colonne (`place(relx=1.0, anchor="ne",
relheight=1.0)`), dans la marge droite du rail ; le canvas garde toute la largeur. Vérifié au pixel
après coup : toutes les tuiles à 15→81, centre 48 = centre du rail, barre toujours dessinée au même
endroit (x 90-94) et sans recouvrement.

## Plan d'améliorations « Versatilité & Clarté d'usage » (9 nov 2026)

Analyse complète consignée dans `AMELIORATIONS.md` et découpée en **5 chantiers activables** en tête de `ROADMAP.md` (« Plan de travail actif — 9 nov 2026 ») :
1. **Chantier 1** : Réparer le mode « Surveillance seule » (`CommandType.validate` accepte un port sans commande, masquage du bouton Start/Stop en lecture seule, ajout des boutons *Navigateur*, *Copier l'URL*, *Ouvrir le dossier*).
2. **Chantier 2** : Rendre chaque onglet auto-explicatif (bandeau dynamique « Que fait cet onglet ? » sous le titre + découpage du formulaire *Service surveillé* en 3 blocs titrés : *Sonde*, *Pilotage*, *Affichage*).
3. **Chantier 3** : Modèles rapides à l'ajout (*Port seul*, *Script local*, *LM Studio*, *Ollama*) + boutons *Parcourir…* sur les dossiers/exécutables + bouton *Ouvrir dans l'explorateur* pour `config.json`.
4. **Chantier 4** : Capture `stdout`/`stderr` des commandes lancées (tampon mémoire 80 lignes + remontée automatique de l'erreur si la commande meurt au lancement + vue *Sortie console*).
5. **Chantier 5** : Réorganisation des tuiles (Monter/Descendre/Dupliquer/Pause `enabled`), menu clic droit du Tray listant tous les serveurs, et option de notification Windows sur chute inattendue.

**Protocole de reprise** : dès que l'utilisateur dit « continue le développement », ouvrir `ROADMAP.md`, prendre le premier chantier `[ ]` non coché, l'exécuter et le vérifier.

## Serveur MCP local + icône en un bouton (9 nov 2026)

**Demande** : un bouton qui ouvre le navigateur de fichiers pour l'icône (plus de chemin à taper),
et un **serveur MCP** pour qu'un agent IA (Hermes, par exemple) pilote les serveurs et **configure
les tuiles** lui-même — avec la config du PC et de la documentation, pour qu'un utilisateur
inexpérimenté obtienne un résultat correct. `start_server` / `stop_server` exposés dès le départ.

### Icône

- `ui.IconField` remplace le `TextField` de chemin dans les **réglages** et à **l'ajout** :
  aperçu carré, nom du fichier, **« Choisir une icône… »** (`askopenfilename`, images + .exe/.dll)
  et **« Retirer l'icône »**. Le chemin complet n'est plus affiché ni saisissable ; le choix est
  appliqué tout de suite dans les réglages (validation par `widgets.load_icon`, retour arrière si
  illisible). À l'ajout, il remplit seulement le champ, enregistré avec la tuile.
- **Correction au passage** : la rangée « Modèles rapides » avait une largeur fixe de 150 px et
  coupait « Programme ou script local » (vu à l'image). Les boutons mesurent maintenant leur
  libellé (`tkfont.measure`) et passent à la ligne ; captures `apercu_icone_bouton.png`,
  `apercu_mcp_ajout.png`.

### Serveur MCP

- `hermesserv/mcpserver.py` : **Streamable HTTP** MCP en JSON-RPC 2.0 sur `POST /mcp`, dans un fil
  démon de l'application (comme le pont STT). **Aucune dépendance** (`http.server`), écoute
  **127.0.0.1:8781** uniquement, pas de session (`Mcp-Session-Id` absent → le client n'ouvre pas
  de flux GET), `initialize` négocie `2025-11-25` (le SDK Hermes envoie cette version).
- **12 outils** : `list_servers`, `get_server`, `list_server_types`, `get_machine_info`,
  `list_presets`, `get_server_log`, `get_app_log`, `start_server`, `stop_server`,
  `create_server`, `update_server`, `delete_server`. Les descriptions et le bloc `instructions`
  d'`initialize` donnent la marche à suivre (lire les types + la machine, éviter les doublons,
  valider en démarrant, consulter le journal en cas d'échec).
- **Pilotage** : mêmes contrôleurs que les boutons (`controller.start()` / `.stop()`), sans passer
  par Tk ; `runtime.refresh()` puis rafraîchissement du panneau. Les refus du socle (arrêter ce
  qu'on n'a pas lancé, tuile en pause) sont renvoyés **tels quels** au modèle, avec le message
  traduit et les paramètres réels (pid, commande) relus dans le journal — sinon le modèle recevait
  `Commande lancée (pid {pid})` avec les accolades.
- **Configuration** : `create/update/delete` passent par `App.ui_call` (fil Tk) pour reconstruire
  le rail sans toucher aux widgets hors du fil d'affichage. `update_server` fusionne les `params`,
  valide le résultat avec le type, **signale les clés inconnues** et n'écrit rien si la validation
  échoue. `delete_server` exige `confirm == server_id`. **Sauvegarde horodatée**
  (`config.json.bak-…`) avant chaque écriture venue de l'agent. Les valeurs de `env` (et de tout
  champ dont le nom contient key/token/secret/password) sont **masquées** dans les résultats lus
  par le modèle (`NOM=***`) ; réécrire `***` dans `env` est refusé, il faut omettre le champ pour
  le conserver ou fournir les vraies valeurs pour le remplacer.
- **Réglages** : section « Serveur MCP (agent IA) » (interrupteur `mcp_enabled`, port `mcp_port`
  dans `config.json`, état réel, bouton « Copier l'adresse MCP »). Défauts : **activé**, port
  **8781**. Si le port est pris, l'erreur est affichée et journalisée ; l'app continue.
- **Pour brancher Hermes** (non exécuté — modification de sa config, à faire avec son accord) :
  `hermes mcp add hermesserv --url http://127.0.0.1:8781/mcp` (l'app doit tourner), puis
  `/reload-mcp` ou redémarrage du gateway. `hermes mcp list` doit montrer 12 outils.

### Vérifié

- Sonde unitaire : `initialize` + `tools/list` (12 outils), `list_server_types` (4 types),
  `get_machine_info` (OS, GPU AMD + adaptateur Parsec, ports, WSL, outils), `list_presets`.
- **Client MCP officiel** (`mcp` 2.0, le SDK utilisé par Hermes) contre le serveur HTTP :
  `initialize` → `tools/list` → `list_server_types` → `get_machine_info` → `list_servers` OK.
- Harnais d'intégration sur **config de test** (`HERMESSERV_CONFIG`) : **22/22** puis **28/28** —
  création, validation (port invalide, clé inconnue signalée), démarrage réel (pid, phase
  `running`), second démarrage idempotent, arrêt, second arrêt idempotent, mise à jour (label,
  port), tuile en pause (démarrage refusé), journal, suppression avec confirmation, sauvegardes de
  config. Le processus factice (`python -c sleep`) est bien tué ; aucun orphelin.
- **Exe 2.1.0** reconstruit (`dist/HermesServ.exe.bak-20261109T225546` conservé) : lancé sur config
  de test, le client SDK lit 12 outils, crée/démarre/arrête/supprime une tuile factice depuis le
  binaire. App relancée sur la vraie config : fenêtre « HermesServ » (pid 20764), MCP sur **8781**,
  5 tuiles lues, Hermes `stopped` 7 lignes / 4 actions ; **9119 et 8770 sans écouteur, gateway 8642
  (pid 26468) intact**.
- Parité i18n : **430 clés × 5 langues** (nouvelles clés MCP + icône).

**Limites assumées** : le serveur MCP vit tant que l'app tourne (pas de mode service/headless) ;
pas d'authentification au-delà de l'écoute locale (127.0.0.1) ; l'agent peut modifier les tuiles,
donc chaque écriture est sauvegardée mais une suppression reste une suppression (confirmation
exigée dans l'outil). Aucun branchement automatique dans la config Hermes.

## Chantiers 1 et 2 du plan « Versatilité & Clarté » (9 nov 2026)

### Chantier 1 — surveillance seule réparée

- `CommandType.validate()` accepte désormais **port seul** (ou commande d'arrêt seule) : une tuile
  `LlamaGUI` (port 5240, aucune commande) se réenregistre sans erreur ; tout vide donne la
  nouvelle clé `add.error.command_or_port`.
- Actions du panneau calculées dans `CommandController.snapshot()` : `toggle` seulement s'il y a
  une commande de démarrage (ou un process lancé par nous), plus `browser` / `copy_url` dès
  qu'une URL **ou** un port existe, plus `folder` si `workdir` est un dossier existant.
  `browser` et `folder` sont traités dans `action()` (`webbrowser.open`, `os.startfile`) ;
  `copy_url` reste générique dans `App.run_action` et lit `snapshot["url"]` — d'où l'URL dérivée
  `http://hôte:port` quand `url` est vide (sinon on copiait une chaîne vide).
- **Piège corrigé** : les interrupteurs du panneau étaient construits depuis `runtime.type.toggles`
  (déclaration statique) et ne regardaient jamais `snapshot["toggles"]` → un serveur en lecture
  seule affichait quand même les 4 réglages. Ajout de `ServerView._paint_toggles` (repack dans
  l'ordre, ancré `before=self._log_title` pour ne pas glisser sous le journal).
- Vérifié : harnais `harness.py` **28/28** (validation, actions, `webbrowser`/`os.startfile`
  espionnés plutôt qu'exécutés, cycle réel démarrer/arrêter d'un `python -c sleep`, parité i18n) ;
  `ui_check.py` (interrupteurs réellement masqués/affichés selon le serveur) ; 2 captures
  (surveillance / commandes) + une capture de l'exe.
- Non-régression Hermes sur la vraie config (lecture seule) : `phase=stopped`, 5 lignes,
  4 actions — inchangé.

### Chantier 2 — onglet auto-explicatif

- `ServerType.summary(params) -> (clé, paramètres) | None` : le **plugin** décrit son mode réel.
  Implémenté pour les 4 types (`summary.command_watch|control|kill|stop_only` + variantes
  `_noport`, `summary.hermes`, `summary.wsl_distro|wsl_all`, `summary.stt`). L'UI affiche la
  phrase en gris sous le titre, sans connaître un seul champ.
- `Field.section_key` + `ui._pack_fields()` : les champs consécutifs qui partagent une section
  s'affichent sous un titre traduit, avec une aide facultative `<clé>_hint`. Le type « Service
  surveillé » découpe ainsi son formulaire en *Sonde* / *Pilotage* / *Accès & affichage* — les
  autres types n'ont pas de `section_key` et gardent leur formulaire à plat. Ordre des champs
  réordonné pour que les sections soient contiguës (`url` passe en dernier).
- **Piège de test** : le premier cycle du sondeur peut dépasser plusieurs secondes (WSL, STT) ;
  une capture à délai fixe photographie « Lecture de l'état… ». Attendre que
  `runtime.snapshot_data` soit renseigné avant de conclure.
- Vérifié : `harness2.py` **25/25** (résumés par type et par variante, aucun `{placeholder}`
  résiduel, 3 sections contiguës, parité i18n 317 clés × 5) ; 5 captures (surveillance,
  commandes, Hermes, formulaire haut et bas).

### Chantier 3 — modèles rapides et chemins

- **Presets** (`AddServerView._build_presets`, visibles seulement pour le type « Service
  surveillé ») : *Port / réseau (lecture seule)* (vide les commandes, focus sur le port),
  *Programme ou script local* (focus sur la commande), *LM Studio* (`lms server start` /
  `lms server stop`, port 1234, `/v1/models`, nom + icône de l'exe détecté), *Ollama*
  (`ollama serve`, port 11434, `/api/tags`, icône si `ollama.exe` trouvé). Rien n'est exécuté :
  les presets ne remplissent que des champs (le dit l'aide sous la rangée).
- **Boutons « Parcourir… »** : `Field.browse` = `"dir"` (dossier) ou `"exe"` (programme), rendu par
  `widgets.TextField(action=…)` collé à droite du champ. Posé sur `hermes_root`, `workdir` et
  `command` ; un chemin d'exécutable avec espaces est mis entre guillemets (sinon le shell coupe).
- **Réglages généraux** : deux boutons sous le chemin du `config.json` — *Ouvrir le dossier*
  (`os.startfile`) et *Copier le chemin* (presse-papiers), tous deux journalisés.
- `AddServerView` reçoit un champ **Icône** (avec son bouton Parcourir) : sans lui, le preset
  LM Studio n'avait aucun endroit où poser l'icône détectée. `App.create_server(..., icon="")`
  enregistre l'icône dans la foulée de l'ajout.
- Vérifié : `harness3.py` **22/22** (presets, guillemets, focus, présence/absence du bouton
  Parcourir champ par champ, icône enregistrée puis serveur de test supprimé, dossier + copie du
  chemin espionnés) ; 3 captures (ajout avec presets, réglages généraux, réglages Hermes).

### Chantier 4 — sortie console des commandes

- `CommandController.start()` lance désormais avec `stdout=PIPE`, `stderr=STDOUT` ; un thread démon
  (`_start_reader`) vide le tube dans un `deque(maxlen=80)`. Décodage en cascade utf-8 → cp1252 →
  cp850 (cmd.exe écrit dans la page de code de la console, sinon les accents sortent en `?`).
- `_check_exit()` (appelé par `snapshot()`, donc par le sondeur) journalise
  `log.command_exit_output` = code + durée + dernière ligne émise. **Deux filtres appris du cas
  réel** : rien si la commande rend la main avec le code 0 *et* que le service tourne (c'est
  exactement `lms server start`, qui délègue à un démon), rien après un arrêt demandé depuis
  l'app (`_stop_requested`), sinon chaque clic sur Stop produisait une fausse « chute ».
- Bouton **« Sortie console »** sur le panneau (masqué en observation pure) → fenêtre
  rafraîchissable dans `App.show_output()`, alimentée par `controller.output_lines()`.
- Vérifié : `harness4.py` **15/15** (échec code 3 avec le message exact du programme remonté une
  seule fois, sortie code 0 informative, lecture en direct d'une commande vivante, aucune alerte
  trompeuse, fenêtre ouverte et lignes affichées). Harnais 1 à 3 rejoués après coup : 28/28,
  25/25, 22/22 (le seul attendu modifié : la liste d'actions d'une commande contient maintenant
  `output`).

### Chantier 5, tuiles partageables, plugins et démarrage avec Windows (9 nov 2026)

**Organisation des tuiles** (`config.move_server` / `duplicate_server` / `update_server(enabled=)`)
— monter/descendre échange deux entrées de la liste et sauve ; dupliquer insère la copie *juste
après* l'original (paramètres, interrupteurs, boutons, icône) ; la pause est le booléen `enabled`,
désormais respecté de bout en bout.

- **Piège corrigé** : le sondeur ne rafraîchissait que les tuiles actives, donc une tuile mise en
  pause gardait son **ancien panneau** (« En marche » figé). `refresh()` est maintenant appelé
  pour toutes les tuiles — il renvoie un instantané « en pause » sans rien sonder — et
  `set_server_enabled` rafraîchit immédiatement.
- Une tuile en pause n'a plus de point d'état dans le rail et disparaît du menu du tray.

**Menu du tray multi-serveurs** : une ligne par serveur *pilotable* (`●`/`○`, libellé + action
réelle reprise de l'instantané — donc « Arrêter l'accès distant » pour Hermes, « Démarrer » pour
les autres), action `toggle:<id>` traitée par le fil d'affichage. Les serveurs en pause sont
masqués, pas grisés : un menu Windows ne sait pas griser proprement un élément dynamique ici.

**Alerte de chute** : transition `running → autre` détectée dans le sondeur → `log.fall_detected`
+ une bulle (option générale `notify_on_fall`, activée par défaut). Deux filtres : rien si l'arrêt
a été demandé depuis l'app (`expect_stop_until`, fenêtre de 20 s posée au clic sur Stop) et rien
si le serveur n'a jamais tourné. La bulle part par un événement `("balloon", …)` : l'appel Win32
se fait sur le fil d'affichage, pas dans le thread de sondage.

**Compteurs + filtre du journal** : `runtime.stats` (chutes, démarrages, instant du dernier
départ) ; le nombre de chutes s'ajoute à la ligne d'état. Le filtre du journal agit sur le texte
**traduit** (insensible à la casse) et vide/repeuple la vue — `_reset_log()` remet le compteur de
lignes à zéro, sinon la vue incrémentale ne voyait pas le changement de filtre.

**Actions sur mesure** : `server["actions"] = [{label, target}]` (3 maximum, champs vides
écartés). Une cible `http(s)` s'ouvre dans le navigateur, tout le reste est lancé par le shell
dans le dossier de travail du service, détaché, sortie non capturée (ce n'est pas un service à
surveiller). L'identifiant interne est `custom:<n>`, traité par l'application et non par le
plugin — n'importe quel type en profite.

**Tuiles partageables** (`hermesserv/tiles.py`) : format `{"format": "hermesserv-tile",
"version": 1, type, label, params, options, actions, icon}`. Exporter = un bouton dans les
réglages de la tuile ; importer = un bouton dans les réglages généraux. À l'import : format et
version vérifiés, type obligatoire (sinon « installe d'abord le plugin »), paramètres repassés par
`validate()` du type, options complétées par les défauts, icône reprise **seulement si le fichier
existe** sur cette machine. Import invalide = ligne de journal, aucun serveur ajouté.

**Plugins utilisateur** (`hermesserv/plugin_loader.py`) : `%LOCALAPPDATA%\HermesServ\plugins\<nom>\`
avec `plugin.json` (`id`, `name`, `module`) et un module exposant `TYPE` (ou `register()`).
Contraintes tenues : identifiant déjà pris = refus, exception d'un plugin = erreur enregistrée
(`plugins.LOAD_ERRORS`) et **les autres types continuent**, aucun accès hors du dossier du plugin.
Les textes d'un plugin peuvent être littéraux (`Field("port", "Port TCP")`), `i18n.t` rendant la
clé telle quelle. Le bouton « Dossier des plugins » crée le dossier et y écrit un `README.txt`
d'usage ; « Recharger les plugins » relit sans redémarrer.

**Démarrage avec Windows** (`hermesserv/autostart.py`) : valeur `HermesServ` dans
`HKCU\...\CurrentVersion\Run`. L'état affiché est **lu dans le registre** (pas un booléen de
config qui mentirait si la clé est supprimée à la main) ; l'écriture n'a lieu que sur clic. En exe
la commande est l'exécutable, depuis les sources c'est `pythonw.exe hermes_control.py`.

**Vérifié** : `harness5.py` **49/49** (ordre/duplication/pause + relecture disque, tuiles valides
et 4 refus, plugin valide + plugin cassé dans un dossier redirigé vers le scratch, registre
seulement lu, menu du tray par serveur, transitions et bulle unique, arrêt demandé silencieux,
actions URL/commande espionnées, import/export de bout en bout, filtre du journal, parité i18n
385 clés × 5) ; harnais 1 à 4 rejoués : 28/28, 25/25, 22/22, 15/15. Captures : réglages d'une
tuile (organisation, actions, export) et réglages généraux (démarrage, tuiles/plugins).

### Bouton « sortie console » seulement quand il y a une sortie (9 nov 2026)

Le bouton apparaissait sur toute tuile pilotable, même quand la commande n'écrivait rien : avec le
lanceur LlamaGUI (`powershell -File …ps1`, qui démarre le serveur par `Start-Process`), il ouvrait
une fenêtre vide. Il n'est plus ajouté que si le tampon de sortie contient des lignes — un bouton
sans effet est un piège. Conséquence : `harness.py` attend `["toggle"]` avant le premier octet,
`harness4.py` vérifie l'apparition du bouton dès qu'une ligne arrive.

### Cible en une seule ligne (9 nov 2026)

Trois champs (hôte, port, chemin de vérification) disaient la même chose sous trois formes et
faisaient hésiter : « si je veux juste regarder le port 5451, je mets quoi où ? ». Désormais **un
seul champ** :

- `127.0.0.1:5451` ou même `5451` → surveille le port 5451 de cet ordinateur ;
- `:5451` → idem (hôte par défaut) ;
- `192.168.1.50:8123` → une autre machine du réseau ;
- `http://127.0.0.1:9931/v1/models` → une URL décompose hôte, port **et** chemin de vérification ;
- vide → seul le processus lancé est surveillé.

Le **fichier de configuration ne change pas** : `host`, `port` et `check_url` y restent, ce qui
laisse intactes la sonde, les résumés, les boutons et les tuiles déjà exportées. Le type déclare
`TARGET_FIELDS`, `target_text()` (recomposition pour l'affichage) et `parse_target()` (décomposition
à l'enregistrement) ; l'UI fait le pont (`_collect_params`, `_apply_preset_to`). Seul le type
« commande » en déclare — Hermes garde ses champs séparés (pas de chemin de vérification, et un
`url` publique distincte). Étiquettes retirées des presets : `host`/`port`/`check_url` d'un preset
se recombinent dans la ligne.

Vérifié : `target_line.py` **9/9** (un seul champ, plus de Host/Port/URL, affichage
`127.0.0.1:1234/v1/models`, `127.0.0.1:5451` et `5451` enregistrés, URL décomposée, preset
LlamaGUI → `127.0.0.1:5240`, Hermes inchangé).

**Tuile consolidée le 9 nov 2026** (accord de l'utilisateur) : la tuile réelle porte désormais la
commande directe `"…\LLama-GUI\.venv\Scripts\python.exe" server.py`, le dossier du projet, la
cible `127.0.0.1:5240`, la sonde `llama-server = 9931` et les deux boutons (`Open LlamaGUI`,
`Open the llama.cpp web UI`). Le lanceur `launch-llama-gui.ps1` est abandonné pour cette tuile :
il démarrait le serveur par `Start-Process`, donc hors de portée du bouton « Arrêter ».
Sauvegarde : `config.json.bak-20261109215550` (et une seconde avant l'essai). Vérifié sur une
copie de la config : démarrage → port 5240 en écoute, phase *running*, pid, **4 lignes de sortie
capturées** (`GUI listening at http://127.0.0.1:5240`) ; arrêt → port fermé, phase *stopped*.

**Rangée de boutons** : la largeur de chaque bouton est désormais mesurée sur son libellé
(`tkfont.measure` + 34 px) et la ligne se ferme quand il n'y a plus la place — avec sept boutons,
« Open the llama.cpp web UI » passait à la ligne suivante mais restait coupé à largeur fixe.

**Ancien état, avant consolidation** : la tuile portait une commande vers
`C:\Users\<user>\LLama-GUI\.launcher\launch-llama-gui.ps1` (créé le 2 nov). Ce lanceur ouvrait le
navigateur et affichait une boîte d'erreur, mais il démarrait le serveur par `Start-Process` : le
processus n'était plus un enfant de HermesServ, donc « Arrêter » ne pouvait rien tuer et aucune
sortie n'était capturée — d'où le bouton « Console output » qui ouvrait une fenêtre vide.

### Tuile LlamaGUI complète : preset, sous-serveur, accès en un clic (9 nov 2026)

Demande de l'utilisateur : LlamaGUI sert son UI sur `127.0.0.1:5240`, et **c'est elle** qui lance le
`llama-server` (port par défaut **9931**, confirmé dans `backend/config.py` et `docs/directory.md`
du projet) dont la webui sert à discuter. Or la tuile ne savait rien lancer et n'offrait aucun
accès.

- **Preset LlamaGUI** (`hermesserv/presets.py`, table partagée ajout/serveur) : détecte le dossier
  (celui qui contient `server.py`), prend le Python du `.venv` du projet, remplit
  commande `"…\.venv\Scripts\python.exe" server.py`, dossier de travail, port 5240, URL publique,
  icône `assets\Llama-GUI.ico`, **sonde supplémentaire `llama-server = 9931`** et deux boutons
  sur mesure (`Ouvrir LlamaGUI` → 5240, `Ouvrir la webui llama` → 9931). Rien n'est exécuté.
- **Les presets existent maintenant aussi dans les réglages d'une tuile** (même table) : on peut
  donc transformer la tuile LlamaGUI existante en trois clics au lieu de tout retaper.
- **Ajout d'un serveur** : éditeur de boutons sur mesure (3 couples libellé/cible) et
  `create_server(..., actions=)` ; sinon un preset posé à la création perdait ses boutons.
- **Rangée de boutons qui passe à la ligne** : avec 7 boutons (LlamaGUI), le dernier était coupé au
  bord. `_paint_actions` calcule combien tiennent par ligne (minimum 132 px) et empile des lignes.
- Vérifié : `llamagui_preset.py` **17/17** (commande, dossier, ports, sonde 9931, icône, deux
  boutons, enregistrement, bouton Démarrer + lignes du panneau, création avec boutons) ;
  captures avant/après sur la tuile réelle reconstituée.

### Défilement de la colonne des tuiles (9 nov 2026)

Constaté par l'utilisateur avec 6 serveurs : les tuiles sortaient de l'écran sans moyen de les
atteindre. Trois causes, dont deux invisibles jusqu'au test :

1. **La molette était morte dès qu'on avait ouvert une vue de réglages.** Chaque `ScrollFrame`
   posait son propre `bind_all("<MouseWheel>")` ; Tk n'accepte qu'**un** gestionnaire par
   séquence et par tag, donc le dernier créé écrasait le précédent — celui du rail, construit au
   démarrage, disparaissait à la première navigation.
2. **Aucune barre visible** : le contenu était simplement coupé au bord de la colonne.
3. **Ordre de `pack()`** : une barre packée *après* la zone défilante (elle-même `fill=both,
   expand`) restait à 1×1 px — mesuré, pas supposé.

Correctifs (`widgets.py`, `ui.py`) :

- un **répartiteur unique** pour toutes les zones défilantes : registre des `ScrollFrame` vivantes
  + routage selon le widget **sous le pointeur** (`event.x_root/y_root`, pas `winfo_pointerxy`,
  pour rester testable) ; les instances mortes sont retirées au passage.
- `widgets.ThinBar` : barre fine dessinée à la main (pouce arrondi, glisser pour défiler,
  survol éclairci), qui **ne se dessine que si le contenu dépasse** ; branchée sur le crochet
  `ScrollFrame.on_scroll` (appelé à chaque défilement et redimensionnement).
- dans le rail, la barre est packée **avant** la zone défilante, à droite ; la tuile « Réglages »
  reste épinglée en bas et n'est jamais recouverte.

**Vérifié** : sonde `rail_probe2.py` **8/8** sur une config de 10 serveurs (contenu 1 396 px pour
583 px visibles) — molette bas/haut, barre dessinée quand ça déborde et absente quand tout tient,
glisser du pouce jusqu'au bout, **molette encore active après ouverture de la vue Réglages**.
Capture sur la config réelle de l'utilisateur (6 tuiles) : barre présente, tuiles coupées
proprement, engrenage intact.

### Gateway local visible dans l'onglet Hermes + sondes supplémentaires (9 nov 2026)

Constat de l'utilisateur : « Desktop backend : non détecté » alors que le backend tournait.
Mesuré sur la machine : le gateway local écoute bien sur `127.0.0.1:8642`
(`python -m hermes_cli.main --profile default gateway run --quiet`, pid 26468) et son `/health`
répond `{"status":"ok","platform":"hermes-agent","version":"0.23.0"}` ; le desktop est réglé sur
`primary: "local"` dans `%APPDATA%\Hermes\connections.json`. Le détecteur existant ne cherchait
que `hermes.exe serve|dashboard` — le gateway lancé par Electron n'a pas cette forme, d'où le
« aucun backend » permanent. **La ligne ne mentait pas : elle répondait à une autre question.**

- `sysinfo.read_gateway_config(root)` : lit `config.yaml` sans PyYAML. Deux blocs `api_server:`
  coexistent (un sous `gateway:`, un sous `platforms:`) → on retient celui qui porte un `port` ou
  un `enabled`, pas le premier venu. Défauts 127.0.0.1:8642.
- `sysinfo.hermes_gateway_state(root)` : config + écouteur réel (`listener_pid`) + sonde `/health`
  (récupère la version). TTL 4 s dans le contrôleur.
- Trois lignes dans l'onglet Hermes, chacune honnête :
  * `row.hermes_gateway` → « activé · 127.0.0.1:8642 · v0.23.0 » / « aucun processus sur 8642 » /
    « désactivé dans config.yaml » ;
  * `row.hermes_desktop_target` → « ce PC (gateway local) » ou l'URL distante (registre du
    desktop) ;
  * `row.hermes_backend_procs` (ex-`row.desktop_backend`, renommé) → « processus : {count} » /
    « aucun détecté » : ne parle plus que des `serve`/`dashboard`.
- **Sondes supplémentaires** sur les tuiles génériques (option, champ `extra_checks`) : une par
  ligne, `nom = cible` ; cible = port (`8642`), hôte:port (`192.168.1.50:8123`) ou URL
  (`http://…/health`). 6 au maximum, cache 5 s (sinon six sondes HTTP par cycle coûteraient plus
  que la sonde principale). Chaque ligne a son point d'état ; un libellé littéral passe par le
  repli de `i18n.t` (clé inconnue rendue telle quelle).
- Vérifié : gateway détecté (`pid 26468`, `v0.23.0`, desktop « ce PC ») ; sondes supplémentaires
  `gateway = 8642` → « écoute · pid 26468 », `sante = http://127.0.0.1:8642/health` → « répond
  (200) », `vide = 45999` → « fermé », `invalide = abc` → « cible invalide » ; deux captures
  (onglet Hermes, tuile générique) ; harnais 1 à 5 rejoués sans régression.

### Exécutable et relance

- `dist/HermesServ.exe` reconstruit avec `Python314` (PyInstaller 6.22.3, `HermesServ.spec`) ;
  sauvegardes `.bak-<horodatage>` conservées. Taille 32,6 Mo (le build du 7 nov en faisait 39,4 —
  même spec, autre interpréteur ; l'exe est vérifié de bout en bout : fenêtre trouvée par titre,
  capture réelle, fermeture propre).
- App relancée sur la **vraie** config (`DETACHED_PROCESS`, env purgé des `_PYI*`), onglet Hermes
  intact ; aucun test destructif sur le service de l'utilisateur (9119 était libre).

## « Stop server » devient « Stop remote access » (8 nov 2026)

Le libellé mentait, vérifié sur la machine : le bouton ne tue que le dashboard du port 9119
(le listener + ses enfants, `terminate_tree`), jamais le moteur Hermes — celui-ci appartient à
l'app desktop (gateway du port 8642, process séparé, survit à l'arrêt).

Décision : étiquettes propres au type, `btn.hermes_start` / `btn.hermes_stop` dans les 5 locales
(`Start remote access` / `Stop remote access`), déclarées dans `plugins/hermes.py`. Les clés
génériques `btn.start` / `btn.stop` restent pour les autres types (WSL, commande), qui eux
arrêtent bien « leur » process. Pas d'infobulle pour l'instant.

Conséquence relevée au passage : `adopt()` n'est appelé nulle part, donc `owned_pid` ne se
remplit qu'au démarrage fait *par* l'app. Après un rebuild + relance d'HermesServ avec un
dashboard déjà en marche, le bouton est grisé (un contrôleur neuf voit `owned_pid = None`,
mesuré). Ce n'est pas un bug de l'arrêt — c'est le garde-fou « on ne tue pas ce qu'on n'a pas
lancé » — mais c'est à trancher : soit on laisse (arrêt manuel une fois), soit on adopte les
listeners dont la ligne de commande correspond exactement au dashboard attendu.

## Étape 1 du plan « icônes + généralisation » : icônes dans le rail (7 nov 2026)

Plan de travail dans `ROADMAP.md` (« Plan de travail — 7 nov 2026 »), carte blanche de
l'utilisateur sur l'ordre des étapes, **une seule réserve : le plugin Hermes ne régresse jamais**.

**Ce qui a été ajouté** (`widgets.py`, `ui.py`, `config.py`, 5 locales) :
- **Pictogrammes vectoriels par type** dessinés au Canvas (`widgets.draw_icon`), dans la couleur
  du type, à la place de la lettre : bulle de discussion (hermes), terminal (wsl), microphone
  (stt), cible (portwatch). Un type inconnu garde sa lettre (repli). Toujours en complément du
  point d'état + libellé — jamais en remplacement.
- **Icône personnalisée par serveur** : clé `icon` (chemin vers `.png`/`.ico`) dans `config.json`,
  champ « Icône » dans les réglages du serveur. Chargement `widgets.load_icon` en repli en
  cascade : PIL (tous formats, carré centré puis redimensionné) → `tk.PhotoImage` (PNG) →
  pictogramme du type si fichier absent/illisible.

**Bug trouvé et corrigé pendant la vérification** : `config._normalise` reconstruit chaque entrée
de serveur avec une **liste de clés en dur** — toute clé inconnue (dont `icon`, et aussi
`created`) était épluchée au chargement. Symptôme : le pictogramme s'affichait au lieu de
l'icône personnalisée alors que le chargeur marchait isolément. Corrigé : `icon` fait partie des
clés conservées. **Piège général** : ajouter un champ dans `config.json` exige de l'ajouter aussi
dans `_normalise`, sinon il disparaît silencieusement au premier chargement.

**Vérifié le 7 nov 2026** (config de test `HERMESSERV_CONFIG`, aucun effet de bord) :
- `--shot` → `apercu_icones_rail.png` : les 4 pictogrammes + losange orange personnalisé sur
  LlamaGUI (522 px orange, boîte 39×39, centre blanc — vérifié au pixel, pas seulement à l'œil),
  tuile WSL avec chemin introuvable → repli pictogramme correct.
- Page de réglages → `apercu_reglages_icone.png` : champ « Icône » + aide traduite, aucun texte
  tronqué, aucune clé brute.
- **Non-régression Hermes** (ligne rouge 1) : `snapshot()` en lecture seule sur la vraie config →
  `phase=stopped` (le serveur n'était pas lancé), 5 lignes, 4 actions, aucune exception.
- Round-trip `icon` : `update_server` → save → reload conserve la valeur ; vidage → `""`.
- i18n : 245 clés × 5 langues (fr/en/es/pt-BR/zh), parité vérifiée.
- Panneau Hermes visible et intact sur `apercu_icones_rail.png` (« Arrêté » = état réel du moment).

**État** : source + `HermesServ.bat` à jour ; **`dist/HermesServ.exe` NON reconstruit** (l'app de
l'utilisateur verrouillait le fichier — reconstruire à sa demande, avec la recette habituelle).
Reste en tête : le libellé `rail.add` (« add server ») est en anglais dans toutes les locales —
cosmétique, à trancher avec l'étape 3 (refonte du flux d'ajout).

## Permission : lancer / fermer l'app HermesServ librement (7 nov 2026)

À la demande de l'utilisateur : **j'ai le droit de lancer et de fermer l'application HermesServ
moi-même**, selon les besoins du développement (rebuild de l'exécutable, tests, remise en service
à la fin d'un chantier). Ce qui reste soumis à accord : tout effet sur **le reste** de la machine
(process tiers, config, tâches planifiées, écriture hors projet) — et bien sûr le serveur Hermes
reste pilotable uniquement via l'app ou à sa demande explicite.
**Relancer l'app après un rebuild** : `subprocess.Popen` avec `DETACHED_PROCESS` et un `env=`
filtré des variables `_PYI*` (voir pièges ci-dessous) — **pas** `cmd //c start` depuis le shell
de l'agent : constaté le 7 nov, ça ouvre une console interactive au lieu de lancer l'app.

## Étape 2 : type « commande personnalisée » (7 nov 2026)

`hermesserv/plugins/command.py` — le type généraliste : lance, surveille et arrête **n'importe
quelle ligne de commande** (serveur de dev, script, application) sans écrire de plugin.

**Contrat** : champs `command` (requis), `workdir`, `env` (une par ligne `NOM=valeur`), `port`
(optionnel), `check_url`, `url` ; interrupteurs `start_on_app_start` et `stop_on_quit` (le
second est géré génériquement par `App.quit`). Libellés d'état propres (`phase_labels` /
`external_label_key` / `phase_keys` du contrat WSL) : « En marche », « Arrêté », « En marche
(hors de l'app) ». Pictogramme « boîte + triangle exécuter » (turquoise #2dd4bf), lettre de
repli `›_`.

**Règles du socle tenues** : arrêt ciblé `sysinfo.terminate_tree` (enfants récursifs puis
process) ; un jumeau démarré hors de l'app est **adopté** (affiché, jamais tué) — le bouton
d'arrêt reste cliquable et écrit le refus (`log.command_stop_refused`) ; détection par
**jetons d'argv** (nom du binaire sans extension + nos arguments exacts, TTL 5 s), avec garde
contre les faux positifs Electron (`--type=…`).

**Pièges vérifiés dans le harnais**
- **`Popen(shell=True).terminate()` ne tue que l'enveloppe `cmd`, l'enfant survit.** Constaté :
  le ping orphelin du test a contaminé les deux tests suivants (« déjà en marche » à tort).
  C'est exactement pour ça que le plugin tue l'**arbre** ; un test qui simule un arrêt doit
  faire pareil (`sysinfo.terminate_tree`, pas `terminate()`).
- **Le pid journalisé ≠ le pid affiché** si on lit juste après `Popen` : le shell n'a pas encore
  élu son enfant. `_real_pid()` prend l'unique enfant récursif du shell ; 0,3 s d'attente au
  lancement pour que le journal et l'affichage donnent le même pid.
- Le shell (`shell=True`) est assumé : « ligne de commande » au sens console, `.bat` compris.

**Vérifié le 7 nov 2026** — harnais hors dépôt, **26/26 assertions** : validation du formulaire
(port vide accepté, port invalide/commande vide refusés) ; cycle arrêté → démarré (pid à nous,
« en marche ») → arrêté (process mort vérifié) → re-arrêt « pas lancé » ; **jumeau externe**
détecté et adopté, **arrêt refusé deux fois**, son process intact ; `env` + `workdir` prouvés par
le fichier écrit par la commande ; auto-démarrage **une seule tentative**, arrêt propre.
Interface : `--shot` source (`apercu_type_commande.png`) et exe (`apercu_exe_commande.png`) —
panneau complet « En marche » (pid, RAM, uptime, 3 lignes, bouton rouge, 2 interrupteurs,
journal réel), pictogrammes des 5 tuiles, aucune clé brute, aucun blocage « lecture de l'état »
(le piège du type absent du binaire est écarté). **Aucun ping orphelin** après chaque session
(`stop_on_quit` du config de test). i18n : **271 clés × 5 langues**, parité vérifiée.
Non-régression Hermes : `snapshot()` en lecture seule sur la vraie config, inchangé.

## Traductions et exécutable (7 nov 2026, suite)

- « add server » → **« Ajouter serveur »** (fr, à la demande), « Add server » (en),
  « Añadir servidor » / « Adicionar servidor » (capitalisés), réf. mises à jour dans
  `settings.no_servers` et `empty.hint` des 5 langues, zh « 添加服务器 ». pt-BR
  `value.phone_offline` → « fora do ar » (cohérent avec « no ar »). Reste identique à l'anglais
  volontairement : `HermesServ`, `Desktop`, `Shell`, `API`, `Port`, `Distribution`, `WSL`.
- **`dist/HermesServ.exe` reconstruit** (recette `HermesServ.spec`, 7 nov 21:50, 32,6 Mo) et
  vérifié en packagé (`--shot` sur configs de test : textes résolus depuis le bundle, 5 types
  présents). Ancien binaire : `dist/HermesServ.exe.bak-20261107T214128`.
- **App relancée sur la vraie config** à la fin du chantier (2 PID, pont STT auto-démarré sur
  8770 comme réglé par l'utilisateur).

## Champ Icône : Parcourir / Appliquer + icônes de .exe/.dll (7 nov 2026, suite)

À la demande de l'utilisateur : un bouton **Parcourir** (sélecteur de fichier, filtre « images et
exécutables ») et un bouton **Appliquer** (retour immédiat : la tuile change tout de suite, et un
message vert ✓ / rouge ⚠ dit si le chemin est correct). Parcourir applique aussitôt après le
choix. Appliquer persiste dans `config.json` (« Enregistrer » persiste le formulaire entier).
Champ vidé + Appliquer = retour au pictogramme par défaut.

**Réponse à la question « peut-on avoir l'icône d'un .exe ou .dll ? » : OUI.** L'icône
embarquée est extraite par l'API Windows — `PrivateExtractIconsW` d'abord (taille exacte
demandée, donc nette), repli `ExtractIconExW` (32 px), conversion HICON → image PIL via
`GetIconInfo`/`GetDIBits` (`widgets._pe_icon`). **Zéro dépendance** (ctypes). Seule la première
icône (index 0) est retenue ; un fichier sans icône (`kernelbase.dll` vérifié) renvoie None et
le pictogramme du type reste affiché.

**Pièges de l'extraction (vérifiés, à ne pas refaire)**
- **`ctypes` sans `argtypes` tronque les handles 64 bits** : `OverflowError: int too long to
  convert` sur `shell32.dll` alors que `notepad.exe` passait (handle par chance < 2^31).
  Déclarer les signatures (`HICON`, `HBITMAP`, `HDC`…) pour tous les appels.
- **`GetDIBits` avec `biHeight > 0` = lignes bas vers haut** → retourner l'image
  (`FLIP_TOP_BOTTOM`). Ma condition était **inversée** : l'icône sortait à l'envers — à 44 px,
  une vision l'a décrite en « cube 3D » (c'étaient les anneaux de la spirale du bloc-notes, en
  haut au lieu d'en bas).
- **Les octets d'icône sont prémultipliés** : sans conversion (`rgb * 255 / alpha`), les bords
  semi-transparents deviennent sombres et « déchiquetés ». Comparé à 256 px : variante sans
  conversion = « corrompue », avec conversion = « propre et reconnaissable ».
- **Leçon vérification visuelle** : le modèle de vision peut **confabuler une page entière** —
  il a décrit « Serveur : Hermes » sur une capture qui montrait « Serveur : LlamaGUI » (vérifié
  par diagnostic d'état imprimé + recadrage). Croiser avec l'état réel (`app.view`,
  `selected_id`, `runtime.id`) avant de chercher un bug.

**Vérifié le 7 nov 2026** : messages exacts (« ✓ Icône appliquée : … » / « ⚠ Icône introuvable
ou illisible : … ») visibles à l'image, valeur persistée relue dans la config de test ;
`notepad.exe` et `shell32.dll` extraites et nettes, `kernelbase.dll` → repli pictogramme ;
captures `apercu_icone_exe.png` (état appliqué) et `apercu_icone_pourrie.png` (état refusé) ;
i18n **278 clés × 5 langues** (parité), aide `field.icon` mise à jour pour mentionner .exe/.dll ;
**exe reconstruit (22:17, 39,4 Mo) et app relancée** (2 PID, pont STT auto 8770).

## Fenêtre redimensionnable + taille d'interface (7 nov 2026)

Demande : « pouvoir redimensionner la fenêtre, globalement écrit trop petit ». **Mesuré** :
écran 2560×1440 en 96 DPI → `SC = 1.0` → les `font(8..11)` étaient de vrais 8-11 px.

**Changements** : `widgets.UI_SCALE` (multiplicateur dans `sc()` — tout est dimensionné via
`sc()`, donc l'agrandissement est uniforme : textes, boutons, cartes, fenêtre) ; réglage
persistant `general.ui_scale` (Réglages → **Taille de l'interface** : 100/115/130/150 %,
**défaut 130 %**) ; fenêtre `resizable(True)` avec `minsize` sc(860)×sc(560) ; les boutons
d'action se recalculent sur la largeur réelle de leur rangée (la signature de `_paint_actions`
inclut la largeur → reconstruits au redimensionnement, bord droit aligné sur les cartes).

**Piège** : `config._normalise` filtre `general` sur les clés de `default_general()` — il a
fallu y ajouter `ui_scale` pour qu'elle survive au chargement (même piège que `icon`, cf. plus
haut). **Leçon préférence** : la règle « fenêtre fixe pour un utilitaire » de la skill est
remplacée ici par « fenêtre redimensionnable + réglage de taille » (demande explicite).

**Vérifié le 7 nov 2026** : captures `apercu_taille_130.png` (1170×988, textes confortables)
et `apercu_taille_agrandie.png` (1560×1080 simulée à la souris : cartes pleine largeur,
journal qui grandit, bouton d'action aligné, rien de tronqué ni chevauché) ;
`apercu_reglages_taille.png` (case « Taille de l'interface », 130 % sélectionné, aide
traduite) ; exe reconstruit pareil (1170×988 en packagé) ; 285 clés × 5 langues en parité.

## Type commande : commande d'arrêt personnalisée — le cas LM Studio (7 nov 2026)

Demande : « pouvoir mettre des commandes pour lancer ou arrêter le serveur que l'on commande
(ex. LM Studio) ». **Le cas réel** : `lms server start` rend la main immédiatement et laisse
un serveur qui **n'est pas notre enfant** — l'ancien modèle (PID = notre process) ne tenait pas.

**Changements** (`plugins/command.py`) :
- champ **`stop_command`** (facultatif, ex. « lms server stop ») ; sans lui, l'arrêt tue le
  process lancé et ses enfants (comme avant) ;
- `_started_here` (« la montée courante vient de nous ») **survit à la mort du shell** de
  lancement ; « vivant » = notre process, un jumeau argv, **ou le port en écoute** (l'état
  vient de la sonde, pas du process — le vrai modèle LM Studio) ;
- arrêt par commande personnalisée **réservé à ce qu'on a lancé**, sauf option explicite
  `stop_external` (« Autoriser l'arrêt d'un service lancé hors de l'app ») ; échec de la
  commande d'arrêt → erreur (stderr) au journal, service intact.

**Vérifié** : harnais **faux `lms`** (`fakelms.py` : `start` détaché + fichier pid, `stop` ciblé)
— **23/23** : cycle complet (start → running prouvé par le port, pid = celui qui écoute,
reconnu comme le nôtre → stop par commande → port libéré) ; externe : refusé par défaut,
arrêtable avec l'option ; échec de la commande d'arrêt = `log.command_stop_cmd_failed`, service
intact ; régression ping (arrêt ciblé) 26/26 OK.

**Config réelle** : `config.json.bak-20261107T223620`, entrée **« LM Studio »** ajoutée
(`command-1` : `lms server start` / `lms server stop`, port 1234, check `/v1/models`, icône
extraite de `LM Studio.exe`). LM Studio installé : `%LOCALAPPDATA%\Programs\LM Studio\` +
`lms.exe` dans `~\.lmstudio\bin` (sur le PATH). **À trancher par l'utilisateur** : il avait
ajouté lui-même une tuile « LMS » (portwatch, port 1234) — deux tuiles sur le même port, garder
ou supprimer l'une des deux.

## Bug « le bouton Arrêter relançait le serveur » + relance automatique configurable (7 nov 2026)

**Symptôme rapporté** : une fois le serveur LM Studio lancé depuis la tuile, l'arrêter (« par
LMStudio ») le relançait, et il était impossible de l'arrêter sans quitter HermesServ.

**Cause (reproduite puis prouvée par harnais)** : le bouton « Arrêter le serveur » passe par
`Controller.action("toggle")`, qui choisissait start/stop avec **`_pid_state()` seul** — qui
ignore le port en écoute. Pour un service à la LM Studio (le vrai process n'est ni notre enfant
ni détectable par argv), `_pid_state()` renvoyait toujours `(None, …)` : **le bouton « Arrêter »
exécutait `start()`**. Le clic sur « Arrêter » relançait donc le serveur (ou ne l'arrêtait jamais
s'il tournait encore → `log.command_already`). Repro avant correctif : `toggle` → `start()` ;
après : `toggle` → `log.command_stop_cmd_ok`, port libéré.

**Correctif** : `action("toggle")` décide avec la même notion de « vivant » que l'affichage
(process **ou** port en écoute). Piège général : **une décision start/stop doit utiliser le même
critère que l'état affiché**, sinon le bouton et l'affichage divergent silencieusement.

**Demande suivante** : « si tu as mis une option de relance automatique, il serait bien de
pouvoir configurer cela à la main ». Nouvel interrupteur **`auto_restart`** (« Relancer la
commande si le service retombe »), **désactivé par défaut**, configurable dans le panneau du
serveur et dans `config.json` :
- **jamais après un arrêt demandé ici** (`_stop_requested`, armé par `stop()`, désarmé par
  `start()`) — un arrêt délibéré reste définitif ;
- **3 tentatives maximum consécutives** (compteur remis à zéro dès que le service tourne),
  10 s minimum entre deux tentatives, abandon annoncé une seule fois au journal.

**Vérifié** : harnais dédié **11/11** (option éteinte → aucune relance ; option allumée → une
relance par chute, journal `log.command_restart` {count}/3 ; arrêt délibéré → jamais de relance ;
3 échecs → abandon annoncé une fois) ; repro du bouton corrigé (arrêt réel, port libéré) ;
régressions : 26/26 (type commande) + 23/23 (faux lms) inchangées ; 288 clés × 5 langues en
parité ; exe reconstruit et app relancée.

## Retours utilisateur : lisibilité QHD, libellés, formulaire d'ajout + étape 3 (7 nov 2026, soir)

**1. « Les petites écritures sont trop petites (en QHD) ».** Mesuré : `font(8..10)` rendaient
10-13 px → illisible sur 2560×1440. Correctif : **plancher de lisibilité `FONT_MIN = 11`**
dans `font()`/`mono()` (aucune écriture ne descend sous 11 px de base ; les titres sont
inchangés). Les libellés du rail passant à 2 lignes plus souvent : tuiles hauteur `size+38`
et largeur de libellé `sc(88)`. **Le rail débordait aussi** avec 6+ serveurs à 130 % (8 tuiles
ne tenaient plus au-dessus de Réglages) : zone de tuiles dans un `ScrollFrame`, Réglages épinglé
en bas. Vérifié à l'image (8 tuiles sans chevauchement, libellés complets sur 2 lignes).

**2. « Chemin à interroger, tu veux parler du port ? »** — libellé mal choisi : c'est un chemin
HTTP interrogé **sur le port** pour vérifier que le service répond vraiment. Renommé
**« URL de vérification (facultatif) »** + aide (`field.check_url_hint`) : « Ex. /v1/models —
interroge ce chemin sur le port pour vérifier que le service répond vraiment. Vide = seul le
port est vérifié. »

**3. « Commande à lancer est aussi facultatif, non ? »** — oui : `command` n'est plus requis,
un serveur peut être piloté à l'arrêt seulement (`stop_command`). Validation croisée dans
`CommandType.validate` : au moins l'une des deux, sinon `add.error.command`. `start()` sans
commande → `log.command_no_start` (message clair).

**4. « Pourquoi « nom affiché » est pré-rempli avec le type ? »** — `AddServerView` pré-remplissait
le nom avec le libellé du type (`default_name = i18n.t(type.label_key)`). Supprimé : champ
vide, l'utilisateur choisit (la validation du nom existait déjà).

**5. Étape 3 du plan : ajout assisté par détection** (`sysinfo.listening_ports()` + WSL
`list_distros()`). Section **« Détecté sur cette machine »** dans le formulaire d'ajout, au-
dessus des champs (visible sans défiler) : ports en écoute (≥ 1024, triés, 12 max, libellés
`port · processus` raccourcis avec « … ») pour les types à port, distributions pour le type
WSL — **cliquer remplit les champs** (port/distro + nom suggéré si vide). L'utilisateur choisit
au lieu de taper.

**Vérifié** : captures `apercu_rail_8tuiles.png` (8 tuiles, rail défilant, rien de coupé) et
`apercu_ajout_assiste.png` (nom vide, 12 suggestions cliquables sans recouvrement, « URL de
vérification » + aide) ; zooms pixel pour les libellés ; validate commande (vide refusé, stop
seul accepté) ; `listening_ports()` = 38 ports réels détectés ; régressions 26/26 + 23/23 +
11/11 ; 292 clés × 5 langues en parité ; exe reconstruit et app relancée.

## Fusion « port / site » → « service surveillé » + aides de champs + plancher 13 px (7 nov 2026)

**Question de l'utilisateur** : « faut-il distinguer port/site à surveiller et commande
personnalisée, ou l'un n'est-il pas inclus dans l'autre ? » — **Si : c'était redondant** depuis
que la commande de lancement est facultative. Fusion en un seul type :
- `command` devient **« Service surveillé »** (`type.command.label`) : sonde port/URL + commandes
  de démarrage/arrêt facultatives ; **champ `host` ajouté** (127.0.0.1 par défaut) avec sonde
  **TCP pour un hôte distant** (`sysinfo.tcp_open`, 0,5 s) — un hôte distant n'a pas de PID
  local, la ligne port affiche « ouvert sur {host} / fermé ».
- **`portwatch` n'est plus enregistré** (module conservé comme référence). Les configs
  existantes sont **migrées au chargement** (`config._normalise`) : type → `command`, `host`,
  `port`, `check_url`, `url` conservés, commandes vides. Vérifié sur une copie de la vraie
  config (portwatch-1 → command, port 5240 et host conservés).
- Le « type sûr sans action » reste disponible : un service **sans commandes** ne peut rien
  lancer (`log.command_no_start`) et refuse l'arrêt (`log.command_stop_refused`).

**Aides « à quoi ça sert »** (5 langues, `field.*_hint`) : hôte (ce PC ou IP réseau), port
(facultatif : sans port seul le processus est surveillé), **URL de vérification** (chemin HTTP
interrogé sur le port), **URL publique** (affichée dans le bandeau, purement informative,
aucune vérification), **dossier de travail** (dossier de lancement), **variables
d'environnement** (passées au processus lancé uniquement, `NOM=valeur` par ligne), commande de
démarrage (exécutée au clic Démarrer, vide = surveillance seule), commande d'arrêt.

**Lisibilité** : découverte au passage — l'utilisateur avait réglé l'app en **anglais à 100 %**
(d'où « trop petit » malgré le plancher en unités de base). Plancher désormais **absolu en
pixels écran : 13 px** (`FONT_MIN_PX`), en plus des 11 px de base. Vérifié à l'image en anglais
100 % : tout lisible, aucun débordement, libellés de tuiles sur 2 lignes propres (hauteur de
tuile `size+44`).

**Autre correction vue à l'image** : « Nom affiché » s'affichait en double sur la page de
réglages du serveur (label autonome + label du champ) — doublon supprimé.

**Vérifié** : harnais migration+distant **11/11** (migration, sonde distante bornée 0,51 s,
validation) ; régressions 26/26 + 23/23 + 11/11 ; captures `apercu_aides_ajout.png` (4 types,
aides visibles, 12 suggestions), `apercu_aides_reglages.png` (toutes les aides, URL publique
« purement informative »), `apercu_echelle_100.png` (anglais 100 %) ; 296 clés × 5 langues en
parité ; exe reconstruit et app relancée.

## Libellés : « adresse publique » générique, « Tailscale device », « Run command (optional) » (7 nov 2026)

**Retours utilisateur** : « Public URL ne doit pas parler de téléphone — l'usage est plus général
que de checker si un téléphone est dispo » ; idem dans l'onglet Hermes ; « Command to run devrait
être Run command (optional) — on peut juste surveiller un port » ; question sur la portée des
variables d'environnement.

**Changements (5 langues, 297 clés, parité vérifiée)** :
- `field.url` → **« Adresse publique (facultatif) »** / **“Public address (optional)”**, aide
  générique : « Adresse à laquelle ce service est joignable depuis un autre appareil (ex. une URL
  Tailscale). Affichée dans le bandeau d'état — aucune vérification n'est faite dessus. » —
  plus aucune mention de téléphone. Le champ `url` du type **Hermes** n'avait pas d'aide du
  tout : `hint_key` ajouté (même texte).
- `field.phone_node` → **« Appareil Tailscale à surveiller »** + aide (« téléphone, portable,
  tablette… ») ; `row.phone` → **« Appareil Tailscale »** ; `log.intro_url` → « Adresse
  publique : {url} » (les 4 autres langues parlaient toutes de téléphone).
- `field.command` → **« Commande de démarrage (facultatif) »** / **“Run command (optional)”**
  (la surveillance d'un port seul est le cas nominal).
- `field.command_env_hint` précise la portée : variables passées **aux commandes de démarrage ET
  d'arrêt**, **locales à HermesServ** (rien d'autre ne les utilise, l'environnement système
  n'est pas modifié) — c'était une question explicite de l'utilisateur, réponse désormais dans
  l'interface elle-même.

**Vérifié** : captures en anglais (langue réglée par l'utilisateur) — plus aucun « phone » sur la
page Hermes, « Public address (optional) » et « Tailscale device to watch (optional) » présents ;
297 clés × 5 langues ; exe reconstruit et app relancée.

## Deux bugs d'affichage corrigés le 1er nov 2026 (soir)

**1. L'onglet Hermes clignotait « en ligne » ↔ « arrêté ».**
`sysinfo.listener_pid` filtrait sur `conn.status == "LISTEN"`. Sous **Windows, psutil 7.2.2 donne
des états fantaisistes pour le même socket d'écoute** : sur 6 appels de suite au port 9119 d'un
dashboard qui répondait **200 à chaque fois**, le PID a alterné `None`, `0`, `17212`. À chaque
`None` la phase tombait à *arrêté*, à chaque `17212` à *en ligne* — d'où le clignotement.
Corrigé deux fois plutôt qu'une :
- `listener_pid` reconnaît un écouteur à l'absence de **pair distant** (`raddr` vide) au lieu de
  l'état, et ne renvoie plus `0` comme s'il s'agissait d'une absence (`0` = écoute confirmée,
  propriétaire illisible) ; `proc_info(0)` renvoie vide au lieu de lire le « System Idle Process ».
- le contrôleur Hermes décide de la phase **par l'API d'abord** (`/api/status` répond = en ligne),
  le port ne servant plus qu'à distinguer *démarrage* de *arrêté*.

**2. La tuile STT restait sur « lecture de l'état… » indéfiniment.**
L'app tournait depuis `dist/HermesServ.exe` **buildé le 24 oct**, donc sans le type `stt` :
`service.get("stt")` → `None`, `ServerRuntime.controller` → `None`, et `refresh()` sort
immédiatement quand il n'y a pas de contrôleur → `snapshot_data` reste `None`, l'UI affiche
« premier sondage en cours » pour toujours. Ce n'était pas une sonde lente : rien ne sondait.
Rebuild de l'exe (recette inchangée) + vérification à l'image : 3 tuiles, états réels, aucune clé
i18n brute. Sauvegarde de l'ancien : `dist/HermesServ.exe.bak-20261101T175640`.

**Piège redécouvert (il était déjà noté plus bas)** : lancer un exe PyInstaller `--onefile` depuis
le shell de l'agent laisse fuiter `_PYI_ARCHIVE_FILE`, `_PYI_APPLICATION_HOME_DIR`,
`_PYI_PARENT_PROCESS_LEVEL`. Le lancement **suivant** échoue **sans aucun message** (`--noconsole`).
Symptôme observé : `Start-Process` réussit, aucune erreur, aucun process. Remède :
`Remove-Item Env:_PYI_ARCHIVE_FILE,Env:_PYI_APPLICATION_HOME_DIR,Env:_PYI_PARENT_PROCESS_LEVEL`
avant de relancer.

**Vérifié** : `listener_pid(9119)` stable (17212 × 8 appels), phases stables sur 8 cycles pour les
3 serveurs, capture de l'exe reconstruit (`--shot` sur config de test, `auto_launch_desktop` à
false), app relancée sur la vraie config (PIDs 14832/35860), **dashboard 17212 intact** (api 200
avant et après).

## 400 « Invalid request » : le sidecar n'accepte que du WAV (1er nov 2026, soir)

**Symptôme rapporté** : la tuile STT affiche « Amont injoignable : HTTPError 400 Bad Request »,
alors que le bouton *Vérifier l'amont* répond « c'est bon ». Les deux avaient raison — et les
deux messages étaient trompeurs.

**Cause** : le `whisper-server` lancé par OpenWhispr ne reçoit **pas** `--convert` (args observés :
`--model … --host --port --threads --language --max-len`). Il n'accepte donc que du **WAV**. Or une
dictée du desktop arrive en **webm/opus** → réponse **400 « Invalid request » en 1,7 ms**, sans
même toucher le GPU. Mes tests précédents passaient parce qu'ils utilisaient un `.wav` (le clip de
benchmark) : le cas réel n'était pas couvert. Et `GET /` répond 200 quel que soit le problème, donc
la sonde « Vérifier l'amont » ne pouvait pas le voir.

**Correctifs**
1. **Conversion dans le pont** (`to_wav_16k`) : WAV détecté à la signature (`RIFF....WAVE`), sinon
   ffmpeg → 16 kHz mono `pcm_s16le`. ffmpeg cherché dans l'ordre : `HERMESSERV_FFMPEG`, `PATH`,
   `%LOCALAPPDATA%\hermes\tools\ffmpeg-*\bin\ffmpeg.exe`, ffmpeg-static d'OpenWhispr.
2. **HTTPError ≠ URLError** (c'est une sous-classe !) : l'amont qui *répond mal* est maintenant
   distingué de l'amont *injoignable* — message « l'amont a refusé la requête : HTTP 400 —
   Invalid request », code `upstream_rejected`. L'ancien message « injoignable » était faux.
3. **« Vérifier l'amont » teste le vrai chemin** : `probe_inference()` envoie `POST /inference`
   avec 0,5 s de silence en WAV et exige un 200. Un `GET /` ne prouve que « le port écoute ».
4. **Démarrage automatique** du pont au lancement de l'app (interrupteur « Démarrer le pont avec
   HermesServ », coché par défaut, une seule tentative par session — pas de boucle si le port est
   pris). Hook générique `maybe_auto_start` ajouté dans `ServerRuntime.refresh`, à côté de
   `maybe_auto_desktop`.

**Mesuré après correctif** : webm/opus via le pont **0,91 s** (dont 0,53 s de GPU et ~0,35 s de
ffmpeg) ; via le vrai `transcribe_audio` de Hermes : 2,52 s à froid (init du client), **0,71 s**
à chaud sur WAV. Compteur `conversions` exposé par `/health` et affiché dans la tuile.

**Vérifié** : WAV 200 en 0,56 s, WEBM 200 en 1,17 s sur pont de test, `probe_inference` vrai/faux
correctement (amont fermé → message de connexion refusée), action `check` → `log.stt_inference_ok`,
exe reconstruit et relancé, pont démarré tout seul sur 8770. 243 clés i18n × 5 langues.

## Pont STT (GPU) — troisième plugin réel, et fin du « tout CPU » (1er nov 2026)

**Le problème mesuré.** Hermes transcrit avec **faster-whisper** (CTranslate2), qui ne fait du GPU
qu'en **CUDA**. Sur un GPU **AMD** (RX 6950 XT, RDNA2), `ctranslate2.get_cuda_device_count()` → 0 :
repli CPU `int8`. Mesuré sur un vrai clip de dictée (8,7 s) :

| Moteur | Chargement | Transcription | RTF |
|---|---|---|---|
| Hermes / faster-whisper **turbo**, CPU int8 | 3,9 s | **11,2 s** | 1,29× |
| Hermes / faster-whisper **small**, CPU int8 | 2,7 s | 2,8 s | 0,32× |
| **whisper.cpp Vulkan** (OpenWhispr) turbo | — | **0,55 s** | 0,06× |
| **notre pont** (bout en bout, HTTP) | — | **0,54-0,60 s** | 0,06× |

Même modèle (`ggml-large-v3-turbo.bin` = `faster-whisper-large-v3-turbo`, 1,6 Go des deux côtés).
L'écart n'est **que** le moteur d'exécution. `beam_size: 5` codé en dur côté Hermes aggrave.

**Ce qui a été construit.** `hermesserv/sttbridge.py` + `hermesserv/plugins/sttbridge.py`.
Le type `stt` démarre un **fil** (pas un sous-processus) qui expose
`POST /v1/audio/transcriptions` (dialecte OpenAI, celui que parle Hermes) et relaie vers
`POST {upstream}/inference` (whisper.cpp), puis reformate : `{"text": …}` ou texte brut selon
`response_format`. Défauts : `127.0.0.1:8770` → `http://127.0.0.1:8178`.

**Pourquoi un fil et pas un process :** un exe PyInstaller `--onefile` n'a pas de
`sys.executable` utilisable comme interpréteur. Le fil évite le problème, ne laisse rien survivre
à l'app et ne demande aucun PID à suivre.

**Constats vérifiés (à ne pas refaire)**
- Le binaire Vulkan **est déjà sur la machine**, installé par OpenWhispr :
  `%APPDATA%\open-whispr\bin\whisper-vulkan\whisper-server-win32-x64-vulkan.exe`, modèle dans
  `~\.cache\openwhispr\whisper-models\ggml-large-v3-turbo.bin`. Rien à installer.
- OpenWhispr lance ce serveur **lui-même** (sidecar) : args réels observés
  `--model … --host 127.0.0.1 --port 8178 --threads 12 --language auto --max-len 4096`, PID dans
  `sidecar-pids\whisper.pid`. Il tourne même sans dictée en cours ; s'il est fermé, l'amont meurt.
- **`/v1/audio/transcriptions` répond 404** sur ce binaire : il ne parle que `/inference`. D'où le
  pont — la « config en 3 lignes » côté Hermes n'aurait jamais suffi.
- **Piège Windows majeur** : `ThreadingHTTPServer` a `allow_reuse_address = 1`, et sous Windows
  `SO_REUSEADDR` permet à **deux** sockets d'écouter le même port — le pont « démarrait » alors
  qu'un autre process tenait 8770 (vérifié). Corrigé par `_BridgeServer.allow_reuse_address = False`
  → refus net `WinError 10048`, traduit en `log.stt_port_busy`.
- `psutil.net_connections` renvoie parfois **pid 0** pour un port occupé : `pid is not None` ne
  suffit pas à dire « process tiers » (`bool(pid)`), et un port tiers ne doit pas faire afficher
  « en ligne » pour un pont qu'on ne sert pas.

**Vérifié le 1er nov 2026** — harnais hors interface, 7 scénarios, toutes assertions passées :
état initial arrêté → démarrage (phase `running`) → **transcription réelle par le pont**
(HTTP 200, 0,54 s, 104 car., compteur à 1) → arrêt (port libéré) → arrêt d'un pont déjà arrêté
(`log.stt_not_running`) → **port tenu par un tiers** (`log.stt_port_busy`, rien n'est écrasé) →
**amont fermé** (phase `starting`, ligne ambre, journal explicite).
Interface : `--shot` sur config de test (`HERMESSERV_CONFIG`), tuile **« S » ambre**, 3 lignes,
3 boutons, **aucune clé i18n brute** (239 clés par langue, parité vérifiée sur les 5).
Serveur `stt-1` ajouté au vrai `config.json` (sauvegarde `config.json.bak-20261101T173339`).

**Reste à décider (côté Hermes, pas touché)** : brancher Hermes sur le pont =
`stt.provider: openai`, `stt.openai.base_url: http://127.0.0.1:8770/v1`,
`stt.openai.api_key: local` (factice) + `stt.openai.model`. **Piège** : si le pont ne tourne pas,
Hermes n'a plus de STT du tout (pas de repli automatique vers `local`) — d'où « on garde `local`
par défaut et on bascule à la main », ou un garde-fou.

## v2 : configurable, multilingue, rail de serveurs (23 oct 2026)

L'app n'est plus « le lanceur du serveur Hermes » mais un **tableau de bord de serveurs** :
rail de tuiles à gauche (un onglet par serveur), panneau à droite, réglages généraux au pied du
rail, et **plus rien d'important en dur**.

**Arborescence**
| Fichier | Rôle |
|---|---|
| `hermes_control.py` | point d'entrée : fenêtre, rail, sondeur, icône de notification, `--shot`/`--selftest` |
| `hermesserv/config.py` | `config.json` (général + serveurs), migration de l'ancien `settings.json` |
| `hermesserv/i18n.py` + `locales/*.json` | traductions (une langue = **un fichier**), police par langue |
| `hermesserv/service.py` | contrat de plugin : `ServerType`, `Field`, `Toggle`, `Controller`, registre |
| `hermesserv/plugins/hermes.py` | plugin Hermes (ex-`hermes_control.py`), paramétrable par instance |
| `hermesserv/plugins/portwatch.py` | type « port / site à surveiller », **lecture seule** |
| `hermesserv/sysinfo.py` | sondes psutil/HTTP/Tailscale/Hermes (aucun CLI par cycle) |
| `hermesserv/widgets.py` | palette, cartes, boutons, interrupteurs, tuiles, champs, journal |
| `hermesserv/tray.py` | icône de notification Win32 (`ctypes`, sans pystray) |
| `hermesserv/ui.py` | rail, panneau de serveur, réglages (général + par serveur), ajout |
| `legacy_hermes_control_mono.py` | **ancienne** version mono-fichier (23 oct), gardée pour référence |

**Configuration** : `%LOCALAPPDATA%\HermesServ\config.json`
```json
{ "version": 1,
  "general": { "language": "fr", "hide_on_close": true },
  "servers": [ { "id": "hermes-1", "type": "hermes", "label": "Hermes", "enabled": true,
                 "params": { "hermes_root": "…\\hermes", "host": "127.0.0.1", "port": 9119,
                             "url": "", "phone_node": "mon-telephone",
                             "dashboard_args": "dashboard --no-open --skip-build" },
                 "options": { "auto_launch_desktop": false, "stop_on_quit": false } } ] }
```
- **Par défaut : aucun serveur** (premier lancement = écran vide + « add server »), comme demandé.
- Migration : si `settings.json` existe encore, il est absorbé dans `general` et un serveur Hermes
  est recréé avec les chemins détectés (une installation existante ne casse pas).
- Écriture atomique (`.tmp` puis `os.replace`).

**Traductions** : fr, en, es, pt-BR, zh — **149 clés** chacune, parité vérifiée.
- Ajouter une langue = copier `fr.json`, changer `meta` (+ `ui_font` pour les écritures
  non latines), traduire **les valeurs** : voir `hermesserv/locales/README.md`.
- Le chinois exige `Microsoft YaHei UI` : Segoe UI n'a pas les glyphes (vérifié à l'image).
- **Le journal ne stocke que des clés + paramètres** : changer de langue retraduit aussi
  l'historique affiché (un journal en français dans une UI anglaise serait incohérent).

**Contrat de plugin** (le point qui décide de la suite) : un plugin déclare ses champs de réglages
(`Field`) et ses interrupteurs (`Toggle`) — l'UI génère le formulaire — et un `Controller` qui
renvoie un instantané **sans texte** (`label_key` + paramètres) : `phase`, `rows`, `actions`,
`toggles`, `meta`, `url`. Aucun plugin n'écrit de libellé, donc tout est traduisible.
`portwatch` sert de type par défaut (surveille un port/chemin HTTP, ne démarre rien : aucun
risque d'arrêter un process qui ne nous appartient pas).

**Choix d'UI** : fenêtre fixe 900×760 ; rail 96 px (tuiles icône + libellé, point d'état,
sélection au contour) ; « add server » sous les serveurs ; **Réglages** en bas du rail ;
réglages généraux (langue, fermeture→tray, liste des serveurs, chemin de config, journal de
l'app) vs réglages **par serveur** (nom, champs du type, interrupteurs, suppression à double
confirmation). Changer de langue reconstruit toute l'UI.

**Vérifié le 23 oct 2026 (à l'image, `PIL ImageGrab(window=hwnd)`)**
- Panneau serveur en fr/en/pt-BR/zh : « En ligne » vert après adoption du dashboard existant
  (pid 23532), journal traduit dans les 4 langues, 4 boutons de largeur égale (184 px).
- Réglages généraux : 5 langues listées, chemin de config, journal de l'app.
- Ajout : 2 types, dossier Hermes **pré-rempli**, formulaire généré depuis les `Field`.
- Réglages serveur : champs pré-remplis, options du serveur, Enregistrer/Supprimer (double clic).
- Exe packagé (32,5 Mo, `--add-data "hermesserv/locales;hermesserv/locales"`) : écran vide
  correct, textes français résolus depuis le bundle, capture 900×760.

**Piège de capture (important)** : `ImageGrab.grab(bbox=…)` photographie l'ÉCRAN — si une autre
fenêtre recouvre la nôtre, on capture l'autre (et on croit à un bug d'UI, voire à une fenêtre
noire). Utiliser **`ImageGrab.grab(window=hwnd)`** (PrintWindow) : rendu de la fenêtre même
recouverte. `winfo_rootx()` vaut `-32000` quand la fenêtre est démontée : repère utile.

**Reste à faire**
- Types de plugins suivants : DSH, service Windows, .exe à surveiller (le contrat est prêt ;
  WSL est fait, voir ci-dessous).
- « add server » au clic droit sur la tuile (renommer / dupliquer / masquer).
- Partage de plugins (dossier + manifeste) : voir `ROADMAP.md` étape 5.

## WSL : deuxième plugin réel — le cas « ce n'est pas un process » (24 oct 2026)

Étape 4 de la roadmap. Question posée par l'utilisateur et **vérifiée** : peut-on surveiller une
distribution **que HermesServ n'a pas lancée** ? Oui — une distro WSL est une VM gérée par
Windows, l'état vient de `wsl.exe -l -v`, qui liste tout ce qui existe quel que soit le lanceur.
La sonde est en lecture seule ; c'est la même règle que pour un serveur Hermes adopté.

**Fichier** : `hermesserv/plugins/wsl.py`, enregistré dans `plugins/__init__.py`.
Tuile « W » violette (`TYPE_GLYPHS`/`TYPE_COLOURS` dans `ui.py`).

**Deux usages, un seul champ `distro`**
| `distro` | Ce qu'on voit | Boutons |
|---|---|---|
| vide ou `*` | une ligne par distribution (état + version WSL) + défaut + sonde + mémoire VM | **aucun** (vue d'ensemble, lecture seule) |
| un nom (`Ubuntu`) | distro, état, défaut, origine, sonde, mémoire VM, puis uptime / charge / processus invités | Démarrer-Arrêter · Shell · Fichiers |

**Coûts mesurés sur cette machine** (i5-13400F, WSL 2.7.13.0) : `wsl -l -v` **36 ms**,
`wsl --version` 32 ms, sonde invitée (`/proc` par `/bin/sh`) **95 ms** à chaud (1 970 ms au tout
premier appel, VM froide) → caches : liste 2 s, version 300 s, invitée 10 s, mémoire VM 5 s.
Ces CLI sont acceptables **parce qu'ils sont bon marché** ; la règle « pas de CLI par cycle »
reste la référence pour les CLI lents (3 s pour `hermes serve --status`).

**Pièges vérifiés**
- **Sortie UTF-16LE.** `wsl.exe` écrit en UTF-16LE dès que la sortie est redirigée :
  `sysinfo.run(text=True, encoding="utf-8")` donnerait des `U\x00b\x00u\x00n\x00t\x00u\x00`.
  D'où un `_decode()` local (UTF-16LE si les octets nuls dominent, sinon UTF-8) + `replace("\x00","")`.
- **Lignes de message prises pour des distros.** Sans distribution installée, `wsl -l -v` écrit une
  phrase en clair ; on ne garde donc une ligne que si le 2ᵉ jeton est un état connu
  (`running`, `stopped`, `installing`…). Sinon → état `unknown` + message en rouge.
- **Ne jamais démarrer une distro pour la sonder** : la sonde invitée n'est lancée que si l'état
  est déjà `running` (sinon `wsl -d … -e` boote la VM — un effet de bord non demandé).

**Sûreté (socle respecté)**
- Distro démarrée hors de l'app → **adoptée** (journal), ligne « Origine : déjà démarrée hors de
  l'app », état affiché en ambre « Démarrée hors de l'app ».
- **Arrêt refusé** tant que l'option « Autoriser l'arrêt d'une distribution lancée hors de l'app »
  n'est pas cochée : la commande `wsl --terminate` n'est alors **pas envoyée** (vérifié : aucune
  ligne `--terminate` dans les appels). Arrêter une distro tue les processus qui tournent dedans.
- Le bouton reste cliquable (un bouton grisé n'explique rien) : le clic écrit le refus au journal
  avec la marche à suivre.

**Extension du contrat (nécessaire pour un type sans process ni port)**
`service.ServerType` accepte désormais trois dictionnaires facultatifs, sinon l'UI garde les
libellés génériques :
- `phase_labels` — « Démarrée » / « Arrêtée » au lieu de « En ligne » / « Arrêté » ;
- `external_label_key` — « Démarrée hors de l'app » au lieu de « En ligne (process externe) » ;
- `phase_keys` — clés de journal des transitions (`{name}` au lieu de `{port}`).
Et `snapshot["meta"]["line"]` permet de fournir la ligne d'état du bandeau (au lieu de
« aucun process en écoute sur le port ? »). `ui.ServerRuntime.refresh` passe désormais `port`,
`name` et `label` aux clés de transition (les paramètres non utilisés sont ignorés).
Les lignes d'un type multi-objets passent par `value.wsl_name` comme **libellé** (le nom de la
distro) et `label_params` — le contrat n'a pas eu besoin d'être changé pour ça.

**Test isolé : `HERMESSERV_CONFIG`** — `config.py` lit ce chemin s'il est défini, ce qui permet de
lancer une instance de test sur une configuration à part (`--shot`) **sans toucher** au
`config.json` réel ni à l'instance de l'utilisateur.

**Vérifié le 24 oct 2026**
- Instantané hors interface (vue d'ensemble, distro externe, distro inexistante) : lignes, boutons
  et option attendus ; `start()` sur une distro absente journalise « introuvable » sans rien lancer.
- Cycle démarrer → arrêt **sur un faux `wsl`** (`wsl._wsl` remplacé) : `-d Ubuntu -e /bin/true`
  puis `--terminate Ubuntu`, `_started_here` passe à True puis False, origine correcte à chaque
  étape ; en mode externe, **aucun** `--terminate` n'est émis ; avec l'option cochée, il l'est.
- Captures (`PIL.ImageGrab(window=hwnd)`) : `apercu_wsl_toutes.png` (5 lignes, aucune action) et
  `apercu_wsl_ubuntu.png` (9 lignes, 3 boutons de largeur égale, journal = adoption).
  Panneau Hermes recapturé après les changements d'UI : aucune régression.
- **`dist/HermesServ.exe` : reconstruit le 24 oct 2026 à 11:39** (32,5 Mo, recette
  `HermesServ.spec` avec Python314). L'app tournait et Windows verrouillait l'exe : fermée par
  `Stop-Process` sur ses deux PID (25512/3528) **sans `/T`** — le dashboard (pid 31860) n'était pas
  un enfant de la GUI, `9119` est resté en écoute et `/api/status` a répondu 200 avant/après.
  Vérifié ensuite en lançant l'exe packagé avec `--shot` sur les deux configs de test : textes
  français résolus depuis le bundle (`1 distribution démarrée`, singulier), panneau WSL complet.
  Puis relancé sur la vraie config (2 PID, fenêtre « HermesServ », dashboard toujours adopté).
  Note : arrêter la GUI de force laisse une icône de notification fantôme jusqu'au prochain survol
  (le `NIM_DELETE` du tray ne tourne que sur une sortie propre).
- Traductions : 52 clés ajoutées, **201 clés par langue** (fr, en, es, pt-BR, zh), parité vérifiée,
  aucune clé non résolue dans les 5 langues. Le bandeau a deux clés (`status.wsl_line_one` /
  `_many`) pour éviter le « distribution(s) ».
- **Non exercé sur la vraie distribution** : la distro Ubuntu de l'utilisateur tournait (30
  processus) — démarrer/arrêter une VM en cours d'usage est un effet sur sa machine, pas un test.
  À faire à la demande, distro arrêtée. `Shell` et `Fichiers` n'ont pas été ouverts non plus
  (commande construite et affichée : `wt.exe -w new wsl.exe -d Ubuntu`).
- **`dist/HermesServ.exe` n'est PAS à jour** : l'app tournait pendant la session, Windows verrouille
  l'exe. Reconstruire après fermeture (recette plus bas) ; en attendant, `HermesServ.bat` lance la
  source à jour.

## Lancement auto du desktop : attendre l'URL que l'app utilise vraiment (24 oct 2026)

**Symptôme** : au démarrage, l'app desktop lancée par HermesServ affiche « Hermes couldn't start —
Could not reach the remote Hermes gateway while refreshing its WebSocket ticket » pendant ~40 s
avant de se connecter (mesuré 16:19:39 → 16:20:15 : 16 tentatives ; 37,8 s).

**Cause** : deux choses distinctes, à ne pas confondre.
1. Le desktop n'est pas en mode local : `%APPDATA%\Hermes\connections.json` → `primary` =
   `ma-machine-tailXXXX-ts-net`, `connection.json` → `mode: remote`. L'UI passe donc par
   l'URL tailnet (preuve serveur : `ws accepted peer=100.x.y.z`, `ws_ticket_minted ... ip=100.x.y.z`).
2. La sonde de `snapshot()` (`http://127.0.0.1:9119/api/status`) prouve que le port écoute côté
   boucle locale, **pas** que le backend délivre déjà les tickets d'auth par l'URL distante. Le
   `maybe_auto_desktop()` d'origine se contentait de la phase `running` : il ouvrait l'UI à
   16:19:37, soit 40 s avant que le premier ticket ne sorte (16:20:17). Le backend complet met
   ~55 s (lancement 16:19:22 → API utilisable 16:20:17), plugins + 2 profils compris.

**Ce n'était pas Tailscale** : `tailscaled` (PID 6016) tourne depuis le 20/10 20:42, et un
`tailscale down` / `up` remet `serve` en place en moins de 10 s (config intacte). Attention : couper
Tailscale **coupe bien l'UI du desktop** tant qu'elle est en mode remote (`ws closed ... 1006`, puis
reconnexion auto ~7 s plus tard) — le backend, lui, n'en dépend pas.

**Correctif** (option 2 retenue) :
- `sysinfo.desktop_target_url()` lit le registre de l'app (v2 `connections.json`, repli v1
  `connection.json`) et renvoie l'URL que le desktop va composer, `""` en mode local, `None` si
  illisible. **Lecture seule** : ce fichier appartient à l'app, qui le réécrit à chaque connexion
  (`lastUsed`, mtime 16:20:21 sur la session du 24 oct).
- `HermesController.desktop_url()` préfère cette source, sinon `params.url` puis
  `dashboard.public_url` (TTL registre 5 s, config 300 s).
- `maybe_auto_desktop()` devient une machine à états **non bloquante** : en phase `running`, elle
  sonde l'URL du desktop (cache 1 s) à chaque cycle du sondeur (1,6 s), journalise une seule fois
  l'attente, puis lance dès que l'URL répond. Plafond `WAIT_REMOTE_MAX = 120 s` puis lancement
  quand même avec une ligne de journal explicite. Mode local détecté = lancement immédiat.
  `PHASE_STOPPED` réarme la garde **et** vide le cache `remote` (une URL muette ne doit pas rester
  « prête »). Le bouton Desktop reste immédiat (action demandée explicitement).
- 3 clés i18n (`log.desktop_wait_remote`, `log.desktop_wait_timeout`, `log.desktop_local_mode`) →
  **204 clés par langue**, parité vérifiée.

**Vérification** : harnais jetable (hors dépôt) sur la machine à états, `launch_desktop` remplacé
par un enregistreur, sondes simulées → 25 assertions (attente, lancement unique par montée,
réarmement après stop, mode local, plafond, phase `starting`, interrupteur à off, parité i18n) et
`desktop_target_url()` sur le vrai registre → `https://ma-machine.tailXXXX.ts.net`.
`snapshot()` sur la vraie config : phase `running`, 6 lignes, aucune exception.
**`--selftest` non lancé volontairement** : il adopterait le listener de l'utilisateur puis
l'arrêterait (c'est le backend qui sert la session en cours).

**Décision : pas de switch local / tailscale dans HermesServ.** L'app desktop a déjà le sien
(`ConnectionSwitcher` : « This device » / URL tailnet, `selectConnection()`), et les deux modes
tapent le **même** backend `127.0.0.1:9119` — un switch ne changerait pas de serveur, seulement de
chemin réseau. Écrire dans le registre d'une autre app serait un contournement qui devient un
comportement par défaut, fragile (l'app le réécrit) et redondant. On **lit** ce registre pour savoir
quoi attendre, on ne l'écrit jamais. Garder le mode remote est aussi ce qui prouve en continu que
le chemin téléphone fonctionne.

## App précédente : `legacy_hermes_control_mono.py` (19-23 oct 2026)

Refonte propre de l'idée, en Python + Tkinter (rien à installer : Python 3.14 a déjà
Tk 9.0, psutil, pyinstaller).

**Fichiers**
| Fichier | Rôle |
|---|---|
| `hermes_control.py` | l'application (GUI) + `--selftest`, `--perf`, `--shot <png>` |
| `HermesServ.bat` | lancement sans console (`pythonw`) — double-clic |
| `hermes_serv.ico` | icône (barre des tâches + exécutable) |
| `HermesServ.spec` | recette PyInstaller (reproductible) |
| `dist/HermesServ.exe` | exécutable autonome (~32 Mo, `--onefile --noconsole`) |
| `apercu_en_ligne.png`, `apercu_arrete.png`, `apercu_desktop.png`, `apercu_tray.png` | captures des états (dernière = icône de notification) |
| `hermes_gui.py`, `dist/HermesServer.exe` | **ancienne** version (5 oct), conservée pour référence |

Recette de build (relancer telle quelle après une modification) :
```
python -m PyInstaller --noconfirm --clean --onefile --noconsole --name HermesServ ^
  --icon hermes_serv.ico --add-data "hermes_serv.ico;." ^
  --add-data "hermesserv/locales;hermesserv/locales" hermes_control.py
```
*(Python 3.14 : `C:\Users\<user>\AppData\Local\Programs\Python\Python314\python.exe`. Le second
`--add-data` est **indispensable** : sans lui, l'exe démarre avec des clés de traduction brutes.)*

**Ce qu'elle fait**
- Bouton unique Démarrer / Arrêter + état détaillé : port 9119, `/api/status`, auth,
  route Tailscale, téléphone (en ligne / hors ligne), backend du desktop.
- RAM + uptime du process du serveur, journal des événements (démarrage, adopté, arrêt
  détecté…), boutons Desktop / Navigateur / Copier l'URL, deux interrupteurs (arrêt à la
  fermeture, lancement auto du desktop).
- Titre de fenêtre sombre, DPI-aware, cartes et boutons arrondis dessinés au canvas.

**Desktop : bouton + interrupteur (20 oct 2026)**
- « Ouvrir » ne disait pas ce qu'il ouvrait → renommé **Navigateur** (URL publique).
  Nouveau bouton **Desktop** : lance `…\hermes-agent\apps\desktop\release\win-unpacked\Hermes.exe`
  directement (repli `hermes desktop --skip-build`), sans rebuild, en process détaché
  (`DETACHED_PROCESS`) → l'app survit à la fermeture de HermesServ.
- Il refuse un second lancement si l'app tourne déjà (scan psutil des `Hermes.exe` sous
  `win-unpacked`), et affiche « Desktop ✓ » vert dans ce cas (sonde TTL 3 s dans le sondeur).
- Interrupteur « Lancer l'app desktop quand le serveur est prêt » : une seule tentative par
  montée du serveur (remis à zéro quand le port 9119 retombe), jamais de doublon.
- Les deux interrupteurs sont **persistés** dans `%LOCALAPPDATA%\HermesServ\settings.json`
  (`stop_on_exit`, `auto_desktop`) — un fichier absent ou illisible = tout désactivé.
- Vérifié : `--shot` (mise en page 470×700, rien de rogné), lancement simulé (Popen détaché,
  cwd correct, garde anti-doublon, chemin introuvable), chemin auto avec `auto_desktop=true`
  → « l'app desktop est déjà ouverte », aucune seconde instance.
- `dist/HermesServ.exe` **reconstruit** le 20 oct 2026 (32,4 Mo, recette identique) et
  vérifié en lançant l'exe packagé avec `--shot` : capture 470×700, bouton Desktop ✓,
  Navigateur, Copier l'URL, les deux interrupteurs.
- **Piège de test (important)** : lancer un exe PyInstaller `--onefile` depuis le shell de
  l'agent laisse fuiter `_PYI_ARCHIVE_FILE`, `_PYI_APPLICATION_HOME_DIR` et
  `_PYI_PARENT_PROCESS_LEVEL` **dans l'environnement de la session bash**. Le lancement
  suivant du **même chemin** se croit alors enfant d'une instance déjà extraite, vise un
  `_MEI…` périmé et affiche « Failed to load Python DLL ». Ce n'est PAS un défaut du build :
  la même exe copiée ailleurs, ou lancée avec un environnement propre, démarre normalement.
  → après un test d'exe onefile, `unset _PYI_ARCHIVE_FILE _PYI_APPLICATION_HOME_DIR
  _PYI_PARENT_PROCESS_LEVEL` (ou tester via un `subprocess` avec `env=` explicite).

**Icône de notification (zone de notification Windows) — 20 oct 2026**
- **Aucune dépendance** : `pystray` n'existe pas dans le Python 3.14 qui construit l'exe, donc
  l'icône est faite en **Win32 natif via `ctypes`** (`Shell_NotifyIconW`) — rien à installer, rien
  de plus à empaqueter. La classe `TrayIcon` du fichier porte tout : fenêtre invisible de réception
  (`HermesServTray<pid>_<id>`) + boucle `GetMessage` dans un thread dédié.
- Clic **gauche** = afficher/ramener la fenêtre ; clic **droit** = menu (Afficher · Démarrer/Arrêter
  le serveur · Ouvrir l'app desktop · Quitter). Infobulle = état + pid, réécrite quand l'état change.
- Le thread de l'icône ne touche **jamais** Tk : il pousse `("tray", action)` dans la file
  d'événements, traitée par `_drain` (même règle que le sondeur). Fermeture = `NIM_DELETE` pour ne
  pas laisser d'icône fantôme ; si l'icône ne peut pas être créée, l'app démarre quand même.
- **Windows 11 masque les nouvelles icônes dans le débordement** (l'entrée
  `HKCU\Control Panel\NotifyIconSettings\…` est créée, `IsPromoted` vide). Pour l'épingler :
  Paramètres → Personnalisation → Barre des tâches → **Autres icônes système** → HermesServ.
- Pièges vérifiés : un menu contextuel doit être **créé ET affiché dans le fil qui possède la
  fenêtre** (`TrackPopupMenu` renvoie 0 / erreur 87 depuis un autre fil) ; `SetForegroundWindow`
  échoue quand l'app n'est pas au premier plan, d'où le `PostMessage(WM_NULL)` après fermeture ;
  donner à la fenêtre du tray un titre **distinct** de la fenêtre principale (sinon `FindWindow`
  tombe dessus et un WM_CLOSE de test ne ferme rien).
- Vérifié : `Shell_NotifyIcon` ADD/MODIFY/DELETE = True, infobulle, menu ouvert puis refermé par
  `EndMenu`, clic gauche simulé → action `show`, fermeture par WM_CLOSE → code 0, icône retirée ;
  exe packagé : « icône de notification : ajoutée » + capture OK.

**Usages du tray : fermer = réduire, quitter pour de bon (20 oct 2026)**
- **Bouton X = réduire dans la zone de notification** (nouvel interrupteur `hide_on_close`,
  **activé par défaut**, persisté). `root.withdraw()` + **une ligne dans le journal** uniquement :
  pas de bulle d'information, l'utilisateur trouve ça agaçant (dit le 20 oct 2026).
- **Quitter** ne se fait plus que par le menu de l'icône (ou en désactivant l'interrupteur) : c'est
  la porte de sortie explicite, avec l'arrêt du serveur si l'interrupteur correspondant est actif.
- **Repli** : si l'icône n'a pas pu être créée, X quitte quand même (sinon l'app deviendrait
  injoignable : fenêtre cachée sans moyen de la rouvrir).
- Menu adaptatif : « Afficher HermesServ » quand la fenêtre est cachée, « Masquer dans la zone de
  notification » quand elle est visible (état suivi par un booléen `_window_hidden` — jamais en
  interrogeant Tk depuis le fil de l'icône).
- **Cohérence corrigée** : l'interrupteur « Arrêter le serveur » devient « Arrêter le serveur quand
  je **quitte** HermesServ » et n'est appliqué que sur une vraie sortie — sinon un simple X aurait
  arrêté le serveur alors que l'app continue de tourner.
- Fenêtre passée à **470×728** pour le troisième interrupteur ; journal toujours entier, rien de
  rogné (vérifié à l'image).
- **Sortie propre** : le `after()` de la boucle d'affichage est mémorisé (`_drain_job`) et annulé
  avant `destroy()`, sinon Tk imprime `invalid command name "..._drain"` à la fermeture.
- Vérifié (source, en process) : X → état `withdrawn`, app vivante, **aucune bulle demandée**
  (espion sur `TrayIcon.balloon`), journal = « Fenêtre réduite dans la zone de notification… » ;
  1er libellé du menu = « Afficher HermesServ » ; clic gauche simulé → `normal`, libellé =
  « Masquer… » ; action du menu → `withdrawn` ; « Quitter » → fenêtre détruite sans erreur Tcl ;
  bulle d'information = True. Exe reconstruit (16:50) : « icône de notification : ajoutée » et
  capture 470×728 — le cycle complet X→tray et la sortie ont été exercés sur la source, pas sur
  l'exe.

**Choix techniques (importants)**
- Lance `hermes dashboard --host 127.0.0.1 --port 9119 --no-open --skip-build` en process
  séparé, avec suivi de son PID.
- **N'utilise jamais `hermes serve --stop`** (qui tuerait aussi le backend du desktop) :
  l'arrêt cible le PID qui écoute sur 9119, et **refuse** d'arrêter un serveur qu'il n'a
  pas lancé.
- Statut = ce qui compte réellement : le port 9119 écoute **et** l'API répond.
- Le backend du desktop est compté et affiché à part (« jamais touché »).

**Vérifié le 19 oct 2026**
```
python hermes_control.py --selftest
  démarrage=OK (prêt en ~7 s, /api/status 200, auth requise, ~211 Mo)
  arrêt=OK (port 9119 libéré)
  backend desktop intact=OK (pid 16716 toujours vivant)
```

**Gel « ne répond plus » — diagnostiqué et corrigé (19 oct 2026)**

Symptôme rapporté par l'utilisateur : la fenêtre ne répond pas, par intermittence.
Cause : **tout le sondage tournait sur le fil d'affichage**. À chaque cycle, la fenêtre
lançait jusqu'à 5 sous-processus, dont deux très chers, mesurés sur cette machine :

| Sonde (ancien cycle, à chaque fois) | Coût mesuré |
|---|---|
| `hermes serve --status` (lance un interpréteur Python) | **3 155 ms** |
| `hermes config get dashboard.public_url` (idem) | **3 322 ms** |
| `tailscale serve status` | 37 ms |
| `tailscale status --json` | 52 ms |

→ ~4 s de travail bloquant dans un cycle de 2 s : Windows finit par afficher « Ne répond
pas ». Corrections apportées :

1. **Sondeur en tâche de fond** (`StatusPoller`) : l'interface ne fait plus que peindre le
   dernier instantané connu.
2. **Plus aucun CLI par cycle** : les backends Hermes sont détectés par **scan psutil**
   (analyse par *jetons* d'argv — un script python dont le code contient « hermes serve »
   n'est pas un backend, et le lanceur `hermes.exe --profile … serve` en est un), et
   `public_url` est lu **directement dans `config.yaml`**.
3. **Cache par TTL** sur les sondes lentes (URL 300 s, tunnel 15 s, téléphone 10 s, backends 5 s).

| Mesure (`python hermes_control.py --perf`) | Avant | Après |
|---|---|---|
| Sondage à froid (démarrage) | 4 035 ms | **714 ms** |
| Sondage chaud (tâche de fond) | — | **150-200 ms** |
| **Peinture d'un cycle (fil d'affichage)** | ~4 000 ms | **2-5 ms** |
| Sous-processus lancés pendant 12 s d'observation | ~20 | **0** |

Repère pour la suite (à réutiliser pour chaque plugin) : **le fil d'affichage ne fait que
peindre** ; toute sonde vit dans le sondeur, avec un TTL, et jamais un CLI par cycle.

**Reste à faire (propositions)**
- Build `.exe` PyInstaller (`HermesServ.exe`) pour un lancement sans dépendre du chemin
  Python — pyinstaller 6.22 est déjà installé.
- Icône de barre des tâches : ~~nécessite `pip install pystray`~~ **fait le 20 oct 2026 en Win32
  natif (`ctypes`), sans rien installer** — voir la section « Icône de notification ».
- ~~Fixer `dashboard.basic_auth.secret`~~ **fait le 20 oct 2026** : il était **vide** (l'idée
  qu'il était réglé était fausse — vérifié dans `config.yaml`), donc clé de signature
  aléatoire à chaque démarrage → déconnexion à chaque relance. Secret 32 octets (base64)
  posé, et `session_ttl_seconds` passé de `0` (→ 60 s, `max(60, 0)` dans le code du
  provider) à `43200` (12 h, le défaut). Effectif au prochain redémarrage du dashboard.
- Afficher le vrai nom du téléphone configurable (aujourd'hui `mon-telephone` en dur).

## But du projet

Voir `IDEA.md` : une appli Windows (GUI) pour **lancer / monitorer / arrêter** le serveur
Hermes d'un bouton, afin qu'Hermes soit joignable depuis le téléphone via
Tailscale.

**Extension prévue (19 oct 2026)** : l'appli devient un tableau de bord des *services* de la
machine (onglets verticaux à gauche, un par service, interface propre à chacun, structure de
plugins partageables). Architecture cible, étapes et évaluation : **`ROADMAP.md`**.

## État réel de l'app (18 oct 2026)

- `hermes_gui.py` : Tkinter + pystray. Icône de barre des tâches (verte = running,
  rouge = stopped), fenêtre 360x220, **un seul bouton start/stop**, URL cliquable,
  mini-résumé `tailscale status`, verrou anti-doublon (PID dans `%TEMP%\hermes_gui.lock`),
  log dans `%TEMP%\hermes_gui.log`, refresh UI toutes les 5 s.
- `dist/HermesServer.exe` (32 Mo) : build PyInstaller du 5 oct.
- Pas de dépôt git dans ce dossier.
- Ce que l'app exécute :
  - start : `hermes serve --host 127.0.0.1 --port 9119 --skip-build`
  - status : `hermes serve --status`
  - stop : `hermes serve --stop`
  - URL : `hermes config get dashboard.public_url`

## Pièges à corriger avant de s'appuyer dessus

1. **`serve --stop` tue aussi le backend du desktop.** Le desktop lance *son* backend
   (`hermes serve --host 127.0.0.1 --port 0`). Appuyer sur **Stop** dans l'app coupe donc
   la session desktop en cours. Un stop ciblé (le PID qui écoute sur 9119) est nécessaire.
2. **Faux positif de statut.** `is_hermes_running()` cherche `[serve]` dans
   `serve --status` : il renvoie *Running* même quand seul le backend du desktop tourne
   (port aléatoire). L'UI dit « Running » alors que **rien n'écoute sur 9119** — donc que
   le téléphone est injoignable. Il faut tester le port 9119, pas le mot `[serve]`.
3. **Fallback d'URL erroné** : `https://{hostname}.ma-machine.ts.net` — l'URL réelle est
   `https://ma-machine.tailXXXX.ts.net`.
4. **L'app ne vérifie rien de la chaîne d'exposition** : mapping `tailscale serve` → 9119,
   réponse `302 → /login` (tunnel + TLS + auth), `auth_required`/`auth_providers` sur
   `/api/status`.
5. **À trancher** : `serve` sert-il l'UI web au navigateur du téléphone, ou faut-il
   `dashboard --no-open` ? La recette vérifiée OK le 18 oct est
   `hermes dashboard --host 127.0.0.1 --port 9119 --no-open --skip-build`.

## Faits vérifiés le 18 oct 2026 (source de l'app installée + machine)

- **Le backend géré par le desktop est épinglé sur `--port 0` en dur**
  (`apps/desktop/electron/backend-command.ts`, `main.ts` : « the OS assigns an ephemeral
  port »), avec des tests unitaires qui verrouillent ce comportement. Aucune variable ni
  clé de config ne permet de le fixer. → On ne peut pas faire servir le téléphone par le
  backend du desktop ; il faut un process séparé sur un port fixe.
- **En mode Remote gateway, le desktop ne lance pas le backend distant** : il s'y attache
  (doc officielle). Il ne bascule pas tout seul en local si le distant est absent
  (comportement non vérifié bout en bout).
- **UI du desktop** : passer par `Settings → Gateways → Connection mode → Remote gateway`
  (URL + *Sign in*, formulaire identifiants). La page *Add connection* du registre
  n'offre que *Session token* ou *OAuth* — inadapté à une auth `basic` (bug docs/UX
  connu, issue NousResearch/hermes-agent#109469).
- **Connexion de type SSH** = seul mode où l'app démarre elle-même le dashboard (par-dessus
  le tunnel). Il faudrait un **serveur** SSH sur la machine visée : ici il n'y a que le
  client, aucun listener sur `:22`. Non retenu.
- **`dashboard.basic_auth.secret` est vide** → déconnexion à chaque relance du dashboard.
  À fixer si on s'appuie dessus durablement.
- **Sessions partagées en données, pas en direct.** Une seule base (`~\.hermes\state.db`,
  tables `sessions` + `messages`) : `hermes sessions list` montre toutes les surfaces
  (desktop, webui, tui, acp…). Mais une session *vivante* est exclusive (lease par tour) :
  reprise possible seulement si personne d'autre ne l'a ouverte — dashboard → **Chat** ou
  page **Sessions** → *resume* (`/chat?resume=<id>`).
- **`/handoff`** : sérial, unidirectionnel (CLI/desktop → plateforme de messagerie), le
  client source s'arrête. **Il n'existe pas de handoff vers le webui/dashboard.**

## Décisions (18 oct 2026)

- **Aucune tâche planifiée, aucune modification** de la config / du mode de connexion du
  desktop pour l'instant.
- **Prochaine étape = développer HermesServ** (lancer + monitorer le serveur). Si c'est
  fait, on réévaluera le passage du desktop en *Remote gateway* pour avoir les mêmes
  sessions en direct (le desktop n'aurait alors plus de backend à lui : il dépendrait du
  process 9119).
- En attendant, l'utilisateur **teste `/handoff` à l'usage** et jugera s'il s'y habitue ou
  non.

## TODO app (propositions, non validées)

- Statut sans ambiguïté : distinguer « mon 9119 écoute » de « un backend desktop tourne ».
- Stop ciblé sur le PID qui écoute sur 9119 (jamais `serve --stop` global).
- Monitoring : uptime, RAM/VRAM, tok/s, taille du log + tail du log ; état de la chaîne
  Tailscale et de l'auth.
- Corriger le fallback d'URL ; ajouter un bouton « copier l'URL » / « ouvrir ».
- Trancher `serve` vs `dashboard --no-open` pour servir l'UI web.

## Comment vérifier (lecture seule)

```
hermes serve --status                          # quels backends tournent, avec quels arguments
netstat -ano -p tcp | grep ':9119 '            # doit montrer 127.0.0.1:9119 seulement
curl -sI http://127.0.0.1:9119/ | head -3      # 302 -> /login = auth + TLS OK
tailscale serve status                         # mapping -> http://127.0.0.1:9119
hermes sessions list --limit 8                 # une seule liste, toutes surfaces
```
