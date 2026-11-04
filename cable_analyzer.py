# Youcef ADDA | GEODECI
# FR : Plugins QGIS, solutions SIG et administration PostgreSQL/PostGIS ; audit, automatisation et accompagnement.
# EN : QGIS plugins, GIS solutions and PostgreSQL/PostGIS administration; audits, workflow automation and business support.
# AR : تطوير إضافات QGIS وحلول نظم المعلومات الجغرافية وإدارة PostgreSQL/PostGIS، والتدقيق والأتمتة ومرافقة المؤسسات.
# Site / Website / الموقع : https://geodeci.xyz/
# Contact professionnel / Business enquiries / للتواصل المهني : youcef.geodesien@gmail.com | contact@geodeci.xyz
# Détails / Details / التفاصيل : SIGNATURE.md
# ============================================================================
"""
Analyseur de charge câbles par appui - Police C6 v2.0
Compte les câbles découpés qui intersectent chaque appui
"""

from dataclasses import dataclass, field
from typing import List, Dict, Optional, Tuple
import re
from qgis.core import (
    QgsVectorLayer, QgsFeature, QgsGeometry, QgsPointXY,
    QgsMessageLog, Qgis, QgsSpatialIndex, QgsRectangle,
    QgsFeatureRequest
)

from .cable_segment import CableSegment
from .compat import MSG_INFO, MSG_WARNING

_RE_CABLE_CAPACITE = re.compile(r'([A-Z]{1,3}\d*\.?\d*)-(\d+)P', re.IGNORECASE)
_RE_CABLE_CAPACITE_SIMPLE = re.compile(r'-(\d+)P', re.IGNORECASE)


def _safe_attr_text(feature, idx):
    if idx < 0:
        return ''
    value = feature[idx]
    if value is None:
        return ''
    text = str(value).strip()
    return '' if not text or text.upper() == 'NULL' else text


@dataclass
class AppuiChargeResult:
    """Résultat de l'analyse de charge pour un appui"""
    num_appui: str
    geom: QgsGeometry
    # Données BDD (câbles découpés)
    nb_cables_bdd: int = 0
    capacite_totale_bdd: int = 0
    cables_details: List[CableSegment] = field(default_factory=list)
    # Données C6 (Annexe C6)
    nb_cables_c6: int = 0
    capacite_totale_c6: int = 0
    refs_cables_c6: List[Dict] = field(default_factory=list)
    
    # Comparaison
    match_nb: bool = False
    match_capa: bool = False
    anomalie: bool = False
    message_anomalie: str = ""


@dataclass
class CableRefC6:
    """Référence de câble extraite de l'Annexe C6"""
    reference: str      # Ex: "L192.11"
    capacite: int       # Ex: 26 (de "-26P")
    raw_text: str       # Texte original


class CableAnalyzer:
    """
    Analyse la charge de câbles par appui.
    Compare les câbles découpés (BDD) avec les déclarations Annexe C6.
    """
    
    def __init__(self, tolerance: float = 0.5):
        """
        Args:
            tolerance: Distance max (m) pour considérer qu'un câble touche un appui
        """
        self.tolerance = tolerance
        self.resultats: Dict[str, AppuiChargeResult] = {}
    
    def analyser_charge_appuis(
        self,
        appuis: List[Dict],
        cables_decoupes: List[CableSegment],
        only_aerien: bool = True
    ) -> Dict[str, AppuiChargeResult]:
        """
        Pour chaque appui, compte les câbles découpés qui le touchent.
        
        Args:
            appuis: Liste de dicts avec 'num_appui' et 'geom' (QgsGeometry ou QgsPointXY)
            cables_decoupes: Segments de câbles depuis fddcpi2
            only_aerien: Si True, ne compte que les câbles aériens (posemode=1)
        
        Returns:
            Dict[num_appui, AppuiChargeResult]
        """
        self.resultats = {}
        
        # Filtrer câbles aériens + façade si demandé
        if only_aerien:
            cables = [c for c in cables_decoupes if c.posemode in (1, 2)]
        else:
            cables = cables_decoupes
        
        QgsMessageLog.logMessage(
            f"Analyse charge: {len(appuis)} appuis, {len(cables)} câbles "
            f"({'aériens/façade' if only_aerien else 'tous'})",
            "PoleAerien", MSG_INFO
        )
        
        # Pre-extraire les extremites des cables une seule fois
        parsed_cables = []
        for cable in cables:
            if not cable.geom_wkt:
                continue
            cable_geom = QgsGeometry.fromWkt(cable.geom_wkt)
            if not cable_geom or cable_geom.isEmpty():
                continue
            if cable_geom.type() == 1:
                line = (
                    cable_geom.asMultiPolyline()[0]
                    if cable_geom.isMultipart() and cable_geom.asMultiPolyline()
                    else cable_geom.asPolyline()
                )
                if len(line) >= 2:
                    parsed_cables.append((cable, line[0], line[-1]))

        tol = self.tolerance
        for appui in appuis:
            num_appui = str(appui.get('num_appui', ''))
            geom = appui.get('geom')

            if not num_appui or not geom:
                continue

            if isinstance(geom, QgsPointXY):
                appui_pt = geom
                geom = QgsGeometry.fromPointXY(geom)
            else:
                appui_pt = geom.asPoint()

            ax, ay = appui_pt.x(), appui_pt.y()
            cables_touchant = []
            for cable, start_pt, end_pt in parsed_cables:
                if (
                    (abs(ax - start_pt.x()) <= tol and abs(ay - start_pt.y()) <= tol
                     and appui_pt.distance(start_pt) <= tol)
                    or (abs(ax - end_pt.x()) <= tol and abs(ay - end_pt.y()) <= tol
                        and appui_pt.distance(end_pt) <= tol)
                ):
                    cables_touchant.append(cable)

            result = AppuiChargeResult(
                num_appui=num_appui,
                geom=geom,
                nb_cables_bdd=len(cables_touchant),
                capacite_totale_bdd=sum(c.cab_capa for c in cables_touchant),
                cables_details=cables_touchant
            )

            self.resultats[num_appui] = result

        return self.resultats
    
    def _find_cables_touching_appui(
        self,
        appui_geom: QgsGeometry,
        cables: List[CableSegment]
    ) -> List[CableSegment]:
        """
        Trouve tous les câbles dont une extrémité touche l'appui.
        """
        touching = []
        appui_point = appui_geom.asPoint()
        
        for cable in cables:
            # Parser la géométrie WKT du câble
            cable_geom = QgsGeometry.fromWkt(cable.geom_wkt)
            if not cable_geom or cable_geom.isEmpty():
                continue
            
            # Vérifier les extrémités
            if cable_geom.type() == 1:  # LineString
                line = cable_geom.asPolyline()
                if len(line) >= 2:
                    start_point = line[0]
                    end_point = line[-1]
                    
                    # Distance aux extrémités
                    dist_start = appui_point.distance(start_point)
                    dist_end = appui_point.distance(end_point)
                    
                    if dist_start <= self.tolerance or dist_end <= self.tolerance:
                        touching.append(cable)
        
        return touching
    
    def enrichir_avec_c6(
        self,
        donnees_c6: Dict[str, str]
    ):
        """
        Enrichit les résultats avec les données de l'Annexe C6.
        
        Args:
            donnees_c6: Dict[num_appui, nom_cable] depuis l'Annexe C6
        """
        for num_appui, result in self.resultats.items():
            nom_cable_c6 = donnees_c6.get(num_appui, '')
            
            if nom_cable_c6:
                refs = self.parser_references_cables_c6(nom_cable_c6)
                result.nb_cables_c6 = len(refs)
                result.capacite_totale_c6 = sum(r['capacite'] for r in refs)
                result.refs_cables_c6 = refs
            
            # Comparer
            result.match_nb = (result.nb_cables_bdd == result.nb_cables_c6)
            result.match_capa = (result.capacite_totale_bdd == result.capacite_totale_c6)
            result.anomalie = not (result.match_nb and result.match_capa)
            
            if result.anomalie:
                result.message_anomalie = self._generer_message_anomalie(result)
    
    @staticmethod
    def parser_references_cables_c6(nom_cable: str) -> List[Dict]:
        """
        Parse la colonne 'Nom du câble' de l'Annexe C6.
        
        Formats reconnus:
        - "L192.11-26P" → ref='L192.11', capa=26
        - "L192.11-26P | L193.12-6P" → 2 références
        - "L192.11-26P L193.12-6P" → 2 références
        
        Args:
            nom_cable: Contenu de la colonne "Nom du câble"
        
        Returns:
            Liste de dicts {'reference': str, 'capacite': int, 'raw': str}
        """
        if not nom_cable or not isinstance(nom_cable, str):
            return []
        
        references = []

        matches = _RE_CABLE_CAPACITE.findall(nom_cable)

        for ref, capa in matches:
            references.append({
                'reference': ref,
                'capacite': int(capa),
                'raw': f"{ref}-{capa}P"
            })

        if not references:
            simple_matches = _RE_CABLE_CAPACITE_SIMPLE.findall(nom_cable)
            for idx, capa in enumerate(simple_matches):
                references.append({
                    'reference': f"REF{idx+1}",
                    'capacite': int(capa),
                    'raw': f"-{capa}P"
                })
        
        return references
    
    def _generer_message_anomalie(self, result: AppuiChargeResult) -> str:
        """Génère un message explicatif pour l'anomalie."""
        messages = []
        
        if result.nb_cables_bdd != result.nb_cables_c6:
            diff = result.nb_cables_bdd - result.nb_cables_c6
            if diff > 0:
                messages.append(
                    f"Base : {result.nb_cables_bdd} cable(s), annexe C6 : {result.nb_cables_c6} "
                    f"(+{diff} cable(s) en base non declare(s) dans l'annexe C6)"
                )
            else:
                messages.append(
                    f"Base : {result.nb_cables_bdd} cable(s), annexe C6 : {result.nb_cables_c6} "
                    f"({abs(diff)} cable(s) de l'annexe C6 introuvable(s) en base)"
                )
        
        if result.capacite_totale_bdd != result.capacite_totale_c6:
            diff = result.capacite_totale_bdd - result.capacite_totale_c6
            messages.append(
                f"Capacite fibre : base = {result.capacite_totale_bdd} FO, annexe C6 = {result.capacite_totale_c6} FO "
                f"(ecart {diff:+d} FO)"
            )
        
        return " | ".join(messages) if messages else ""
    
    def get_anomalies(self) -> List[AppuiChargeResult]:
        """Retourne uniquement les appuis avec anomalie."""
        return [r for r in self.resultats.values() if r.anomalie]
    
    def get_stats_globales(self) -> Dict:
        """Retourne les statistiques globales."""
        total = len(self.resultats)
        anomalies = len(self.get_anomalies())
        
        return {
            'total_appuis': total,
            'appuis_ok': total - anomalies,
            'appuis_anomalie': anomalies,
            'total_cables_bdd': sum(r.nb_cables_bdd for r in self.resultats.values()),
            'total_cables_c6': sum(r.nb_cables_c6 for r in self.resultats.values()),
            'total_capa_bdd': sum(r.capacite_totale_bdd for r in self.resultats.values()),
            'total_capa_c6': sum(r.capacite_totale_c6 for r in self.resultats.values()),
        }


