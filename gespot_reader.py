# -*- coding: utf-8 -*-
# Youcef ADDA | GEODECI
# FR : Plugins QGIS, solutions SIG et administration PostgreSQL/PostGIS ; audit, automatisation et accompagnement.
# EN : QGIS plugins, GIS solutions and PostgreSQL/PostGIS administration; audits, workflow automation and business support.
# AR : تطوير إضافات QGIS وحلول نظم المعلومات الجغرافية وإدارة PostgreSQL/PostGIS، والتدقيق والأتمتة ومرافقة المؤسسات.
# Site / Website / الموقع : https://geodeci.xyz/
# Contact professionnel / Business enquiries / للتواصل المهني : youcef.geodesien@gmail.com | contact@geodeci.xyz
# Détails / Details / التفاصيل : SIGNATURE.md
# ============================================================================
"""
Lecture et normalisation des données GESPOT (CSV ou BDD/GPKG).

Responsabilité unique : parser les sources GESPOT et appliquer les règles
métier BR-01 à BR-06 (+ BR-03bis/ter, BR-04bis) pour produire des
enregistrements comparables aux champs C6.

Deux modes de lecture :
    - CSV positionnel  : load_gespot_dir() (historique, dossier de CSV)
    - Noms de colonnes : load_gespot_from_rows() (BDD, GPKG, dict nommés)

Zéro dépendance QGIS - thread-safe.
"""

import csv
import logging
import os
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set, Tuple

from .core_utils import normalize_appui_num

_logger = logging.getLogger(__name__)

_GESPOT_HEADER_MARKERS = {'NUM', 'CARAC1', 'CENTRE'}


# ===========================================================================
#  CONSTANTES - indices de colonnes CSV GESPOT (0-bases)
# ===========================================================================

_COL_NUM = 1
_COL_VOIE = 6
_COL_NUM_VOIE = 7
_COL_CENTRE = 8
_COL_TYPE = 12
_COL_STRATEGIQUE = 13
_COL_PRES_ELECT = 14
_COL_SUPPORT_PC = 15
_COL_INACC = 17
_COL_RECALAGE = 19
_COL_CARAC = [21, 22, 23, 24, 25]
_COL_ENV = [26, 27, 28, 29, 30]
_COL_ETAT_PT1 = 33
_COL_DECLASS_PT1 = 34
_COL_ETAT_PT2 = 43
_COL_DECLASS_PT2 = 44
_COL_ETAT_PT3 = 53
_COL_DECLASS_PT3 = 54
_COL_DIST_ELEC = 67

_CODES_DECLASSE = {'00', '01'}
_CODES_UNUSABLE = {'00', '01', '05'}
_CODES_INACC_ENV = {'IN8', 'IN9'}


# ===========================================================================
#  DATACLASSES
# ===========================================================================

@dataclass
class GespotRecord:
    """Un appui GESPOT après normalisation."""
    num: str
    voie: str
    num_voie: str
    centre: str
    dist_elec: str
    source_file: str

    type_calc: str
    strategie_calc: str
    milieu_calc: str
    pres_elect_calc: str
    inacc_calc: str
    recalage_raw: str
    etat_no_yellow: str
    etat_usable: str
    ctrl_visuel: str


@dataclass
class GespotLoadResult:
    """Résultat de chargement d'un répertoire GESPOT."""
    records: Dict[str, GespotRecord] = field(default_factory=dict)
    anomalies: List[dict] = field(default_factory=list)
    file_counts: Dict[str, int] = field(default_factory=dict)


# ===========================================================================
#  RÈGLES MÉTIER
# ===========================================================================

def _normalize_oui_non(value: str) -> str:
    """Normalise une valeur binaire Oui/Non en tenant compte des variantes NULL.

    Toute valeur représentant une absence (None, '', 'Non', 'NON', 'N', 'NULL',
    'null') est normalisée en 'Non'. Sinon, la valeur est retournée telle quelle
    (typiquement 'O' ou 'Oui' pour les cas positifs).

    Args:
        value: La valeur brute à normaliser.

    Returns:
        'Non' si la valeur représente une absence, sinon la valeur originale.
    """
    if value is None:
        return 'Non'
    v = str(value).strip().upper()
    if not v or v in {'NON', 'N', 'NULL'}:
        return 'Non'
    return str(value).strip()


def _normalize_to_empty(value: str) -> str:
    """Normalise une valeur texte en remplaçant les variantes NULL par vide.

    Toute valeur représentant une absence (None, '', 'Non', 'NON', 'N', 'NULL',
    'null') est normalisée en ''.

    Args:
        value: La valeur brute à normaliser.

    Returns:
        '' si la valeur représente une absence, sinon la valeur originale.
    """
    if value is None:
        return ''
    v = str(value).strip().upper()
    if not v or v in {'NON', 'N', 'NULL'}:
        return ''
    return str(value).strip()


