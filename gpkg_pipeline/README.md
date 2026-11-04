# gpkg_pipeline

Pipeline Python de découpage de câbles aériens pour le livrable
**sous-traitant aérien (STAIR)**.

Porte en Python la fonction SQL `rip_avg_nge.fddcpiax` côté serveur
dans une version **simplifiée et hors ligne**, afin que les sous-traitants
puissent exécuter le découpage chez eux sur un GeoPackage fourni par
le bureau d'études.

## Périmètre

| Aspect | Statut |
|---|---|
| Découpage sur **poteaux** (`infra_pt_pot`) | Inclus |
| Découpage sur **BPE** (`bpe`) | Inclus |
| Découpage sur **endpoints d'attaches** (start + end) | Inclus |
| Classification posemode aérien (1) / orphelin (4) | Inclus |
| Souterrain (`infra_pt_chb`, chambres) | **Hors périmètre** |
| Façades / adresses / immeubles (`infra_pt_autres`) | **Hors périmètre** |
| Recalage `fdrca` (ST_SetPoint sur attaches) | Non nécessaire : la logique est déjà intégrée dans le découpage via `attache_endpoints()` |

## Format du GeoPackage d'entrée

Un GPKG par SRO, filtré serveur-side sur le code SRO, contenant :

| Couche | Type géom | Rôle |
|---|---|---|
| `cables` | LINESTRING | Câbles bruts BPE-BPE à découper |
| `bpe` | POINT | Boîtes de distribution (points de split + extrémités) |
| `attaches` | LINESTRING | Attaches (chaque extrémité sert de point de split) |
| `infra_pt_pot` | POINT | Poteaux (points de split intermédiaires) |
| `etude_cap_ft` *(optionnel)* | POINT | Tables métier FTTH (non utilisées ici) |
| `etude_comac` *(optionnel)* | POINT | Tables métier BT (non utilisées ici) |

SRID obligatoire : **2154 (Lambert 93)**.

## Utilisation depuis le plugin

### Cas 1 : GeoPackage direct (déjà décompressé)

```python
from PoleAerien_shp.gpkg_pipeline import split_cables_from_gpkg

stats = split_cables_from_gpkg(
    input_gpkg="SRO_XXXYYY.gpkg",
    output_gpkg=None,       # None = écrit la couche dans le GPKG source
    tolerance=0.70,         # mètres, identique au SQL fddcpiax
    sro="XXXYYY",
)
print(stats.as_dict())
```

### Cas 2 : ZIP contenant un GeoPackage (livrable du plugin annexe)

```python
from PoleAerien_shp.gpkg_pipeline import (
    extract_gpkg_tempdir,
    split_cables_from_gpkg,
    load_gpkg_layers_into_qgis,
)

# Dezippage + decoupage + nettoyage automatiques
with extract_gpkg_tempdir("SRO_XXXYYY.zip") as gpkg_path:
    stats = split_cables_from_gpkg(gpkg_path, sro="XXXYYY")
    load_gpkg_layers_into_qgis(gpkg_path, group_name="SRO XXXYYY")
# Le dossier temp est supprime ici. Si tu veux conserver le GPKG,
# utilise extract_gpkg_from_zip(zip_path, dest_dir="chemin/permanent").
```

### Variantes pour le dézippage

| Fonction | Usage |
|---|---|
| `list_gpkg_in_zip(zip_path)` | Lister les `.gpkg` sans extraire |
| `extract_gpkg_from_zip(zip_path, dest_dir, gpkg_member=None)` | Extraction permanente |
| `extract_gpkg_tempdir(zip_path)` | Context manager avec nettoyage auto |
| `load_gpkg_layers_into_qgis(gpkg_path, group_name="...")` | Ajouter les couches dans le projet QGIS |

Protections intégrées :

- **Anti Zip-Slip** : les noms d'archive avec `../` sont aplatis (`_flatten_name`) et vérifiés via `_safe_join`
- **Multi-GPKG** : si le ZIP contient plusieurs `.gpkg`, `ValueError` explicite qui force à préciser `gpkg_member`
- **Fichiers auxiliaires** : `-wal`, `-shm`, `-journal` extraits silencieusement si présents