def extraire_appuis_from_layer(layer: QgsVectorLayer, field_num_appui: str = 'num_appui',
                               keep_commune: bool = False) -> List[Dict]:
    """
    Extrait les appuis depuis une couche QGIS.
    
    Args:
        layer: Couche de points (poteaux)
        field_num_appui: Nom du champ contenant le numéro d'appui
        keep_commune: Si True, conserve /commune dans num_appui normalise
    
    Returns:
        Liste de dicts {'num_appui': str, 'geom': QgsGeometry}
    """
    appuis = []
    
    if not layer or not layer.isValid():
        return appuis
    
    # Chercher le bon nom de champ
    field_names = [f.name() for f in layer.fields()]
    actual_field = None
    
    for candidate in [field_num_appui, 'inf_num', 'INF_NUM', 'num_appui', 'NUM_APPUI', 'pt_ad_numsu', 'PT_AD_NUMSU']:
        if candidate in field_names:
            actual_field = candidate
            break
    
    if not actual_field:
        QgsMessageLog.logMessage(
            f"Champ numéro d'appui non trouvé dans {layer.name()}. "
            f"Champs disponibles: {field_names[:10]}...",
            "PoleAerien", MSG_WARNING
        )
        return appuis
    
    from .core_utils import normalize_appui_num
    fields = layer.fields()
    idx_actual = fields.indexFromName(actual_field)
    idx_inf_num = fields.indexFromName('inf_num')
    idx_inf_type = fields.indexFromName('inf_type')
    idx_etat = fields.indexFromName('etat')
    idx_noe_codext = fields.indexFromName('noe_codext')

    attr_indices = sorted({
        i for i in (idx_actual, idx_inf_num, idx_inf_type, idx_etat, idx_noe_codext)
        if i >= 0
    })
    req = QgsFeatureRequest()
    if attr_indices:
        req.setSubsetOfAttributes(attr_indices)

    for feature in layer.getFeatures(req):
        num_appui = feature[idx_actual] if idx_actual >= 0 else feature[actual_field]
        if num_appui:
            num_norm = normalize_appui_num(num_appui, keep_commune=keep_commune)
            inf_num_norm = normalize_appui_num(_safe_attr_text(feature, idx_inf_num), keep_commune=keep_commune)
            if num_norm:
                appuis.append({
                    'num_appui': num_norm,
                    'inf_num': inf_num_norm or num_norm,
                    'geom': feature.geometry(),
                    'feature_id': feature.id(),
                    'inf_type': _safe_attr_text(feature, idx_inf_type),
                    'etat': _safe_attr_text(feature, idx_etat),
                    'noe_codext': _safe_attr_text(feature, idx_noe_codext),
                })

    return appuis


def extraire_appuis_wkb_both(
    layer: QgsVectorLayer,
    field_num_appui: str = 'num_appui',
) -> Tuple[List[Dict], List[Dict]]:
    """Extrait les appuis WKB avec et sans commune en une seule passe couche."""
    if not layer or not layer.isValid():
        return [], []

    fields = layer.fields()
    field_names = [f.name() for f in fields]
    actual_field = None
    for candidate in (
        field_num_appui, 'inf_num', 'INF_NUM',
        'num_appui', 'NUM_APPUI', 'pt_ad_numsu', 'PT_AD_NUMSU',
    ):
        if candidate in field_names:
            actual_field = candidate
            break
    if not actual_field:
        return [], []

    from .core_utils import normalize_appui_num
    idx_actual = fields.indexFromName(actual_field)
    idx_inf_num = fields.indexFromName('inf_num')
    idx_inf_type = fields.indexFromName('inf_type')
    idx_etat = fields.indexFromName('etat')
    idx_noe_codext = fields.indexFromName('noe_codext')

    attr_indices = sorted({
        i for i in (idx_actual, idx_inf_num, idx_inf_type, idx_etat, idx_noe_codext)
        if i >= 0
    })
    req = QgsFeatureRequest()
    if attr_indices:
        req.setSubsetOfAttributes(attr_indices)

    with_commune: List[Dict] = []
    without_commune: List[Dict] = []

    for feature in layer.getFeatures(req):
        num_appui = feature[idx_actual] if idx_actual >= 0 else feature[actual_field]
        if not num_appui:
            continue
        raw_inf_text = _safe_attr_text(feature, idx_inf_num)
        num_com = normalize_appui_num(num_appui, keep_commune=True)
        num_nocom = normalize_appui_num(num_appui, keep_commune=False)
        if not num_com and not num_nocom:
            continue

        geom = feature.geometry()
        wkb_bytes = geom.asWkb().data() if geom and not geom.isNull() else None
        fid = feature.id()
        inf_type = _safe_attr_text(feature, idx_inf_type)
        etat = _safe_attr_text(feature, idx_etat)
        noe_codext = _safe_attr_text(feature, idx_noe_codext)

        if num_com:
            inf_com = normalize_appui_num(raw_inf_text, keep_commune=True) or num_com
            with_commune.append({
                'num_appui': num_com,
                'inf_num': inf_com,
                'feature_id': fid,
                'geom_wkb': wkb_bytes,
                'inf_type': inf_type,
                'etat': etat,
                'noe_codext': noe_codext,
            })
        if num_nocom:
            inf_nocom = normalize_appui_num(raw_inf_text, keep_commune=False) or num_nocom
            without_commune.append({
                'num_appui': num_nocom,
                'inf_num': inf_nocom,
                'feature_id': fid,
                'geom_wkb': wkb_bytes,
                'inf_type': inf_type,
                'etat': etat,
                'noe_codext': noe_codext,
            })

    return with_commune, without_commune