def _clean_code(value: str) -> str:
    """Retourne le code nettoye, '' pour None, vide ou NULL."""
    if value is None:
        return ''
    v = str(value).strip()
    return '' if v.upper() == 'NULL' else v


def _br01_calc_type(type_raw: str, caracs: List[str]) -> str:
    """BR-01 : Calcule le type d'appui final depuis type + CARAC1-5."""
    base = type_raw.strip()
    if not base:
        return ''
    has_anc = any(c.strip().upper() == 'ANC' for c in caracs)
    is_mc = base.upper().startswith('MC')
    if is_mc and has_anc:
        return f'{base} ANC MIN'
    if is_mc:
        return f'{base} MIN'
    if has_anc:
        return f'{base} ANC'
    return base


def _br02_calc_strategie(strategique: str, support_pc: str,
                         caracs: List[str], whitelist_upper: Set[str]) -> str:
    """BR-02 : Calcule la stratégie depuis strategique + support_pc + CARAC1-5."""
    if strategique.strip().upper() != 'O':
        return 'Non'
    sp = _normalize_to_empty(support_pc)
    if sp:
        return sp
    for carac in caracs:
        v = carac.strip()
        if v and v.upper() != 'ANC' and v.upper() in whitelist_upper:
            return v
    return 'Non'


def _br03_calc_env(envs: List[str]) -> Tuple[str, str]:
    """BR-03 : Extrait milieu environnant et code inaccessibilité ENV."""
    inacc_env = ''
    milieu = ''
    for v in envs:
        clean = _clean_code(v)
        code = clean.upper()
        if code in _CODES_INACC_ENV:
            inacc_env = inacc_env or code
        elif code and not milieu:
            milieu = clean
    return milieu, inacc_env


def _br04_calc_etat(etat_pts: List[Tuple[str, str]]) -> Tuple[str, str, str]:
    """BR-04 : Détermine etat_no_yellow, etat_usable, ctrl_visuel."""
    no_yellow = 'Oui'
    usable = 'Oui'
    ctrl = 'Oui'
    for etat, declass in etat_pts:
        e_code = _clean_code(etat)
        d_code = _clean_code(declass)
        if e_code in _CODES_DECLASSE:
            no_yellow = 'Non'
            usable = 'Non'
        elif e_code in _CODES_UNUSABLE:
            usable = 'Non'
        if d_code and ctrl == 'Oui':
            ctrl = d_code
    return no_yellow, usable, ctrl


def _br04bis_calc_pres_elect(pres_elect_raw: str) -> str:
    """BR-04 bis : Normalise pres_elect (vide/NULL/Non → 'Non')."""
    return _normalize_oui_non(pres_elect_raw)


def _br05_calc_inacc(inacc_raw: str, inacc_env: str) -> str:
    """BR-05 : Code C6 d'inaccessibilité (IN8/IN9 env, INV, Non)."""
    # Code env prioritaire
    if inacc_env:
        return inacc_env
    # Inaccessibilite vehicule
    if _normalize_oui_non(inacc_raw) != 'Non':
        return 'INV'
    return 'Non'


def _br06_calc_recalage(recalage_raw: str) -> str:
    """BR-06 : Inversion RECALAGE → verticalité attendue C6."""
    if _normalize_oui_non(recalage_raw) != 'Non':
        return 'Non'
    return 'Oui'


# ===========================================================================
#  PARSING CSV
# ===========================================================================

def _row_to_cols(row: List[str], indices: List[int]) -> List[str]:
    return [row[i] if i < len(row) else '' for i in indices]


def _parse_one_csv(filepath: str) -> Tuple[List[List[str]], str]:
    """Lit un fichier CSV GESPOT, retourne (rows, encoding_used).

    Raises:
        ValueError: si décodage impossible ou header invalide.
    """
    for enc in ('utf-8-sig', 'latin-1'):
        try:
            with open(filepath, encoding=enc, newline='') as f:
                reader = csv.reader(f, delimiter=';')
                header = next(reader, None)
                if not _validate_header(header):
                    raise ValueError(
                        f"Header CSV invalide (colonnes attendues: {_GESPOT_HEADER_MARKERS})"
                    )
                rows = list(reader)
            return rows, enc
        except UnicodeDecodeError:
            continue
    raise ValueError(f"Impossible de decoder {filepath}")


