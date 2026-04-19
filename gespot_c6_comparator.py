# -*- coding: utf-8 -*-
"""
Lecture des fichiers C6 Excel et comparaison champ par champ avec GESPOT.

Responsabilité unique : lire les colonnes utiles de l'onglet Export 1,
agréger la whitelist depuis l'onglet Bases, puis produire les statuts
de comparaison C1-C11 par appui.

Zéro dépendance QGIS - thread-safe.
"""

import os
import re as _re
import warnings
import xml.etree.ElementTree as _ET
import zipfile as _zipfile
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional, Tuple

import openpyxl

warnings.filterwarnings("ignore", category=UserWarning, module="openpyxl")
warnings.filterwarnings("ignore", category=ResourceWarning, module="openpyxl")

from .core_utils import is_plugin_output_file, normalize_appui_num
from .gespot_reader import GespotRecord, GespotLoadResult

_PLACEHOLDERS_NUM_VOIE = {'9999', '99999'}
_RE_LEADING_NUM = _re.compile(r'^(\d+)\s+')


# ===========================================================================
#  CONSTANTES - indices colonnes C6 (0-bases depuis le header ligne 8)
# ===========================================================================

_C6_HDR_ROW = 7        # header ligne 8 (0-base)
_C6_DATA_ROW = 8       # données à partir ligne 9 (0-base)
_C6_CENTRE_ROW = 2     # cellule E3 (0-base)
_C6_CENTRE_COL = 4     # colonne E (0-base)

_C6_COL_NUM = 0        # A - N° appui
_C6_COL_TYPE = 1       # B - Type d'appui
_C6_COL_ADRESSE = 2    # C - Adresse
_C6_COL_CTRL_VIS = 5   # F - Contrôle visuel
_C6_COL_VERTIC = 6     # G - Verticalité
_C6_COL_YELLOW = 12    # M - Absence étiquette jaune
_C6_COL_USABLE = 13    # N - Appui utilisable
_C6_COL_ENV = 14       # O - Milieu environnant
_C6_COL_ELEC = 15      # P - Environnement électrique
_C6_COL_STRAT = 16     # Q - Appui stratégique
_C6_COL_INACC = 17     # R - Appui inaccessible

_BASES_STRAT_COL = 13  # colonne M (1-base pour openpyxl)
_BASES_STRAT_ROW_START = 2  # ligne 2 (1-base)
_BASES_STRAT_EXCLUDED = {'non', 'appui stratégique', ''}

_COMPARISONS = [
    'adresse', 'centre', 'type', 'strategie', 'env',
    'elec', 'inacc', 'vertic', 'yellow', 'usable', 'ctrl_vis',
]


# ===========================================================================
#  DATACLASSES
# ===========================================================================

@dataclass
class C6Record:
    """Un appui C6 extrait de l'onglet Export 1."""
    num: str
    adresse: str
    centre: str
    type_c6: str
    ctrl_vis: str
    vertic: str
    yellow: str
    usable: str
    env: str
    elec: str
    strat: str
    inacc: str
    source_file: str


@dataclass
class ComparisonResult:
    """Résultat de comparaison pour un appui."""
    num: str

    voie_gespot: str
    num_voie_gespot: str
    adresse_c6: str
    statut_adresse: str
    detail_adresse: str

    centre_gespot: str
    centre_c6: str
    statut_centre: str
    detail_centre: str

    type_gespot: str
    type_c6: str
    statut_type: str
    detail_type: str

    strat_gespot: str
    strat_c6: str
    statut_strat: str
    detail_strat: str

    env_gespot: str
    env_c6: str
    statut_env: str
    detail_env: str

    elec_gespot: str
    elec_c6: str
    statut_elec: str
    detail_elec: str

    inacc_gespot: str
    inacc_c6: str
    statut_inacc: str
    detail_inacc: str

    recalage_gespot: str
    vertic_c6: str
    statut_vertic: str
    detail_vertic: str

    yellow_gespot: str
    yellow_c6: str
    statut_yellow: str
    detail_yellow: str

    usable_gespot: str
    usable_c6: str
    statut_usable: str
    detail_usable: str

    ctrl_vis_gespot: str
    ctrl_vis_c6: str
    statut_ctrl_vis: str
    detail_ctrl_vis: str

    dist_elec_gespot: str

    nb_ecarts: int
    statut_global: str

    source_gespot: str
    source_c6: str

    etude: str = ''


@dataclass
class C6LoadResult:
    """Résultat de chargement d'un répertoire C6."""
    records: Dict[str, C6Record] = field(default_factory=dict)
    whitelist: set = field(default_factory=set)
    anomalies: List[dict] = field(default_factory=list)
    files_read: int = 0
    files_skipped: int = 0