def extraire_appuis_wkb(layer: QgsVectorLayer, field_num_appui: str = 'num_appui',
                        keep_commune: bool = False) -> List[Dict]:
    """Extrait appuis depuis couche QGIS et serialise geometries en WKB.
    
    Combine extraire_appuis_from_layer() + WKB serialization en un seul appel.
    Le resultat est thread-safe (pas de QgsGeometry, juste des bytes WKB).
    
    Args:
        layer: Couche de points (poteaux)
        field_num_appui: Nom du champ contenant le numero d'appui
        keep_commune: Si True, conserve /commune dans num_appui normalise
    
    Returns:
        Liste de dicts {'num_appui': str, 'feature_id': int, 'geom_wkb': bytes|None}
    """
    raw_appuis = extraire_appuis_from_layer(layer, field_num_appui, keep_commune=keep_commune)
    appuis_wkb = []
    for appui in raw_appuis:
        geom = appui.get('geom')
        appuis_wkb.append({
            'num_appui': appui.get('num_appui', ''),
            'inf_num': appui.get('inf_num', ''),
            'feature_id': appui.get('feature_id'),
            'geom_wkb': geom.asWkb().data() if geom and not geom.isNull() else None,
            'inf_type': appui.get('inf_type', ''),
            'etat': appui.get('etat', ''),
            'noe_codext': appui.get('noe_codext', ''),
        })
    return appuis_wkb


def _parse_attaches_geoms(attaches_raw: List[Dict]) -> List[Dict]:
    """Parse les attaches brutes (WKT) en geometries avec endpoints extraits.
    
    Args:
        attaches_raw: Liste de dicts {gid, geom_wkt} depuis query_attaches_by_sro()
    
    Returns:
        Liste de dicts {gid, geom, start, end} ou start/end sont des QgsGeometry points
    """
    parsed = []
    for att in attaches_raw:
        wkt = att.get('geom_wkt', '')
        if not wkt:
            continue
        geom = QgsGeometry.fromWkt(wkt)
        if not geom or geom.isNull() or geom.isEmpty():
            continue
        if geom.isMultipart():
            lines = geom.asMultiPolyline()
            line = lines[0] if lines else []
        else:
            line = geom.asPolyline()
        if len(line) < 2:
            continue
        parsed.append({
            'gid': att.get('gid', 0),
            'geom': geom,
            'start': QgsGeometry.fromPointXY(line[0]),
            'end': QgsGeometry.fromPointXY(line[-1]),
            'sx': line[0].x(),
            'sy': line[0].y(),
            'ex': line[-1].x(),
            'ey': line[-1].y(),
        })
    return parsed


def _get_attache_extensions(
    appui_geom: QgsGeometry,
    attaches: List[Dict],
    tolerance: float = 0.5
) -> List[QgsGeometry]:
    """Trouve les points d'extension accessibles depuis un appui via des attaches.
    
    Pour chaque attache dont un endpoint est proche de l'appui,
    retourne l'AUTRE endpoint (le cote cable/BPE).
    
    Args:
        appui_geom: Geometrie point de l'appui
        attaches: Liste de dicts {gid, geom, start, end} (depuis _parse_attaches_geoms)
        tolerance: Distance max en metres
    
    Returns:
        Liste de QgsGeometry points representant les extensions
    """
    extensions = []
    if not appui_geom or appui_geom.isEmpty():
        return extensions
    pt = appui_geom.asPoint()
    ax, ay = pt.x(), pt.y()
    tol_sq = tolerance * tolerance
    for att in attaches:
        if 'sx' in att:
            dx_s = ax - att['sx']
            dy_s = ay - att['sy']
            if abs(dx_s) <= tolerance and abs(dy_s) <= tolerance and (dx_s * dx_s + dy_s * dy_s) <= tol_sq:
                extensions.append(att['end'])
                continue
            dx_e = ax - att['ex']
            dy_e = ay - att['ey']
            if abs(dx_e) <= tolerance and abs(dy_e) <= tolerance and (dx_e * dx_e + dy_e * dy_e) <= tol_sq:
                extensions.append(att['start'])
        else:
            if appui_geom.distance(att['start']) <= tolerance:
                extensions.append(att['end'])
            elif appui_geom.distance(att['end']) <= tolerance:
                extensions.append(att['start'])
    return extensions


