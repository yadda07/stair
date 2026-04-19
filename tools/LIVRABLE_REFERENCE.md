# Reference livrable sous-traitant ST-AIR

Synthese factuelle du livrable
`C:\Users\yadda\Downloads\pol\63041-B1I-PMZ-00003`
(source pour calibrer les tests du plugin).

Inventaire global : **646 shapefiles, 822 xlsx, 4 csv, 1 txt, 11609 autres**
(PDF, JPG, PCM, DBF, PRJ, CPG, SHX, QGS, ZIP, QMD).

---

## 1. Arborescence physique

```
63041-B1I-PMZ-00003/
├── BASE DE DONNEES/
│   ├── FIBEES/                  <- export Orange brut (source)
│   │   ├── APPUIS.shp           (1072 pts, 85 champs)
│   │   └── LIAISONS AERIENNES.shp (1085 lines, 16 champs)
│   ├── GSPOT/                   <- CSV GSPOT par commune + fusion.csv
│   ├── NGE/                     <- SHP NGE (CORE DU LIVRABLE)
│   │   └── {attaches, bpe, cables, commentaire, infra_pt_autres,
│   │        infra_pt_chb, infra_pt_pot, rbal_auvergne, t_cheminement,
│   │        t_zpa, t_zpbo, za_nro, za_sro}.shp + 63041B1IPMZ00003.qgs
│   ├── SHP APT/
│   │   ├── SHAPES ETUDES/       (etude_cap_ft.shp, etude_comac.shp)
│   │   └── SHAPES TRAVAUX/      (TRAVAUX_SUPPORTS.shp EPSG:4326)
│   └── v2 nge/CDC-DTR_63S07_00109/  (version DTR en preparation)
├── CAP FT/
│   ├── CMD 1..4/FTTH-NGE-ETUDE-B1L-63041-<N>/  (FicheAppui_<id>.xlsx par appui)
│   └── PoliceC6_Export_<timestamp>.xlsx        (4 exports successifs)
├── COMAC/
│   ├── ANALYSE_COMAC.xlsx
│   └── Artere OK/NGE-B1L-00003-PA-<1..37>/
│       ├── NGE-...-PA-<N>_ExportComac.xlsx    (77x60, format COMAC officiel)
│       ├── NGE-...-PA-<N>.pcm                 (binaire pcm = projet Comac)
│       ├── NGE-...-PA-<N>_ExportPdf.pdf
│       ├── NGE-...-PA-<N>_Livrable.pdf
│       ├── NGE-...-PA-<N>_Photos.zip
│       └── NGE-...-PA-<N>_SIG/                (SupportsXXX.shp par artere)
├── NOTES - 63388-B1I-PMZ-00003.txt      (instructions techniques du chef d'equipe)
├── COMPTE_NGE.xlsx                      (fiche simple d'identification ville)
├── FT-BT KO B1L_00003.xlsx              (synthese anomalies : FT + BT)
├── comac_ecarts_..._<ts>.log            (sortie texte du module VERIF COMAC, 6 versions)
├── rapport_63041_..._<ts>.xlsx          (rapport multi-modules, 6 versions datees)
├── rapport_batch_<ts>.xlsx              (rapports batch, 5 versions)
└── ex/                                  (ancienne generation de rapports)
```

---

## 2. Schema NGE (SHP produits par le sous-traitant)