@dataclass
class GespotC6Result:
    """Résultat global de la comparaison GESPOT vs C6."""
    comparisons: List[ComparisonResult] = field(default_factory=list)
    absent_c6: List[GespotRecord] = field(default_factory=list)
    absent_gespot: List[C6Record] = field(default_factory=list)
    anomalies: List[dict] = field(default_factory=list)
    output_path: str = ''
    gespot_file_counts: Dict[str, int] = field(default_factory=dict)
    c6_files_read: int = 0
    c6_files_skipped: int = 0
    etude_map: Dict[str, str] = field(default_factory=dict)


# ===========================================================================
#  LECTURE C6
# ===========================================================================

def _cell_str(ws, row: int, col: int) -> str:
    val = ws.cell(row=row, column=col).value
    return str(val).strip() if val is not None else ''


def _safe_cell(row_tuple, idx: int) -> str:
    """Extrait une cellule d'un tuple openpyxl en toute securite."""
    if idx < len(row_tuple) and row_tuple[idx] is not None:
        return str(row_tuple[idx]).strip()
    return ''


def _read_whitelist(wb: openpyxl.Workbook) -> set:
    whitelist = set()
    if 'Bases' not in wb.sheetnames:
        return whitelist
    ws = wb['Bases']
    for row_idx in range(_BASES_STRAT_ROW_START, ws.max_row + 1):
        val = ws.cell(row=row_idx, column=_BASES_STRAT_COL).value
        if val is None:
            continue
        s = str(val).strip()
        if s.lower() not in _BASES_STRAT_EXCLUDED:
            whitelist.add(s)
    return whitelist


def _validate_xlsx_zip(filepath: str) -> Optional[str]:
    """Pre-valide la structure ZIP/XML d'un .xlsx avant openpyxl.

    Retourne un message d'erreur si invalide, None si OK.
    Objectif : intercepter les fichiers corrompus en Python pur
    avant que le parser C (lxml/libxml2) ne provoque un segfault.
    """
    try:
        with _zipfile.ZipFile(filepath, 'r') as zf:
            bad = zf.testzip()
            if bad:
                return f"Archive ZIP corrompue (entree: {bad})"
            if '[Content_Types].xml' not in zf.namelist():
                return "Manifest [Content_Types].xml absent"
            src = zf.read('[Content_Types].xml')
            _ET.fromstring(src)
    except _zipfile.BadZipFile:
        return "Fichier non reconnu comme archive ZIP/Excel valide"
    except _ET.ParseError as exc:
        return f"XML manifest corrompu: {exc}"
    except OSError as exc:
        return f"Erreur lecture: {exc}"
    return None