def compter_cables_par_appui(
    cables: List[CableSegment],
    appuis: List[Dict],
    tolerance: float = 1.5,
    group_by_gid: bool = False,
    attaches_parsed: Optional[List[Dict]] = None,
    cab_types: Optional[set] = None
) -> Dict[str, Dict]:
    """
    Compte les câbles qui touchent chaque appui.
    
    Args:
        cables: Liste de CableSegment depuis fddcpi2
        appuis: Liste de dicts avec 'num_appui' et 'geom'
        tolerance: Distance max en mètres pour l'intersection
        group_by_gid: Si True, compte les câbles physiques (distinct GID)
            au lieu des segments découpés (gid_dc2). Utiliser True pour COMAC
            (références = câbles physiques), False pour Police C6
            (références = câbles découpés par zone d'étude).
        attaches_parsed: Liste de dicts {gid, geom, start, end} depuis
            _parse_attaches_geoms(). Si fourni, les cables connectes via
            une attache sont aussi comptabilises.
        cab_types: Set de cab_type acceptes (ex: {'CDI'} ou {'CDI','TRA','RAC'}).
            Si None, accepte tous les types passes (pas de filtre interne).
    
    Returns:
        Dict[num_appui, {'count': int, 'capacites': List[int], 'cables': List}]
    """
    result = {}
    
    if not cables or not appuis:
        QgsMessageLog.logMessage(
            f"compter_cables_par_appui: SKIP (cables={len(cables) if cables else 0}, appuis={len(appuis) if appuis else 0})",
            "PoleAerien", MSG_INFO
        )
        return result

    # Creer index spatial des appuis + extensions via attaches
    appuis_by_id = {}
    extensions_by_appui = {}
    nb_appuis_no_geom = 0
    for appui in appuis:
        num = appui.get('num_appui', '')
        if num:
            appuis_by_id[num] = appui
            result[num] = {
                'count': 0,
                'capacites': [],
                'cables': [],
                '_gids_seen': set()
            }
            if not appui.get('geom'):
                nb_appuis_no_geom += 1
            if attaches_parsed and appui.get('geom'):
                extensions_by_appui[num] = _get_attache_extensions(
                    appui['geom'], attaches_parsed, tolerance
                )
    
    if nb_appuis_no_geom:
        QgsMessageLog.logMessage(
            f"compter_cables_par_appui: {nb_appuis_no_geom}/{len(appuis_by_id)} appuis SANS geometrie",
            "PoleAerien", MSG_WARNING
        )

    # Index spatial O(log n): appuis + points d'extension via attaches
    _appuis_idx = QgsSpatialIndex()
    _pos_id_to_num: Dict[int, str] = {}
    _pos_counter = 0
    for _num, _appui_data in appuis_by_id.items():
        _geom = _appui_data.get('geom')
        if _geom and not _geom.isNull():
            _feat = QgsFeature(_pos_counter)
            _feat.setGeometry(_geom)
            _appuis_idx.addFeature(_feat)
            _pos_id_to_num[_pos_counter] = _num
            _pos_counter += 1
        for _ext_pt in extensions_by_appui.get(_num, []):
            _feat = QgsFeature(_pos_counter)
            _feat.setGeometry(_ext_pt)
            _appuis_idx.addFeature(_feat)
            _pos_id_to_num[_pos_counter] = _num
            _pos_counter += 1

    # Pour chaque câble, trouver les appuis aux extrémités
    # Filtre type + posemode aerien/facade
    nb_cables_skipped_type = 0
    nb_cables_skipped_geom = 0
    nb_cables_tested = 0
    nb_total_matches = 0
    nb_endpoint_matches = 0
    for cable in cables:
        if cab_types is not None and getattr(cable, 'cab_type', '') not in cab_types:
            nb_cables_skipped_type += 1
            continue
        if getattr(cable, 'posemode', 0) not in (1, 2):
            nb_cables_skipped_type += 1
            continue
        
        wkt = cable.geom_wkt if hasattr(cable, 'geom_wkt') else None
        if not wkt:
            nb_cables_skipped_geom += 1
            continue
        nb_cables_tested += 1
        
        try:
            cable_geom = QgsGeometry.fromWkt(wkt)
            if cable_geom.isNull() or cable_geom.isEmpty():
                continue
            
            # Récupérer les extrémités du câble
            if cable_geom.isMultipart():
                lines = cable_geom.asMultiPolyline()
                line = lines[0] if lines else []
            else:
                line = cable_geom.asPolyline()
            
            if len(line) < 2:
                continue
            
            start_point = QgsGeometry.fromPointXY(line[0])
            end_point = QgsGeometry.fromPointXY(line[-1])

            # Candidats via index spatial (deux boites serrees aux extremites)
            _sx, _sy = line[0].x(), line[0].y()
            _ex, _ey = line[-1].x(), line[-1].y()
            _bbox_start = QgsRectangle(
                _sx - tolerance, _sy - tolerance,
                _sx + tolerance, _sy + tolerance
            )
            _bbox_end = QgsRectangle(
                _ex - tolerance, _ey - tolerance,
                _ex + tolerance, _ey + tolerance
            )
            _cand_fids = set(_appuis_idx.intersects(_bbox_start))
            _cand_fids.update(_appuis_idx.intersects(_bbox_end))
            candidate_nums = {
                _pos_id_to_num[_fid]
                for _fid in _cand_fids
                if _fid in _pos_id_to_num
            }

            for appui_num in candidate_nums:
                appui_data = appuis_by_id[appui_num]
                appui_geom = appui_data.get('geom')
                if not appui_geom:
                    continue

                # fddcpi2: cable = segment decoupe, extremites aux appuis
                dist_start = appui_geom.distance(start_point)
                dist_end = appui_geom.distance(end_point)
                touches = (dist_start <= tolerance or dist_end <= tolerance)

                if not touches and appui_num in extensions_by_appui:
                    for ext_pt in extensions_by_appui[appui_num]:
                        if (ext_pt.distance(start_point) <= tolerance or
                                ext_pt.distance(end_point) <= tolerance):
                            touches = True
                            break

                if touches:
                    # Déduplication par GID physique si demandé (COMAC)
                    if group_by_gid:
                        gid = getattr(cable, 'gid', 0)
                        if gid in result[appui_num]['_gids_seen']:
                            continue
                        result[appui_num]['_gids_seen'].add(gid)

                    nb_endpoint_matches += 1
                    nb_total_matches += 1

                    result[appui_num]['count'] += 1
                    cable_entry = {
                        'id': cable.gid_dc2,
                        'gid': getattr(cable, 'gid', 0),
                        'capacite': cable.cab_capa,
                        'posemode': cable.posemode,
                        'cb_etiquet': getattr(cable, 'cb_etiquet', '') or '',
                        'cab_type': getattr(cable, 'cab_type', '') or '',
                    }
                    if cable.cab_capa:
                        result[appui_num]['capacites'].append(cable.cab_capa)
                    result[appui_num]['cables'].append(cable_entry)
        except Exception as e:
            QgsMessageLog.logMessage(
                f"Erreur comptage câble: {e}",
                "PoleAerien", MSG_WARNING
            )
    
    # Resume matching
    nb_with_cables = sum(1 for v in result.values() if v.get('count', 0) > 0)
    QgsMessageLog.logMessage(
        f"compter_cables_par_appui RESULTAT: "
        f"{nb_cables_tested} cables testes ({nb_cables_skipped_type} exclus type/pose, "
        f"{nb_cables_skipped_geom} sans geom) | "
        f"{nb_total_matches} matches endpoint | "
        f"{nb_with_cables}/{len(result)} appuis avec cables",
        "PoleAerien", MSG_INFO
    )

    if group_by_gid:
        nb_gid_dedup = sum(
            max(0, len(data.get('cables', [])) - len(data.get('_gids_seen', set())))
            for data in result.values()
        )
        if nb_gid_dedup > 0:
            QgsMessageLog.logMessage(
                f"compter_cables_par_appui: {nb_gid_dedup} segments fusionnes par dedup GID",
                "PoleAerien", MSG_INFO
            )

    # Nettoyer les sets internes
    for data in result.values():
        data.pop('_gids_seen', None)
    
    return result


