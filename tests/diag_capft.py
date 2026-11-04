# Youcef ADDA | GEODECI
# FR : Plugins QGIS, solutions SIG et administration PostgreSQL/PostGIS ; audit, automatisation et accompagnement.
# EN : QGIS plugins, GIS solutions and PostgreSQL/PostGIS administration; audits, workflow automation and business support.
# AR : تطوير إضافات QGIS وحلول نظم المعلومات الجغرافية وإدارة PostgreSQL/PostGIS، والتدقيق والأتمتة ومرافقة المؤسسات.
# Site / Website / الموقع : https://geodeci.xyz/
# Contact professionnel / Business enquiries / للتواصل المهني : youcef.geodesien@gmail.com | contact@geodeci.xyz
# Détails / Details / التفاصيل : SIGNATURE.md
# ============================================================================
"""
Diagnostic chirurgical CAP FT.
Reproduit le flux EXACT du plugin et identifie le point de divergence.

Usage dans la console Python QGIS:
  from pathlib import Path; exec(Path(r'C:/Users/yadda/AppData/Roaming/QGIS/QGIS4/profiles/default/python/plugins/PoleAerien/tests/diag_capft.py').read_text())
"""
import os
import re
from qgis.core import QgsProject, NULL

from PoleAerien.core_utils import normalize_appui_num
from PoleAerien.CapFt import CapFt
from PoleAerien.qgis_utils import extraire_poteaux_etude, detect_etude_field
from PoleAerien.project_detector import detect_project

SEP = "-" * 70

# =====================================================================
# ETAPE 1 : COUCHES
# =====================================================================
print(f"\n{SEP}\n[1] COUCHES\n{SEP}")
_e = QgsProject.instance().mapLayersByName('etude_cap_ft')
_p = QgsProject.instance().mapLayersByName('infra_pt_pot')
if not _e or not _p:
    raise SystemExit(f"Couches manquantes: etude_cap_ft={len(_e)} infra_pt_pot={len(_p)}")
etude, pot = _e[0], _p[0]
col = detect_etude_field(etude, 'DIAG')
print(f"pot: {pot.name()} ({pot.featureCount()} features) src={pot.source()[:80]}...")
print(f"etude: {etude.name()} ({etude.featureCount()} features)")
print(f"colonne: {col!r}")
print(f"CRS: pot={pot.crs().authid()} etude={etude.crs().authid()}")
# Subset string (filtre provider)?
ss_pot = pot.subsetString()
ss_etu = etude.subsetString()
if ss_pot:
    print(f"  /!\\ pot subsetString: {ss_pot}")
if ss_etu:
    print(f"  /!\\ etude subsetString: {ss_etu}")

# =====================================================================
# ETAPE 2 : EXTRACTION SPATIALE (appel EXACT du plugin)
# =====================================================================
print(f"\n{SEP}\n[2] EXTRACTION SPATIALE (extraire_poteaux_etude)\n{SEP}")
doublons, hors_etude, dico_qgis, dico_prives, all_inf_nums, coords_qgis = \
    extraire_poteaux_etude(pot.name(), etude.name(), col, 'POT-FT', 'DIAG')

total_in = sum(len(v) for v in dico_qgis.values())
print(f"doublons etudes: {doublons}")
print(f"hors_etude: {len(hors_etude)}")
print(f"poteaux dans etudes: {total_in} ({len(dico_qgis)} etudes)")
print(f"all_inf_nums: {len(all_inf_nums)} cles normalisees")
if hors_etude:
    print(f"  5 premiers hors_etude bruts: {hors_etude[:5]}")
    norms_he = [normalize_appui_num(x, strip_e_prefix=True) for x in hors_etude[:5]]
    print(f"  5 premiers hors_etude norm:  {norms_he}")

# =====================================================================
# ETAPE 3 : INDEX QGIS (meme logique que traitementResultatFinauxCapFt)
# =====================================================================
print(f"\n{SEP}\n[3] INDEX QGIS\n{SEP}")
index_qgis = {}
for etude_name, poles in dico_qgis.items():
    for inf_num_ft in poles:
        cle = normalize_appui_num(inf_num_ft, strip_e_prefix=True)
        index_qgis.setdefault(cle, []).append((etude_name, inf_num_ft))

keys_idx = set(index_qgis.keys())
print(f"index_qgis: {len(index_qgis)} cles")

