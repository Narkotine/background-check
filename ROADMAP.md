# Background Check (ex-HermesServ) — feuille de route (vision « un œil sur mes services »)

> Renommé le 16 nov 2026 : HermesServ → **Background Check** (BGC), paquet `hermesserv` → `bgc`,
> `hermes_control.py` → `background_check.py`, `HERMESSERV_CONFIG` → `BGC_CONFIG`. Les
> sections plus bas gardent les anciens noms : c'est l'historique.

Rédigé le **19 octobre 2026**, d'après la demande de l'utilisateur. À lire avec `NOTES.md`
(constats vérifiés) — ici, c'est l'intention et l'architecture cible.

## La vision

> « Avoir un œil sur les serveurs ou services lancés par mon ordi, que je peux oublier, ou
> qu'il n'est pas pratique de lancer à la main — le tout avec des onglets rangés en vertical
> sur le côté gauche. Chaque service avec l'interface / les boutons qui lui sont les plus
> appropriés. Une structure de plugins pour chaque serveur, pour pouvoir les partager. »

Concrètement : HermesServ cesse d'être « le lanceur du serveur Hermes » et devient un
**tableau de bord local d'exploitation** : une barre d'onglets verticale à gauche (un onglet
par service), un panneau à droite propre à chaque service, et un socle commun (supervision,
démarrage/arrêt, journal, sécurité) que chaque plugin hérite au lieu de le réécrire.

Exemples cités par l'utilisateur : **WSL**, **DSH (DeepSeek Harness)**, et Hermes (le premier).

## Modèle cible

**Un service = un plugin.** Un plugin décrit :

| Élément | Rôle |
|---|---|
| `id`, `label`, `icône` | l'onglet dans la barre de gauche |
| `probe()` | l'état réel, en lecture seule : liste d'**items** (processus, distro WSL, conteneur, port…) avec un statut normalisé |
| `start()` / `stop()` (facultatifs) | une action sur un item précis, **ciblée**, jamais globale |
| `actions()` | les boutons pertinents (démarrer, arrêter, redémarrer, ouvrir l'URL, ouvrir un shell, voir les logs…) |
| `panel()` *(facultatif)* | un panneau sur mesure quand le panneau générique ne suffit pas (terminal, table, graphe) |

**Règle de conception importante** : ne pas supposer « un service = un processus = un port ».
WSL est une machine virtuelle gérée par Windows, Docker a un démon + des conteneurs, un
serveur SSH est un service Windows. D'où le `probe()` qui renvoie une **liste d'items** et des
actions **par item**. Faire ce choix maintenant évite de réécrire chaque plugin ensuite.

## Le socle commun (déjà écrit pour Hermes, à généraliser)

- **Statut = la preuve, pas le nom.** Sonder ce qui répond (port + API), afficher le PID du
  listener. Jamais « en ligne » parce qu'un mot apparaît dans une sortie de commande.
- **Arrêt ciblé.** Terminer les enfants récursifs du process visé, attendre la libération du
  port, vérifier après coup qu'un process tiers n'a pas été emporté. **Aucune commande
  d'arrêt globale** (`serve --stop` a tué le backend du desktop — leçon apprise).
- **Revendiquer ou refuser.** Un service déjà lancé hors de l'app est *adopté* (affiché, non
  tué) ; l'app refuse d'arrêter ce qu'elle n'a pas lancé et dit qui le tient.
- **Rien sans accord.** Pas de tâche planifiée, pas d'auto-démarrage, pas de config touchée
  sans demande explicite.
