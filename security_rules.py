# -*- coding: utf-8 -*-
# Youcef ADDA | GEODECI
# FR : Plugins QGIS, solutions SIG et administration PostgreSQL/PostGIS ; audit, automatisation et accompagnement.
# EN : QGIS plugins, GIS solutions and PostgreSQL/PostGIS administration; audits, workflow automation and business support.
# AR : تطوير إضافات QGIS وحلول نظم المعلومات الجغرافية وإدارة PostgreSQL/PostGIS، والتدقيق والأتمتة ومرافقة المؤسسات.
# Site / Website / الموقع : https://geodeci.xyz/
# Contact professionnel / Business enquiries / للتواصل المهني : youcef.geodesien@gmail.com | contact@geodeci.xyz
# Détails / Details / التفاصيل : SIGNATURE.md
# ============================================================================
import re

"""
Module de règles de sécurité pour câbles aériens.
Basé sur norme NFC 11201-A1 et spécifications Prysmian.

Sources données:
- BDD comac_db/ (CSV officiels): cables.csv, comm_XX.csv, hypothese.csv
- BDD PostgreSQL (rip_avg_nge): t_cheminement.cm_long, cables.cab_capa
- Excel Comac: Longueur à facturer (AU), Type de ligne (L1092-xx-P), Conducteur (CU/BT)
"""

# =============================================================================
# CONSTANTES - Portées maximales (mètres) selon capacité FO et zone climatique
# =============================================================================

# Zone A-ZVN (Zone Vent Normal)
PORTEES_MAX_ZVN = {
    6: 81,
    12: 77,
    24: 73,
    36: 74,
    48: 78,
    72: 77,
    144: 65
}

# Zone A-ZVF (Zone Vent Fort)
PORTEES_MAX_ZVF = {
    6: 79,
    12: 74,
    24: 73,
    36: 74,
    48: 78,
    72: 77,
    144: 65
}

# =============================================================================
# CONSTANTES - Distances câble/BT selon type câble Enedis (mètres)
# =============================================================================

# Distance min/max câble FO / câble BT Enedis
DIST_CABLE_BT = {
    'fil_nu': (1.0, 1.2),      # Conducteur commence par "CU"
    'sans_cuivre': (0.5, 0.7)  # Conducteur commence par "BT" ou autre
}

# Distance minimale câble/sol (fixe)
DIST_CABLE_SOL_MIN = 4.0

# =============================================================================
# CONSTANTES - Mapping codes câbles Prysmian -> Capacité FO
# =============================================================================

# Codes L1092-xx-P (Excel Comac, colonne Type de ligne FIBRE OPTIQUE)
CODES_CABLE_PRYSMIAN = {
    'L1092-1-P': 12,
    'L1092-2-P': 36,
    'L1092-3-P': 72,
    'L1092-11-P': 6,
    'L1092-12-P': 12,
    'L1092-13-P': 36,
    'L1092-14-P': 72,
    'L1092-15': 144,
    'L1092-15-P': 144
}

# Capacités possibles par référence câble (une référence peut couvrir plusieurs capacités)
CAPACITES_POSSIBLES = {
    'L1092-1-P': [12],
    'L1092-2-P': [36],
    'L1092-3-P': [72],
    'L1092-11-P': [6],
    'L1092-12-P': [12],
    'L1092-13-P': [24, 36],
    'L1092-14-P': [48, 72],
    'L1092-15': [144],
    'L1092-15-P': [144],
}

# =============================================================================
# CONSTANTES - Colonnes Excel Comac
# =============================================================================

# Positions colonnes (0-indexed pour openpyxl)
# NB: La colonne B = "N° dans l'étude" décale tout de +1 à partir de C
EXCEL_COL_NUM_APPUI = 0          # A - N° appui
EXCEL_COL_NUM_ETUDE = 1          # B - N° dans l'étude
EXCEL_COL_TENSION_ELEC = 2       # C - Tension élec.
EXCEL_COL_TYPE_POTEAU = 3        # D - Type de poteau
EXCEL_COL_HAUTEUR_TOTALE = 4     # E - Hauteur totale
EXCEL_COL_SURIMPLANTATION = 5    # F - Surimplantation
EXCEL_COL_HAUTEUR_HORS_SOL = 6   # G - Hauteur hors sol (= distance câble/sol)
EXCEL_COL_CLASSE = 7             # H - Classe
EXCEL_COL_EFFORT = 8             # I - Effort
EXCEL_COL_CONDUCTEUR = 12        # M - Conducteur (CU/BT)
EXCEL_COL_LONGUEUR_FACTURER = 46 # AU - Longueur à facturer (portée)