def comparer_source_cables(
    dico_cables_source: Dict[str, List[str]],
    cables_par_appui: Dict[str, Dict],
    source_label: str = "SOURCE",
    get_capacites_fn=None,
    ref_label: str = 'BDD',
    dedup_refs: bool = False
) -> List[Dict]:
    """Compare les cables declares dans une source (C6 ou COMAC) avec les cables BDD (fddcpi2).
    
    Fonction generique qui factorise la logique identique de
    Comac.comparer_comac_cables() et PoliceC6.comparer_c6_cables().
    
    Args:
        dico_cables_source: {appui_norm -> [refs_cables]} depuis parsing Excel
        cables_par_appui: {appui_norm -> {count, capacites, cables}} depuis compter_cables_par_appui()
        source_label: Label pour les messages et cles de sortie ('C6' ou 'COMAC')
        get_capacites_fn: Callable(ref) -> List[int] pour resoudre capacites possibles.
            Si None, utilise security_rules.get_capacites_possibles.
        ref_label: Label pour la source de reference ('BDD' ou 'GraceTHD').
        dedup_refs: Si True, deduplique les references par appui avant comparaison.
            Utiliser True pour COMAC (Excel liste chaque cable 2x: entree+sortie)
            conjointement avec group_by_gid=True cote BDD/GraceTHD.
    
    Returns:
        Liste de dicts avec comparaison par appui:
        [{num_appui, nb_cables_source, cables_source, capas_source,
          nb_cables_bdd, capas_bdd, statut, message}]
    """
    if get_capacites_fn is None:
        from .security_rules import get_capacites_possibles
        get_capacites_fn = get_capacites_possibles

    comparaison = []
    label_lower = source_label.lower()

    # Diagnostic: cles source vs cles reference
    source_keys = set(dico_cables_source.keys())
    ref_keys = set(cables_par_appui.keys())
    matched_keys = source_keys & ref_keys
    missing_keys = source_keys - ref_keys
    QgsMessageLog.logMessage(
        f"comparer_source_cables({source_label} vs {ref_label}): "
        f"{len(source_keys)} appuis {source_label}, {len(ref_keys)} appuis {ref_label}, "
        f"{len(matched_keys)} correspondances, {len(missing_keys)} absents",
        "PoleAerien", MSG_INFO
    )
    if missing_keys:
        bt_missing = sorted(k for k in missing_keys if k.startswith('BT'))
        other_missing = sorted(k for k in missing_keys if not k.startswith('BT'))
        if bt_missing:
            QgsMessageLog.logMessage(
                f"  {len(bt_missing)} appui(s) BT absents (poteaux BT non charges dans infra_pt_pot): "
                f"{bt_missing[:8]}{'...' if len(bt_missing) > 8 else ''}",
                "PoleAerien", MSG_INFO
            )
        if other_missing:
            QgsMessageLog.logMessage(
                f"  {len(other_missing)} appui(s) COMAC sans poteau dans {ref_label}: {other_missing[:5]}",
                "PoleAerien", MSG_WARNING
            )

    nb_logged_ecarts = 0
    for num_appui, refs_source in dico_cables_source.items():
        bdd_data = cables_par_appui.get(num_appui, {})
        nb_cables_bdd = bdd_data.get('count', 0)

        refs_before_dedup = list(refs_source)
        if dedup_refs:
            seen = set()
            refs_unique = []
            for r in refs_source:
                if r not in seen:
                    seen.add(r)
                    refs_unique.append(r)
            refs_source = refs_unique

        nb_cables_source = len(refs_source)

        capas_possibles = [get_capacites_fn(ref) for ref in refs_source]
        capas_bdd = sorted(bdd_data.get('capacites', []))
        capas_display = ['/'.join(str(c) for c in cp) for cp in capas_possibles]

        messages = []
        if not bdd_data:
            statut = f"ABSENT_{ref_label.upper().replace(' ', '_')}"
            messages.append(f"Appui introuvable en base de donnees ({ref_label})")
        else:
            ecart_count = nb_cables_source != nb_cables_bdd
            ecart_capa = not capacites_compatibles(capas_possibles, capas_bdd)

            if ecart_count:
                diff = nb_cables_bdd - nb_cables_source
                messages.append(
                    f"Nombre de cables different : {ref_label}={nb_cables_bdd}, {source_label}={nb_cables_source} (ecart {diff:+d})"
                )
            if ecart_capa:
                src_str = '+'.join(capas_display) or '?'
                bdd_str = '+'.join(str(c) for c in capas_bdd) or '?'
                messages.append(
                    f"Capacites FO differentes : {source_label}=[{src_str}] FO vs {ref_label}=[{bdd_str}] FO"
                )

            statut = "ECART" if (ecart_count or ecart_capa) else "OK"

            if statut == 'ECART':
                nb_logged_ecarts += 1

        entry = {
            'num_appui': num_appui,
            f'nb_cables_{label_lower}': nb_cables_source,
            f'cables_{label_lower}': '; '.join(refs_source),
            f'capas_{label_lower}': capas_display,
            'nb_cables_bdd': nb_cables_bdd,
            'capas_bdd': capas_bdd,
            'statut': statut,
            'message': ' | '.join(messages),
        }
        if statut == 'ECART':
            entry['_refs_brut'] = list(refs_before_dedup)
            entry['_refs_dedup'] = list(refs_source)
            entry['_capas_possibles'] = capas_possibles
            entry['_bdd_cables_raw'] = bdd_data.get('cables', [])
        comparaison.append(entry)

    return comparaison


def capacites_compatibles(
    capas_possibles_source: List[List[int]],
    capas_bdd: List[int]
) -> bool:
    """
    Vérifie si les capacités BDD sont compatibles avec les câbles source (C6 ou COMAC).
    Chaque capacité BDD doit pouvoir être associée à un câble source
    dont les capacités possibles incluent cette valeur.
    
    Ex: source = [[24,36], [24,36]], BDD = [24, 36] → True
    Ex: source = [[24,36]], BDD = [72] → False
    
    Args:
        capas_possibles_source: Liste de listes de capacités possibles par câble source
        capas_bdd: Liste des capacités BDD (cab_capa)
    
    Returns:
        True si toutes les capacités BDD matchent un câble source
    """
    if len(capas_possibles_source) != len(capas_bdd):
        return False
    
    if not capas_bdd and not capas_possibles_source:
        return True
    
    # Matching glouton : pour chaque capacité BDD, trouver un câble source compatible
    used = [False] * len(capas_possibles_source)
    
    for bdd_capa in sorted(capas_bdd):
        matched = False
        for i, possibles in enumerate(capas_possibles_source):
            if not used[i] and bdd_capa in possibles:
                used[i] = True
                matched = True
                break
        if not matched:
            return False
    
    return True


_BOITIER_TYPE_COMPAT = {
    'PB':  {'PB', 'PBO', 'PBR'},
    'PEO': {'PA', 'PEP'},
}


def verifier_boitiers(
    boitier_source: Dict[str, str],
    appuis_data: List[Dict],
    bpe_geoms: List[Dict],
    tolerance: float = 1.0,
    attaches_parsed: Optional[List[Dict]] = None
) -> Dict[str, Dict]:
    """Verifie la presence de BPE pour chaque appui declarant un boitier.
    
    Fonction generique utilisee par ComacTask et PoliceC6Task.
    
    Args:
        boitier_source: {num_appui: valeur_boitier} (ex: "PB", "PEO", "oui")
        appuis_data: Liste de dicts avec 'num_appui' et 'geom' (QgsGeometry)
        bpe_geoms: Liste de dicts avec 'geom' (QgsGeometry), 'noe_type', 'gid'
        tolerance: Distance max en metres pour le matching spatial (defaut 1m)
        attaches_parsed: Liste de dicts {gid, geom, start, end} depuis
            _parse_attaches_geoms(). Si fourni, les BPE connectes via
            une attache sont aussi detectes.
    
    Returns:
        Dict[num_appui, {boitier_source, bpe_trouve, bpe_noe_type, statut}]
    """
    result = {}
    
    if not boitier_source:
        return result
    
    appuis_by_num = {}
    for appui in appuis_data:
        num = appui.get('num_appui', '')
        if num and appui.get('geom'):
            appuis_by_num[num] = appui['geom']

    # Index spatial sur les BPE: O(log n) matching au lieu de O(appuis × bpes)
    _bpe_idx = QgsSpatialIndex()
    _bpe_id_to_bpe: Dict[int, Dict] = {}
    for _bi, _bpe in enumerate(bpe_geoms):
        _bpe_geom = _bpe.get('geom')
        if _bpe_geom and not _bpe_geom.isNull():
            _bfeat = QgsFeature(_bi)
            _bfeat.setGeometry(_bpe_geom)
            _bpe_idx.addFeature(_bfeat)
            _bpe_id_to_bpe[_bi] = _bpe

    for num_appui, type_boitier in boitier_source.items():
        appui_geom = appuis_by_num.get(num_appui)

        entry = {
            'boitier_source': type_boitier,
            'bpe_trouve': False,
            'bpe_noe_type': '',
            'statut': ''
        }

        if not appui_geom:
            entry['statut'] = 'ERREUR'
            entry['bpe_noe_type'] = 'coordonnees GPS absentes'
            result[num_appui] = entry
            continue

        bpe_proche = None
        dist_min = 999999
        _abbox = appui_geom.boundingBox()
        _abbox.grow(tolerance)
        for _bid in _bpe_idx.intersects(_abbox):
            _bpe = _bpe_id_to_bpe[_bid]
            dist = appui_geom.distance(_bpe['geom'])
            if dist < tolerance and dist < dist_min:
                dist_min = dist
                bpe_proche = _bpe

        if not bpe_proche and attaches_parsed:
            ext_points = _get_attache_extensions(appui_geom, attaches_parsed, tolerance)
            for ext_pt in ext_points:
                _ebbox = ext_pt.boundingBox()
                _ebbox.grow(tolerance)
                for _bid in _bpe_idx.intersects(_ebbox):
                    _bpe = _bpe_id_to_bpe[_bid]
                    dist = ext_pt.distance(_bpe['geom'])
                    if dist < tolerance and dist < dist_min:
                        dist_min = dist
                        bpe_proche = _bpe

        if bpe_proche:
            entry['bpe_trouve'] = True
            entry['bpe_noe_type'] = bpe_proche['noe_type']
            types_attendus = _BOITIER_TYPE_COMPAT.get(type_boitier.upper())
            if types_attendus is None:
                entry['statut'] = 'OK'
            elif bpe_proche['noe_type'].upper() not in types_attendus:
                entry['statut'] = 'ECART'
            else:
                entry['statut'] = 'OK'
        else:
            entry['statut'] = 'ERREUR'
        
        result[num_appui] = entry
    
    return result


