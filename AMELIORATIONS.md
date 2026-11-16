# HermesServ — Pistes d'améliorations (versatilité & clarté d'usage)

> **Historique (11 novembre 2026)** — toutes les pistes de ce document ont été réalisées et
> vérifiées : chantiers 1 à 5 du 9 novembre 2026 (validés par harnais et captures), puis revue
> critique du 11 novembre 2026 (sondeur parallèle, détection réparée, pied de page fixe, contrastes,
> largeurs mesurées, clavier). Le document est conservé comme analyse d'origine : la vision et les
> étapes sont dans `ROADMAP.md`, les constats vérifiés dans `NOTES.md`, le mode d'emploi dans
> `README.md`.

Ce document recense les défauts actuels qui brident la polyvalence de l'outil, ainsi que les améliorations proposées pour rendre chaque possibilité **immédiatement compréhensible et utilisable** sans connaître le code sous-jacent.

---

## 1. Lever le blocage actuel sur la « surveillance pure » (Urgent)

Aujourd'hui, le texte du type **« Service surveillé »** (`Watched service`) promet :
> *« Surveille un port ou une URL, et peut le lancer/arrêter avec tes commandes (facultatif). Vide = le service est seulement surveillé. »*

Mais dans le code (`plugins/command.py`), il reste deux contradictions qui empêchent cet usage :

1. **Impossible d'enregistrer un serveur sans commande :**
   `CommandType.validate()` rejette le formulaire si `command` ET `stop_command` sont vides (`"Fill in at least the start command or the stop command"`).
   - *Conséquence :* impossible d'ajouter un service tiers ou une machine du réseau (`192.168.1.50`) pour simplement observer son port, ni même de modifier et ré-enregistrer la tuile **LlamaGUI** (port 5240, sans commande) déjà présente dans ta config !
   - *Correction :* autoriser l'enregistrement dès qu'il y a **au moins un port OU une commande** (démarrage ou arrêt).
2. **Bouton « Start / Stop » affiché sur un service sans commande :**
   Sur une tuile en lecture seule (port seul, aucune commande configurée), le gros bouton principal **« Start server » / « Stop server »** s'affiche quand même, et cliquer dessus écrit juste un refus dans le journal.
   - *Correction :* si aucune commande n'est définie, masquer le bouton Start/Stop (ou afficher un badge clair *« Mode observation (aucune commande configurée) »*) et proposer à la place des boutons adaptés : **« Ouvrir dans le navigateur »** et **« Tester maintenant »**.

---

## 2. Rendre les possibilités auto-explicatives dans l'interface

Le type **« Service surveillé »** (`command`) est très puissant (processus classique, CLI démon type LM Studio, port local, machine distante sur le LAN), mais un grand formulaire de 8 champs à plat ne montre pas comment les combiner.

### A. Pré-réglages (« Modèles rapides ») en haut du formulaire d'ajout
Au lieu de remplir 8 champs en devinant lesquels laisser vides, proposer 4 boutons de **Scénario / Modèle** qui pré-configurent ou filtrent les champs avec un exemple parlant :
1. **Surveiller un port ou un appareil réseau (lecture seule)** — met en avant `Hôte`, `Port` et `URL de vérification`, replie la section commandes.
2. **Lancer un programme ou script local** — met en avant `Commande de démarrage`, `Dossier de travail`, `Port` (optionnel).
3. **Piloter un démon par CLI (ex. LM Studio, Ollama, Docker)** — met en avant `Commande de démarrage` (`lms server start`) + `Commande d'arrêt` (`lms server stop`) + `Port` (`1234`) + `URL de vérification` (`/v1/models`).
4. **Catalogue 1-clic de services connus détectés sur le PC :** si l'app repère `lms.exe`, `ollama.exe`, `llama-server.exe` ou un port connu, proposer un bouton *« Pré-remplir pour LM Studio / Ollama »* (commandes, port, URL `/v1/models` et icône du `.exe` remplis d'un coup).

### B. Regrouper visuellement le formulaire en 3 blocs clairs
Dans `AddServerView` et `ServerSettingsView`, séparer les champs par de petits sous-titres explicites :
- **1. Quoi observer ? (Sonde)** : `Hôte`, `Port`, `URL de vérification`.
  *Texte d'aide : « Permet de savoir si le service est en ligne, même s'il a été lancé hors d'HermesServ. »*
- **2. Comment le piloter ? (Facultatif)** : `Commande de démarrage`, `Commande d'arrêt`, `Dossier de travail`, `Variables d'environnement`.
  *Texte d'aide : « Laissez vide si vous voulez seulement surveiller l'état sans lancer ni arrêter le service depuis l'application. »*
- **3. Accès & Affichage** : `Nom affiché`, `Icône`, `Adresse publique`.

### C. Bandeau « Que fait cet onglet ? » en haut de chaque serveur
Sur la vue principale d'un serveur, ajouter une phrase de résumé dynamique (1 ligne discrète sous le titre) qui explique en français/anglais courant le comportement exact configuré :
- *Ex. LlamaGUI :* `Surveillance seule de 127.0.0.1:5240 (aucune commande de lancement ou d'arrêt).`
- *Ex. LM Studio :* `Sonde 127.0.0.1:1234/v1/models · Démarrage via « lms server start » · Arrêt via « lms server stop ».`
- *Ex. Hermes :* `Pilote uniquement le serveur web distant (port 9119) · Ne coupe jamais le moteur de l'app Desktop.`

Cela évite tout doute sur « qu'est-ce qui va se passer si je clique sur Stop ? ».

---

## 3. Rendre chaque onglet plus polyvalent au quotidien