# Delta critique : cles dans all_inf_nums mais PAS dans index_qgis
delta = all_inf_nums - keys_idx
if delta:
    print(f"\n  *** {len(delta)} cles dans all_inf_nums MAIS PAS dans index_qgis ***")
    print(f"  => Ces poteaux POT-FT existent dans la couche mais hors de toute etude")
    print(f"  => C'est le RESERVOIR de HORS PERIMETRE potentiels")
    for d in sorted(delta)[:15]:
        print(f"     {d!r}")
    if len(delta) > 15:
        print(f"     ... +{len(delta) - 15}")
else:
    print(f"  OK: all_inf_nums == index_qgis (0 candidats HORS PERIMETRE)")

# =====================================================================
# ETAPE 4 : TROUVER LE REPERTOIRE CAP FT
# =====================================================================
print(f"\n{SEP}\n[4] REPERTOIRE CAP FT\n{SEP}")
capft_dir = None

# Methode A : project_detector depuis le projet QGIS home
home = QgsProject.instance().homePath()
if home and os.path.isdir(home):
    det = detect_project(home)
    if det.has_capft:
        capft_dir = det.capft_dir
        print(f"Detecte via project_detector: {capft_dir}")

# Methode B : chercher sous home
if not capft_dir and home and os.path.isdir(home):
    for entry in os.listdir(home):
        full = os.path.join(home, entry)
        if os.path.isdir(full):
            det2 = detect_project(full)
            if det2.has_capft:
                capft_dir = det2.capft_dir
                print(f"Detecte sous home/{entry}: {capft_dir}")
                break

# Methode C : recherche brute FicheAppui sous home (profondeur 5)
if not capft_dir and home and os.path.isdir(home):
    for root, dirs, files in os.walk(home):
        depth = root.replace(home, '').count(os.sep)
        if depth > 5:
            dirs.clear()
            continue
        if any('FicheAppui_' in f and f.endswith('.xlsx') for f in files):
            # Remonter de 2 niveaux (etude/CMD -> CAP FT)
            candidate = root
            for _ in range(2):
                parent = os.path.dirname(candidate)
                if parent != candidate:
                    candidate = parent
            capft_dir = candidate
            print(f"Detecte par recherche FicheAppui: {capft_dir}")
            break

if not capft_dir:
    print(f"NON TROUVE. home={home}")
    print("==> Modifier capft_dir dans le script et relancer")
    raise SystemExit("capft_dir introuvable")

# =====================================================================
# ETAPE 5 : LECTURE FICHIERS EXCEL (meme appel que plugin)
# =====================================================================
print(f"\n{SEP}\n[5] LECTURE EXCEL: {capft_dir}\n{SEP}")
cap = CapFt()
dico_excel = cap.LectureFichiersExcelsCap_ft(capft_dir)
total_fiches = sum(len(v) for v in dico_excel.values())
print(f"{len(dico_excel)} dossiers, {total_fiches} fiches FicheAppui")
for dir_name, files in sorted(dico_excel.items()):
    print(f"  {dir_name}: {len(files)} fiches")

# =====================================================================
# ETAPE 6 : MATCHING CHIRURGICAL
# =====================================================================
print(f"\n{SEP}\n[6] MATCHING CHIRURGICAL\n{SEP}")

n_match = 0
n_hp = 0
n_absent = 0
hp_details = []
absent_details = []
match_details = []

for excel_dir, files in dico_excel.items():
    for filename in files:
        raw_name = filename.replace("FicheAppui_", "").replace(".xlsx", "").strip()
        potSt = normalize_appui_num(raw_name, strip_e_prefix=True)

        in_idx = potSt in index_qgis
        in_all = potSt in all_inf_nums

        if in_idx:
            n_match += 1
            info = index_qgis[potSt][0]
            match_details.append((filename, potSt, info[0], excel_dir))
        elif in_all:
            n_hp += 1
            hp_details.append((filename, raw_name, potSt, excel_dir))
        else:
            n_absent += 1
            absent_details.append((filename, raw_name, potSt, excel_dir))

print(f"MATCH:           {n_match}")
print(f"HORS PERIMETRE:  {n_hp}")
print(f"ABSENT:          {n_absent}")