def _read_one_c6(filepath: str,
                 fname: str) -> Tuple[Optional[dict], set, List[dict]]:
    """Lit un fichier C6 Excel. Retourne (records_dict, whitelist, anomalies)."""
    anomalies = []

    zip_err = _validate_xlsx_zip(filepath)
    if zip_err:
        anomalies.append({
            'source': 'C6', 'fichier': fname, 'num': '',
            'type': 'FICHIER_IGNORE',
            'detail': (
                f"Le fichier C6 '{fname}' a echoue la pre-validation: {zip_err}"
            ),
            'action': (
                f"Ouvrir '{fname}' dans Excel pour verifier qu'il n'est pas "
                f"corrompu, puis relancer"
            ),
        })
        return None, set(), anomalies

    try:
        wb = openpyxl.load_workbook(filepath, data_only=True)
    except Exception as e:
        anomalies.append({
            'source': 'C6', 'fichier': fname, 'num': '',
            'type': 'FICHIER_IGNORE',
            'detail': (
                f"Le fichier C6 '{fname}' n'a pas pu etre lu comme fichier Excel .xlsx "
                f"({e})"
            ),
            'action': (
                f"Ouvrir '{fname}', verifier qu'il ne s'agit pas d'un fichier corrompu "
                f"ou d'un faux .xlsx, puis relancer"
            ),
        })
        return None, set(), anomalies

    try:
        if 'Export 1' not in wb.sheetnames:
            anomalies.append({
                'source': 'C6', 'fichier': fname, 'num': '',
                'type': 'FEUILLE_EXPORT_1_ABSENTE',
                'detail': (
                    f"Le fichier C6 '{fname}' ne contient pas l'onglet 'Export 1', "
                    f"qui est obligatoire pour lire les donnees appuis"
                ),
                'action': (
                    f"Verifier que '{fname}' est bien un export C6 complet contenant "
                    f"l'onglet 'Export 1', puis relancer"
                ),
            })
            return None, set(), anomalies

        ws = wb['Export 1']
        whitelist = _read_whitelist(wb)
        centre_raw = _cell_str(ws, _C6_CENTRE_ROW + 1, _C6_CENTRE_COL + 1)
        if not centre_raw:
            anomalies.append({
                'source': 'C6', 'fichier': fname, 'num': '',
                'type': 'CENTRE_C6_ABSENT',
                'detail': (
                    f"Le code centre est absent dans le fichier C6 '{fname}' : "
                    f"onglet 'Export 1', cellule E3 vide. Tous les appuis de ce fichier "
                    f"auront un centre C6 vide"
                ),
                'action': (
                    f"Ouvrir '{fname}', aller dans l'onglet 'Export 1', renseigner le code "
                    f"centre en cellule E3, enregistrer puis relancer le controle"
                ),
            })

        records = {}
        for row in ws.iter_rows(min_row=_C6_DATA_ROW + 1, values_only=True):
            if not row or row[_C6_COL_NUM] is None:
                continue
            num_raw = str(row[_C6_COL_NUM]).strip()
            num_key = normalize_appui_num(num_raw)
            if not num_key:
                continue

            records[num_key] = C6Record(
                num=num_key,
                adresse=_safe_cell(row, _C6_COL_ADRESSE),
                centre=centre_raw,
                type_c6=_safe_cell(row, _C6_COL_TYPE),
                ctrl_vis=_safe_cell(row, _C6_COL_CTRL_VIS),
                vertic=_safe_cell(row, _C6_COL_VERTIC),
                yellow=_safe_cell(row, _C6_COL_YELLOW),
                usable=_safe_cell(row, _C6_COL_USABLE),
                env=_safe_cell(row, _C6_COL_ENV),
                elec=_safe_cell(row, _C6_COL_ELEC),
                strat=_safe_cell(row, _C6_COL_STRAT),
                inacc=_safe_cell(row, _C6_COL_INACC),
                source_file=fname,
            )
        return records, whitelist, anomalies
    except Exception as e:
        anomalies.append({
            'source': 'C6', 'fichier': fname, 'num': '',
            'type': 'FICHIER_IGNORE',
            'detail': (
                f"Erreur lors de la lecture des donnees du fichier C6 '{fname}' : {e}"
            ),
            'action': (
                f"Ouvrir '{fname}' dans Excel, enregistrer sous un nouveau .xlsx, "
                f"puis relancer"
            ),
        })
        return None, set(), anomalies
    finally:
        wb.close()


def load_c6_dir(c6_dir: str) -> C6LoadResult:
    """Charge tous les fichiers C6 d'un répertoire (récursif)."""
    result = C6LoadResult()
    seen_nums: Dict[str, dict] = {}

    for subdir, _, files in os.walk(c6_dir):
        for fname in sorted(files):
            if not fname.lower().endswith('.xlsx') or '~$' in fname:
                continue
            if is_plugin_output_file(fname):
                continue
            path = os.path.join(subdir, fname)
            records, wl, anoms = _read_one_c6(path, fname)
            result.anomalies.extend(anoms)
            result.whitelist |= wl
            if records is None:
                result.files_skipped += 1
                continue
            result.files_read += 1
            for num_key, rec in records.items():
                if num_key in seen_nums:
                    _handle_c6_dup(num_key, rec, seen_nums[num_key],
                                   result.anomalies, result.records)
                else:
                    result.records[num_key] = rec
                    seen_nums[num_key] = {'rec': rec, 'payload': str(rec)}
    return result


def _handle_c6_dup(num: str, new_rec: C6Record,
                   existing: dict, anomalies: List[dict],
                   records: Dict[str, C6Record]) -> None:
    if str(new_rec) == existing['payload']:
        anomalies.append({
            'source': 'C6',
            'fichier': f"{existing['rec'].source_file} / {new_rec.source_file}",
            'num': num,
            'type': 'DOUBLON_IDENTIQUE',
            'detail': (
                f"L'appui {num} est present dans plusieurs fichiers C6 avec exactement "
                f"les memes valeurs. Une seule version a ete conservee automatiquement"
            ),
            'action': (
                "Aucune correction bloquante. Vous pouvez supprimer les doublons C6 si "
                "vous voulez nettoyer la source"
            ),
        })
    else:
        records.pop(num, None)
        anomalies.append({
            'source': 'C6',
            'fichier': f"{existing['rec'].source_file} / {new_rec.source_file}",
            'num': num,
            'type': 'DOUBLON_CONFLICTUEL',
            'detail': (
                f"L'appui {num} est present dans plusieurs fichiers C6 avec des valeurs "
                f"differentes. Le plugin ne peut pas choisir automatiquement la bonne version"
            ),
            'action': (
                f"Comparer les fichiers C6 cites pour l'appui {num}, garder une seule "
                f"version coherente, puis relancer"
            ),
        })