### A. Boutons d'action utiles sur le type « Service surveillé »
Contrairement à l'onglet Hermes (qui a 4 boutons) ou WSL (3 boutons), le type généraliste n'a aujourd'hui **qu'un seul bouton** (`Start/Stop`), même quand une URL ou un port HTTP est configuré.
- Ajouter automatiquement le bouton **« Navigateur »** (`Browser`) et **« Copier l'URL »** dès qu'une `Adresse publique` ou un `Port` HTTP est renseigné.
- Ajouter un bouton **« Ouvrir le dossier »** si `workdir` (ou le chemin de l'exécutable) est connu.
- **Actions personnalisées (boutons sur mesure) :** permettre d'ajouter dans les réglages du serveur 1 à 3 boutons personnels `{ Libellé, Commande ou URL }` (ex. *« Vider la VRAM »*, *« Ouvrir les logs »*, *« Page Admin »*).

### B. Voir la sortie console (stdout / stderr) quand une commande échoue
Actuellement, `CommandController.start()` lance le processus avec `stdout=DEVNULL, stderr=DEVNULL`.
- Si un script Python ou un serveur local plante au démarrage (erreur de syntaxe, port déjà pris, argument invalide), le journal dit seulement que le serveur est retombé, **sans jamais montrer l'erreur émise par le programme**.
- *Amélioration :* capturer les dernières lignes de `stdout`/`stderr` dans un tampon mémoire (circulaire, ex. 50 lignes) et :
  1. En cas d'arrêt prématuré dans les premières secondes, écrire automatiquement la dernière ligne d'erreur (`stderr`) dans le journal d'HermesServ.
  2. Proposer un petit bouton **« Sortie console »** (ou un onglet à côté du journal) pour lire ce que le programme affiche en direct.

### C. Boutons « Parcourir… » sur tous les champs de chemin
Le bouton **Parcourir** ajouté pour l'icône est très pratique. Or trois autres champs demandent encore de copier-coller des chemins à la main :
- `Dossier de travail` (`workdir` du type Service surveillé) → bouton **Parcourir un dossier** (`askdirectory`).
- `Dossier Hermes` (`hermes_root`) → bouton **Parcourir un dossier**.
- `Commande de démarrage` → bouton **Choisir un .exe / .bat** qui insère le chemin (avec guillemets s'il contient des espaces).

---

## 4. Confort de gestion (Barre de gauche, Tray, Réglages)

1. **Réorganiser, dupliquer ou désactiver un onglet :**
   - Aujourd'hui l'ordre des tuiles à gauche est figé dans l'ordre de création, et on ne peut que supprimer un serveur.
   - Ajouter dans les réglages (ou au clic droit sur une tuile) : **Monter / Descendre**, **Dupliquer** (pratique pour créer un 2e serveur similaire), et utiliser le booléen `enabled` (déjà présent dans `config.json` !) pour **Masquer / Mettre en pause la surveillance** d'un serveur sans le supprimer.
2. **Menu de la zone de notification (Tray) multi-serveurs :**
   - Le clic droit sur l'icône Windows ne permet aujourd'hui de démarrer/arrêter **que le serveur actuellement sélectionné** dans la fenêtre (`current_runtime()`), sans même nommer lequel dans l'intitulé du bouton (`Start server` / `Stop server`).
   - *Amélioration :* lister chaque serveur dans le menu du clic droit avec son voyant d'état (ex. `🟢 Hermes — Arrêter`, `⚪ LM Studio — Démarrer`) pour tout piloter sans ouvrir la fenêtre.
3. **Notification discrète en cas de chute inattendue :**
   - La méthode Win32 `tray.balloon()` est déjà codée et testée. Ajouter une option générale (désactivée ou activée au choix) : *« M'alerter par une notification Windows uniquement si un serveur en marche tombe tout seul »*.
4. **Bouton « Ouvrir le dossier de config » dans les Réglages généraux :**
   - Le chemin `%LOCALAPPDATA%\HermesServ\config.json` est affiché en bleu dans les réglages, mais il n'est ni sélectionnable ni cliquable. Ajouter un bouton **« Ouvrir dans l'explorateur »** (et **« Copier le chemin »**) juste en dessous.
5. **Adoption au redémarrage d'HermesServ (cas Hermes 9119) :**
   - Comme noté le 8 nov dans `NOTES.md`, quand on relance HermesServ alors que le dashboard 9119 tourne déjà, `HermesController.adopt()` le reconnaît comme le nôtre, mais si le PID change ou selon l'ordre d'initialisation, clarifier dans l'UI pourquoi un bouton est grisé ou permettre de reprendre la main proprement.

---

## Résumé par priorité suggérée

| Priorité | Chantier | Gain immédiat |
|---|---|---|
| **1 (Correctif clé)** | Validation `command.py` (autoriser port seul sans commande) + masquer Start/Stop en mode lecture seule + boutons Navigateur/Copier URL | Répare l'édition de *LlamaGUI* et rend la surveillance pure (locale ou LAN) 100 % fonctionnelle. |
| **2 (Clarté & UX)** | Modèles rapides à l'ajout + formulaire découpé en 3 blocs + phrase de résumé « Que fait cet onglet ? » + boutons Parcourir sur les dossiers | Tout est expliqué visuellement dans l'app, plus besoin de deviner les combinaisons de champs. |
| **3 (Diagnostic)** | Capture des dernières lignes `stderr`/`stdout` des commandes lancées | On sait immédiatement *pourquoi* une commande personnalisée refuse de démarrer. |
| **4 (Polyvalence)** | Boutons d'actions personnalisées par serveur + menu Tray multi-serveurs + réorganisation des tuiles | Chaque onglet a ses propres raccourcis et se pilote directement depuis la barre des tâches. |