# ---- Detail HORS PERIMETRE ----
if hp_details:
    print(f"\n--- DETAIL HORS PERIMETRE ({n_hp}) ---")
    for fname, raw, norm, edir in hp_details[:25]:
        print(f"  {fname}")
        print(f"    raw={raw!r} -> norm={norm!r}")
        print(f"    in index_qgis=False  in all_inf_nums=True")
        print(f"    dossier Excel: {edir}")
        # Chercher cles proches dans index_qgis
        if norm.isdigit():
            n_int = int(norm)
            proches = sorted(
                [(k, v[0]) for k, v in index_qgis.items()
                 if k.isdigit() and abs(int(k) - n_int) <= 200],
                key=lambda x: abs(int(x[0]) - n_int)
            )[:3]
            if proches:
                print(f"    Cles proches index_qgis:")
                for pk, pv in proches:
                    print(f"      {pk!r} (etude={pv[0]}, raw={pv[1]})")
    if n_hp > 25:
        print(f"  ... +{n_hp - 25}")

# ---- Detail ABSENT ----
if absent_details:
    print(f"\n--- DETAIL ABSENT ({n_absent}) ---")
    for fname, raw, norm, edir in absent_details[:10]:
        print(f"  {fname} -> norm={norm!r} -> PAS dans all_inf_nums")
    if n_absent > 10:
        print(f"  ... +{n_absent - 10}")

# ---- Correspondances croisees etude QGIS vs dossier Excel ----
if match_details:
    print(f"\n--- VERIF CROISEE etude QGIS vs dossier Excel ---")
    mismatches = [(f, n, eq, ex) for f, n, eq, ex in match_details if eq != ex]
    if mismatches:
        print(f"  /!\\ {len(mismatches)} poteaux ou l'etude QGIS != dossier Excel parent:")
        for f, n, eq, ex in mismatches[:10]:
            print(f"    {f}: etude QGIS={eq!r} vs dossier Excel={ex!r}")
    else:
        print(f"  OK: {n_match} matchs, etude QGIS == dossier Excel pour tous")

# ---- Poteaux QGIS sans FicheAppui ----
excel_norms = set()
for files in dico_excel.values():
    for f in files:
        raw = f.replace("FicheAppui_", "").replace(".xlsx", "").strip()
        excel_norms.add(normalize_appui_num(raw, strip_e_prefix=True))

qgis_orphelins = keys_idx - excel_norms
if qgis_orphelins:
    print(f"\n--- {len(qgis_orphelins)} POTEAUX QGIS SANS FicheAppui ---")
    for k in sorted(qgis_orphelins)[:10]:
        print(f"  cle={k!r} etude={index_qgis[k][0][0]}")
    if len(qgis_orphelins) > 10:
        print(f"  ... +{len(qgis_orphelins) - 10}")

# =====================================================================
# ETAPE 7 : APPEL COMPLET traitementResultatFinauxCapFt (preuve finale)
# =====================================================================
print(f"\n{SEP}\n[7] APPEL REEL traitementResultatFinauxCapFt\n{SEP}")
import copy
dico_qgis_copy = copy.deepcopy(dico_qgis)
cap2 = CapFt()
resultats = cap2.traitementResultatFinauxCapFt(dico_qgis_copy, dico_excel, all_inf_nums)

r_absent_excel = resultats[0]  # introuvables Excel (ABSENT QGIS)
r_absent_qgis = resultats[1]   # introuvables QGIS (pas de FicheAppui)
r_ok = resultats[2]             # correspondances
r_hp = resultats[3]             # HORS PERIMETRE

nb_ok = len(r_ok)
nb_abs_e = sum(len(v) for v in r_absent_excel.values())
nb_abs_q = sum(len(v) for v in r_absent_qgis.values())
nb_hp_r = sum(len(v) for v in r_hp.values())

print(f"RESULTAT REEL DU PLUGIN:")
print(f"  OK:              {nb_ok}")
print(f"  ABSENT Excel:    {nb_abs_e}")
print(f"  ABSENT QGIS:     {nb_abs_q}")
print(f"  HORS PERIMETRE:  {nb_hp_r}")

if r_hp:
    print(f"\n  Detail HORS PERIMETRE reel:")
    for dir_name, fichiers in r_hp.items():
        print(f"    Dossier {dir_name}:")
        for f in fichiers[:10]:
            raw = f.replace("FicheAppui_", "").replace(".xlsx", "").strip()
            norm = normalize_appui_num(raw, strip_e_prefix=True)
            print(f"      {f} -> norm={norm!r}")
        if len(fichiers) > 10:
            print(f"      ... +{len(fichiers) - 10}")

if nb_hp_r == 0:
    print(f"\n  >>> AUCUN HORS PERIMETRE avec les donnees actuelles <<<")
    print(f"  >>> Si le plugin en a produit avant, les donnees BDD ont change depuis <<<")

print(f"\n{SEP}\n=== FIN DIAGNOSTIC ===\n{SEP}")