@dataclass
class TronconBDD:
    """Troncon reconstruit depuis les segments BDD fddcpi2."""
    cable_gid: int
    cable_ref: str
    capacite_fo: int
    support_depart: str
    support_arrivee: str
    portee_m: float
    source: str = 'BDD'
    confiance: float = 1.0


def reconstituer_portees_bdd(
    cables: List[CableSegment],
    appuis: List[Dict],
    tolerance: float = 0.5,
) -> List[TronconBDD]:
    """Reconstruit les portees (troncons pole-pole) depuis les segments fddcpi2.

    Chaque segment fddcpi2 est decoupe aux poteaux. On identifie le poteau
    de depart et d'arrivee de chaque segment via proximite endpoint-appui.

    Args:
        cables: Segments CDI aeriens/facade depuis fddcpi2
        appuis: Liste de dicts {'num_appui': str, 'geom': QgsGeometry}
        tolerance: Distance max (m) endpoint-appui

    Returns:
        Liste de TronconBDD (un par segment ayant ses deux extremites identifiees)
    """
    if not cables or not appuis:
        return []

    appuis_by_num = {a['num_appui']: a['geom'] for a in appuis
                     if a.get('num_appui') and a.get('geom')}
    if not appuis_by_num:
        return []

    appui_idx = QgsSpatialIndex()
    fid_to_appui: Dict[int, Tuple[str, QgsGeometry]] = {}
    for fid_counter, (num, geom) in enumerate(appuis_by_num.items()):
        if geom and not geom.isNull():
            feat = QgsFeature(fid_counter)
            feat.setGeometry(geom)
            appui_idx.addFeature(feat)
            fid_to_appui[fid_counter] = (num, geom)

    def _nearest_indexed(pt_xy: QgsPointXY, pt_geom: QgsGeometry) -> Optional[str]:
        px, py = pt_xy.x(), pt_xy.y()
        bbox = QgsRectangle(
            px - tolerance, py - tolerance,
            px + tolerance, py + tolerance
        )
        best_name = None
        best_dist = tolerance + 1.0
        for fid in appui_idx.intersects(bbox):
            item = fid_to_appui.get(fid)
            if not item:
                continue
            name, geom = item
            dist = pt_geom.distance(geom)
            if dist <= tolerance and dist < best_dist:
                best_dist = dist
                best_name = name
        return best_name

    troncons = []
    nb_one_end = 0
    nb_no_end = 0

    for cable in cables:
        if getattr(cable, 'cab_type', '') != 'CDI':
            continue
        if getattr(cable, 'posemode', 0) not in (1, 2):
            continue
        wkt = getattr(cable, 'geom_wkt', '')
        if not wkt:
            continue

        cable_geom = QgsGeometry.fromWkt(wkt)
        if cable_geom.isNull() or cable_geom.isEmpty():
            continue

        if cable_geom.isMultipart():
            lines = cable_geom.asMultiPolyline()
            line = lines[0] if lines else []
        else:
            line = cable_geom.asPolyline()
        if len(line) < 2:
            continue

        start_pt = QgsGeometry.fromPointXY(line[0])
        end_pt = QgsGeometry.fromPointXY(line[-1])

        best_start = _nearest_indexed(line[0], start_pt)
        best_end = _nearest_indexed(line[-1], end_pt)

        if best_start and best_end and best_start != best_end:
            troncons.append(TronconBDD(
                cable_gid=getattr(cable, 'gid', 0),
                cable_ref=getattr(cable, 'cb_etiquet', ''),
                capacite_fo=cable.cab_capa,
                support_depart=best_start,
                support_arrivee=best_end,
                portee_m=round(cable_geom.length(), 1),
                source='BDD',
                confiance=1.0,
            ))
        elif best_start or best_end:
            nb_one_end += 1
        else:
            nb_no_end += 1

    QgsMessageLog.logMessage(
        f"reconstituer_portees_bdd: {len(troncons)} troncons reconstitues "
        f"({nb_one_end} avec 1 seul appui, {nb_no_end} sans appui)",
        "PoleAerien", MSG_INFO
    )
    return troncons