# Section FIBRE OPTIQUE (colonnes AL+)
EXCEL_COL_BOITIER = 43           # AR - Boîtier fibre optique (oui/non)
EXCEL_COL_FO_TYPE_LIGNE = 40     # AO - Type de ligne (L1092-xx-P)

# =============================================================================
# CONSTANTES - Colonnes BDD PostgreSQL (rip_avg_nge)
# =============================================================================

# Table t_cheminement
BDD_COL_CM_LONG = 'cm_long'           # Portée entre appuis
BDD_COL_CM_NDCODE1 = 'cm_ndcode1'     # Noeud origine
BDD_COL_CM_NDCODE2 = 'cm_ndcode2'     # Noeud destination
BDD_COL_CM_PROPRIO = 'proprio'        # Propriétaire (ORANGE, ENEDIS, RAUV)

# Table cables
BDD_COL_CAB_CAPA = 'cab_capa'         # Capacité FO (6,12,24,36,48,72,144)
BDD_COL_CAB_TYPE = 'cab_type'         # Type (CDI, RAC, TRA)
BDD_COL_CAB_NATURE = 'cab_nature'     # Mode pose (AER, FAC, FOU, IMM)

# Table infra_pt_pot
BDD_COL_INF_TYPE = 'inf_type'         # Type poteau (POT-FT, POT-BT, POT-AC)
BDD_COL_INF_PROPRI = 'inf_propri'     # Propriétaire (ORANGE, ENEDIS, RAUV)
BDD_COL_INF_NUM = 'inf_num'           # Numéro appui
BDD_COL_ADR_INSEE = 'adr_insee'       # Code INSEE commune
BDD_COL_COMMENTAIRE = 'commentaire'   # Commentaire (contient "PRIVE" si terrain privé)

# =============================================================================
# FONCTIONS DE VALIDATION
# =============================================================================

def get_capacites_possibles(code_cable: str) -> list:
    """
    Retourne la liste des capacités FO possibles pour un code câble.
    Source prioritaire: PostgreSQL (table comac.cable_capacites_possibles)
    Fallback: dict CAPACITES_POSSIBLES local
    
    Ex: L1092-13-P → [24, 36], L1092-14-P → [48, 72]
    
    Args:
        code_cable: Code type "L1092-13-P"
    
    Returns:
        Liste de capacités possibles, ou [0] si non reconnu
    """
    if not code_cable:
        return [0]

    # Mapping local hardcode (source unique depuis suppression BDD)
    code_clean = str(code_cable).strip().upper()
    for code, capas in CAPACITES_POSSIBLES.items():
        if code.upper() in code_clean:
            return capas
    
    # Fallback: capacité unique via get_capacite_fo_from_code
    capa = get_capacite_fo_from_code(code_cable)
    return [capa] if capa > 0 else [0]


def get_capacite_fo_from_code(code_cable: str, debug: bool = False) -> int:
    """
    Extrait la capacité FO depuis un code câble Prysmian (mapping local).

    Args:
        code_cable: Code type "L1092-13-P" ou "L1092-13-P-"
        debug: Active les logs de debug

    Returns:
        Capacité FO (6, 12, 36, 72, 144) ou 0 si non reconnu
    """
    if debug:
        print(f"[COMAC_CAPA] Input code_cable: '{code_cable}' (type={type(code_cable).__name__})")

    if not code_cable:
        if debug:
            print("[COMAC_CAPA] -> code_cable vide/None, return 0")
        return 0

    code_clean = str(code_cable).strip().upper()
    if debug:
        print(f"[COMAC_CAPA] Code nettoyé: '{code_clean}'")
    
    for code, capa in CODES_CABLE_PRYSMIAN.items():
        if code in code_clean:
            if debug:
                print(f"[COMAC_CAPA] -> MATCH local: '{code}' in '{code_clean}' => capacité={capa}")
            return capa

    # Codes Prysmian gamme H (A-H0342A-24F-60-P, A-H0388A-6FO-0-P, etc.)
    # Capacité = nombre de fibres extrait du segment <n>F ou <n>FO
    m = re.search(r'(\d+)(?:F|FO)', code_clean)
    if m:
        capa = int(m.group(1))
        if debug:
            print(f"[COMAC_CAPA] -> MATCH gamme H: '{code_clean}' => capacité={capa}")
        return capa

    if debug:
        print(f"[COMAC_CAPA] -> AUCUN MATCH pour '{code_clean}'")
    return 0