def _validate_header(header: Optional[List[str]]) -> bool:
    """Vérifie que le header contient les colonnes marqueurs GESPOT."""
    if not header:
        return False
    upper_cols = {c.strip().upper() for c in header}
    return _GESPOT_HEADER_MARKERS.issubset(upper_cols)


def _build_record(row: List[str], source: str,
                  whitelist_upper: set) -> GespotRecord:
    """Construit un GespotRecord depuis une ligne CSV brute."""
    def col(i):
        return row[i].strip() if i < len(row) else ''

    caracs = [col(i) for i in _COL_CARAC]
    envs = [col(i) for i in _COL_ENV]
    etat_pts = [
        (col(_COL_ETAT_PT1), col(_COL_DECLASS_PT1)),
        (col(_COL_ETAT_PT2), col(_COL_DECLASS_PT2)),
        (col(_COL_ETAT_PT3), col(_COL_DECLASS_PT3)),
    ]

    milieu, inacc_env = _br03_calc_env(envs)
    etat_no_yellow, etat_usable, ctrl_visuel = _br04_calc_etat(etat_pts)
    num_voie_raw = col(_COL_NUM_VOIE)

    return GespotRecord(
        num=col(_COL_NUM),
        voie=col(_COL_VOIE),
        num_voie=num_voie_raw,
        centre=col(_COL_CENTRE),
        dist_elec=col(_COL_DIST_ELEC),
        source_file=source,
        type_calc=_br01_calc_type(col(_COL_TYPE), caracs),
        strategie_calc=_br02_calc_strategie(
            col(_COL_STRATEGIQUE), col(_COL_SUPPORT_PC), caracs, whitelist_upper),
        milieu_calc=milieu,
        pres_elect_calc=_br04bis_calc_pres_elect(col(_COL_PRES_ELECT)),
        inacc_calc=_br05_calc_inacc(col(_COL_INACC), inacc_env),
        recalage_raw=col(_COL_RECALAGE),
        etat_no_yellow=etat_no_yellow,
        etat_usable=etat_usable,
        ctrl_visuel=ctrl_visuel,
    )


# ===========================================================================
#  LECTURE PAR NOMS DE COLONNES (BDD / GPKG)
# ===========================================================================

_NAMED_CARAC_KEYS = ('carac1', 'carac2', 'carac3', 'carac4', 'carac5')
_NAMED_ENV_KEYS = ('env1', 'env2', 'env3', 'env4', 'env5')
_NAMED_ETAT_KEYS = (
    ('etat_pt1', 'declass_pt1'),
    ('etat_pt2', 'declass_pt2'),
    ('etat_pt3', 'declass_pt3'),
)


def _attr_str(attrs: Dict[str, Any], key: str) -> str:
    """Extrait une valeur d'un dict d'attributs, normalise en str.strip()."""
    return _clean_code(attrs.get(key))


def _build_record_from_dict(
    attrs: Dict[str, Any],
    source: str,
    whitelist_upper: Set[str],
) -> GespotRecord:
    """Construit un GespotRecord depuis un dict d'attributs nommés.

    Mapping entre colonnes BDD/GPKG et GespotRecord :
        num, voie, num_voie, centre, distance_electrique, source_file,
        type, strategique, pres_elect, support_pc, inacc, recalage,
        carac1-5, env1-5, etat_pt1-3 + declass_pt1-3

    Réutilise les mêmes règles métier BR-01 à BR-06 que _build_record().
    """
    caracs = [_attr_str(attrs, k) for k in _NAMED_CARAC_KEYS]
    envs = [_attr_str(attrs, k) for k in _NAMED_ENV_KEYS]
    etat_pts = [
        (_attr_str(attrs, ek), _attr_str(attrs, dk))
        for ek, dk in _NAMED_ETAT_KEYS
    ]

    milieu, inacc_env = _br03_calc_env(envs)
    etat_no_yellow, etat_usable, ctrl_visuel = _br04_calc_etat(etat_pts)

    return GespotRecord(
        num=_attr_str(attrs, 'num'),
        voie=_attr_str(attrs, 'voie'),
        num_voie=_attr_str(attrs, 'num_voie'),
        centre=_attr_str(attrs, 'centre'),
        dist_elec=_attr_str(attrs, 'distance_electrique'),
        source_file=_attr_str(attrs, 'source_file') or source,
        type_calc=_br01_calc_type(_attr_str(attrs, 'type'), caracs),
        strategie_calc=_br02_calc_strategie(
            _attr_str(attrs, 'strategique'),
            _attr_str(attrs, 'support_pc'),
            caracs, whitelist_upper),
        milieu_calc=milieu,
        pres_elect_calc=_br04bis_calc_pres_elect(
            _attr_str(attrs, 'pres_elect')),
        inacc_calc=_br05_calc_inacc(
            _attr_str(attrs, 'inacc'), inacc_env),
        recalage_raw=_attr_str(attrs, 'recalage'),
        etat_no_yellow=etat_no_yellow,
        etat_usable=etat_usable,
        ctrl_visuel=ctrl_visuel,
    )


