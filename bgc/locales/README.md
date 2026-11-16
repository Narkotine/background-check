# Ajouter une langue

Chaque langue est **un seul fichier JSON** dans ce dossier : `locales/<code>.json`.
Aucun code à modifier — le programme découvre les fichiers au démarrage.

## Marche à suivre

1. Copier `fr.json` en `<code>.json` (code court : `it`, `de`, `pt-PT`, `ja`, `zh-TW`…).
2. Remplir `meta` :
   ```json
   "meta": { "code": "it", "name": "Italiano", "ui_font": "Segoe UI" }
   ```
   - `name` : nom **dans cette langue**, affiché dans les réglages.
   - `ui_font` : police qui contient les glyphes de la langue. `Segoe UI` suffit pour les
     alphabets latins et cyrilliques/grecs ; pour le chinois/japonais/coréen mettre une police
     système adaptée (`Microsoft YaHei UI`, `Yu Gothic UI`, `Malgun Gothic`).
3. Traduire les valeurs de `strings` — **jamais les clés**. Les `{parametres}` entre accolades
   doivent rester tels quels (ils sont remplacés à l'affichage).
4. Redémarrer l'application : la langue apparaît dans **Réglages → Langue**.

## Règles

- Une clé absente retombe sur le **français**, puis sur la clé elle-même : l'app ne casse jamais,
  mais un texte non traduit se voit — comparer avec `fr.json` (le fichier de référence).
- Les journaux ne stockent que des **clés + paramètres** : changer de langue retraduit aussi
  l'historique déjà affiché.
- Vérifier la parité des clés avant de livrer :
  ```bash
  python -c "import json,sys;\
a=json.load(open('locales/fr.json'))['strings'];b=json.load(open('locales/it.json'))['strings'];\
print('manquantes:', sorted(set(a)-set(b))); print('en trop:', sorted(set(b)-set(a)))"
  ```
- Empaquetage : les fichiers de ce dossier doivent être joints au `.exe`
  (`--add-data "bgc/locales;bgc/locales"`).