Couche produite : `cables_decoupes` avec les colonnes :

| Colonne | Type | Description |
|---|---|---|
| `fid` | INTEGER | Clé technique (auto) |
| `gid` | INTEGER | GID du câble parent |
| `gid_dc` | INTEGER | ID segment unique (généré) |
| `sro` | TEXT | Code SRO |
| `length` | REAL | Longueur du segment (m) |
| `start_ref` | TEXT | Référence infra côté StartPoint (`pot_123`, `bpe_45`, ou NULL) |
| `end_ref` | TEXT | Idem côté EndPoint |
| `posemode` | INTEGER | 1 = aérien, 4 = orphelin |
| *attributs cable* | * | `cab_type`, `cab_capa`, `cab_nature`, etc. (copiés) |
| `geom` | BLOB | Géométrie LINESTRING |

## Algorithme (résumé)

Voir `splitter.py` pour l'implémentation complète. Étapes :

1. **Collecte points de split** pour chaque câble :
   - Tous les poteaux (`pot_`) et BPE (`bpe_`) dans une tolérance
   - **Les 2 endpoints** de chaque attache proche (`att_gid_s`, `att_gid_e`)
2. **Projection perpendiculaire** (`ST_ClosestPoint`) mid-câble uniquement (fraction 1%-99%) pour éviter les micro-segments
3. **Snap + Split** (`ST_Snap` + `ST_Split`) avec précision 1cm
4. **Passthrough** : câble sans projection mid-câble mais avec appui proche → gardé entier
5. **Orphelin** : câble sans aucun appui dans `max(5m, 2×tol)` → posemode=4
6. **Matching endpoint → infra** en 2 tiers (tolérance directe, puis fallback), priorité `pot_` sur `bpe_`
7. **Classification posemode** : `pot_|bpe_` des deux côtés → 1, sinon 4
8. **Déduplication** (gid câble + hash WKB)

## Pourquoi les attaches livrent DEUX points de split

Le SQL d'origine `FDDCPFA` n'utilise que `ST_EndPoint(attache)`. Ce plugin
utilise **les deux endpoints** car empiriquement le BPE ou le poteau
correspondant peut se trouver de n'importe quel côté de l'attache. Ne
snapper que sur un seul endpoint laisserait parfois le câble non découpé.

Voir test `test_attache_decoupe_sur_endpoint` pour un cas reproduit.

## Modules

```
gpkg_pipeline/
  __init__.py       # API publique
  dataclasses.py    # structures immuables + constantes
  spatial_ops.py    # opérations géométriques pures (Shapely)
  gpkg_io.py        # lecture/écriture GeoPackage (sqlite3 + shapely.wkb)
  posemode.py       # règles de classification
  splitter.py       # orchestration algo découpage
  pipeline.py       # orchestrateur GPKG → GPKG
  zip_loader.py     # dézippage ZIP → GPKG + chargement QGIS
  README.md         # ce fichier
```

## Dépendances

- `shapely >= 2.0` (disponible dans QGIS 3.34+ et 4.x)
- `sqlite3`, `zipfile`, `tempfile` (stdlib Python)
- `qgis.core` (uniquement pour `load_gpkg_layers_into_qgis`, facultatif)

Aucune dépendance externe `fiona`, `osgeo`, ou `geopandas`.

## Tests

```bash
cd PoleAerien_shp
python -m pytest tests/test_gpkg_pipeline.py -v
```

**48 tests** couvrant :
- `posemode` : matrice complète (6 tests)
- `spatial_ops` : projection, snap, split, matching, dedup (12 tests)
- `gpkg_io` : header binaire + lecture/écriture (4 tests)
- `splitter` : cas métier (12 tests, dont le cas critique attache)
- `pipeline` : end-to-end GPKG (4 tests)
- `zip_loader` : extraction mono/multi, erreurs, Zip-Slip, tempdir, flux complet (10 tests)