- **Journal horodaté** par service + transitions d'état détectées.
- **Sondage hors du fil d'affichage, avec TTL.** Leçon du gel du 19 oct 2026 : une sonde qui
  lance un CLI (`hermes serve --status` = 3,1 s ici) bloque la fenêtre si elle s'exécute sur
  le fil d'affichage. Le contrat de plugin doit donc imposer : sondes en tâche de fond,
  **TTL déclaré par sonde**, et **jamais de CLI par cycle** (préférer psutil, un fichier de
  config, ou un point d'API). Mesures avant/après : `NOTES.md`.

## Découpage cible

```
HermesServ/
  core/        serve.py (contrat Service, registry, sondage, arrêt ciblé, journal)
  ui/          rail vertical (onglets), panneau générique, widgets (Card, Button, Toggle), thème
  plugins/     hermes.py · wsl.py · <service>.py     ← partageables
  app.py       assemble rail + panneaux, boucle de rafraîchissement
```

Le rail vertical : `Canvas` avec un bouton par service (icône + libellé, état par un point
coloré, sélection sur fond plus clair) — la même technique que les boutons actuels, donc pas
de nouvelle dépendance.

## Étapes (chacune livrable et testable)

1. **Fait** — plugin Hermes (l'app actuelle : `hermes_control.py`, `HermesServ.exe`).
2. **Fait (23 oct 2026)** — socle extrait : `hermesserv/config.py` (général + serveurs),
   `service.py` (contrat `ServerType`/`Controller`), `sysinfo.py`, `widgets.py`, `tray.py`.
   L'ancien mono-fichier est conservé en `legacy/legacy_hermes_control_mono.py` comme référence.
3. **Fait (23 oct 2026)** — rail vertical de tuiles + panneau **générique** (lignes d'état et
   boutons viennent du `probe()` du plugin, sans texte en dur) + réglages généraux et par
   serveur + ajout de serveur. Deuxième type livré : `portwatch` (port/HTTP, lecture seule).
   Traductions fr/en/es/pt-BR/zh, une langue = un fichier JSON.
4. **Fait (24 oct 2026)** — **plugin WSL** : `hermesserv/plugins/wsl.py`. Le cas non-processus est
   validé : l'état vient de `wsl -l -v` (aucun PID, aucun port), un champ unique `distro` donne
   soit une **vue d'ensemble en lecture seule** de toutes les distributions (une ligne par distro),
   soit une distribution **pilotable** (démarrer / arrêter / shell / fichiers). Une distro démarrée
   hors de l'app est adoptée et l'arrêt est **refusé** sans option explicite. Ce que l'abstraction a
   demandé en plus (et qui reste utile aux plugins suivants) : `phase_labels`, `external_label_key`,
   `phase_keys` et `meta.line` sur `ServerType` — un type sans process peut nommer ses états et sa
   ligne de bandeau. Détails, coûts mesurés et pièges : `NOTES.md`.
5. **Fait (9 nov 2026)** — **Partage des plugins** : chaque dossier de
   `%LOCALAPPDATA%\HermesServ\plugins\<nom>\` (manifeste `plugin.json` + module) enregistre un
   type de serveur ; l'erreur d'un plugin est isolée et journalisée. Une **tuile entière**
   s'exporte en `.tile.json` et se réimporte (type, paramètres, interrupteurs, boutons, icône).
   **Réserve honnête, inchangée** : un plugin exécute du code ; l'installer est une décision de
   confiance (pas de bac à sable dans ce modèle) — c'est écrit dans l'app et dans le dossier.
6. **Fait (9 nov 2026)** — **Confort** : ~~icône de barre des tâches~~ **fait**, ~~réglages
   (liste des services)~~ **fait**, ~~option « démarrer avec Windows »~~ (interrupteur qui lit et
   écrit la clé `Run`, jamais sans clic), ~~ordre/visibilité des onglets~~ (monter/descendre/
   dupliquer/mettre en pause).

## Plan de travail — 9 nov 2026 (serveur MCP pour l'agent + icône en un bouton)

> **Demande de l'utilisateur** : « pour choper une icône pour la tuile, mets un bouton qui ouvre un
> navigateur de fichiers, plutôt que de demander un chemin à rentrer » ; et **un serveur MCP** qui
> sert à lancer/piloter les serveurs enregistrés mais surtout à ce qu'un LLM **configure lui-même
> les tuiles** (config du PC + docs internet), pour qu'un utilisateur inexpérimenté obtienne quelque
> chose de correct. `start_server` / `stop_server` doivent être exposés dès le départ.

### Lignes rouges

1. Le plugin Hermes ne régresse jamais ; tests sur `HERMESSERV_CONFIG` (aucun effet sur la vraie config).
2. Le serveur MCP n'écoute que sur `127.0.0.1` ; rien n'est exposé au réseau.
3. Toute écriture de config faite par l'agent est précédée d'une sauvegarde horodatée.
4. Parité i18n stricte sur les 5 langues.

### Étapes

- [x] **1 — Icône en un geste** : sélecteur (aperçu + « Choisir une icône… » + « Retirer ») dans
  les réglages et à l'ajout ; application immédiate, aucun chemin à taper. *(9 nov 2026, captures
  `apercu_icone_bouton.png` / `apercu_mcp_ajout.png`.)*
- [x] **2 — Socle MCP** : serveur HTTP local dans l'app (JSON-RPC MCP, `127.0.0.1:8781`, sans
  dépendance), outils de lecture (`list_servers`, `get_server`, `list_server_types`,
  `get_machine_info`, journaux, presets). *(9 nov 2026 — client MCP officiel : initialize +
  tools/list OK.)*
- [x] **3 — Pilotage** : `start_server` / `stop_server` réels (mêmes contrôleurs que les boutons,
  refus et erreurs remontés tels quels, journal d'app). *(9 nov 2026 — cycle start/stop réel sur
  config de test.)*
- [x] **4 — Configuration** : `create_server`, `update_server`, `delete_server` — validation par le
  type, clés inconnues signalées, sauvegarde de config, rail reconstruit. *(9 nov 2026 — 28/28.)*
- [x] **5 — Réglages** : section « Serveur MCP » (interrupteur, URL, état, copie), 5 langues.
  *(9 nov 2026 — 430 clés × 5, parité vérifiée ; capture `apercu_mcp_reglages.png`.)*
- [x] **6 — Vérification** : client MCP officiel (SDK Hermes) sur config de test — initialize,
  tools/list, cycle create → start → stop → update → delete ; captures ; rebuild exe + relance.
  *(9 nov 2026 — 22/22 puis 28/28, exe 2.1.0 : 12 outils, app réelle sur 8781, 9119/8770/8642
  intacts.)*

## Plan de travail actif — 9 nov 2026 (Versatilité & Clarté d'usage)


> **Instruction de reprise (« continue le développement »)** : prendre la **première étape non cochée (`[ ]`)** ci-dessous, l'implémenter, la vérifier par un harnais en processus neuf + capture `--shot` sur config isolée (`HERMESSERV_CONFIG`), vérifier la parité des 5 langues (`locales/*.json`), reconstruire `dist/HermesServ.exe` et relancer l'app de l'utilisateur, puis cocher l'étape (`[x]`) ici et consigner le résultat dans `NOTES.md`. Détail complet de l'analyse : voir `AMELIORATIONS.md`.

### Lignes rouges (valables à chaque étape)

1. **Le plugin Hermes ne régresse jamais** : à la fin de chaque étape, `snapshot()` sur la vraie config en lecture seule + capture du panneau Hermes. Jamais de `--selftest` tant que le service de l'utilisateur tourne.
2. **Rien n'est écrit** dans le dossier d'installation Hermes ni dans sa config.
3. **Tests sur config de test** (`HERMESSERV_CONFIG`) : le vrai `config.json` n'est pas touché ; le config de test a `auto_launch_desktop: false` et `start_on_app_start: false`.
4. **Rebuild de l'exécutable et relance** : autorisés par l'utilisateur pour HermesServ (fermer les PID de la GUI sans `/T` pour ne jamais tuer le serveur 9119, rebuild via `HermesServ.spec`, relancer en `DETACHED_PROCESS` avec env purgé des `_PYI*`).
5. **Parité i18n stricte** : toute clé ajoutée ou modifiée l'est dans les 5 fichiers (`fr`, `en`, `es`, `pt-BR`, `zh`).

### Étapes activables (dans l'ordre d'exécution)

- [x] **Chantier 1 — Réparer et compléter le mode « Surveillance seule » + raccourcis du type Service surveillé**
  *Fait le 9 nov 2026 — 28/28 au harnais (`harness.py`), interface vérifiée par captures ; détails et pièges dans `NOTES.md`. La logique d'affichage des interrupteurs a dû suivre le snapshot (`ui._paint_toggles`), sinon le panneau gardait les 4 interrupteurs du type.*
  - **Problème résolu** : `CommandType.validate()` refuse actuellement d'enregistrer un serveur sans commande (ce qui bloque l'édition de *LlamaGUI* ou l'ajout d'un port seul), et affiche un bouton *Start/Stop* trompeur quand aucune commande n'est configurée.
  - **À faire** :
    1. Dans `plugins/command.py`, assouplir `validate()` : accepter dès qu'il y a **au moins un `port` OU une commande (`command` / `stop_command`)** (nouvelle clé d'erreur explicite si les trois sont vides).
    2. Adapter les boutons (`actions`) de `CommandController.snapshot()` :
       - N'afficher le bouton `toggle` (*Start / Stop*) que si `command` ou `stop_command` est renseigné (ou si un process lancé par nous tourne). Sinon, le serveur est en mode *observation pure* (`readonly: True`, aucun interrupteur inutile).
       - Ajouter automatiquement les boutons **« Navigateur »** (`browser`, ouvre `url` ou `http://{host}:{port}`) et **« Copier l'URL »** (`copy_url`) dès qu'une `url` ou un `port` est configuré.
       - Ajouter un bouton **« Ouvrir le dossier »** (`folder`) si `workdir` est un dossier existant.
  - **Vérification** : harnais validant (a) sauvegarde d'un serveur avec port seul (type *LlamaGUI*), (b) absence de bouton Start/Stop en mode port seul, présence de *Navigateur* et *Copier l'URL*, (c) présence de Start/Stop dès qu'une commande existe ; capture `--shot` des deux variantes.

- [x] **Chantier 2 — Rendre chaque onglet auto-explicatif : bandeau « Que fait cet onglet ? » + formulaire structuré**
  *Fait le 9 nov 2026 — 25/25 au harnais (`harness2.py`), 5 captures ; détails dans `NOTES.md`. Le résumé est fourni par le plugin (`ServerType.summary()`), l'UI ne connaît aucun champ.*
  - **Problème résolu** : face aux 8 champs du type *Service surveillé* ou aux boutons d'un onglet, l'utilisateur doit deviner comment les champs se combinent et ce que le bouton d'action va réellement exécuter ou couper.
  - **À faire** :
    1. **Résumé dynamique sous le titre de chaque serveur (`ServerView`)** : une phrase claire (générée par le plugin via une clé i18n + paramètres) qui résume le mode réel :
       - *Service surveillé (port seul)* : « Surveillance seule de `{host}:{port}` — aucune commande de démarrage ou d'arrêt. »
       - *Service surveillé (avec commandes)* : « Sonde `{target}` · Démarrage : `{cmd}` · Arrêt : `{stop_cmd}` (ou arrêt du processus lancé). »
       - *Hermes* : « Pilote uniquement l'accès web distant (port `{port}`) · Ne coupe jamais le moteur de l'app Desktop. »
       - *WSL* : « Vue d'ensemble en lecture seule » ou « Pilote la distribution `{distro}` ».
       - *Pont STT* : « Relaie `127.0.0.1:{port}/v1` vers `{upstream}` (whisper.cpp GPU). »
    2. **Découper visuellement les formulaires (`AddServerView` & `ServerSettingsView`) en sections titrées** pour le type *Service surveillé* (support d'un attribut `section_key` optionnel sur `Field`) :
       - *1. Quoi observer ? (Sonde)* : `host`, `port`, `check_url`
       - *2. Comment le piloter ? (Facultatif)* : `command`, `stop_command`, `workdir`, `env`
       - *3. Accès & Affichage* : `url`
  - **Vérification** : captures `--shot` d'un onglet en surveillance pure, d'un onglet avec commandes (*LM Studio*), de l'onglet *Hermes*, et du formulaire découpé en sections.

- [x] **Chantier 3 — Modèles rapides à l'ajout (« Presets ») + boutons « Parcourir… » sur les chemins**
  *Fait le 9 nov 2026 — 22/22 au harnais (`harness3.py`), 3 captures ; détails dans `NOTES.md`. L'ajout d'un serveur passe aussi par un champ « Icône » (sinon le preset LM Studio ne pouvait pas poser l'icône détectée).*
  - **Problème résolu** : configurer un service courant ou choisir un dossier/exécutable demande encore de tout taper à la main.
  - **À faire** :
    1. Dans `AddServerView` (quand *Service surveillé* est choisi), afficher une rangée **« Modèles rapides »** au-dessus des champs :
       - *Port / Appareil réseau (lecture seule)* (vide les commandes, focus sur Hôte/Port)
       - *Programme ou script local* (focus sur Commande + Dossier)
       - *LM Studio (CLI `lms`)* (pré-remplit `lms server start`, `lms server stop`, port `1234`, `/v1/models`, et l'icône de `LM Studio.exe` si détectée sur la machine)
       - *Ollama / llama-server* (si détecté sur la machine ou en modèle prêt à l'emploi)
    2. Ajouter un bouton **« Parcourir… »** (`kind="dir"` → `askdirectory`, `kind="exe"` → `askopenfilename`) directement dans `TextField` pour les champs de chemin : `hermes_root` (*Dossier Hermes*), `workdir` (*Dossier de travail*), et un bouton *Choisir un exécutable* sur `command`.
    3. Dans `GeneralSettingsView`, ajouter deux boutons sous le chemin `config.json` : **« Ouvrir dans l'explorateur »** et **« Copier le chemin »**.
  - **Vérification** : test unitaire des presets (remplissage exact des champs), capture `--shot` de la vue d'ajout avec les modèles rapides et les boutons *Parcourir*.

- [x] **Chantier 4 — Diagnostic en direct : capturer la sortie console (`stdout` / `stderr`) des commandes lancées**
  *Fait le 9 nov 2026 — 15/15 au harnais (`harness4.py`, dont l'ouverture réelle de la fenêtre et son contenu). Deux garde-fous ajoutés au plan : pas d'alerte quand la commande rend la main avec le code 0 et que le service tourne (`lms server start`), pas d'alerte après un arrêt demandé depuis l'app.*
  - **Problème résolu** : `CommandController.start()` redirige actuellement `stdout` et `stderr` vers `DEVNULL`. Quand une commande échoue au lancement (erreur Python, port occupé, mauvais argument), l'utilisateur ne voit jamais le message d'erreur du programme.
  - **À faire** :
    1. Remplacer `DEVNULL` par un tube (`PIPE`) lu par un thread démon léger qui conserve les 80 dernières lignes dans un tampon circulaire (`deque(maxlen=80)`).
    2. Si le processus meurt dans les secondes qui suivent le démarrage (ou retourne un code != 0), journaliser automatiquement la dernière ligne pertinente de sortie dans le journal du serveur (`log.command_exit_output`).
    3. Ajouter un bouton **« Sortie console »** (ou bascule Journal / Sortie console) sur le panneau *Service surveillé* pour inspecter les lignes émises par le processus.
  - **Vérification** : harnais lançant une commande qui écrit sur `stderr` puis quitte en erreur → vérifier que le message exact remonte dans le tampon et dans le journal ; capture `--shot`.

- [x] **Chantier 5 — Gestion avancée des onglets (Ordre, Dupliquer, Pause) & Menu Tray multi-serveurs**
  *Fait le 9 nov 2026 — 49/49 au harnais (`harness5.py`), 2 captures ; détails dans `NOTES.md`. Le sondeur appelle désormais `refresh()` même pour une tuile en pause (sinon son panneau restait figé sur l'ancien état).*
  - **Problème résolu** : impossible de réordonner les tuiles à gauche, de mettre un serveur en pause sans le supprimer, ou de démarrer/arrêter un autre serveur que celui sélectionné depuis l'icône de notification Windows.
  - **À faire** :
    1. Dans les réglages (et/ou clic droit sur une tuile du rail) : actions **Monter (`▲`)**, **Descendre (`▼`)**, **Dupliquer**, et interrupteur **Activer la surveillance (`enabled`)** (un serveur désactivé n'est plus sondé et apparaît en grisé/compact ou masqué selon le choix).
    2. Dans `hermes_control.py` (`_tray_menu`), lister **chaque serveur actif** dans le menu clic droit du Tray avec son état et l'action directe (`● Hermes — Arrêter l'accès distant`, `○ LM Studio — Démarrer`, etc.).
    3. Option générale dans Réglages : **« Notification Windows quand un service en marche tombe tout seul »** (utilise `tray.balloon()` déjà prêt, une seule bulle par chute inattendue, jamais sur un arrêt manuel).
  - **Vérification** : harnais sur le réordonnancement/duplication dans `config.py`, vérification de la liste générée par `_tray_menu()`, et capture `--shot` des réglages.

---

## Plan de travail — 10 nov 2026 (audit des tuiles & interfaces web)

> **Demande** : « configure HermesServ comme il se doit » — savoir si un serveur tourne sans aucun
> visuel Windows ; **piloter les interfaces web** d'un serveur, cas typique des moteurs LLM qui
> séparent backend et frontend ; pour Hermes, contrôler le lancement du gateway **puis** du desktop,
> avec des bonus là où c'est possible (présence d'un périphérique Tailscale) ; re-juger chaque
> tuile et créer des **modèles de tuiles** (ports par défaut vérifiés sur internet).

**Lignes rouges** : identiques au plan précédent (plugin Hermes sans régression, tests sur
`HERMESSERV_CONFIG`, parité 5 langues). En plus : **la configuration réelle de l'utilisateur n'est
écrite qu'après son accord explicite** — audit, harnais sur copie, puis proposition chiffrée.

### Étapes

- [x] **1 — Boutons d'interface web « vivants »** *(10 nov 2026 — sonde 5 s, grisé si muet ;
  « Navigateur » grisé hors marche ; harnais 49/49, captures `apercu_web_actions.png`.)*
- [x] **2 — Modèles de tuiles groupés, ports par défaut vérifiés** *(10 nov 2026 — 3 groupes,
  8 nouveaux modèles, parité 442 clés × 5, capture `apercu_presets_groupes.png`.)*
- [x] **3 — Corrections des tuiles de l'utilisateur** *(10 nov 2026 — appliqué : LM Studio reçoit
  « Open LM Studio » + « Close LM Studio » (sonde `proc:`). Refusés par l'utilisateur : `check_url`
  sur LlamaGUI et mise en pause de WSL.)*

---

## Plan de travail actif — 11 nov 2026 (revue critique : logique puis esthétique)

> **Demande** : « ok, comme tu veux, commence par le logique puis l'esthétique » — carte blanche sur
> une revue critique du projet. Défauts constatés : voir la liste ci-dessous (chacun a été trouvé
> par lecture du code, calcul, ou sonde exécutée).

### Lignes rouges (identiques aux plans précédents)

1. Le plugin Hermes ne régresse jamais (snapshot en lecture seule + capture du panneau).
2. Rien n'est écrit dans le dossier d'installation Hermes ni dans sa config.
3. Tests sur `HERMESSERV_CONFIG` isolée (`auto_launch_desktop: false`, `start_on_app_start: false`).
4. Parité i18n stricte sur les 5 langues pour toute clé ajoutée.
5. La config réelle de l'utilisateur n'est pas modifiée sans son accord.

### Chantier L — Logique

- [x] **L1 — Détection « Détecté sur cette machine » cassée** (régression du chantier « cible en une
  ligne », 9 nov) : `_apply_suggestion` écrit désormais dans la ligne cible quand le champ demandé y
  est fusionné. *Vérifié : la cible contient `127.0.0.1:5240` après clic, nom suggéré rempli.*
- [x] **L2 — Sondeur séquentiel** → un fil par tuile + garde « sonde précédente en vol » (pas
  d'empilement). *Vérifié : une sonde de 4 s n'empêche pas les deux autres tuiles d'être sondées ;
  aucun second appel lancé sur la tuile lente.*
- [x] **L3 — Scan de détection sur le fil d'affichage** → thread + `after()` de remplissage.
  *Vérifié : ouverture en 13 ms avec un scan artificiel de 1 s ; les boutons arrivent ensuite.*
- [x] **L4 — `run_action` reconstruisait toute la vue** → `_paint()` seulement. *Vérifié : render=0,
  paint=1 sur un clic d'action ; filtre et défilement du journal survivent.*
- [x] **L5 — Portée du serveur MCP explicitée** : clé `settings.mcp_scope` (5 langues), affichée en
  ambre sous l'aide des Réglages. *Vérifié : parité i18n OK.*
- [x] **L6 — Garde dans `--selftest`** : refus (code 2) si la tuile est déjà en marche.
  *Vérifié : aucun adopt/start/stop tenté quand le port est tenu.*
- [x] **L7 — `Tile._draw`** suit maintenant son paramètre `size`. *Vérifié : géométrie et peinture
  à la même taille demandée.*

### Chantier E — Esthétique

- [x] **E1 — Pied de page fixe** : Enregistrer / Supprimer / Ajouter hors de la zone défilante.
  *Vérifié : bouton hors du `ScrollFrame`, visible en bas de vue ; captures `apercu_revue_reglages.png`
  et `apercu_revue_ajout.png` (1 552 et 1 380 pixels du bleu primaire dans la bande basse).*
- [x] **E2 — Largeurs mesurées** : `Button(width=None)` mesure son libellé ; `SelectRow`, boutons de
  réglages, « Parcourir », suggestions et pieds de page passent par là. Corrige au passage un double
  `sc()` sur le bouton « Parcourir » (invisible à 100 %, flagrant à 130 %). *Vérifié : 144 px pour
  « Português (Brasil) » ; `width=100` à 130 % = 130 px, pas 169.*
- [x] **E3 — Contraste et échelle typographique** : FAINT 4,72:1 sur carte, MUTED 6,88:1, blanc sur
  bleu primaire 5,17:1 ; deux paliers de police (aides 13 px, corps 15 px). *Vérifié : calculs WCAG
  + `font(8/9)=13`, `font(10/11)=15`, titres 15/17/20.*
- [x] **E4 — Journal** : ne suit plus le curseur quand on a remonté (restauration par `yview(index)`).
  *Vérifié : défilement 0,000 après une ligne ajoutée pendant la lecture, et suivi normal en bas.*
- [x] **E5 — Fenêtre « Sortie console »** : barre de titre sombre (vérifiée par
  `DwmGetWindowAttribute` = 1) et rafraîchissement automatique toutes les secondes.
- [x] **E6 — Clavier** : boutons atteignables au Tab avec anneau de focus, Espace/Entrée les activent,
  Échap revient en arrière, Entrée enregistre/ajoute. *Vérifié : 9/9 au harnais.*
- [x] **E7 — Infobulle du tray** : résumé global des tuiles actives (pauses exclues).

### Hygiène du dossier — faite le 11 novembre 2026

- [x] **71 captures** `apercu_*.png` rangées dans `captures/` (la racine ne contient plus que du
  code et de la documentation).
- [x] **Supprimés** : `nul` (racine et `dist/`), `err.txt` à `err4.txt`, `hermes_gui.py` +
  `test_gui.py` (ancienne app Tkinter/pystray), `hermes_gui.spec` et `HermesServer.spec` (recettes de
  build de cette ancienne app) et leurs binaires `dist\hermes_gui.exe` / `dist\HermesServer.exe`.
- [x] **`README.md`** d'entrée : démarrer, réglages, structure du dossier, ajouter un service.
- [x] **`AMELIORATIONS.md`** marqué **historique** (tout réalisé ; lien vers ROADMAP/NOTES/README).
- [x] **Sauvegardes de build** : les deux plus récentes conservées (`bak-20261110T092631` et
  `bak-20261110T093925`), les 19 autres supprimées — **599 Mo récupérés**, `dist/` passe de 693 à
  94 Mo (sur accord du 11 nov 2026).

### Vérification de fin de chantier — faite le 11 nov 2026

14/14 au harnais logique, 19/19 au harnais esthétique, 9/9 au clavier ; captures
`apercu_revue_serveur/reglages/ajout/hermes.png` (panneau Hermes sur copie de la vraie config,
phase `stopped`, 7 lignes, 4 actions) ; parité 5 langues ; `dist/HermesServ.exe` reconstruit
(11 nov 09:53) et relancé (fenêtre « HermesServ »).

---

## Plan de travail — 16 nov 2026 (revue critique : hygiène, sécurité, robustesse)

- [x] **1 — Hygiène** : git, dépendances, tests pytest, `legacy/`, `.bat` portable.
- [x] **2 — Sécurité** : MCP durci (Origin/Host/Content-Type/jeton), import de tuile avec aperçu et
  auto-démarrage coupé, export sans `env`, types intégrés protégés des plugins.
- [x] **3 — Robustesse** : config illisible protégée, instance unique, journal fichier, arrêt
  propre (CTRL_BREAK), `ui_call` annulable, fermeture sans gel, sondeur à pool + instantané TCP.
- [x] **4 — UX de vue d'ensemble** (v2.2.0) : tableau de bord d'accueil, rail compact à badges
  colorés + recherche, statut honnête de la chaîne Hermes, libellés, journal à onglets, ajout
  guidé en 3 étapes, premier lancement, sous-menus tray, raccourci global. Thème clair reporté
  (voir NOTES).
- [x] **4 bis — Renommage** « Background Check » (BGC), v2.3.0 : textes, exe, icône, dossier de
  données copié, autostart, paquet `bgc` (shim `hermesserv`), raccourci du bureau.
- [x] **5 — Modèle « liste d'items »** (v2.4.0) : arrêt propre depuis le tray corrigé, découpage
  `bgc/core/` (runtime, sondeur, actions communs à l'UI, au tray et au MCP) + `bgc/ui/views/`,
  objet de thème `T` ; `snapshot()` publie des éléments avec actions par élément (UI, tray,
  MCP `list_items`/`item_action`) ; types Docker/Podman, service Windows, WSL par distribution ;
  thème clair et « comme Windows ».
- Fonctions voisines (QR code, VRAM, uptime, alertes…) : `PLUS_TARD.md`, **non planifiées**.

## Plan de travail — 7 nov 2026 (icônes + généralisation — terminé)

Demande de l'utilisateur : **des icônes dans le menu de gauche** (rendre le rail convivial), et
carte blanche sur le reste **dans l'ordre d'importance que je choisis**, sous une seule réserve :
**la fonctionnalité première — l'onglet Hermes, qui pilote le serveur du dossier Hermes — reste
intacte**.

### Lignes rouges (valables à chaque étape)

1. **Le plugin Hermes ne régresse jamais** : à la fin de chaque étape, `snapshot()` sur la vraie
   config en lecture seule + capture du panneau Hermes. Jamais de `--selftest` tant que le
   service de l'utilisateur tourne (il adopterait son listener puis l'arrêterait).
2. **Rien n'est écrit** dans le dossier d'installation Hermes ni dans sa config.
3. **Tests sur config de test** (`HERMESSERV_CONFIG`) : le vrai `config.json` n'est pas touché
   ; le config de test a `auto_launch_desktop: false` et `start_on_app_start: false` (aucun
   effet de bord).
4. **Rebuild de l'exécutable** = demander avant (l'app ouverte verrouille le fichier) ; tester
   depuis la source + `HermesServ.bat` en attendant.
5. À la fin de chaque étape : `NOTES.md` à jour, parité des clés i18n sur les 5 langues,
   captures livrées (`--shot`, les deux états si l'état change).

### Étapes (ordre retenu, chacune livrable seule)

1. **Fait (7 nov 2026)** — **Icônes dans le rail** (demande explicite). Pictogrammes vectoriels
   dessinés au Canvas par type (Hermes, WSL, STT, port, générique) dans la couleur du type, à la
   place de la lettre (lettre gardée en repli pour un type inconnu) ; **icône personnalisée par
   serveur** (clé `icon` = chemin vers un `.png`/`.ico`, chargée via PIL, repli pictogramme si
   absente ou illisible) + champ « Icône » dans les réglages du serveur. Le point d'état et le
   libellé restent : l'icône ne remplace jamais le doublon couleur+mot de l'état.
2. **Fait (7 nov 2026)** — **Type « commande personnalisée »** — le levier « généraliste » :
   lancer / surveiller / arrêter n'importe quelle commande (ligne, dossier de travail, variables
   d'environnement), sonde au choix (port, URL, « le process tourne »), arrêt ciblé PID + enfants
   récursifs, adoption/refus comme le socle. Couvre DSH, OpenClaw, Home Assistant, tout serveur
   de dev — sans écrire de plugin. Détails et pièges : `NOTES.md` (26/26 au harnais).
3. **Fait (7 nov 2026)** — **Ajout assisté par détection** — dans « add server » : les ports en
   écoute (≥ 1024, `port · processus`) et les distributions WSL sont listés et **cliquables** ;
   un clic remplit les champs (port/distro + nom suggéré). Section « Détecté sur cette machine »
   au-dessus des champs, visible sans défiler.
4. **Fait (9 nov 2026, via le chantier 5)** — **Notification quand un service tombe** : bulle
   du tray dès qu'un serveur *en marche* retombe, une seule par chute, jamais sur un arrêt
   demandé depuis l'app (fenêtre de 20 s) ni sur un simple `withdraw()`. Option dans les réglages.
5. **Fait (9 nov 2026)** — **Compteurs + journal filtrable** : nombre de chutes depuis le
   lancement affiché sur la ligne d'état de chaque tuile, et champ de filtre au-dessus du journal
   (filtre sur le texte traduit, insensible à la casse).
6. **Fait (9 nov 2026)** — **Actions personnalisées par serveur** : jusqu'à 3 boutons
   « libellé + adresse ou commande » par tuile, affichés dans la rangée d'actions. Une adresse
   `http(s)` s'ouvre, tout le reste est lancé comme commande dans le dossier de travail du
   service.

### Confort au fil de l'eau (reste à faire)

Clic droit sur la tuile (renommer / dupliquer / masquer — aujourd'hui c'est dans les réglages de
la tuile), mode CLI `--list --json`, type « service Windows », `portwatch` présenté comme
« surveiller une machine du réseau » (le champ `host` existe déjà). L'ordre et la visibilité,
l'import/export de tuile et le démarrage avec Windows sont faits (9 nov 2026).

## Ajouts demandés — 9 nov 2026 (terminé)

- **Gateway local dans l'onglet Hermes** : ligne « Gateway local » (port/host lus dans
  `config.yaml`, écouteur réel + `/health`, version), ligne « Connexion du desktop » (ce PC ou
  l'URL distante, lue dans le registre du desktop) et ligne « Processus serve/dashboard »
  renommée — l'ancienne « Backend desktop » ne cherchait que `hermes.exe serve|dashboard` et
  affichait donc « aucun » devant un gateway lancé par Electron.
- **Sondes supplémentaires** (facultatif) sur les tuiles génériques : champ `extra_checks`,
  une ligne par sonde (`nom = port` / `nom = hôte:port` / `nom = URL`), six au maximum, cache 5 s.
  Détails et pièges : `NOTES.md`.
- **Défilement de la colonne des tuiles** : molette réparée (un seul répartiteur pour toutes les
  zones défilantes, l'ancien `bind_all` par instance était écrasé par la première vue de
  réglages) + barre fine visible qui n'apparaît que si le contenu dépasse, glisser du pouce.
  Une tuile « Réglages » reste épinglée en bas.
- **Réordonner au glisser-déposer** : on saisit une tuile du rail, un trait montre où elle sera
  posée, on relâche. Les boutons Monter/Descendre des réglages sont supprimés (Dupliquer reste).
- **Cible en une seule ligne** : hôte, port et chemin de vérification fusionnés en un champ
  unique (`127.0.0.1:5451`, `5451`, `192.168.1.50:8123` ou une URL complète) ; le fichier de
  configuration garde ses trois champs, donc les tuiles existantes et exportées restent valides.
- **Tuile LlamaGUI complète** : preset qui remplit commande (Python du projet + `server.py`),
  dossier, port 5240, icône, sonde du **llama-server sur 9931** et deux boutons « Ouvrir
  LlamaGUI » / « Ouvrir la webui llama ». Les presets sont aussi disponibles dans les réglages
  d'une tuile existante.

## Questions ouvertes

- **DSH (DeepSeek Harness)** : mis de côté à la demande de l'utilisateur (24 oct 2026). Quand on y
  reviendra, il faudra son mode de lancement et de supervision (commande, port, service Windows).
  **OpenClaw** et **Home Assistant** sont cités aussi.
- Quels autres services ensuite ? (une liste par ordre d'utilité évite de concevoir dans le vide)
- Faut-il un état « historique » (uptime, plantages) ou seulement l'instant présent ?
- ~~La config des services doit-elle être éditable dans l'UI, ou un simple fichier suffit-il ?~~
  **Tranché le 23 oct 2026** : éditable dans l'UI (réglages généraux + par serveur), doublée d'un
  `config.json` lisible et éditable à la main.

## Faisabilité — évaluation (19 oct 2026)

Verdict : **faisable, sans dépendance nouvelle, mais ce n'est pas « petit »**. Les points
durs ne sont pas l'interface :

- Le 90 % (rail, panneaux, boutons, journal, notifications d'état) réutilise ce qui existe déjà.
- Le vrai travail est le **contrat de plugin** : s'il est trop étroit, chaque service finit en
  code sur mesure ; trop large, personne ne l'implémente. Le passage par la liste d'items +
  actions par item est le choix qui décide de la réussite.
- Le socle sécurité (adopter / refuser / arrêt ciblé / journal) est déjà écrit et testé : c'est
  ce qui rend le reste mécanique.

Estimation : ~3 à 4 sessions ciblées pour un outil réellement général (socle + rail) et deux
plugins. Le partage de plugins est simple techniquement ; la confiance est le seul point de
vigilance.
