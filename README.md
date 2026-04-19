# ST-AIR - Plugin QGIS sous-traitant FTTH aerien

[![QGIS 3.28+](https://img.shields.io/badge/QGIS-3.28%20%E2%86%92%204.99-green)](https://qgis.org/)
[![Licence](https://img.shields.io/badge/licence-interne-blue)](#licence)

Plugin QGIS pour le controle qualite des livrables **FTTH aerien** par les
sous-traitants. Tout en local, sans serveur distant, sans cloud.

## En une minute

1. Vous avez un dossier projet type `63041-B1I-PMZ-00003` avec vos shapefiles
   (poteaux, etudes, cables) et vos Excel terrain (CAP_FT, COMAC, C6).
2. Vous installez le plugin dans QGIS.
3. Vous pointez le plugin sur votre dossier, vous cochez les modules
   d'analyse souhaites, vous cliquez **Demarrer**.
4. Vous recevez un **rapport Excel unique** avec :
   - Tableau de bord KPI,
   - Detail des anomalies par module,
   - Dictionnaire de donnees et glossaire metier.

## Pour les sous-traitants

Lisez d'abord le guide complet : **[docs/GUIDE_SOUS_TRAITANT.md](docs/GUIDE_SOUS_TRAITANT.md)**

Il explique pas a pas :
- L'installation,
- La structure attendue du dossier projet,
- Comment lancer une premiere analyse,
- Comment lire le rapport,
- Le modele de donnees et les valeurs metier autorisees,
- Le depannage des anomalies frequentes,
- Une FAQ.

## Modules d'analyse

| Module              | But                                                 |
|---------------------|-----------------------------------------------------|
| MAJ BD              | Preparer les MAJ sur le shapefile des poteaux       |
| Verification CAP_FT | Poteaux FT (shapefile) vs fiches d'appuis Orange    |
| Verification COMAC  | Poteaux BT, cables, boitiers, portees PCM vs terrain|
| GESPOT vs C6        | CSV GSPOT vs Excel C6, 11 criteres                  |
| C6 vs BD            | Annexes C6 Orange vs shapefile                      |

## Installation rapide

Prerequis : **QGIS 3.28 LTR ou superieur** (compatible QGIS 4.x).

1. Telechargez le ZIP fourni par votre donneur d'ordre.
2. QGIS -> **Extensions** -> **Installer/Gerer** -> **Installer depuis un ZIP**.
3. Lancez via le menu **ST-AIR** ou l'icone dans la barre d'outils.

Details et depannage : [docs/GUIDE_SOUS_TRAITANT.md](docs/GUIDE_SOUS_TRAITANT.md).

## Prerequis minimum

- QGIS 3.28 ou superieur (3.44 LTR recommande).
- 8 Go RAM (16 Go pour > 10 000 appuis).
- Tous les shapefiles en **EPSG:2154** (Lambert-93).
- Dossier projet nomme d'apres le SRO avec tirets
  (ex. `63041-B1I-PMZ-00003`).

## Dependances reseau

**Aucune.** Le plugin est 100 % local :

- Pas de serveur distant.
- Pas d'API externe.
- Pas de telemetrie.
- Pas d'authentification.

## Structure du projet

```
dossier_SRO/
|-- BASE DE DONNEES/NGE/*.shp     (poteaux, etudes, cables, bpe)
|-- BASE DE DONNEES/GSPOT/*.csv    (exports terrain GSPOT)
|-- CAP FT/                        (FicheAppui_*.xlsx + annexes C6)
|-- COMAC/                         (*ExportComac*.xlsx + PCM XML)
|-- FT-BT KO *.xlsx                (optionnel, recap MAJ)
|-- rapport_<SRO>_<date>.xlsx      (genere par le plugin)
```

## Developpeurs

Documentation technique interne :

- `BACKLOG_ST-AIR.md` : plan de refonte (fork full-local, non public).
- `ECARTS.md` : ecarts code vs livrable reel, avec statut (FIXED/FORK-ONLY/DOC).
- `tools/` : scripts internes (extraction DB, analyse livrable).
- `tests/test_livrable_reel.py` : 46 tests non-regression.

Lancer les tests :

```powershell
& "C:\Program Files\QGIS 4.0.0\bin\python-qgis.bat" -m pytest tests\test_livrable_reel.py -v
```

## Historique des versions

Voir `metadata.txt` section `changelog` pour l'historique complet.
Highlights recents :

- **3.2.0 (2026-04)** : Renommage ST-AIR, guide sous-traitant complet,
  corrections qgis_utils (46 tests).
- **3.1.0 (2026-03)** : Compatibilite QGIS 4.0 (Qt6/PyQt6).
- **3.0.0 (2026-03)** : Rapport unifie, schemas PCM, dictionnaire de donnees.
- **2.5.0 (2026-02)** : GraceTHD, detection projet, interface V2.

## Licence

Diffusion restreinte. Usage reserve aux sous-traitants autorises du
donneur d'ordre.