# ===========================================================================
#  COMPARAISON
# ===========================================================================

def _cmp(a: str, b: str, label_gespot: str = 'GESPOT',
         label_c6: str = 'C6') -> Tuple[str, str]:
    """Compare deux valeurs, retourne (statut OK/KO, detail explicatif)."""
    va, vb = a.strip(), b.strip()
    na, nb = va.lower(), vb.lower()
    if na == nb:
        return 'OK', ''
    if va and not vb:
        return 'KO', f"{label_gespot}='{va}' mais {label_c6} est vide"
    if not va and vb:
        return 'KO', f"{label_gespot} est vide mais {label_c6}='{vb}'"
    return 'KO', f"{label_gespot}='{va}' vs {label_c6}='{vb}'"


def _recalage_to_vertic(recalage_raw: str) -> str:
    """BR-06 : transforme RECALAGE brut en valeur attendue C6 col G."""
    return 'Non' if recalage_raw.strip().upper() == 'O' else 'Oui'


def _compare_adresse(gespot: GespotRecord,
                     c6_adresse: str) -> Tuple[str, str]:
    """Compare l'adresse GESPOT (voie + num_voie) avec l'adresse C6.

    Logique :
    1. Construire adresse_gespot = num_voie + voie si num_voie valide.
    2. Match exact (full) -> OK.
    3. Match voie seule vs C6 -> OK (info: num_voie absent du C6).
    4. Match voie GESPOT vs C6 sans son numero de tete -> OK.
    5. Sinon KO.
    """
    voie = gespot.voie.strip()
    num_voie = gespot.num_voie.strip()
    adr_c6 = c6_adresse.strip()

    has_num = bool(num_voie) and num_voie not in _PLACEHOLDERS_NUM_VOIE
    adresse_gespot = f"{num_voie} {voie}" if has_num else voie

    ag = adresse_gespot.lower()
    ac = adr_c6.lower()

    if ag == ac:
        return 'OK', ''
    if not ag and not ac:
        return 'OK', ''

    if has_num and voie.lower() == ac:
        return 'OK', ''

    if not has_num and voie:
        m = _RE_LEADING_NUM.match(adr_c6)
        if m and adr_c6[m.end():].strip().lower() == voie.lower():
            return 'OK', ''

    if adresse_gespot and not adr_c6:
        return 'KO', f"GESPOT='{adresse_gespot}' mais C6 vide"
    if not adresse_gespot and adr_c6:
        return 'KO', f"GESPOT vide mais C6='{adr_c6}'"
    return 'KO', f"GESPOT='{adresse_gespot}' vs C6='{adr_c6}'"