def get_type_cable_enedis(conducteur: str) -> str:
    """
    Détermine le type de câble Enedis depuis la colonne Conducteur.
    
    Args:
        conducteur: Valeur colonne Conducteur (ex: "CU 12 1+3+1", "BT 4*25")
    
    Returns:
        'fil_nu' si commence par CU, 'sans_cuivre' sinon
    """
    if not conducteur:
        return 'sans_cuivre'
    
    if str(conducteur).strip().upper().startswith('CU'):
        return 'fil_nu'
    
    return 'sans_cuivre'


def get_distance_cable_bt(type_cable: str) -> tuple:
    """
    Retourne la distance min/max câble FO / câble BT selon type Enedis.
    
    Args:
        type_cable: 'fil_nu' ou 'sans_cuivre'
    
    Returns:
        Tuple (min, max) en mètres
    """
    return DIST_CABLE_BT.get(type_cable, DIST_CABLE_BT['sans_cuivre'])


def get_portee_max(capacite_fo: int, zone: str = 'ZVN') -> float:
    """
    Retourne la portée maximale selon capacité FO et zone climatique.
    
    Args:
        capacite_fo: Capacité en FO (6, 12, 24, 36, 48, 72, 144)
        zone: 'ZVN' (vent normal) ou 'ZVF' (vent fort)
    
    Returns:
        Portée max en mètres, ou 0 si capacité non reconnue
    """
    portees = PORTEES_MAX_ZVF if zone == 'ZVF' else PORTEES_MAX_ZVN
    return portees.get(capacite_fo, 0)


def verifier_portee(portee: float, capacite_fo: int, zone: str = 'ZVN', debug: bool = False) -> dict:
    """
    Vérifie si une portée respecte la limite selon capacité FO et zone climatique.
    
    Args:
        portee: Portée mesurée (mètres)
        capacite_fo: Capacité FO du câble (6,12,24,36,48,72,144)
        zone: Zone climatique ('ZVN' ou 'ZVF')
        debug: Active logs debug
        
    Returns:
        dict: {
            'valide': bool,
            'portee_max': float,
            'depassement': float (en %),
            'message': str
        }
    """
    # CRIT-004: Validation entrées
    if portee is None or portee < 0:
        return {
            'valide': False,
            'portee_max': None,
            'depassement': None,
            'message': f"Portée invalide: {portee}"
        }
    
    if capacite_fo is None or capacite_fo <= 0:
        return {
            'valide': False,
            'portee_max': None,
            'depassement': None,
            'message': f"Capacité FO invalide: {capacite_fo}"
        }
    
    portees_ref = PORTEES_MAX_ZVF if zone == 'ZVF' else PORTEES_MAX_ZVN
    portee_max = portees_ref.get(capacite_fo)
    
    if portee_max is None:
        return {
            'valide': False,
            'portee_max': None,
            'depassement': None,
            'message': f"Capacité FO {capacite_fo} non référencée (valeurs: {list(portees_ref.keys())})"
        }
    
    # CRIT-004: Guard division par zéro
    if portee_max == 0:
        return {
            'valide': False,
            'portee_max': portee_max,
            'depassement': None,
            'message': f"Portée max nulle pour capacité {capacite_fo}"
        }
    
    depassement_pct = ((portee - portee_max) / portee_max) * 100 if portee > portee_max else 0   
    return {
        'valide': portee <= portee_max,
        'portee_max': portee_max,
        'depassement': round(depassement_pct, 2),
        'message': "OK" if portee <= portee_max else f"PORTÉE MOLLE: {portee}m > {portee_max}m (dépassement: {depassement_pct:.2f}m)"
    }