def comparer_portees(
    portees_pcm: list,
    troncons_ref: List[TronconBDD],
    tolerance_pct: float = 15.0,
    name_map: Optional[Dict[str, str]] = None,
) -> List[Dict]:
    """Compare les portees PCM avec les troncons reconstruits depuis la BDD.

    Matching par paire de poteaux (depart, arrivee). Les noms PCM sont
    traduits via name_map si fourni (BT0030 -> inf_num QGIS normalise).
    La direction est ignoree (A->B == B->A).

    Args:
        portees_pcm: Liste de PorteePCM depuis pcm_parser.extraire_portees_par_cable()
        troncons_ref: Liste de TronconBDD depuis reconstituer_portees_bdd
        tolerance_pct: Ecart max (%) entre portee PCM et portee ref pour statut OK
        name_map: {nom_pcm_normalise -> inf_num_qgis} pour traduire les noms PCM

    Returns:
        Liste de dicts, un par troncon PCM:
        {etude, cable, capacite_fo, support_depart_pcm, support_arrivee_pcm,
         support_depart_ref, support_arrivee_ref, portee_pcm, portee_ref,
         ecart_m, ecart_pct, confiance_ref, source_ref, statut, message}
    """
    if name_map is None:
        name_map = {}

    try:
        from .core_utils import normalize_appui_num as _norm
    except ImportError:
        from core_utils import normalize_appui_num as _norm

    # Prefixes PCM a striper pour matcher les inf_num QGIS
    _PCM_PREFIXES = ('FT', 'NCBT', 'NC')

    def _strip_pcm_prefix(nom: str) -> str:
        upper = nom.upper()
        for pfx in _PCM_PREFIXES:
            if upper.startswith(pfx) and len(nom) > len(pfx):
                stripped = nom[len(pfx):]
                if stripped[:1].isdigit():
                    return stripped
        return nom

    def _translate(nom: str) -> str:
        norm = _norm(nom)
        if norm in name_map:
            return name_map[norm]
        # Fallback: striper prefixes PCM et retenter
        stripped = _strip_pcm_prefix(norm)
        if stripped != norm:
            stripped_norm = _norm(stripped)
            if stripped_norm in name_map:
                return name_map[stripped_norm]
            return stripped_norm
        return norm

    # Indexer troncons ref par paire de poteaux (direction-agnostic)
    ref_index = {}
    for t in troncons_ref:
        key_fwd = (_translate(t.support_depart), _translate(t.support_arrivee))
        key_rev = (key_fwd[1], key_fwd[0])
        if key_fwd not in ref_index:
            ref_index[key_fwd] = []
        ref_index[key_fwd].append(t)
        if key_rev not in ref_index:
            ref_index[key_rev] = []
        ref_index[key_rev].append(t)

    result = []
    nb_ok = 0
    nb_ecart = 0
    nb_absent = 0

    nb_existant = 0

    for pcm in portees_pcm:
        dep_pcm = pcm.support_depart
        arr_pcm = pcm.support_arrivee
        dep_ref = _translate(dep_pcm)
        arr_ref = _translate(arr_pcm)

        entry = {
            'etude': pcm.etude,
            'cable': pcm.cable,
            'capacite_fo': pcm.capacite_fo,
            'a_poser': pcm.a_poser,
            'support_depart_pcm': dep_pcm,
            'support_arrivee_pcm': arr_pcm,
            'support_depart_ref': dep_ref,
            'support_arrivee_ref': arr_ref,
            'portee_pcm': pcm.portee_m,
            'portee_ref': 0.0,
            'ecart_m': 0.0,
            'ecart_pct': 0.0,
            'confiance_ref': 0.0,
            'source_ref': '',
            'statut': '',
            'message': '',
        }

        # APoser=0 : cable existant (BT/cuivre), pas dans fddcpi2 -> ignore
        if not pcm.a_poser:
            nb_existant += 1
            continue

        key = (dep_ref, arr_ref)
        matches = ref_index.get(key, [])

        if not matches:
            entry['statut'] = 'ABSENT_REF'
            entry['message'] = (
                f"Portee {dep_pcm} vers {arr_pcm} introuvable dans la reference "
                f"({dep_ref} vers {arr_ref})"
            )
            nb_absent += 1
        else:
            # Prendre le match le plus proche en portee
            best = min(matches, key=lambda t: abs(t.portee_m - pcm.portee_m))
            ecart_m = round(best.portee_m - pcm.portee_m, 1)
            ecart_pct = round(
                abs(ecart_m) / pcm.portee_m * 100, 1
            ) if pcm.portee_m > 0 else 0.0

            entry['portee_ref'] = best.portee_m
            entry['ecart_m'] = ecart_m
            entry['ecart_pct'] = ecart_pct
            entry['confiance_ref'] = best.confiance
            entry['source_ref'] = best.source

            if ecart_pct <= tolerance_pct:
                entry['statut'] = 'OK'
                nb_ok += 1
            else:
                entry['statut'] = 'ECART'
                entry['message'] = (
                    f"Ecart de portee : PCM = {pcm.portee_m}m, reference = {best.portee_m}m "
                    f"(difference {ecart_m:+.1f}m, soit {ecart_pct:.1f}%)"
                )
                nb_ecart += 1

        result.append(entry)

    # Troncons ref non matches par aucun PCM a_poser=1
    # (les cables existants APoser=0 ne comptent pas dans la couverture PCM)
    pcm_keys = set()
    for pcm in portees_pcm:
        if not pcm.a_poser:
            continue
        dep = _translate(pcm.support_depart)
        arr = _translate(pcm.support_arrivee)
        pcm_keys.add((dep, arr))
        pcm_keys.add((arr, dep))

    # Compter troncons ref hors perimetre PCM (log uniquement, pas dans le rapport)
    nb_ref_hors_pcm = 0
    ref_seen = set()
    for t in troncons_ref:
        key = (_translate(t.support_depart), _translate(t.support_arrivee))
        key_rev = (key[1], key[0])
        if key not in pcm_keys and key not in ref_seen:
            ref_seen.add(key)
            ref_seen.add(key_rev)
            nb_ref_hors_pcm += 1

    source = troncons_ref[0].source if troncons_ref else '?'
    QgsMessageLog.logMessage(
        f"comparer_portees (PCM vs {source}): "
        f"{nb_ok} OK, {nb_ecart} ecarts, {nb_absent} absents ref, "
        f"{nb_ref_hors_pcm} ref hors perimetre PCM (ignores), "
        f"{nb_existant} existants ignores (tolerance={tolerance_pct}%)",
        "PoleAerien", MSG_INFO
    )
    # Diagnostic: premiers ecarts pour comprendre les valeurs
    ecarts_sample = [e for e in result if e.get('statut') == 'ECART'][:10]
    for e in ecarts_sample:
        QgsMessageLog.logMessage(
            f"  [ECART] {e['support_depart_pcm']}->{e['support_arrivee_pcm']} "
            f"(ref: {e['support_depart_ref']}->{e['support_arrivee_ref']}): "
            f"PCM={e['portee_pcm']}m, Ref={e['portee_ref']}m, "
            f"ecart={e['ecart_m']:+.1f}m ({e['ecart_pct']:.1f}%), "
            f"conf={e['confiance_ref']}",
            "PoleAerien", MSG_INFO
        )
    absents_sample = [e for e in result if e.get('statut') == 'ABSENT_REF'][:5]
    for e in absents_sample:
        QgsMessageLog.logMessage(
            f"  [ABSENT_REF] {e['support_depart_pcm']}->{e['support_arrivee_pcm']} "
            f"(traduit: {e['support_depart_ref']}->{e['support_arrivee_ref']}): "
            f"PCM={e['portee_pcm']}m, cable={e['cable']}",
            "PoleAerien", MSG_INFO
        )
    return result


def _find_nearest_appui(
    point_geom: QgsGeometry,
    appui_list: List[Tuple[str, QgsGeometry]],
    tolerance: float,
) -> Optional[str]:
    """Trouve l'appui le plus proche d'un point, dans la tolerance."""
    best_name = None
    best_dist = tolerance + 1.0
    for name, geom in appui_list:
        dist = point_geom.distance(geom)
        if dist <= tolerance and dist < best_dist:
            best_dist = dist
            best_name = name
    return best_name


def collect_anomaly_cables(
    comparaison: List[Dict],
    cables_par_appui: Dict[str, Dict],
    cables: List[CableSegment],
    etude: str = ''
) -> List[Dict]:
    """Collecte les segments fddcpi2 impliques dans des anomalies Police C6.

    Pour chaque appui en ECART, recupere les cables qui le touchent
    et les enrichit avec les metadonnees d'anomalie pour export spatial.

    Args:
        comparaison: Resultat de comparer_source_cables / comparer_c6_cables
        cables_par_appui: Resultat de compter_cables_par_appui
        cables: Liste originale de CableSegment (pour geometrie WKT)
        etude: Nom de l'etude pour attribution

    Returns:
        Liste de dicts serialisables (thread-safe) avec geometrie + anomalie
    """
    cables_by_id = {c.gid_dc2: c for c in cables}

    anomaly_cables = []
    seen_keys = set()

    for entry in comparaison:
        statut = entry.get('statut', '')
        if statut == 'OK':
            continue

        num_appui = entry.get('num_appui', '')
        appui_data = cables_par_appui.get(num_appui, {})
        message = entry.get('message', '')

        # Classifier le type d'ecart
        has_count = 'nombre' in message.lower()
        has_capa = 'capacit' in message.lower()
        if has_count and has_capa:
            type_anom = 'NOMBRE+CAPACITE'
        elif has_count:
            type_anom = 'NOMBRE'
        elif has_capa:
            type_anom = 'CAPACITE'
        else:
            type_anom = statut

        for cable_info in appui_data.get('cables', []):
            gid_dc2 = cable_info.get('id', 0)
            key = (gid_dc2, num_appui)
            if key in seen_keys:
                continue
            seen_keys.add(key)

            cable_seg = cables_by_id.get(gid_dc2)
            if not cable_seg or not cable_seg.geom_wkt:
                continue

            nb_c6 = entry.get('nb_cables_c6', entry.get('nb_cables_comac', 0))

            anomaly_cables.append({
                'gid_dc2': gid_dc2,
                'gid_dc': cable_seg.gid_dc,
                'etude': etude,
                'num_appui': num_appui,
                'type_anomalie': type_anom,
                'nb_cables_c6': nb_c6,
                'nb_cables_bdd': entry.get('nb_cables_bdd', 0),
                'message': message,
                'cab_capa': cable_seg.cab_capa,
                'cb_etiquet': cable_seg.cb_etiquet,
                'length': cable_seg.length,
                'geom_wkt': cable_seg.geom_wkt,
            })

    return anomaly_cables