def load_gespot_from_rows(
    rows: List[Dict[str, Any]],
    source_label: str = '',
    whitelist: Optional[set] = None,
) -> GespotLoadResult:
    """Charge des enregistrements GESPOT depuis une liste de dicts nommés.

    Source typique : couche QGIS (data_gespot), requête PostgreSQL, ou
    tout autre producteur de dicts avec clés nommées (num, voie, centre,
    type, carac1-5, env1-5, etat_pt1-3, declass_pt1-3, ...).

    Args:
        rows:         Liste de dicts d'attributs (clés = noms de colonnes).
        source_label: Label de source pour les anomalies (ex: 'data_gespot').
        whitelist:    Ensemble de codes stratégie valides (depuis Bases col M).

    Returns:
        GespotLoadResult avec records indexés par NUM, anomalies, compteurs.
    """
    t0 = time.monotonic()
    if whitelist is None:
        whitelist = set()
    whitelist_upper = {v.upper() for v in whitelist}

    result = GespotLoadResult()
    raw_by_num: Dict[str, List[dict]] = {}

    for idx, attrs in enumerate(rows):
        num_raw = _attr_str(attrs, 'num')
        if not num_raw:
            continue
        num_key = normalize_appui_num(num_raw)
        if not num_key:
            continue
        raw_by_num.setdefault(num_key, []).append({
            'attrs': attrs, 'source': source_label, 'idx': idx,
        })

    _resolve_duplicates_from_dict(raw_by_num, result, whitelist_upper)

    elapsed_ms = int((time.monotonic() - t0) * 1000)
    n_items = len(result.records)
    _logger.debug(
        "load_gespot_from_rows source=%s n_rows=%d n_items=%d "
        "n_anomalies=%d elapsed_ms=%d",
        source_label, len(rows), n_items, len(result.anomalies), elapsed_ms,
    )
    if source_label:
        result.file_counts[source_label] = n_items
    return result


def _resolve_duplicates_from_dict(
    raw_by_num: Dict[str, List[dict]],
    result: GespotLoadResult,
    whitelist_upper: Set[str],
) -> None:
    """Résout les doublons NUM pour les dicts nommés et alimente records."""
    for num, entries in raw_by_num.items():
        if len(entries) == 1:
            rec = _build_record_from_dict(
                entries[0]['attrs'], entries[0]['source'], whitelist_upper)
            result.records[num] = rec
            continue

        payloads = [
            tuple(sorted(e['attrs'].items()))
            for e in entries
        ]
        if len(set(payloads)) == 1:
            rec = _build_record_from_dict(
                entries[0]['attrs'], entries[0]['source'], whitelist_upper)
            result.records[num] = rec
            result.anomalies.append({
                'source': 'GESPOT',
                'fichier': entries[0]['source'], 'num': num,
                'type': 'DOUBLON_IDENTIQUE',
                'detail': (
                    f"L'appui {num} apparait {len(entries)} fois dans la source "
                    f"GESPOT avec exactement les memes valeurs. "
                    f"Une seule version a ete conservee automatiquement"
                ),
                'action': (
                    "Aucune correction bloquante. Vous pouvez nettoyer les "
                    "doublons GESPOT si vous voulez fiabiliser la source"
                ),
            })
        else:
            sources = ', '.join(sorted({e['source'] for e in entries}))
            result.anomalies.append({
                'source': 'GESPOT',
                'fichier': sources, 'num': num,
                'type': 'DOUBLON_CONFLICTUEL',
                'detail': (
                    f"L'appui {num} apparait {len(entries)} fois dans GESPOT "
                    f"avec des valeurs differentes. "
                    f"Le plugin ne peut pas choisir automatiquement la bonne ligne"
                ),
                'action': (
                    f"Comparer les sources GESPOT citees pour l'appui {num}, "
                    f"corriger la source pour ne garder qu'une version coherente, "
                    f"puis relancer"
                ),
            })


# ===========================================================================
#  CHARGEMENT DU RÉPERTOIRE CSV (historique)
# ===========================================================================

