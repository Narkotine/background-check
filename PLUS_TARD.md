# Background Check (ex-HermesServ) — fonctions voisines gardées pour plus tard

Issues de la revue du **16 novembre 2026** (section « fonctionnalités connexes »). À ta
demande, elles sont **notées, pas développées**. Classées par intérêt pour l'usage actuel
(IA locale + Hermes depuis le téléphone).

| # | Fonction | En bref |
|---|---|---|
| 1 | **QR code de l'URL publique** | Afficher un QR code de l'URL Tailscale (Hermes 9119 et toute tuile web exposée) pour l'ouvrir sur le téléphone sans copier-coller. Effort minime. |
| 2 | ~~**VRAM / GPU par service**~~ — **fait le 16 nov 2026** | CPU %, RAM et VRAM par tuile et par élément (arbre de processus, compteurs Windows « GPU Process Memory » via PDH), courbes 5 min, exposé au MCP. Voir NOTES. Reste du TODO du 18 oct : tok/s. |
| 3 | **Historique et uptime** | Frise 24 h / 7 j par tuile (en marche / chutes), compteur de plantages conservé, durée de démarrage. Base : le journal fichier ajouté le 16 nov. Question ouverte de la ROADMAP. |
| 4 | **Contrôle depuis le téléphone** | Mini page web (ou PWA) servie via Tailscale, avec authentification, pour voir l'état et démarrer/arrêter les tuiles à distance. Le MCP est durci (jeton), condition préalable remplie. |
| 5 | **Alertes sortantes** | Quand un service tombe : message ntfy / Telegram / Discord (Hermes sait déjà parler aux messageries), pour être prévenu loin du PC. |
| 6 | **Gestionnaire de modèles LLM** | Lister les GGUF du disque et les modèles Ollama / LM Studio, l'espace occupé, et « charger ce modèle dans llama-server » depuis un preset. |
| 7 | **Conflits de ports** | Quand un démarrage échoue sur « port pris » : dire qui le tient (déjà calculé) avec « arrêter ce processus » ou « changer de port ». |
| 8 | **Tableau de bord Tailscale** | Pairs en ligne, mappings `tailscale serve`, ajout/retrait d'un mapping pour une tuile en un clic (avec confirmation), Funnel on/off. |
| 9 | **Proxy LLM local unifié** | Une adresse unique en API OpenAI qui route vers le moteur actif (Ollama, LM Studio, llama-server) — comme le pont STT le fait déjà pour l'audio. |
| 10 | **Sauvegardes de config dans l'UI** | Liste des `config.json.bak-*`, restauration en un clic, synchronisation facultative vers un dossier cloud. |

Également reportés (hors « fonctions voisines », prévus dans la revue) : refonte UX (tableau
de bord d'accueil, rail compact à badges, statut de chaîne Hermes, ajout guidé) et passage au
modèle « liste d'items » (Docker, WSL par distro, service Windows).