def _compare_one(gespot: GespotRecord, c6: C6Record) -> ComparisonResult:
    vertic_gespot = _recalage_to_vertic(gespot.recalage_raw)

    s_adr, d_adr = _compare_adresse(gespot, c6.adresse)
    s_ctr, d_ctr = _cmp(gespot.centre, c6.centre, 'Centre GESPOT', 'Centre C6')
    s_typ, d_typ = _cmp(gespot.type_calc, c6.type_c6, 'Type GESPOT', 'Type C6')
    s_str, d_str = _cmp(gespot.strategie_calc, c6.strat, 'Strategie GESPOT', 'Strategie C6')
    s_env, d_env = _cmp(gespot.milieu_calc, c6.env, 'Milieu GESPOT', 'Milieu C6')
    s_elc, d_elc = _cmp(gespot.pres_elect_calc, c6.elec, 'Risque elec GESPOT', 'Risque elec C6')
    s_ina, d_ina = _cmp(gespot.inacc_calc, c6.inacc, 'Inacc GESPOT', 'Inacc C6')
    s_vtc, d_vtc = _cmp(vertic_gespot, c6.vertic, 'Verticalite GESPOT', 'Verticalite C6')
    s_ylw, d_ylw = _cmp(gespot.etat_no_yellow, c6.yellow, 'Etiquette jaune GESPOT', 'Etiquette jaune C6')
    s_usb, d_usb = _cmp(gespot.etat_usable, c6.usable, 'Utilisable GESPOT', 'Utilisable C6')
    s_ctv, d_ctv = _cmp(gespot.ctrl_visuel, c6.ctrl_vis, 'Ctrl visuel GESPOT', 'Ctrl visuel C6')

    all_statuts = [s_adr, s_ctr, s_typ, s_str, s_env, s_elc, s_ina, s_vtc, s_ylw, s_usb, s_ctv]
    nb_ecarts = sum(1 for s in all_statuts if s == 'KO')

    return ComparisonResult(
        num=gespot.num,
        voie_gespot=gespot.voie, num_voie_gespot=gespot.num_voie,
        adresse_c6=c6.adresse, statut_adresse=s_adr, detail_adresse=d_adr,
        centre_gespot=gespot.centre, centre_c6=c6.centre, statut_centre=s_ctr, detail_centre=d_ctr,
        type_gespot=gespot.type_calc, type_c6=c6.type_c6, statut_type=s_typ, detail_type=d_typ,
        strat_gespot=gespot.strategie_calc, strat_c6=c6.strat, statut_strat=s_str, detail_strat=d_str,
        env_gespot=gespot.milieu_calc, env_c6=c6.env, statut_env=s_env, detail_env=d_env,
        elec_gespot=gespot.pres_elect_calc, elec_c6=c6.elec, statut_elec=s_elc, detail_elec=d_elc,
        inacc_gespot=gespot.inacc_calc, inacc_c6=c6.inacc, statut_inacc=s_ina, detail_inacc=d_ina,
        recalage_gespot=gespot.recalage_raw,
        vertic_c6=c6.vertic, statut_vertic=s_vtc, detail_vertic=d_vtc,
        yellow_gespot=gespot.etat_no_yellow, yellow_c6=c6.yellow, statut_yellow=s_ylw, detail_yellow=d_ylw,
        usable_gespot=gespot.etat_usable, usable_c6=c6.usable, statut_usable=s_usb, detail_usable=d_usb,
        ctrl_vis_gespot=gespot.ctrl_visuel, ctrl_vis_c6=c6.ctrl_vis, statut_ctrl_vis=s_ctv, detail_ctrl_vis=d_ctv,
        dist_elec_gespot=gespot.dist_elec,
        nb_ecarts=nb_ecarts,
        statut_global='OK' if nb_ecarts == 0 else 'KO',
        source_gespot=gespot.source_file,
        source_c6=c6.source_file,
    )


def compare(gespot_result: GespotLoadResult,
            c6_result: C6LoadResult,
            etude_map: Optional[Dict[str, str]] = None) -> GespotC6Result:
    """Effectue la jointure et la comparaison champ par champ.

    Args:
        etude_map: {normalized_inf_num: etude_name ou 'HORS ETUDE'}.
            Si fourni, chaque resultat de comparaison est annote avec le nom
            de l'etude ou 'HORS ETUDE'.
    """
    out = GespotC6Result()
    out.anomalies.extend(gespot_result.anomalies)
    out.anomalies.extend(c6_result.anomalies)
    if etude_map:
        out.etude_map = etude_map

    gespot_keys = set(gespot_result.records)
    c6_keys = set(c6_result.records)
    _emap = etude_map or {}

    for num in sorted(gespot_keys & c6_keys):
        cmp = _compare_one(gespot_result.records[num], c6_result.records[num])
        cmp.etude = _emap.get(num, '')
        out.comparisons.append(cmp)

    for num in sorted(gespot_keys - c6_keys):
        out.absent_c6.append(gespot_result.records[num])

    for num in sorted(c6_keys - gespot_keys):
        out.absent_gespot.append(c6_result.records[num])

    return out


def _append_global_diagnostics(result: GespotC6Result,
                               gespot_result: GespotLoadResult,
                               c6_result: C6LoadResult) -> None:
    """Ajoute un diagnostic global si aucune jointure exploitable n'existe."""
    gs_count = len(gespot_result.records)
    c6_count = len(c6_result.records)

    if gs_count == 0 and result.gespot_file_counts:
        result.anomalies.append({
            'source': 'GESPOT', 'fichier': '', 'num': '',
            'type': 'AUCUN_APPUI_GESPOT',
            'detail': (
                f"{len(result.gespot_file_counts)} fichier(s) CSV GESPOT ont ete lus, "
                "mais aucun appui exploitable n'a pu etre extrait"
            ),
            'action': (
                "Verifier que les CSV GESPOT contiennent bien des numeros d'appui dans "
                "le format attendu, puis relancer"
            ),
        })
        return

    if c6_count == 0 and c6_result.files_read:
        result.anomalies.append({
            'source': 'C6', 'fichier': '', 'num': '',
            'type': 'AUCUN_APPUI_C6',
            'detail': (
                f"{c6_result.files_read} fichier(s) Excel C6 ont ete lus, mais aucun "
                "appui exploitable n'a pu etre extrait"
            ),
            'action': (
                "Verifier que les exports C6 contiennent bien des numeros d'appui dans "
                "l'onglet 'Export 1', puis relancer"
            ),
        })
        return

    if result.comparisons or gs_count == 0 or c6_count == 0:
        return

    result.anomalies.append({
        'source': 'GESPOT/C6', 'fichier': '', 'num': '',
        'type': 'AUCUNE_JOINTURE_NUM',
        'detail': (
            f"Aucune correspondance de numero d'appui n'a ete trouvee entre les "
            f"{gs_count} appuis GESPOT et les {c6_count} appuis C6"
        ),
        'action': (
            "Ouvrir les feuilles ABSENT_C6 et ABSENT_GESPOT, comparer quelques NUM, "
            "verifier qu'il s'agit bien de la meme etude et que le format des numeros "
            "d'appui est coherent entre les deux sources"
        ),
    })


