# Guide sous-traitant - Plugin ST-AIR

Version 4.3.0 - Public cible : sous-traitants FTTH aerien.

Ce guide est **autonome**. Il ne necessite aucune connaissance prealable du
plugin, de QGIS avance ou du format des donnees. A la fin de la lecture,
vous savez installer le plugin, preparer votre dossier de projet, lancer
une analyse complete et lire le rapport.

---

## Sommaire

1. [Vue d'ensemble](#1-vue-densemble)
2. [Prerequis](#2-prerequis)
3. [Installation du plugin](#3-installation-du-plugin)
4. [Structure du dossier de projet](#4-structure-du-dossier-de-projet)
5. [Premiere analyse : pas a pas](#5-premiere-analyse--pas-a-pas)
6. [Les modules d'analyse](#6-les-modules-danalyse)
7. [Lire le rapport Excel](#7-lire-le-rapport-excel)
8. [Anomalies les plus frequentes](#8-anomalies-les-plus-frequentes)
9. [Modele de donnees attendu](#9-modele-de-donnees-attendu)
10. [Valeurs metier autorisees](#10-valeurs-metier-autorisees)
11. [Depannage](#11-depannage)
12. [FAQ](#12-faq)
13. [Glossaire](#13-glossaire)

---

## 1. Vue d'ensemble

### 1.1 A quoi sert le plugin

ST-AIR controle la **coherence** de votre livrable FTTH aerien **avant** envoi
au donneur d'ordre :

- Les poteaux declares dans vos fichiers Excel terrain (CAP_FT, COMAC, C6)
  existent-ils bien dans le shapefile ?
- Les cables declares dans COMAC correspondent-ils a ceux presents
  geometriquement sur les appuis ?
- Les portees saisies dans PCM sont-elles coherentes avec la distance
  geometrique entre deux appuis ?
- Les boitiers declares dans COMAC existent-ils bien dans le shapefile BPE ?
- Les exports GSPOT de terrain sont-ils conformes aux declarations C6 ?
- Les poteaux FT KO et BT KO sont-ils pret a etre remontes dans la base ?

### 1.2 Ce que vous obtenez

Un **rapport Excel unique** dans votre dossier de projet, nomme
`rapport_<SRO>_<date>.xlsx`, qui contient :

- Un **tableau de bord KPI** (1ere feuille).
- Une feuille par module active (detail ligne par ligne des anomalies).
- Un **glossaire** metier et un **dictionnaire de donnees**.
- Des **schemas PCM** (optionnel, case a cocher) pour visualiser la pose des
  cables sur chaque appui.

### 1.3 Ce dont le plugin n'a PAS besoin

- **Aucune connexion internet.**
- **Aucun serveur distant.** Tout fonctionne sur votre PC.
- **Aucun compte utilisateur.** Pas d'authentification, pas de cle API.
- **Aucune modification de votre livrable d'origine** : le rapport est un
  nouveau fichier dans votre dossier.

---

## 2. Prerequis

### 2.1 Logiciels

| Logiciel | Version minimale | Version recommandee |
|----------|------------------|---------------------|
| QGIS     | 3.28 LTR         | 3.44 ou 4.0         |
| Python   | Fourni par QGIS  | -                   |

QGIS est telechargeable gratuitement : <https://qgis.org/fr/site/forusers/download.html>.

Installez la version **LTR** (Long Term Release) pour stabilite maximale.

### 2.2 Materiel

- **RAM** : 8 Go minimum, 16 Go recommande pour les grands SRO.
- **Disque** : 500 Mo libres pour QGIS + 100 Mo par projet.
- **Ecran** : 1920x1080 recommande.

### 2.3 Competences requises

Vous devez savoir :

- Ouvrir QGIS, charger un shapefile dans un projet.
- Lancer un plugin depuis le menu **Extensions**.
- Ouvrir un fichier Excel.

Vous n'avez **pas** besoin de savoir :

- Ecrire du SQL, du Python.
- Configurer un serveur ou une base de donnees.

---

## 3. Installation du plugin

### 3.1 Installation depuis un fichier ZIP

1. Recuperez le fichier `ST-AIR-v4.1.0.zip` fourni par votre donneur d'ordre.
2. Ouvrez QGIS.
3. Menu **Extensions** -> **Installer/Gerer les extensions**.
4. Onglet **Installer depuis un ZIP**.
5. Cliquez sur **...**, selectionnez le ZIP.
6. Cliquez sur **Installer l'extension**.
7. Fermez la boite de dialogue.

### 3.2 Verification

Dans QGIS, vous devez voir :

- Un nouveau menu **ST-AIR** dans la barre d'extensions, ou
- Une icone ST-AIR dans la barre d'outils.

Cliquez dessus : la fenetre principale du plugin s'ouvre.

### 3.3 Mise a jour

Pour mettre a jour le plugin quand une nouvelle version est diffusee :

1. Menu **Extensions** -> **Installer/Gerer les extensions**.
2. Onglet **Installees** -> cherchez ST-AIR -> **Desinstaller**.
3. Refaites la procedure d'installation avec le nouveau ZIP.

---

## 4. Structure du dossier de projet

### 4.1 Convention de nommage du dossier

Le dossier projet doit etre nomme d'apres votre **SRO** avec des tirets
a la place des slashes.

| SRO metier         | Nom de dossier            |
|--------------------|---------------------------|
| 63041/B1I/PMZ/00003 | `63041-B1I-PMZ-00003`    |
| 12345/AAA/XXX/00001 | `12345-AAA-XXX-00001`    |

Si votre dossier ne respecte pas cette convention, vous pourrez saisir le
SRO manuellement dans l'interface.

### 4.2 Arborescence type

```
63041-B1I-PMZ-00003/
|
|-- BASE DE DONNEES/
|   |-- NGE/                          <-- shapefiles principaux
|   |   |-- infra_pt_pot.shp (+ .dbf .shx .prj .cpg)
|   |   |-- etude_cap_ft.shp
|   |   |-- etude_comac.shp
|   |   |-- cables.shp
|   |   |-- bpe.shp
|   |   |-- za_nro.shp
|   |   |-- ...
|   |-- GSPOT/                        <-- exports CSV terrain
|   |   |-- 20260403_GESPOT_63041.csv
|   |   |-- ...
|
|-- CAP FT/                           <-- Fiches appuis Orange
|   |-- CMD 1/
|   |   |-- FicheAppui_0747699.xlsx
|   |   |-- FicheAppui_0747700.xlsx
|   |-- CMD 2/...
|
|-- COMAC/                            <-- Fichiers terrain COMAC
|   |-- NGE-B1L-00003-PA-1_ExportComac.xlsx
|   |-- ...
|   |-- PCM/                          <-- fichiers XML de calcul PCM
|   |   |-- *.xml
|
|-- livrable_<SRO>.zip | .gpkg        <-- (optionnel) livrable GeoPackage
|                                         (cables, BPE, attaches) - remplace la BDD
|
|-- rapport_63041_B1I_PMZ_00003_<date>.xlsx   <-- genere par le plugin
```

### 4.3 Fichiers obligatoires

Pour lancer l'analyse **minimale** (CAP_FT + COMAC) il faut au minimum :

- `BASE DE DONNEES/NGE/infra_pt_pot.shp` (+ `.dbf`, `.shx`, `.prj`, `.cpg`)
- `BASE DE DONNEES/NGE/etude_cap_ft.shp` pour le module CAP_FT.
- `BASE DE DONNEES/NGE/etude_comac.shp` pour le module COMAC.
- Dossier `CAP FT/` avec les `FicheAppui_*.xlsx`.
- Dossier `COMAC/` avec au moins un `*ExportComac*.xlsx`.

### 4.4 CRS (systeme de coordonnees)

**Tous les shapefiles doivent etre en EPSG:2154 (Lambert-93).**

Si un shapefile est dans un autre CRS, le plugin le signale clairement et
vous demande de le reprojeter (menu QGIS **Vecteur** -> **Outils de gestion
de donnees** -> **Reprojeter une couche**).

---

## 5. Premiere analyse : pas a pas

### 5.1 Charger les couches dans QGIS

1. QGIS -> **Couche** -> **Ajouter une couche** -> **Ajouter une couche vecteur**.
2. Selectionnez `infra_pt_pot.shp`, cliquez **Ajouter**.
3. Repetez pour `etude_cap_ft.shp`, `etude_comac.shp`.

Astuce : vous pouvez glisser-deposer les fichiers `.shp` directement dans
la fenetre QGIS.

### 5.2 Ouvrir le plugin

Menu **ST-AIR** ou icone de la barre d'outils -> la fenetre principale
s'ouvre.

### 5.3 Selectionner le dossier projet

1. Cliquez sur **Parcourir** (ou sur le nom du projet courant s'il est
   deja detecte).
2. Selectionnez votre dossier `63041-B1I-PMZ-00003`.
3. Le plugin affiche une zone verte si la detection a reussi :
   - Nombre de fichiers CAP_FT, COMAC, C6 trouves.
   - SRO detecte depuis le nom du dossier.
4. Le SRO est affiche dans un champ editable : corrigez-le si necessaire.

### 5.4 Choisir les modules a executer

Chaque module est une case a cocher :

- Verification CAP_FT
- Verification COMAC
- GESPOT vs C6
- C6 vs BD
- Police C6
- C6-C3A-C7-BD

> Le module **MAJ BD** (preparation des mises a jour shapefile a partir
> d'un Excel `FT-BT KO`) n'est plus du perimetre sous-traitant : il est
> traite en amont par le pole aerien.

Le plugin desactive automatiquement les modules dont les fichiers requis
manquent. Passez la souris sur un module grise pour voir pourquoi.

### 5.5 Options

- **Schema poteau COMAC** : genere des planches graphiques (une par appui).
  Beaucoup plus lent, utile pour controle visuel.
- **Charger les couches dans le projet QGIS** : si coche, les shapefiles
  restent dans le projet apres analyse ; sinon ils sont retires.

### 5.6 Charger un livrable GeoPackage (optionnel)

Si le pole aerien fournit un livrable `.zip` ou `.gpkg` contenant les
cables, les BPE et les attaches, cliquez sur **Charger GPKG...** en
bas de la fenetre avant de demarrer l'analyse.

- Le plugin dezippe automatiquement, detecte le SRO, decoupe les cables
  si necessaire (`cables_decoupes`) et charge les couches dans le projet.
- Les modules **COMAC** et **Police C6** utiliseront ces donnees au lieu
  de la BDD (qui n'est plus accessible cote sous-traitant).
- Le log indique `[GPKG] Pret : SRO=... - N cables, M BPE, K attaches.`.

En cas d'erreur, un message explicite apparait dans le log (fichier
introuvable, livrable invalide, couche `cables` manquante).

### 5.7 Lancer

Cliquez sur le bouton vert **Demarrer l'analyse**.

La zone de log en bas affiche la progression par module.
L'interface reste utilisable (l'analyse tourne en tache de fond).

Duree typique :
- 700 appuis, 4 modules : **45 a 90 secondes**.
- 10 000 appuis : **2 a 5 minutes**.

### 5.7 Resultat

Un popup vous annonce la fin :

- `rapport_<SRO>_<date>.xlsx` genere dans le dossier du projet.
- Un resume des erreurs/avertissements s'affiche.

Cliquez **Ouvrir le rapport** pour lancer Excel directement.

---

## 6. Les modules d'analyse

### 6.1 Verification CAP_FT

**But** : verifier que chaque poteau FT declare dans les fiches d'appuis
Orange (CAP_FT) existe bien dans le shapefile, avec les bonnes coordonnees
et la bonne zone d'etude.

**Entree** :
- `infra_pt_pot.shp` filtre sur `inf_type = 'POT-FT'`.
- `etude_cap_ft.shp` (polygones des zones d'etude).
- Fichiers `CAP FT/**/FicheAppui_*.xlsx`.

**Sortie** :
- Feuille `CAPFT_ANALYSE` : une ligne par appui avec statut (`OK`, `ABSENT QGIS`, `ABSENT FICHES APPUIS`, `HORS PERIMETRE`).

**Statuts possibles** :
- `OK` : l'appui est dans le shapefile ET dans sa zone d'etude.
- `ABSENT QGIS` : declare dans CAP_FT, inexistant dans le shapefile.
- `ABSENT FICHES APPUIS` : present dans la zone d'etude QGIS mais absent des fiches CAP_FT.
- `HORS PERIMETRE` : present dans le shapefile du SRO mais en dehors des zones d'etude CAP_FT.

### 6.2 Verification COMAC

**But** : croise les fichiers COMAC terrain avec le shapefile des poteaux
et des cables pour verifier :

1. Les **appuis** declares existent.
2. Les **cables** declares passent bien par les appuis attendus
   (appariement geometrique).
3. Les **capacites fibre optique** saisies correspondent aux capacites
   reelles en BDD.
4. Les **boitiers** declares (colonne AR "fibre optique") correspondent
   aux `bpe.shp` aux coordonnees attendues.
5. Les **portees** PCM (distance entre 2 appuis dans le fichier calcul)
   sont coherentes avec la distance geometrique.

**Entree** :
- `infra_pt_pot.shp` (filtre `inf_type = 'POT-BT'`).
- `etude_comac.shp`.
- `cables.shp`, `bpe.shp`.
- Fichiers `COMAC/*ExportComac*.xlsx`.
- (Optionnel) fichiers PCM XML dans `COMAC/PCM/`.

**Sortie** :
- `COMAC_ANALYSE` : ligne par appui, statut et explication detaillee.
- `COMAC_SECURITE` : controles reglementaires et hauteurs NFC 11-201.
- `COMAC_CABLES` : anomalies cables (ecart BDD vs COMAC).
- `COMAC_PORTEES` et `PCM_SUPPORTS` : comparaison portees/supports PCM vs geometrie.
- `D_01` a `D_XX` (optionnel) : planches graphiques des schemas polaires.

### 6.3 GESPOT vs C6

**But** : verifier 11 criteres croises entre les exports CSV GSPOT
(terrain) et les Excel C6 (declaration Orange).

**Entree** :
- Tous les CSV dans `BASE DE DONNEES/GSPOT/*.csv`.
- Annexes C6 detectees dans `CAP FT/**/FTTH-NGE-ETUDE-*.xlsx`.

**Sortie** :
- Feuilles `GESPOT_ANALYSE`, `GESPOT_ABSENT_C6`, `GESPOT_ABSENT_GESPOT` et `GESPOT_ANOMALIES` avec le detail des 11 criteres croises
  (adresse, centre, type appui, strategie, environnement, electricite, inaccessibilite, etc.).

### 6.4 C6 vs BD

**But** : controler les annexes C6 Orange vs shapefile des poteaux.

**Entree** :
- `infra_pt_pot.shp`.
- Annexes C6 `FTTH-NGE-ETUDE-*.xlsx` dans `CAP FT/`.

**Sortie** :
- Feuille `C6BD_ANALYSE` avec le statut par appui.
- Seuls les cables en **gras** dans C6 sont compares (cables nouveaux
  a poser) ; les cables existants (non gras) sont ignores.

---

## 7. Lire le rapport Excel

### 7.1 Feuille TABLEAU DE BORD

Premiere feuille du rapport. Vue synthetique :

| Zone                  | Contenu                                        |
|-----------------------|------------------------------------------------|
| En-tete               | Nom SRO, date d'analyse, version plugin        |
| KPI par module        | 5 cartes avec pourcentage OK et nombre KO      |
| Graphique camembert   | Repartition OK/KO global                       |
| Liens vers les feuilles | Cliquable pour aller directement au detail  |

### 7.2 Feuilles detail

Pour chaque module : une ou plusieurs feuilles avec :

- Une ligne d'en-tete **fixee** (filtre auto active).
- Une colonne `statut` avec codes couleur :
  - **Vert** : OK.
  - **Orange** : avertissement (anomalie mineure).
  - **Rouge** : KO (a traiter obligatoirement).

### 7.3 Feuille GLOSSAIRE

Definit les termes metier utilises :
- SRO, CAP_FT, COMAC, C6, C3A, GSPOT.
- `inf_type`, `inf_propri`, `etat`, etc.

### 7.4 Feuille DICTIONNAIRE

Structure ISO/IEC 11179 : pour chaque champ du rapport, indique :
- Nom, type, longueur, description, exemple, obligatoire ou non.

### 7.5 Feuilles D_01 a D_XX (optionnel)

Planches graphiques COMAC, avec :
- Schema polaire de l'appui (azimuts cables, type armement).
- Tableau des cables passant par l'appui.

---

## 8. Anomalies les plus frequentes

### 8.1 "ABSENT QGIS" sur un appui

**Signification** : l'appui est declare dans votre Excel terrain mais
n'existe pas dans `infra_pt_pot.shp`.

**Causes probables** :
- L'appui a ete cree sur le terrain mais pas encore importe en base.
- Le numero d'appui (colonne `inf_num` dans le shapefile) a ete ecrit
  differemment (ex: avec ou sans prefixe `E`, avec ou sans `/commune`).
- Faute de frappe dans l'Excel.

**Action** : verifier le numero d'appui dans le shapefile QGIS (outil
**Identifier**) et corriger l'Excel **ou** declarer l'appui au chef de
projet.

### 8.2 "HORS ETUDE"

**Signification** : l'appui existe dans le shapefile mais sa position
geographique n'est pas contenue dans les zones d'etude
(`etude_cap_ft.shp` ou `etude_comac.shp`).

**Causes probables** :
- Polygone d'etude mal decoupe (manque un poteau en limite).
- Le poteau n'aurait pas du etre pris dans l'etude.

**Action** : ouvrir le shapefile `etude_*.shp`, verifier que la zone
couvre bien tous les appuis a traiter.

### 8.3 Ecart de capacite fibre

**Signification** : la capacite FO saisie dans COMAC (ex: 12) differe de
celle presente sur le cable dans `cables.shp` (ex: 24).

**Action** : verifier sur le terrain la capacite reelle puis corriger
la source fausse (Excel ou shapefile).

### 8.4 Portee PCM incoherente

**Signification** : la portee saisie dans PCM (ex: 85 m) differe de la
distance geometrique entre les 2 appuis (ex: 62 m) au-dela de la tolerance.

**Action** : verifier la distance sur terrain (le shapefile est plus
souvent le plus fiable, mais pas toujours).

### 8.5 Doublon de zone d'etude

**Signification** : deux polygones dans `etude_cap_ft.shp` portent le meme
nom. Le plugin ne peut pas distinguer les 2 groupes d'appuis.

**Action** : ouvrir le shapefile, renommer une des zones (champ
`nom_etude`).

### 8.6 CRS incoherent

Message type : `CRS incoherent: couche X=EPSG:2154 vs couche Y=EPSG:4326.
Reprojeter la couche en EPSG:2154 avant analyse.`

**Action** : menu QGIS **Vecteur** -> **Outils de gestion de donnees** ->
**Reprojeter une couche** -> CRS cible `EPSG:2154` -> sauver puis relancer.

---

## 9. Modele de donnees attendu

### 9.1 infra_pt_pot.shp

Shapefile des poteaux (geometrie Point EPSG:2154).

| Champ DBF    | Type    | Description                                 | Exemple          |
|--------------|---------|---------------------------------------------|------------------|
| `gid`        | int     | Identifiant unique                          | 12345            |
| `inf_num`    | string  | Numero de l'appui                           | E000757/63041    |
| `inf_type`   | string  | Type (POT-FT, POT-BT, POT-AC, POT-ENR)      | POT-FT           |
| `inf_propri` | string  | Proprietaire (ORANGE, ENEDIS, RAUV)         | ORANGE           |
| `inf_mat_re` | string  | Materiau remplacement (tronque en SHP)      | BS8              |
| `etat`       | string  | Etat (FT KO, BT KO, A RECALER, etc.)        | FT KO            |
| `dce`        | string  | DCE (O/N)                                   | O                |
| `commentair` | string  | Commentaires (tronque en SHP)               | PRIVE/AEROSOUT.. |
| `sro`        | string  | SRO de rattachement                         | 63041/B1I/PMZ/.. |
| `affectatio` | string  | Code affectation (1, 2, 3, ou combinaison)  | 1,2              |
| `noe_codext` | string  | Code externe notation                       | BT0393           |
| `etiquette_` | string  | Etiquette jaune (tronque)                   | J-XXX            |
| `etiquett_1` | string  | Etiquette orange (tronque)                  | O-YYY            |

**Attention : troncature DBF.** Le format DBF limite les noms de champs a
**10 caracteres**. Si vous recevez un export depuis une base de donnees
avec des noms plus longs, ils sont coupes. Exemple :
`commentaire` devient `commentair`, `inf_mat_replace` devient `inf_mat_re`.
Le plugin gere automatiquement les 2 variantes.

### 9.2 etude_cap_ft.shp / etude_comac.shp

Shapefile polygones (EPSG:2154).

| Champ        | Type    | Description                                 |
|--------------|---------|---------------------------------------------|
| `gid`        | int     | Identifiant                                  |
| `nom_etudes` | string  | Nom de l'etude (regroupe les appuis)         |
| `sro`        | string  | SRO                                          |
| `geometry`   | Polygon | Contour de la zone                           |

### 9.3 cables.shp

Shapefile lignes (EPSG:2154), representant les cables fibre.

| Champ        | Type    | Description                                 |
|--------------|---------|---------------------------------------------|
| `gid`        | int     | Identifiant                                  |
| `cb_code`    | string  | Code cable (L1092, ...)                      |
| `cb_capafo`  | int     | Capacite fibre (6, 12, 24, 36, 48, 72, 144)  |
| `cb_typelog` | string  | Type logique (DI, TRA, RAC)                  |
| `posemode`   | int     | Mode pose (0 souterrain, 1 aerien, 2 facade) |

### 9.4 bpe.shp

Shapefile points (EPSG:2154), boitiers de protection d'epissure.

| Champ        | Type    | Description                                 |
|--------------|---------|---------------------------------------------|
| `gid`        | int     | Identifiant                                  |
| `bp_code`    | string  | Code BPE                                     |
| `bp_type`    | string  | Type BPE                                     |

### 9.5 CSV GSPOT

Format **UTF-8 BOM**, delimiteur **`;`**, **68 colonnes**.

En-tete fournie dans le template GSPOT officiel. Colonnes cles :
- `inf_num` (numero appui)
- `X`, `Y` (Lambert-93)
- `HauteurPoteau`, `Force`
- `Commune` (code INSEE)
- `Etat`

---

## 10. Valeurs metier autorisees

Le plugin controle que les valeurs saisies appartiennent aux listes
suivantes.

### 10.1 `inf_type` (type de poteau)

| Code      | Signification                     |
|-----------|-----------------------------------|
| `POT-FT`  | Poteau France Telecom (Orange)    |
| `POT-BT`  | Poteau Basse Tension              |
| `POT-AC`  | Poteau a creer (en attente)       |
| `POT-ENR` | Poteau pour EnR                   |

### 10.2 `inf_propri` (proprietaire)

| Code     | Signification         |
|----------|-----------------------|
| `ORANGE` | Orange (ex-FT)        |
| `ENEDIS` | Enedis                |
| `RAUV`   | Reseau d'Auvergne     |

### 10.3 `etat`

| Code            | Signification                              |
|-----------------|--------------------------------------------|
| `FT KO`         | Poteau FT non conforme                     |
| `BT KO`         | Poteau BT non conforme                     |
| `A RECALER`     | A deplacer / recaler                       |
| `A REMPLACER`   | A remplacer                                |
| `A RENFORCER`   | A renforcer                                |
| `PORTEE MOLLE`  | Portee detendue                            |

### 10.4 `cb_typelog`

| Code | Signification        |
|------|----------------------|
| `DI` | Distribution         |
| `TRA` | Transport           |
| `RAC` | Raccordement        |

### 10.5 `posemode`

| Code | Signification        |
|------|----------------------|
| `0`  | Souterrain           |
| `1`  | Aerien               |
| `2`  | Facade               |
| `4`  | Indefini             |

### 10.6 `affectatio`

Chaine pouvant contenir plusieurs valeurs separees par virgule :
- `1` : affectation principale.
- `2` : affectation secondaire.
- `3` : affectation particuliere (poteaux exclus du controle).

Exemples : `"1"`, `"2"`, `"1,2"`, `"2,3"`.

### 10.7 `dce`

| Code | Signification |
|------|---------------|
| `O`  | Oui           |
| `N`  | Non           |

---

## 11. Depannage

### 11.1 Le plugin ne se lance pas

1. Menu **Extensions** -> **Installer/Gerer** -> verifier que ST-AIR est
   **coche**.
2. Redemarrez QGIS.
3. Menu **Extensions** -> **Journal des messages** -> onglet **ST-AIR** :
   regardez les erreurs.

### 11.2 Le bouton Demarrer est grise

Le bloc de detection affiche la raison. Causes fequentes :
- Aucun SRO saisi ou detecte.
- Aucun module coche.
- Couche `infra_pt_pot` manquante dans QGIS.

### 11.3 "Couche introuvable : infra_pt_pot"

Charger `infra_pt_pot.shp` dans QGIS (glisser-deposer ou menu
**Couche** -> **Ajouter**).

### 11.4 "CRS incoherent"

Une des couches n'est pas en EPSG:2154. Utilisez **Vecteur** -> **Outils
de gestion de donnees** -> **Reprojeter une couche**.

### 11.5 Le rapport ne se genere pas

- Verifiez que Excel n'a pas verrouille un fichier du meme nom deja ouvert.
- Verifiez les droits d'ecriture dans le dossier projet.
- Consultez le **Journal des messages** QGIS (panneau ST-AIR) : l'erreur
  detaillee y est ecrite.

### 11.6 L'analyse est tres lente

- Fermez les couches QGIS inutilisees.
- Desactivez l'option **Schema poteau COMAC** (tres lourde).
- Si le projet fait > 50 000 appuis : attendez la fin, le plugin ne
  plante pas, il traite par paquets.

### 11.7 Erreur "FicheAppui_0747699.xlsx n'est pas un fichier C6"

C'est **normal**. Les FicheAppui sont des fichiers CAP_FT, pas des
annexes C6. Le module C6_vs_BD les ignore automatiquement.

---

## 12. FAQ

### 12.1 Puis-je utiliser mes propres colonnes dans les shapefiles ?

Non. Le plugin attend les noms de champs standard decrits en section 9.
Si vous modifiez les noms, le plugin ne trouvera pas les donnees.

### 12.2 Puis-je analyser plusieurs SRO en meme temps ?

Non. Le plugin traite **un SRO par analyse**. Lancez une analyse par dossier.

### 12.3 Les modifications du rapport modifient-elles mes fichiers d'origine ?

**Non.** Le plugin est en lecture seule sur vos fichiers source. Il ecrit
uniquement le rapport Excel.

### 12.4 Le plugin fonctionne-t-il sur Mac / Linux ?

Oui, QGIS et le plugin sont multiplateformes. Les procedures sont
identiques, seuls les chemins changent.

### 12.5 Comment partager le rapport ?

Le rapport est un fichier Excel standard. Envoyez-le par email, GED ou
SharePoint comme n'importe quel fichier.

### 12.6 Le plugin envoie-t-il des donnees quelque part ?

**Non.** Aucune connexion reseau. Aucune telemetrie.

### 12.7 Puis-je personnaliser les criteres du rapport ?

Non en version 4.1.0. Les seuils metier (tolerances, listes de valeurs) sont
figes dans le code. Demandez au donneur d'ordre si vous pensez qu'un
seuil est mal calibre.

### 12.8 Les FicheAppui_*.xlsx sont ignorees par certains modules, pourquoi ?

Le plugin considere ces fichiers comme des **sources CAP_FT**, pas des
annexes C6 Orange. Donc le module C6_vs_BD ne les lit pas (par design).

---

## 13. Glossaire

| Terme    | Signification                                               |
|----------|-------------------------------------------------------------|
| SRO      | Sous-Repartiteur Optique (identifiant unique du deploiement) |
| SIRO     | Societe d'Implantation du Reseau Optique                    |
| CAP_FT   | Capacite France Telecom (etude Orange)                      |
| COMAC    | Commentaires Appuis Cables (fichier terrain NGE)            |
| C6       | Annexe contractuelle Orange, declarations cables a poser    |
| C3A      | Annexe contractuelle Orange, complement C6                  |
| C7       | Annexe contractuelle Orange, boitiers                       |
| GSPOT    | Export terrain CSV normalise                                |
| PCM      | Plan de Calcul Mecanique (calcul poteau + cables)           |
| BPE      | Boitier de Protection d'Epissure                            |
| BE       | Bureau d'Etudes                                             |
| DI       | Cable de Distribution                                       |
| TRA      | Cable de Transport                                          |
| RAC      | Cable de Raccordement                                       |
| FTTH     | Fiber To The Home                                           |
| EPSG:2154 | Code CRS Lambert-93 (France metropole)                     |
| DBF      | Format base associe au shapefile (10 chars max nom champ)   |
| CRS      | Systeme de reference des coordonnees                        |
| QGIS     | Logiciel SIG open source                                    |

---

## Support

Pour toute question **fonctionnelle** (metier, donnees a livrer) :
contactez votre chef de projet / donneur d'ordre.

Pour toute question **technique** (bug plugin, erreur qui bloque) :
- Capture d'ecran du message d'erreur.
- Extrait du **Journal des messages** QGIS (onglet ST-AIR).
- Version QGIS (menu **Aide** -> **A propos**).
- Version plugin (menu **Extensions** -> **Installees**).

Transmettez ces elements par email avec l'objet
`[ST-AIR] <SRO> - <resume de l'anomalie>`.

---

*Fin du guide.*