Tous en **EPSG:2154 (Lambert-93)**, DBF en UTF-8 (cpg = `UTF-8`), date_modif string ISO.
La cle metier est **`sro`** (ex `63041/B1I/PMZ/00003`), partout presente.
Noms DBF tronques a 10 chars (d'ou `commentair`, `inf_mat_re`, `etiquett_1`).
Dans toutes les tables : `exe` = Integer(1,0) (0/1, drapeau execution).

### Couches points

| couche              | n    | geom  | champs clefs                                                                                                      |
|---------------------|-----:|-------|-------------------------------------------------------------------------------------------------------------------|
| `infra_pt_pot`      | 1133 | Point | `gid, sro, inf_num, inf_mat, inf_type, inf_propri, noe_usage, action, noe_codext, isole, typ_po_mod, love_lng, bandeau_ve, etat, etiquette_, inf_mat_re, dce, affectatio, etiquett_1, etiquett_2, pot_codext, exe` |
| `infra_pt_chb`      |   53 | Point | `gid, sro, inf_num, inf_mat, inf_type, inf_propri, noe_usage, isole, noe_codext, love_lng, aiguillage, gc, correspond, sans_fond, projet, dce, affectatio, projet_tr, projet_di, exe` |
| `infra_pt_autres`   |    5 | Point | `gid, sro, inf_type=FAC, inf_num, inf_mat=FAC, inf_propri=TIERS, adr_num/voie/ville/cplt/insee/cp, noe_usage, inf_taille, noe_codext, isole, nommage_fi, projet, dce, affectatio, exe` |
| `bpe`               |  150 | Point | `gid, sro, noe_nbepis, noe_nblr, mat_ref, noe_type=PBO, mat_capa, noe_usage, inf_propri, noe_pose, noe_num, inf_num, noe_codext, cab_codext, lt_etage, lt_escal, st_bat, nb_reserve, PF99, PT99, CAB99, IDMET99, IDREG99, DCE, insee, dist_pm_pb, projet_tr, projet_di, exe` |
| `rbal_auvergne`     |  347 | Point | `gid, sro, nro, noe_nblr, nb_hab, nb_pro, nb_pr_ftth, nb_pr_ftte, ad_adrkey, ad_voiekey, adr_num/voie/cplt/insee/ville/cp, sf_etage, sf_escal, ad_nombat, ad_imneuf, ad_code, noe_codext, rac_dem, ad_gest, obs, noe_type=BAL, statut, noe_pose, hexacle_or, affectatio, nbre_bal_p, id_parcell, rto, dce, type_site, type_voie, trig_flag, phase, photo_face/bal/racc, classifica, dist_sro, dist_nro, isole, gc_isole, check_temp` |

### Couches lignes

| couche              | n    | geom       | champs clefs                                                                                                      |
|---------------------|-----:|------------|-------------------------------------------------------------------------------------------------------------------|
| `cables`            |  405 | LineString | `gid, sro, length, cab_type, cab_capa, cab_modulo, isole, cab_nature=AER, commentair, cb_etiquet, fon, DCE, dist_type, affectatio, projet_tr, projet_di, cab_codext, exe` |
| `attaches`          |   35 | LineString | `gid, sro, length, modif_par, date_modif, noe_usage, cb_etiquet, dce, exe`                                        |
| `t_cheminement`     | 1228 | LineString | `gid, sro, collecte, gc, proprio, noe_codext, cm_avct, cm_typelog, cm_typ_imp, cm_code, cm_ndcode1/2, cm_r1..r4_code, cm_statut, cm_etat, cm_compo, cm_mod_pos, cm_charge, cm_long, cm_lgreel, cm_creadat, cm_majdate, cm_abddate, cm_abdsrc, cm_codeext, isole, verif, pt_origine/destina, aiguillage, cm_gest_do, cm_prop_do, type, projet_col, projet_tr, projet_di, dce, affectatio, source, target, exe` |

### Couches zones

| couche              | n    | geom    | champs clefs                                                                                                       |
|---------------------|-----:|---------|--------------------------------------------------------------------------------------------------------------------|
| `commentaire`       |   88 | Polygon | `gid, sro, collecte, commentair, ad_commune, exe`                                                                 |
| `t_zpa`             |    7 | Polygon | `gid, sro, zp_r1..r3_code, nb_prises, id_metier, dce, exe`                                                        |
| `t_zpbo`            |  132 | Polygon | `gid, sro, zp_r1..r3_code, nb_prises, id_metier, dce, exe, zn_coext`                                              |
| `za_nro`            |    1 | Polygon | `gid, sro (peut contenir plusieurs SRO separes par `;`), nro, zn_r1/r2_code, zn_nroref, nb_el, affectatio, exe`   |
| `za_sro`            |    1 | Polygon | `gid, sro, nro, zs_r1..r3_code, zs_refpm, zs_capamax, zs_nblogmt, 'Distr > 4.', 'TR Dis > 1', commentair, exe`    |

### Valeurs metier observees

- `inf_type` : `POT-FT`, `POT-AC`, `POT-BT`, `CHB-FT`, `FAC`
- `inf_propri` : `ORANGE`, `RAUV`, `TIERS`, `ENEDIS`
- `noe_usage` : `DI`, `TR` (Transport), `RAC` (suppose)
- `inf_mat` (POT) : `BS8`, `BS7`, `BC7 ANC`, `L1T`, etc. (materiau)
- `noe_type` : `PBO`, `BAL`
- `noe_pose` : `POT-FT`, `AERIEN ENERGIE`
- `cab_type` : `RAC`, `TRA`, `CDI` (principal distribution)
- `cab_nature` : `AER`, (SOUT)
- `affectatio` : chaine composite type `"1,2"`, `"2,3"`, `"3"`, `"1"`, ou `"Non"` (pour rbal)
- `dce` : `"O"` ou `"N"` (string)
- `isole` : `"0"`/`"1"` (String 1) ou `0/1` (Integer) selon table

**Attention** : champs bizarres dans `za_sro` : `Distr > 4.`, `TR Dis > 1` (espaces et caracteres).

---

## 3. Schema SHAPES ETUDES (livrable synthese)

### `etude_cap_ft.shp` (20 polygones, 18 champs)
`gid, sro, nro, nom_etudes (ex "FTTH-NGE-ETUDE-B1L-63041-1"), reserve, val_chemin, val_nommag, val_boitie, val_capa_c, val_ras_po, val_faisab, validation, nbr_ft_cal, nbr_recala, nbr_rempla, nbr_ft_ko, date_modif, modif_par`

### `etude_comac.shp` (41 polygones, 24 champs)
`gid, sro, nro, nom_etudes (ex "NGE-B1L-00003-PA-1"), val_chemin, val_boitie, val_capa_c, nbr_bt_ko, nbr_bt_cal, entreprise, ref_eplan, etat_eplan, val_nommag, val_ras_po, val_faisab, validation, reserve, nbr_bt_adj, pourcentag, validati_1, date_valid, version_e_`

Les champs `val_*` portent probablement des statuts O/N/NA et sont le resultat du controle qualite par le sous-traitant.

---

## 4. CSV GSPOT (reference metier Orange)

4 fichiers : 3 par commune (BIOLLET, CHARENSAT, ST PRIEST DES CHAMPS) + `fusion.csv`.

- **Encoding** : `utf-8-sig` (BOM)
- **Delimiter** : `;`
- **68 colonnes** dont :
  `ID, NUM, CODE_UI, CODE_INSEE, COMMUNE, CODE_RIVOLI, VOIE, num_voie, CENTRE, ZONE, gps_lat, gps_lon, type, strategique, pres_elect, support_pc, prive, inacc, dsp, RECALAGE, desarme, CARAC1..5, ENV1..4, ...`

`NUM` = numero d'appui (ex `1016623`), joint sur `inf_num` NGE par prefixe.

---

## 5. Ce que produit le plugin ST-AIR actuel

Source : `rapport_63041_B1I_PMZ_00003_20260416_163031.xlsx` (dernier rapport le + complet, 161 KB).

### 5 modules identifies (ordre TABLEAU DE BORD)

| # | Module          | Source Excel          | Source BDD                | Feuilles produites                          |
|---|-----------------|-----------------------|---------------------------|---------------------------------------------|
| 1 | VERIF CAP_FT    | FicheAppui_<id>.xlsx  | `infra_pt_pot` + etude_cap_ft | `CAPFT_ANALYSE`                          |
| 2 | MAJ_BD          | `FT-BT KO ...xlsx`    | `infra_pt_pot`            | `MAJ_RESUME`, `MAJ_FT`, `MAJ_BT`, `MAJ_FT_INTROUV` |
| 3 | VERIF COMAC     | `NGE-...-PA-<N>_ExportComac.xlsx` + .pcm | `cables`, `infra_pt_pot`, `bpe` | `COMAC_ANALYSE`, `COMAC_SECURITE`, `COMAC_CABLES`, `COMAC_PORTEES`, `PCM_SUPPORTS` |
| 4 | C6 vs BD        | `FicheAppui_*.xlsx`   | `infra_pt_pot` + `bpe`    | `C6BD_ANALYSE`, `C6BD_VERIF_ETUDES`         |
| 5 | POLICE C6       | `PoliceC6_Export_*.xlsx` | `cables` + `bpe`       | `PLC6_RECAP`, `PLC6_DETAIL`                 |

Dernier rapport en date :
`SRO 63041/B1I/PMZ/00003 - NGE - 4 modules - VERIF CAP_FT: 694 OK/705 - VERIF COMAC: 209 OK/269 - C6 vs BD: 693 OK/712 - POLICE C6: 599 OK/717`

### Structure feuilles cle

**`COMAC_CABLES`** (comparaison cables dessines COMAC vs BDD) :
`APPUI, NOM ETUDE, NB COMAC, REFS COMAC, CAPAS COMAC, NB BDD, CAPAS BDD, STATUT, MESSAGE, BOITIER COMAC, BPE TYPE, BOITIER STATUT`
Ex : `E000851/63094, NGE-B1L-00003-PA-1, 1, L1092-11-P, 6, 1, 6, OK, , non`
Format compact capas : `6+24`, `24/36+6+24/36` (l'operateur `/` = "ou bien", `+` = "et / en parallele").

**`COMAC_PORTEES`** (verification portees PCM vs reference) :
`ETUDE, CABLE, CAPA FO, DEPART PCM, ARRIVEE PCM, PORTEE PCM (m), DEPART REF, ARRIVEE REF, PORTEE REF (m), ECART (m), ECART (%), STATUT, MESSAGE`

**`COMAC_SECURITE`** (verification hauteurs + portee max par type ligne) :
`FICHIER, POTEAU, PORTEE (m), CAPACITE FO, TYPE LIGNE, PORTEE MAX (m), DEPASSEMENT (m), HAUTEUR SOL (m), VERIF PORTEE, VERIF HAUTEUR`

**`PCM_SUPPORTS`** (projection appuis PCM sur BDD, methode de matching) :
`ETUDE, NOM PCM, INF_NUM BDD, NOE_CODEXT BDD, METHODE, TYPE PCM, TYPE BDD, TYPE OK, ECART COORD (m), ETAT PCM, ETAT BDD, STATUT`
Methode observee : `noe_codext` (match direct). Ecart coord < 5 m typique.
`TYPE PCM` vs `TYPE BDD` : `BE` (beton, code PCM) -> `POT-BT` (code BDD) = OK.

**`MAJ_FT`** (propositions de mise a jour `infra_pt_pot`) :
`gid, inf_num, action, etat, inf_mat_replace, Etiquette jaune, Zone privee, Transition aerosout, etiquette_jaune, zone_privee, transition_aerosout, etiquette_orange`
Actions observees : `IMPLANTATION`, `RECALAGE`.
Etats observes : `FT KO`, `A RECALER`.

**`PLC6_RECAP`** (recap par etude CAP FT) :
`ETUDE, APPUIS C6, OK, ECARTS, ABSENTS BDD, BOITIER ERR, % CONFORMITE`

**`GLOSSAIRE`** (61 termes) et **`DICTIONNAIRE`** (206 lignes ISO/IEC 11179) systematiquement ajoutes au rapport depuis la version `20260409_091609`.

### Logs texte `.log`

Format exemple `comac_ecarts_63041_B1I_PMZ_00003_20260416_163021.log` :

```
RAPPORT ECARTS CABLES COMAC vs BDD
SRO  : 63041/B1I/PMZ/00003
Total: 10 ecarts sur 209 appuis analyses
```

Par appui KO : statut, refs COMAC brutes, capacites possibles par ref,
diagnostic code BDD vs refs COMAC, flag `pattern systematique (N appuis)`.

Section finale **`PATTERNS SYSTEMATIQUES`** : si un meme `gid` BDD
apparait dans >= 3 appuis, conclusion typique :
`DIAGNOSTIC: capa BDD 24 INCOHERENTE avec code "63041/B1I/TR 25 0255"`
`=> Capacite saisie incorrectement en BDD pour GID 424352`.

Ce log est un produit DIFFERENT du rapport xlsx (sortie texte additionnelle
pour diagnostic rapide cote operateur).

---

## 6. Fichier sources d'entree par module

| entree                                               | format        | origine          |
|------------------------------------------------------|---------------|------------------|
| `FicheAppui_<id>.xlsx`                               | xlsx          | terrain CAP FT   |
| `NGE-...-PA-<N>_ExportComac.xlsx` + `.pcm`           | xlsx + binaire| logiciel COMAC   |
| `PoliceC6_Export_<ts>.xlsx`                          | xlsx          | export POLICE C6 |
| `FT-BT KO B1L_00003.xlsx`                            | xlsx          | synthese manuelle|
| CSV `GSPOT/*.csv`                                    | csv UTF-8-BOM | export Orange    |
| FIBEES `APPUIS.shp`, `LIAISONS AERIENNES.shp`        | shapefile     | extract Orange   |

Nom d'etude CAP FT : `FTTH-NGE-ETUDE-B1L-<INSEE>-<numero>`.
Nom d'etude COMAC : `NGE-<zone>-<numero>-PA-<numero_artere>`.

---

## 7. Points d'attention pour les tests ST-AIR

### a) Clefs de jointure
- `inf_num` est de forme `<num_appui>/<insee>` (ex `1016647/63094`).
- Le matching COMAC -> BDD se fait principalement par `noe_codext` (preferer) ou `inf_num`, parfois par proximite coord.
- `appui_key` = numero d'appui sans prefixe (ex `1016623`).

### b) Encodage
- SHP : UTF-8 (cpg=5 bytes contient `UTF-8`)
- CSV GSPOT : UTF-8 avec BOM, delimiteur `;`
- XLSX : openpyxl standard

### c) Formats composites a parser
- `CAPAS COMAC` : `6`, `6+24`, `24/36+6+24/36`. `+` = concatenation, `/` = alternatives possibles.
- `REFS COMAC` : `L1092-11-P`, `L1092-13-P; L1092-11-P` (separateur `;` + espace).
- `affectatio` NGE : `"1,2"`, `"2,3"`, `"Non"`.
- `sro` dans `za_nro` : `sro1;sro2;sro3`.

### d) Verifications metier a calibrer
- TYPE `POT-BT` BDD <-> `BE` (beton) PCM = OK. Matrice de correspondance a stabiliser.
- Portee max selon type ligne (ex L1092-11-P -> 81 m observe).
- Detection "pattern systematique" : groupBy(gid BDD) >= 3 appuis avec meme ecart.

### e) SRS
- Tout en EPSG:2154 sauf **`SHAPES TRAVAUX/TRAVAUX_SUPPORTS.shp`** en 4326 (a normaliser avant comparaison).

### f) Troncature DBF 10 chars
Attention aux champs renommes : `commentair`, `inf_mat_re`, `etiquette_`, `etiquett_1`, `etiquett_2`, `version_e_`, `validati_1`. Si le code ecrit ailleurs le nom long, il faudra aligner.

---

## 8. Jeu de test prioritaire pour le plugin

SRO utile pour rejouer un cycle complet :
**`63041/B1I/PMZ/00003`** (presence complete : NGE + CAP FT + COMAC + PoliceC6 + rapports multiples).

SRO dispo dans la BDD AUVERGNE pour tests de variete :
- `15244/MUK/PMZ/12123` : 20 appuis (edge case petit)
- `63309/QIV/PMZ/56990` : 261 appuis (moyen)
- `03144/QLD/PMZ/00006` : 2373 appuis (grand)

Reference sortie attendue : `rapport_63041_..._20260416_163031.xlsx`
(dernier rapport du livrable, 4 modules, 161 KB).