def verifier_distance_cable_bt(distance: float, conducteur: str) -> dict:
    """
    Vérifie si la distance câble FO / câble BT est conforme.
    
    Args:
        distance: Distance mesurée en mètres
        conducteur: Valeur colonne Conducteur pour déterminer type câble
    
    Returns:
        dict avec 'valide', 'type_cable', 'distance_min', 'message'
    """
    type_cable = get_type_cable_enedis(conducteur)
    dist_min, dist_max = get_distance_cable_bt(type_cable)
    
    valide = distance >= dist_min
    
    return {
        'valide': valide,
        'type_cable': type_cable,
        'distance_min': dist_min,
        'distance_max': dist_max,
        'message': "OK" if valide else f"DISTANCE INSUFFISANTE: {distance}m < {dist_min}m (type: {type_cable})"
    }


def verifier_distance_sol(distance: float, debug: bool = False) -> dict:
    """
    Vérifie si la distance câble/sol respecte le minimum réglementaire (4m).
    
    Args:
        distance: Distance câble/sol mesurée (mètres)
        debug: Active logs debug
        
    Returns:
        dict: {
            'valide': bool,
            'distance_min': float,
            'message': str
        }
    """
    # CRIT-004: Validation entrées
    if distance is None or distance < 0:
        return {
            'valide': False,
            'distance_min': DIST_CABLE_SOL_MIN,
            'message': f"Distance invalide: {distance}"
        }
    
    valide = distance >= DIST_CABLE_SOL_MIN
    
    return {
        'valide': valide,
        'distance_min': DIST_CABLE_SOL_MIN,
        'message': "OK" if valide else f"HAUTEUR INSUFFISANTE: {distance}m < {DIST_CABLE_SOL_MIN}m"
    }


def est_terrain_prive(commentaire: str) -> bool:
    """
    Détecte si le terrain est privé via le champ commentaire.
    Cherche le mot "PRIVE" (ex: "PRIVE", "PRIVE/inaccessible").
    
    Args:
        commentaire: Valeur du champ commentaire de infra_pt_pot
    
    Returns:
        True si terrain privé, False sinon
    """
    if not commentaire:
        return False
    
    return 'PRIVE' in str(commentaire).upper()


# =============================================================================
# FONCTION DE VALIDATION COMPLÈTE D'UNE LIAISON
# =============================================================================

def valider_liaison(
    portee: float,
    capacite_fo: int,
    zone: str = 'ZVN',
    distance_bt: float = None,
    conducteur: str = None,
    distance_sol: float = None
) -> dict:
    """
    Valide complètement une liaison câble aérien.
    
    Args:
        portee: Longueur de la liaison en mètres
        capacite_fo: Capacité FO du câble
        zone: Zone climatique ('ZVN' ou 'ZVF')
        distance_bt: Distance câble FO / câble BT (optionnel)
        conducteur: Type conducteur Enedis (optionnel)
        distance_sol: Distance câble / sol (optionnel)
    
    Returns:
        dict avec résultats de toutes les vérifications
    """
    resultats = {
        'valide': True,
        'erreurs': [],
        'details': {}
    }
    
    # Vérification portée
    res_portee = verifier_portee(portee, capacite_fo, zone)
    resultats['details']['portee'] = res_portee
    if not res_portee['valide']:
        resultats['valide'] = False
        resultats['erreurs'].append(res_portee['message'])
    
    # Vérification distance câble/BT
    if distance_bt is not None and conducteur is not None:
        res_bt = verifier_distance_cable_bt(distance_bt, conducteur)
        resultats['details']['distance_bt'] = res_bt
        if not res_bt['valide']:
            resultats['valide'] = False
            resultats['erreurs'].append(res_bt['message'])
    
    # Vérification distance sol
    if distance_sol is not None:
        res_sol = verifier_distance_sol(distance_sol)
        resultats['details']['distance_sol'] = res_sol
        if not res_sol['valide']:
            resultats['valide'] = False
            resultats['erreurs'].append(res_sol['message'])
    
    return resultats
