# ST-AIR : extraction read-only pour ingénierie du fork

Outil : `tools/stair_extract.py`.

## Garanties

- Session PostgreSQL ouverte en **READ ONLY strict** :
  - `conn.set_session(readonly=True, autocommit=False)` côté psycopg2.
  - `SET TRANSACTION READ ONLY` côté serveur.
  - `PGOPTIONS=-c default_transaction_read_only=on` propagé à `ogr2ogr`.
- Aucune requête `INSERT / UPDATE / DELETE / DDL / COPY FROM` dans le code. Toute tentative serait rejetée par le serveur.
- `application_name=stair_extract_readonly` : tu peux le vérifier côté BDD via
  `SELECT application_name, state FROM pg_stat_activity WHERE usename = '<user>';`.

## Ce que l'outil produit

```
<out_dir>/
  discovery.json                 # schemas, tables, colonnes, fonctions, triggers, row_counts
  functions/<schema>__<name>.sql # pg_get_functiondef pour chaque fonction de rip_avg_nge + comac
  triggers/<schema>__<name>.sql  # pg_get_triggerdef pour tous les triggers
  reference_data/                # 7 CSV UTF-8, ';' delim
    cables.csv
    cable_capacites_possibles.csv
    hypothese.csv
    communes.csv
    supports.csv
    armements.csv
    sro_nge_axione.csv
    VERSION.txt                  # date + sha256 de chaque CSV
  projets/<SRO>/<SRO>.gpkg       # GPKG multi-couches : infra_pt_pot, infra_pt_chb,
                                 # infra_pt_autres, etude_cap_ft, etude_comac,
                                 # bpe, attaches, cables_dc (via fddcpiax)
  analysis_report.md             # synthese humaine-lisible
```

## Usage 1 : console Python QGIS (recommandé)

Les credentials sont lus depuis `QgsSettings` (connexion `Auvergne` existante).

```python
import sys
sys.path.insert(0, r"C:\Users\yadda\AppData\Roaming\QGIS\QGIS4\profiles\default\python\plugins\PoleAerien_shp\tools")
import importlib, stair_extract
importlib.reload(stair_extract)
stair_extract.run(out_dir=r"C:\tmp\stair_samples", nb_sros=3)
```

Ou pour cibler des SRO précis :

```python
stair_extract.run(
    out_dir=r"C:\tmp\stair_samples",
    explicit_sros=["63041/B1I/PMZ/00003", "63345/XXX/YYY/00001", "43019/ZZZ/WWW/00002"],
)
```

## Usage 2 : CLI hors QGIS

Nécessite : `pip install psycopg2-binary` + `ogr2ogr` dans le `PATH` (ou OSGeo4W installé).

```powershell
$env:PG_HOST = "..."
$env:PG_PORT = "5432"
$env:PG_DATABASE = "..."
$env:PG_USER = "..."
$env:PG_PASSWORD = "..."
python stair_extract.py C:\tmp\stair_samples 3
```

Le password n'est jamais mis en ligne de commande : il est transmis via variable d'env à `ogr2ogr` (`PGPASSWORD`).

## Après l'extraction

Envoie-moi le dossier `<out_dir>` (ou au minimum `discovery.json` + `analysis_report.md` + les 7 CSV + **1 seul GPKG SRO** pour démarrer). Tu peux anonymiser les valeurs `inf_num` / `adr_insee` si besoin, mais les noms de colonnes doivent être conservés intacts.

Une fois reçu, je peux :
1. Valider les schémas réels des couches et des CSV (confirmation ou ajustement des hypothèses du backlog).
2. Lire les définitions SQL de `fddcpiax`, `fdrca`, `infra`, triggers `insert_inf_num_*`.
3. Commencer `shp_cable_reader.py`, `reference_data_reader.py`, `maj_shp_edit.py`.

## Dépannage rapide

- **`psycopg2 absent`** : console QGIS > `pip install --user psycopg2-binary`. Si impossible, utiliser l'Usage 2 en CLI dans un Python externe.
- **`ogr2ogr introuvable`** : adapter la liste `candidates` dans `_find_ogr2ogr()`, ou ajouter `C:\Program Files\QGIS X.Y.Z\bin` au `PATH`.
- **`Aucune connexion PostgreSQL trouvee`** : soit le nom QGIS ne contient pas "auvergne" et le `database` non plus -> ajouter ton nom de connexion dans la condition, soit utiliser les variables `PG_*`.
- **Erreur `default_transaction_read_only`** sur ogr2ogr : certaines versions de GDAL ignorent `PGOPTIONS`. La garantie read-only reste assurée côté psycopg2 ; pour `ogr2ogr`, aucune instruction de mutation n'est envoyée (uniquement `SELECT` via `-sql`).
