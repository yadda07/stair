#!/usr/bin/python
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
Fonctions utilitaires pures (sans dépendance QGIS) pour le plugin PoleAerien.
Sécurisées pour l'utilisation dans les threads workers.
"""

import xml.etree.ElementTree as ET


# =============================================================================
# PARSING HELPERS (centralises - utilises par comac_db_reader, pcm_parser)
# =============================================================================

def safe_float(value, default: float = 0.0) -> float:
    """Parse float avec protection None/erreur et virgule française.
    
    Args:
        value: Valeur à parser (None, int, float, str)
        default: Valeur par défaut si parsing échoue
        
    Returns:
        float parsée ou default
    """
    if value is None:
        return default
    if isinstance(value, (int, float)):
        return float(value)
    val = str(value).strip().replace('m', '')
    if not val:
        return default
    try:
        return float(val.replace(',', '.'))
    except (ValueError, AttributeError):
        return default


def safe_int(value, default: int = 0) -> int:
    """Parse int avec protection None/erreur.
    
    Args:
        value: Valeur à parser (None, int, float, str)
        default: Valeur par défaut si parsing échoue
        
    Returns:
        int parsé ou default
    """
    if value is None:
        return default
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    val = str(value).strip()
    if not val:
        return default
    try:
        return int(float(val.replace(',', '.')))
    except (ValueError, AttributeError):
        return default


def get_xml_text(element: ET.Element, tag: str, default: str = "") -> str:
    """Extrait le texte d'un sous-élément XML de manière sûre.
    
    Args:
        element: Élément XML parent
        tag: Nom du sous-élément à chercher
        default: Valeur par défaut si absent
        
    Returns:
        Texte du sous-élément ou default
    """
    child = element.find(tag)
    return child.text.strip() if child is not None and child.text else default


def get_xml_float(element: ET.Element, tag: str, default: float = 0.0) -> float:
    """Extrait un float d'un sous-élément XML.
    
    Args:
        element: Élément XML parent
        tag: Nom du sous-élément
        default: Valeur par défaut
        
    Returns:
        float parsée ou default
    """
    return safe_float(get_xml_text(element, tag), default)


def parse_bool(value: str) -> bool:
    """Parse booléen (0/1)."""
    return str(value).strip() == '1'


def normaliser_boitier(raw) -> str:
    """Normalise valeur boîtier PCM/COMAC vers 'oui' ou 'non'.

    Accepte: 'oui', 'non', 1, 0, 1.0, 0.0, True, False,
             '1', '0', 'true', 'false', 'yes', 'no', 'o', 'n'.

    Args:
        raw: Valeur brute depuis Excel (str, int, float, bool) ou None.

    Returns:
        'oui', 'non', ou '' si non reconnu.
    """
    if raw is None:
        return ''
    if isinstance(raw, bool):
        return 'oui' if raw else 'non'
    if isinstance(raw, (int, float)):
        try:
            val_int = int(raw)
            if val_int == 1:
                return 'oui'
            if val_int == 0:
                return 'non'
        except (ValueError, TypeError):
            pass
        return ''
    s = str(raw).strip().lower()
    if s in ('oui', '1', 'true', 'yes', 'o'):
        return 'oui'
    if s in ('non', '0', 'false', 'no', 'n'):
        return 'non'
    return ''


# =============================================================================
# EXPORT PATH HELPER
# =============================================================================

def build_export_path(chemin: str, default_name: str) -> str:
    """Construit le chemin complet pour un fichier d'export Excel.
    
    Args:
        chemin: Chemin fourni (répertoire ou fichier)
        default_name: Nom de fichier par défaut (ex: "analyse.xlsx")
        
    Returns:
        Chemin complet vers le fichier .xlsx
    """
    import os
    if os.path.isdir(chemin):
        return os.path.join(chemin, default_name)
    if not chemin.endswith('.xlsx'):
        return chemin + '.xlsx'
    return chemin


# =============================================================================
# NORMALISATION APPUIS
# =============================================================================

def normalize_appui_num(inf_num, strip_e_prefix=False, strip_bt_prefix=False,
                        keep_commune=False):
    """Normalise un numéro d'appui pour comparaison.
    
    Args:
        inf_num: Numéro d'appui brut (ex: "1016436/63041", "E000123", "BT-123")
        strip_e_prefix: Si True, enlève préfixe E (pour matching existant)
        strip_bt_prefix: Si True, enlève préfixes BT-/BT
        keep_commune: Si True, conserve le suffixe /commune (ex: "78/63041").
            Indispensable quand un même numéro existe dans plusieurs communes.
        
    Returns:
        str: Numéro normalisé (sans zéros de tête, avec ou sans /commune)
    """
    try:
        if inf_num is None:
            return ""
        s = str(inf_num).strip()
        if not s:
            return ""
        
        # Extraire la partie commune (après le slash)
        commune_suffix = ""
        if "/" in s:
            parts = s.split("/", 1)
            s = parts[0].strip()
            if keep_commune and parts[1].strip():
                commune_suffix = "/" + parts[1].strip()
        
        # Enlever le suffixe .0 (conversion float -> str)
        if s.endswith(".0"):
            s = s[:-2]
        
        # Préfixe E: conserver ou enlever selon strip_e_prefix
        if s.startswith("E"):
            if strip_e_prefix:
                s = s[1:]
            else:
                return s + commune_suffix
        
        # Préfixe BT: enlever si demandé
        if strip_bt_prefix:
            if s.startswith("BT-"):
                s = s[3:]
            elif s.startswith("BT"):
                s = s[2:]
        
        # Nettoyer et normaliser
        s_clean = s.replace(" ", "")
        
        # Si c'est un nombre pur, enlever les zéros de tête
        if s_clean.isdigit():
            return (s_clean.lstrip("0") or "0") + commune_suffix
        
        return s + commune_suffix
    except (AttributeError, TypeError, ValueError):
        return ""


# =============================================================================
# FILE FILTERING (shared by C6_vs_Bd, police_workflow, etc.)
# =============================================================================

_OUTPUT_PREFIXES = (
    'analyse_c6', 'policec6_export', 'police_c6_export',
    'rapport_', 'rapport_batch_', 'export_', '~$',
    'analyse_', 'ficheappui', 'gespot',
)
_OUTPUT_KEYWORDS = (
    'ficheappui', '_c7', 'annexe c7', 'annexe_c7',
    'comac', 'capft', 'cap_ft',
)


def is_plugin_output_file(filename):
    """Return True if the file is a plugin output / temp file, not a real C6 annexe.

    Args:
        filename: Filename (basename, not full path)

    Returns:
        True if the file should be excluded from C6 detection
    """
    name = filename.lower()
    if any(name.startswith(p) for p in _OUTPUT_PREFIXES):
        return True
    if any(kw in name for kw in _OUTPUT_KEYWORDS):
        return True
    return False


# =============================================================================
# SPATIAL MATCHING (fallback when name matching fails)
# =============================================================================

_SPATIAL_TOLERANCE_DEFAULT = 7.5


def match_poles_spatial(coords_a, coords_b, tolerance=_SPATIAL_TOLERANCE_DEFAULT):
    """Match poles by spatial proximity when name matching fails.

    For each pole in coords_a, find the nearest pole in coords_b within
    tolerance (meters). Greedy: once a pole in coords_b is matched, it is
    consumed and won't match again.

    Requires projected CRS (Lambert 93) -- distances are Euclidean in meters.

    Args:
        coords_a: {name: (x, y)} - first set (e.g. unmatched QGIS poles)
        coords_b: {name: (x, y)} - second set (e.g. unmatched Excel poles)
        tolerance: max distance in meters (default 1.5)

    Returns:
        list of (name_a, name_b, distance_m) sorted by distance ascending.
    """
    import math
    matches = []
    used_b = set()

    for name_a, (xa, ya) in coords_a.items():
        best_name = None
        best_dist = tolerance + 1.0
        for name_b, (xb, yb) in coords_b.items():
            if name_b in used_b:
                continue
            dist = math.hypot(xa - xb, ya - yb)
            if dist <= tolerance and dist < best_dist:
                best_dist = dist
                best_name = name_b
        if best_name is not None:
            matches.append((name_a, best_name, round(best_dist, 2)))
            used_b.add(best_name)

    matches.sort(key=lambda m: m[2])
    return matches