def write_ecart_log(
    verif_cables: List[Dict],
    export_dir: str,
    sro: str = '',
    source_label: str = 'COMAC'
) -> str:
    """Écrit un fichier .log détaillé des écarts câbles dans le dossier export.

    Pour chaque ECART, journalise:
    - Les références source brutes et déduplicuées
    - Les capacités possibles COMAC
    - Les câbles BDD avec leur code (cb_etiquet), capacité, GID
    - Un diagnostic automatique (code BDD = code COMAC ? capacité BDD dans plage ?)

    Après la liste individuelle, analyse les patterns systématiques:
    - Même GID BDD apparaissant sur N appuis → même câble physique
    - Même code BDD vs même COMAC ref → mapping capacité suspect ou BDD erronée

    Returns:
        Chemin du fichier log créé, ou '' en cas d'échec.
    """
    import os
    import datetime
    from collections import defaultdict

    if not export_dir or not os.path.isdir(export_dir):
        return ''

    ecarts = [e for e in verif_cables if e.get('statut') == 'ECART']
    if not ecarts:
        return ''

    sro_safe = (sro or 'inconnu').replace('/', '_')
    ts = datetime.datetime.now().strftime('%Y%m%d_%H%M%S')
    log_path = os.path.join(export_dir, f'comac_ecarts_{sro_safe}_{ts}.log')

    lines = []
    lines.append('=' * 80)
    lines.append(f'RAPPORT ECARTS CABLES {source_label} vs BDD')
    lines.append(f'SRO  : {sro or "inconnu"}')
    lines.append(f'Date : {datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")}')
    lines.append(f'Total: {len(ecarts)} écarts sur {len(verif_cables)} appuis analysés')
    lines.append('=' * 80)
    lines.append('')

    # --- Analyse systématique par GID BDD ---
    # Pour chaque GID BDD: liste (appui, capa_bdd, ref_comac, code_bdd)
    gid_occurrences: dict = defaultdict(list)
    for entry in ecarts:
        for cable in entry.get('_bdd_cables_raw', []):
            gid = cable.get('gid', 0)
            if gid:
                gid_occurrences[gid].append({
                    'appui': entry['num_appui'],
                    'capa_bdd': cable.get('capacite', 0),
                    'code_bdd': cable.get('cb_etiquet', '') or '?',
                    'refs_comac': entry.get('_refs_dedup', []),
                })

    # GIDs présents dans 3+ appuis : pattern systématique
    systemic_gids = {gid: occ for gid, occ in gid_occurrences.items() if len(occ) >= 3}

    # --- Détail par appui ---
    lines.append('DETAIL PAR APPUI')
    lines.append('-' * 80)
    for entry in sorted(ecarts, key=lambda e: e['num_appui']):
        appui = entry['num_appui']
        refs_brut = entry.get('_refs_brut', [])
        refs_dedup = entry.get('_refs_dedup', [])
        capas_poss = entry.get('_capas_possibles', [])
        bdd_cables = entry.get('_bdd_cables_raw', [])
        bdd_count = entry.get('nb_cables_bdd', 0)
        src_count = len(refs_dedup)
        message = entry.get('message', '')

        lines.append(f'APPUI: {appui}')
        lines.append(f'  Statut : {message}')
        lines.append(f'  {source_label} refs brut : {refs_brut}')
        if refs_brut != refs_dedup:
            lines.append(f'  {source_label} refs dedup: {refs_dedup}')
        for ref, capas in zip(refs_dedup, capas_poss):
            lines.append(f'    {ref} -> capacites possibles: {capas}')
        lines.append(f'  BDD count={bdd_count}  {source_label} count={src_count}')

        for cable in bdd_cables:
            gid = cable.get('gid', 0)
            capa = cable.get('capacite', 0)
            code = cable.get('cb_etiquet', '') or '?'
            cab_type = cable.get('cab_type', '') or '?'
            dc2 = cable.get('id', 0)

            # Diagnostic: le code BDD est-il une des refs COMAC ?
            code_in_comac = code in refs_dedup
            # La capa BDD est-elle dans une des plages COMAC ?
            capa_in_range = any(capa in cp for cp in capas_poss) if capas_poss else False
            # GID systématique ?
            is_systemic = gid in systemic_gids

            diag_parts = []
            if code_in_comac:
                diag_parts.append('code BDD=ref COMAC (meme cable)')
            else:
                diag_parts.append(f'code BDD "{code}" != refs COMAC {refs_dedup}')
            if capa_in_range:
                diag_parts.append('capa dans plage COMAC OK')
            else:
                diag_parts.append(f'capa {capa} HORS plage {["/".join(str(x) for x in cp) for cp in capas_poss]}')
            if is_systemic:
                n = len(systemic_gids[gid])
                diag_parts.append(f'GID {gid} = pattern systematique ({n} appuis)')

            lines.append(f'  BDD: gid={gid} dc2={dc2} capa={capa} type={cab_type} code="{code}"')
            lines.append(f'       DIAG: {" | ".join(diag_parts)}')
        lines.append('')

    # --- Patterns systématiques ---
    if systemic_gids:
        lines.append('')
        lines.append('PATTERNS SYSTEMATIQUES (meme GID BDD >= 3 appuis)')
        lines.append('-' * 80)
        for gid, occ in sorted(systemic_gids.items(), key=lambda x: -len(x[1])):
            code_bdd = occ[0]['code_bdd']
            capa_bdd = occ[0]['capa_bdd']
            comac_refs = list({r for o in occ for r in o['refs_comac']})
            appuis_sample = [o['appui'] for o in occ[:10]]
            lines.append(f'GID {gid}: {len(occ)} appuis | code_bdd="{code_bdd}" | capa_bdd={capa_bdd}')
            lines.append(f'  COMAC refs associees: {sorted(comac_refs)}')
            lines.append(f'  Appuis (sample): {appuis_sample}')
            # Diagnostic
            if code_bdd != '?':
                from .security_rules import get_capacites_possibles
                capas_for_code = get_capacites_possibles(code_bdd) or []
                code_connu = bool(capas_for_code)
                if not code_connu:
                    lines.append(f'  DIAGNOSTIC: code BDD "{code_bdd}" non reconnu par get_capacites_possibles')
                    lines.append(f'  => Code troncon NGE (pas code cable Prysmian) - verification manuelle requise')
                    lines.append(f'  => Question: le cable GID {gid} (capa={capa_bdd}) correspond-il a la ref COMAC {comac_refs} ?')
                elif capa_bdd in capas_for_code:
                    lines.append(f'  DIAGNOSTIC: capa BDD {capa_bdd} COHERENTE avec code "{code_bdd}" ({capas_for_code})')
                    lines.append(f'  => Mapping COMAC->capa incorrect pour {comac_refs}? (ou mauvaise ref COMAC dans Excel)')
                else:
                    lines.append(f'  DIAGNOSTIC: capa BDD {capa_bdd} INCOHERENTE avec code "{code_bdd}" ({capas_for_code})')
                    lines.append(f'  => Capacite saisie incorrectement en BDD pour GID {gid}')
            lines.append('')

    # --- Résumé ---
    lines.append('RESUME')
    lines.append('-' * 80)
    lines.append(f'Ecarts COUNT seuls     : {sum(1 for e in ecarts if "Écart nombre" in e.get("message","") and "Écart capacité" not in e.get("message",""))}')
    lines.append(f'Ecarts CAPA seuls      : {sum(1 for e in ecarts if "Écart capacité" in e.get("message","") and "Écart nombre" not in e.get("message",""))}')
    lines.append(f'Ecarts COUNT+CAPA      : {sum(1 for e in ecarts if "Écart nombre" in e.get("message","") and "Écart capacité" in e.get("message",""))}')
    lines.append(f'GIDs systematiques (>=3): {len(systemic_gids)} GIDs BDD ({sum(len(v) for v in systemic_gids.values())} appuis)')
    lines.append('=' * 80)

    try:
        with open(log_path, 'w', encoding='utf-8') as f:
            f.write('\n'.join(lines))
        return log_path
    except OSError:
        return ''