def load_gespot_dir(gespot_dir: str,
                   whitelist: Optional[set] = None) -> GespotLoadResult:
    """Charge tous les CSV d'un répertoire GESPOT.

    Args:
        gespot_dir: Chemin absolu du dossier GESPOT/GSPOT.
        whitelist:  Ensemble de codes stratégie valides (depuis Bases col M).

    Returns:
        GespotLoadResult avec records indexés par NUM, anomalies, compteurs.
    """
    if whitelist is None:
        whitelist = set()

    whitelist_upper = {v.upper() for v in whitelist}

    result = GespotLoadResult()
    try:
        entries = os.listdir(gespot_dir)
    except OSError as e:
        result.anomalies.append({
            'source': 'GESPOT', 'fichier': '', 'num': '',
            'type': 'FICHIER_IGNORE',
            'detail': (
                f"Le dossier GESPOT '{gespot_dir}' n'a pas pu etre lu "
                f"({e})"
            ),
            'action': (
                "Verifier que le chemin du dossier GESPOT est correct, que le dossier "
                "existe et que vous avez les droits d'acces, puis relancer"
            ),
        })
        return result

    csv_files = sorted(f for f in entries if f.lower().endswith('.csv'))
    if not csv_files:
        result.anomalies.append({
            'source': 'GESPOT', 'fichier': '', 'num': '',
            'type': 'FICHIER_IGNORE',
            'detail': (
                f"Aucun fichier CSV n'a ete trouve dans le dossier GESPOT '{gespot_dir}'. "
                "Le controle ne peut pas lire la source GESPOT"
            ),
            'action': (
                "Placer dans ce dossier les exports GESPOT au format CSV, puis relancer"
            ),
        })
        return result

    raw_by_num: Dict[str, List[dict]] = {}

    for fname in csv_files:
        path = os.path.join(gespot_dir, fname)
        try:
            rows, _ = _parse_one_csv(path)
        except Exception as e:
            result.anomalies.append({
                'source': 'GESPOT', 'fichier': fname, 'num': '',
                'type': 'FICHIER_IGNORE',
                'detail': (
                    f"Le fichier GESPOT '{fname}' n'a pas pu etre lu comme CSV exploitable "
                    f"({e})"
                ),
                'action': (
                    f"Verifier le separateur ';', l'encodage et la structure du fichier '{fname}', "
                    "puis relancer"
                ),
            })
            continue

        valid = 0
        for row in rows:
            num_raw = row[_COL_NUM].strip() if _COL_NUM < len(row) else ''
            if not num_raw:
                continue
            num_key = normalize_appui_num(num_raw)
            if not num_key:
                continue
            valid += 1
            raw_by_num.setdefault(num_key, []).append({'row': row, 'file': fname})

        result.file_counts[fname] = valid

    _resolve_duplicates(raw_by_num, result, whitelist_upper)
    return result


def _resolve_duplicates(raw_by_num: Dict[str, List[dict]],
                        result: GespotLoadResult,
                        whitelist_upper: set) -> None:
    """Résout les doublons NUM et alimente records + anomalies."""
    for num, entries in raw_by_num.items():
        if len(entries) == 1:
            rec = _build_record(entries[0]['row'], entries[0]['file'], whitelist_upper)
            result.records[num] = rec
            continue

        payloads = [str(e['row']) for e in entries]
        if len(set(payloads)) == 1:
            rec = _build_record(entries[0]['row'], entries[0]['file'], whitelist_upper)
            result.records[num] = rec
            result.anomalies.append({
                'source': 'GESPOT', 'fichier': entries[0]['file'], 'num': num,
                'type': 'DOUBLON_IDENTIQUE',
                'detail': (
                    f"L'appui {num} apparait {len(entries)} fois dans la source GESPOT "
                    f"avec exactement les memes valeurs. Une seule version a ete conservee automatiquement"
                ),
                'action': (
                    "Aucune correction bloquante. Vous pouvez nettoyer les doublons GESPOT "
                    "si vous voulez fiabiliser la source"
                ),
            })
        else:
            files = ', '.join(sorted({e['file'] for e in entries}))
            result.anomalies.append({
                'source': 'GESPOT', 'fichier': files, 'num': num,
                'type': 'DOUBLON_CONFLICTUEL',
                'detail': (
                    f"L'appui {num} apparait {len(entries)} fois dans GESPOT avec des valeurs "
                    f"differentes. Le plugin ne peut pas choisir automatiquement la bonne ligne"
                ),
                'action': (
                    f"Comparer les fichiers GESPOT cites pour l'appui {num}, corriger la "
                    f"source pour ne garder qu'une version coherente, puis relancer"
                ),
            })