def _build_analyse_note(result: GespotC6Result) -> str:
    """Retourne une note visible a afficher en tete de l'onglet ANALYSE."""
    if result.comparisons:
        return ''

    gs_count = len(result.comparisons) + len(result.absent_c6)
    c6_count = len(result.comparisons) + len(result.absent_gespot)
    if gs_count and c6_count:
        return (
            "Aucune correspondance GESPOT/C6. Voir ABSENT_C6, ABSENT_GESPOT "
            "et ANOMALIES_SOURCE."
        )
    if gs_count == 0 and c6_count:
        return "Aucun appui GESPOT exploitable. Voir ANOMALIES_SOURCE."
    if c6_count == 0 and gs_count:
        return "Aucun appui C6 exploitable. Voir ANOMALIES_SOURCE."
    return "Aucune donnee exploitable. Voir ANOMALIES_SOURCE."


# ===========================================================================
#  EXPORT EXCEL - helpers
# ===========================================================================

def _fills():
    from openpyxl.styles import PatternFill
    return {
        'OK': PatternFill('solid', fgColor='C6EFCE'),
        'KO': PatternFill('solid', fgColor='FFC7CE'),
    }


def _hdr_fill():
    from openpyxl.styles import PatternFill
    return PatternFill('solid', fgColor='D9D9D9')


def _write_sheet_header(ws, headers: List[str]) -> None:
    from openpyxl.styles import Font
    hfill = _hdr_fill()
    hfont = Font(bold=True)
    for col_idx, h in enumerate(headers, 1):
        cell = ws.cell(row=1, column=col_idx, value=h)
        cell.fill = hfill
        cell.font = hfont


def _auto_widths(ws, max_width: int = 50) -> None:
    from openpyxl.utils import get_column_letter
    for col in ws.columns:
        width = max(
            (len(str(cell.value or '')) for cell in col),
            default=10,
        )
        ws.column_dimensions[get_column_letter(col[0].column)].width = min(
            width + 2, max_width
        )


def _write_absent_c6(wb: openpyxl.Workbook,
                     records: List[GespotRecord],
                     etude_map: Optional[Dict[str, str]] = None) -> None:
    ws = wb.create_sheet('ABSENT_C6')
    headers = ['NUM', 'ETUDE', 'VOIE', 'NUM_VOIE', 'CENTRE', 'TYPE_GESPOT',
               'STRAT_GESPOT', 'ENV_GESPOT', 'ELEC_GESPOT',
               'INACC_GESPOT', 'RECALAGE', 'DIST_ELEC', 'SOURCE_GESPOT']
    _write_sheet_header(ws, headers)
    _emap = etude_map or {}
    for r in records:
        etude = _emap.get(normalize_appui_num(r.num), '')
        ws.append([r.num, etude, r.voie, r.num_voie, r.centre, r.type_calc,
                   r.strategie_calc, r.milieu_calc, r.pres_elect_calc,
                   r.inacc_calc, r.recalage_raw, r.dist_elec, r.source_file])
    ws.freeze_panes = 'A2'
    _auto_widths(ws)


def _write_absent_gespot(wb: openpyxl.Workbook,
                         records: List[C6Record],
                         etude_map: Optional[Dict[str, str]] = None) -> None:
    ws = wb.create_sheet('ABSENT_GESPOT')
    headers = ['NUM', 'ETUDE', 'ADRESSE_C6', 'CENTRE_C6', 'TYPE_C6', 'STRAT_C6',
               'ENV_C6', 'ELEC_C6', 'INACC_C6', 'VERTIC_C6', 'SOURCE_C6']
    _write_sheet_header(ws, headers)
    _emap = etude_map or {}
    for r in records:
        etude = _emap.get(normalize_appui_num(r.num), '')
        ws.append([r.num, etude, r.adresse, r.centre, r.type_c6, r.strat,
                   r.env, r.elec, r.inacc, r.vertic, r.source_file])
    ws.freeze_panes = 'A2'
    _auto_widths(ws)


def _write_anomalies(wb: openpyxl.Workbook,
                     anomalies: List[dict]) -> None:
    ws = wb.create_sheet('ANOMALIES_SOURCE')
    headers = ['SOURCE', 'FICHIER_CONCERNE', 'APPUI_CONCERNE', 'CODE_ANOMALIE',
               'EXPLICATION', 'ACTION_A_REALISER']
    _write_sheet_header(ws, headers)
    for a in anomalies:
        ws.append([a.get('source', ''), a.get('fichier', ''),
                   a.get('num', ''), a.get('type', ''),
                   a.get('detail', ''), a.get('action', '')])
    ws.freeze_panes = 'A2'
    _auto_widths(ws)


# ===========================================================================
#  EXPORT EXCEL
# ===========================================================================

def export_to_excel(result: GespotC6Result, export_dir: str) -> str:
    """Génère le rapport GESPOT_C6_ANALYSE_{timestamp}.xlsx."""
    if not os.path.isdir(export_dir):
        os.makedirs(export_dir, exist_ok=True)

    ts = datetime.now().strftime('%Y%m%d_%H%M')
    filepath = os.path.join(export_dir, f'GESPOT_C6_ANALYSE_{ts}.xlsx')
    wb = openpyxl.Workbook()

    _emap = result.etude_map or None
    _write_analyse(wb, result.comparisons, _build_analyse_note(result))
    _write_absent_c6(wb, result.absent_c6, _emap)
    _write_absent_gespot(wb, result.absent_gespot, _emap)
    _write_anomalies(wb, result.anomalies)

    if 'Sheet' in wb.sheetnames:
        del wb['Sheet']
    try:
        wb.save(filepath)
    except (PermissionError, OSError) as exc:
        raise RuntimeError(
            f"Impossible d'ecrire le rapport: {filepath} "
            f"(fichier ouvert dans Excel ?) - {exc}"
        ) from exc
    finally:
        wb.close()
    result.output_path = filepath
    return filepath


def _write_analyse(wb: openpyxl.Workbook,
                   comparisons: List[ComparisonResult],
                   note: str = '') -> None:
    ws = wb.create_sheet('ANALYSE')
    has_etude = any(c.etude for c in comparisons)
    headers = [
        'NUM', 'VOIE', 'NUM_VOIE', 'ADRESSE_C6', 'STATUT_ADRESSE', 'DETAIL_ADRESSE',
        'CENTRE_GESPOT', 'CENTRE_C6', 'STATUT_CENTRE', 'DETAIL_CENTRE',
        'TYPE_GESPOT', 'TYPE_C6', 'STATUT_TYPE', 'DETAIL_TYPE',
        'STRAT_GESPOT', 'STRAT_C6', 'STATUT_STRAT', 'DETAIL_STRAT',
        'ENV_GESPOT', 'ENV_C6', 'STATUT_ENV', 'DETAIL_ENV',
        'ELEC_GESPOT', 'ELEC_C6', 'STATUT_ELEC', 'DETAIL_ELEC',
        'INACC_GESPOT', 'INACC_C6', 'STATUT_INACC', 'DETAIL_INACC',
        'RECALAGE', 'VERTIC_C6', 'STATUT_VERTIC', 'DETAIL_VERTIC',
        'YELLOW_GESPOT', 'YELLOW_C6', 'STATUT_YELLOW', 'DETAIL_YELLOW',
        'USABLE_GESPOT', 'USABLE_C6', 'STATUT_USABLE', 'DETAIL_USABLE',
        'CTRL_VIS_GESPOT', 'CTRL_VIS_C6', 'STATUT_CTRL_VIS', 'DETAIL_CTRL_VIS',
        'DIST_ELEC', 'NB_KO', 'STATUT_GLOBAL', 'SOURCE_GESPOT', 'SOURCE_C6',
    ]
    if has_etude:
        headers.insert(1, 'ETUDE')
    statut_cols = {i + 1 for i, h in enumerate(headers) if h.startswith('STATUT_')}

    from openpyxl.styles import Font, PatternFill as _PF
    hfill = _hdr_fill()
    hfont = Font(bold=True)
    nb_ko_col = headers.index('NB_KO') + 1
    statut_global_col = headers.index('STATUT_GLOBAL') + 1
    for col_idx, h in enumerate(headers, 1):
        cell = ws.cell(row=1, column=col_idx, value=h)
        cell.fill = hfill
        cell.font = hfont

    fills = _fills()
    zebra = _PF('solid', fgColor='F2F2F2')
    if note:
        note_cell = ws.cell(row=2, column=1, value=note)
        note_cell.font = Font(bold=True)
        note_cell.fill = _PF('solid', fgColor='FFF2CC')

    for row_idx, cmp in enumerate(comparisons, 2):
        values = [
            cmp.num,
        ]
        if has_etude:
            values.append(cmp.etude)
        values.extend([
            cmp.voie_gespot, cmp.num_voie_gespot,
            cmp.adresse_c6, cmp.statut_adresse, cmp.detail_adresse,
            cmp.centre_gespot, cmp.centre_c6, cmp.statut_centre, cmp.detail_centre,
            cmp.type_gespot, cmp.type_c6, cmp.statut_type, cmp.detail_type,
            cmp.strat_gespot, cmp.strat_c6, cmp.statut_strat, cmp.detail_strat,
            cmp.env_gespot, cmp.env_c6, cmp.statut_env, cmp.detail_env,
            cmp.elec_gespot, cmp.elec_c6, cmp.statut_elec, cmp.detail_elec,
            cmp.inacc_gespot, cmp.inacc_c6, cmp.statut_inacc, cmp.detail_inacc,
            cmp.recalage_gespot, cmp.vertic_c6, cmp.statut_vertic, cmp.detail_vertic,
            cmp.yellow_gespot, cmp.yellow_c6, cmp.statut_yellow, cmp.detail_yellow,
            cmp.usable_gespot, cmp.usable_c6, cmp.statut_usable, cmp.detail_usable,
            cmp.ctrl_vis_gespot, cmp.ctrl_vis_c6, cmp.statut_ctrl_vis, cmp.detail_ctrl_vis,
            cmp.dist_elec_gespot, cmp.nb_ecarts, cmp.statut_global,
            cmp.source_gespot, cmp.source_c6,
        ])
        bg = zebra if row_idx % 2 == 0 else None
        for col_idx, val in enumerate(values, 1):
            cell = ws.cell(row=row_idx, column=col_idx, value=val)
            if bg and col_idx not in statut_cols and col_idx != nb_ko_col and col_idx != statut_global_col:
                cell.fill = bg
        for col_idx in statut_cols | {statut_global_col}:
            cell = ws.cell(row=row_idx, column=col_idx)
            cell.fill = fills.get(str(cell.value), fills['KO'])
        nb_cell = ws.cell(row=row_idx, column=nb_ko_col)
        nb_cell.fill = fills['KO'] if cmp.nb_ecarts > 0 else fills['OK']

    nb_ok = sum(1 for c in comparisons if c.statut_global == 'OK')
    nb_ko = len(comparisons) - nb_ok
    summary_row = len(comparisons) + 3
    ws.cell(row=summary_row, column=1, value=f"Total: {len(comparisons)} appuis | {nb_ok} OK | {nb_ko} KO")
    ws.cell(row=summary_row, column=1).font = Font(bold=True)

    ws.freeze_panes = 'A2'
    _auto_widths(ws)


def run_comparison(gespot_dir: str, c6_dir: str,
                   export_dir: str,
                   progress_cb=None,
                   etude_map=None) -> GespotC6Result:
    """Orchestre lecture -> comparaison -> export.

    Args:
        gespot_dir: Chemin du dossier GESPOT/GSPOT.
        c6_dir:     Chemin du dossier C6 (= CAP FT).
        export_dir: Dossier de sortie.
        progress_cb: callable(pct, msg) optionnel.
        etude_map:  {normalized_inf_num: etude_name ou 'HORS ETUDE'} optionnel.

    Returns:
        GespotC6Result avec output_path renseigne.
    """
    def _prog(pct, msg=''):
        if progress_cb:
            progress_cb(pct, msg)

    _prog(5, 'Lecture C6...')
    c6_result = load_c6_dir(c6_dir)

    _prog(30, 'Lecture GESPOT...')
    from .gespot_reader import load_gespot_dir
    gespot_result = load_gespot_dir(gespot_dir, whitelist=c6_result.whitelist)

    _prog(60, 'Comparaison...')
    result = compare(gespot_result, c6_result, etude_map=etude_map)

    result.gespot_file_counts = gespot_result.file_counts
    result.c6_files_read = c6_result.files_read
    result.c6_files_skipped = c6_result.files_skipped

    _append_global_diagnostics(result, gespot_result, c6_result)

    _prog(85, 'Export Excel...')
    export_to_excel(result, export_dir)

    _prog(100, 'Termine.')
    return result
