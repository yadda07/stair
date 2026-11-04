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
Fonctions utilitaires dépendantes de QGIS pour le plugin PoleAerien.
ATTENTION: Ne pas importer dans les workers threads sans précaution.
"""

from typing import Optional

from qgis.core import (
    QgsProject, QgsLayerTreeLayer, QgsMessageLog, Qgis, 
    QgsSpatialIndex, QgsFeatureRequest, NULL
)
from .compat import MSG_INFO, MSG_WARNING, FR_NO_GEOMETRY
import re
from .core_utils import normalize_appui_num


# -----------------------------------------------------------------------------
# Extraction SRO depuis une couche (migre depuis db_connection.py apres
# suppression du module BDD). Cette fonction est agnostique de la source :
# elle cherche le SRO dans les attributs de la couche, peu importe si la
# couche vient d'un GeoPackage, d'un Shapefile ou d'une BDD.
# -----------------------------------------------------------------------------

_SRO_FIELD_CANDIDATES = ['sro', 'SRO', 'code_sro', 'CODE_SRO', 'pt_ad_sro', 'PT_AD_SRO']


def extract_sro_from_layer(layer) -> Optional[str]:
    """Extrait le code SRO depuis les attributs d'une couche QGIS.

    Parcourt une liste de noms de champs candidats et retourne la premiere
    valeur non vide rencontree. Ne depend d'aucune BDD.

    Args:
        layer: QgsVectorLayer

    Returns:
        Code SRO ou None.
    """
    if not layer or not layer.isValid():
        return None

    fields = layer.fields()
    for sro_field in _SRO_FIELD_CANDIDATES:
        idx_sro = fields.indexFromName(sro_field)
        if idx_sro < 0:
            continue
        req = (
            QgsFeatureRequest()
            .setFilterExpression(f'"{sro_field}" IS NOT NULL AND "{sro_field}" != \'\'')
            .setSubsetOfAttributes([idx_sro])
            .setLimit(1)
            .setFlags(FR_NO_GEOMETRY)
        )
        for feature in layer.getFeatures(req):
            value = feature[idx_sro]
            if value and str(value).strip():
                return str(value).strip()
    return None


# Patterns pour auto-detection du champ etude dans une couche
_ETUDE_POLYGON_BUFFER_M = 0.5

_ETUDE_FIELD_PATTERNS = [
    r'^nom[_\s]?etude[s]?$',
    r'^etude[s]?$',
    r'^ref_fci$',
    r'^name$',
    r'^nom$',
    r'^decoupage$',
    r'^zone$',
]


def _index_from_names(layer, candidate_names, context=""):
    """Retourne l'index du premier champ trouve parmi plusieurs alias.

    Utile pour resoudre les noms tronques DBF (10 chars) vs noms PostgreSQL
    complets sur la meme couche logique.

    Args:
        layer: QgsVectorLayer
        candidate_names: iterable de noms de champs a essayer dans l'ordre
        context: Contexte pour message de log

    Returns:
        int: index du champ trouve, ou -1 si aucun.
    """
    if not layer:
        return -1
    fields = layer.fields()
    for name in candidate_names:
        idx = fields.indexFromName(name)
        if idx >= 0:
            return idx
    QgsMessageLog.logMessage(
        f"[{context or '_index_from_names'}] Aucun champ trouve parmi "
        f"{list(candidate_names)}. Champs disponibles: "
        f"{[f.name() for f in fields]}",
        "PoleAerien", MSG_WARNING
    )
    return -1


def detect_etude_field(layer, context=""):
    """Auto-detecte le champ etude dans une couche vectorielle.
    
    Teste les champs de la couche contre une liste de patterns regex
    ordonnee par priorite.
    
    Args:
        layer: QgsVectorLayer
        context: Contexte pour message de log
        
    Returns:
        str: Nom du champ detecte ou None
    """
    if not layer or not layer.isValid():
        return None
    
    field_names = [f.name() for f in layer.fields()]
    
    # ECART-09: les deux derniers patterns ('^nom$', '^zone$') sont des
    # catchalls ambigus qui peuvent matcher des champs non-etude (ex. `nom`
    # sur APPUIS.shp FIBEES = numero d'appui). On loggue un warning quand
    # ces patterns se declenchent pour tracer l'ambiguite.
    _CATCHALL_PATTERNS = {r'^nom$', r'^name$', r'^zone$', r'^decoupage$'}

    for pattern in _ETUDE_FIELD_PATTERNS:
        for field_name in field_names:
            if re.match(pattern, field_name, re.IGNORECASE):
                if pattern in _CATCHALL_PATTERNS:
                    QgsMessageLog.logMessage(
                        f"[{context or 'detect_etude_field'}] Champ etude "
                        f"detecte via pattern catchall '{pattern}' -> "
                        f"'{field_name}'. Verifier qu'il s'agit bien d'un "
                        f"nom d'etude et non d'un autre usage.",
                        "PoleAerien", MSG_WARNING
                    )
                return field_name

    QgsMessageLog.logMessage(
        f"[{context or 'detect_etude_field'}] Aucun champ etude detecte. Champs: {field_names}",
        "PoleAerien", MSG_WARNING
    )
    return None


def remove_group(name):
    """Suppression d'un groupe de couches par son nom.
    
    Args:
        name: Nom du groupe à supprimer
    """
    root = QgsProject.instance().layerTreeRoot()
    group = root.findGroup(name)
    if group is not None:
        for child in group.children():
            if hasattr(child, 'layerId'):
                QgsProject.instance().removeMapLayer(child.layerId())
        root.removeChildNode(group)


def layer_group_error(couche, nom_etude):
    """Ajoute une couche dans un groupe d'erreur.
    
    Args:
        couche: La couche QGIS à ajouter
        nom_etude: Nom de l'étude pour nommer le groupe
    """
    root = QgsProject.instance().layerTreeRoot()
    group_name = f"ERROR_{nom_etude}"
    group = root.findGroup(group_name)
    
    if not group:
        group = root.addGroup(group_name)
    
    group.insertChildNode(1, QgsLayerTreeLayer(couche))


def insert_layer_in_group(couche, group_name):
    """Ajoute une couche dans un groupe spécifié.
    
    Args:
        couche: La couche QGIS à ajouter
        group_name: Nom du groupe
    """
    root = QgsProject.instance().layerTreeRoot()
    group = root.findGroup(group_name)
    if not group:
        group = root.addGroup(group_name)
    group.insertChildNode(1, QgsLayerTreeLayer(couche))


def show_feature_count(layer):
    """Active 'Afficher le nombre d'entites' dans le panneau couches QGIS."""
    root = QgsProject.instance().layerTreeRoot()
    node = root.findLayer(layer.id())
    if node:
        node.setCustomProperty("showFeatureCount", True)


def get_layer_safe(layer_name, context=""):
    """Récupère une couche QGIS de manière sécurisée.
    
    Args:
        layer_name: Nom de la couche à récupérer
        context: Contexte pour message d'erreur (ex: "MAJ FT/BT")
        
    Returns:
        QgsVectorLayer valide
        
    Raises:
        ValueError: Si la couche n'existe pas, n'est pas chargée ou invalide
    """
    if not layer_name:
        raise ValueError(f"[{context}] Nom de couche vide ou None")
    
    layers = QgsProject.instance().mapLayersByName(layer_name)
    if not layers:
        raise ValueError(
            f"[{context}] Couche '{layer_name}' introuvable. "
            "Vérifiez qu'elle est chargée dans le projet."
        )
    
    layer = layers[0]
    if not layer.isValid():
        raise ValueError(
            f"[{context}] Couche '{layer_name}' invalide. "
            "Source de données inaccessible."
        )
    return layer


_crs_validated_pairs = set()


def reset_crs_cache():
    """Vide le cache CRS. Appeler en debut de batch."""
    _crs_validated_pairs.clear()


def validate_same_crs(ref_layer, other_layer, context=""):
    """Valide que deux couches partagent le meme CRS.

    Resultat cache par paire (layer_id, layer_id) pour eviter
    ~8 validations redondantes par batch sur les memes couches.

    Args:
        ref_layer: Couche de reference
        other_layer: Couche a comparer
        context: Contexte pour message d'erreur

    Raises:
        ValueError: CRS differents
    """
    if ref_layer is None or other_layer is None:
        return
    pair = (ref_layer.id(), other_layer.id())
    if pair in _crs_validated_pairs:
        return
    if ref_layer.crs() != other_layer.crs():
        ref_crs = ref_layer.crs().authid() or ref_layer.crs().toWkt()
        other_crs = other_layer.crs().authid() or other_layer.crs().toWkt()
        raise ValueError(
            f"[{context}] CRS incoherent: {ref_layer.name()}={ref_crs} vs "
            f"{other_layer.name()}={other_crs}. "
            f"Reprojeter la couche en {ref_crs} avant analyse (ex. outil "
            f"Reprojeter une couche de QGIS, ou passer par un export "
            f"shapefile reprojetes)."
        )
    _crs_validated_pairs.add(pair)


def build_spatial_index(layer, request=None):
    """Construit index spatial + cache features.
    
    Args:
        layer: QgsVectorLayer source
        request: QgsFeatureRequest optionnel pour filtrer
        
    Returns:
        tuple: (QgsSpatialIndex, dict{fid: QgsFeature})
    """
    idx = QgsSpatialIndex()
    cache = {}
    feats = layer.getFeatures(request) if request else layer.getFeatures()
    
    for feat in feats:
        if feat.hasGeometry():
            idx.addFeature(feat)
            cache[feat.id()] = feat
    
    return idx, cache


def extraire_poteaux_etude(
    table_poteau, table_etude, colonne_etude,
    pot_type_filter, context, keep_commune=False
):
    """Extraction complete poteaux/etude en une seule passe.
    
    Fusionne verifications (doublons, hors_etude) et listing (poteaux par etude,
    terrains prives) en un seul acces couche, un seul index spatial, une seule
    iteration des features.
    
    Args:
        table_poteau: Nom couche poteaux
        table_etude: Nom couche etudes
        colonne_etude: Champ nom etude
        pot_type_filter: 'POT-FT' ou 'POT-BT'
        context: Contexte erreur
        keep_commune: Si True, conserve /commune dans all_inf_nums (pour COMAC)
    
    Returns:
        tuple: (doublons_etudes, poteaux_hors_etude, dict_poteaux_par_etude, dict_poteaux_prives)
        
    Raises:
        ValueError: Couche introuvable ou CRS incoherent
    """
    from .security_rules import est_terrain_prive

    infra_pt_pot = get_layer_safe(table_poteau, context)
    etude = get_layer_safe(table_etude, context)
    validate_same_crs(infra_pt_pot, etude, context)

    total_couche = infra_pt_pot.featureCount()
    idx_inf_num = infra_pt_pot.fields().indexFromName('inf_num')
    idx_commentaire = _index_from_names(
        infra_pt_pot, ('commentaire', 'commentair'), context
    )
    pot_attrs = [i for i in (idx_inf_num, idx_commentaire) if i >= 0]

    req_pot = QgsFeatureRequest()
    if '%' in pot_type_filter:
        req_pot.setFilterExpression(f"inf_type LIKE '{pot_type_filter}'")
    else:
        req_pot.setFilterExpression(f"inf_type = '{pot_type_filter}'")
    if pot_attrs:
        req_pot.setSubsetOfAttributes(pot_attrs)

    idx_etude = etude.fields().indexFromName(colonne_etude)
    req_etude = QgsFeatureRequest()
    if idx_etude >= 0:
        req_etude.setSubsetOfAttributes([idx_etude])
    req_etude.setOrderBy(QgsFeatureRequest.OrderBy(
        [QgsFeatureRequest.OrderByClause(colonne_etude, ascending=True)]))

    # Index spatial poteaux (une seule construction)
    idx_pot, poteaux_dict = build_spatial_index(infra_pt_pot, req_pot)

    QgsMessageLog.logMessage(
        f"[{context}] {len(poteaux_dict)}/{total_couche} poteaux {pot_type_filter} "
        f"dans {etude.featureCount()} zones etude",
        "PoleAerien", MSG_INFO
    )

    # Collecter tous les fid trouves dans au moins une etude
    fids_dans_etude = set()

    dico_etude = {}
    dico_prives = {}
    # Detection des doublons en une seule passe (ECART-07).
    doublons = []
    _noms_vus = set()

    for feat_etude in etude.getFeatures(req_etude):
        nom_etude = feat_etude[idx_etude] if idx_etude >= 0 else feat_etude[colonne_etude]

        # Doublon de nom d'etude (independant de la geometrie).
        if nom_etude in _noms_vus:
            if nom_etude not in doublons:
                doublons.append(nom_etude)
        else:
            _noms_vus.add(nom_etude)

        if not feat_etude.hasGeometry():
            continue

        liste_pot = []
        liste_priv = []
        geom_etude = feat_etude.geometry().buffer(_ETUDE_POLYGON_BUFFER_M, 5)
        bbox = geom_etude.boundingBox()

        for fid in idx_pot.intersects(bbox):
            feat_pot = poteaux_dict[fid]
            if geom_etude.contains(feat_pot.geometry()):
                raw_inf = feat_pot[idx_inf_num] if idx_inf_num >= 0 else feat_pot["inf_num"]
                if raw_inf and raw_inf != NULL:
                    liste_pot.append(raw_inf)
                    fids_dans_etude.add(fid)
                    if idx_commentaire >= 0 and est_terrain_prive(feat_pot[idx_commentaire]):
                        liste_priv.append(raw_inf)

        if liste_pot:
            dico_etude.setdefault(nom_etude, []).extend(liste_pot)
        if liste_priv:
            dico_prives.setdefault(nom_etude, []).extend(liste_priv)

    # Passe unique: poteaux hors etude + index inf_num + coordonnees
    hors_etude = []
    all_inf_nums = set()
    coords_qgis = {}
    for fid, feat_pot in poteaux_dict.items():
        raw_inf = feat_pot[idx_inf_num] if idx_inf_num >= 0 else feat_pot["inf_num"]
        if not raw_inf or raw_inf == NULL:
            continue
        if fid not in fids_dans_etude:
            hors_etude.append(raw_inf)
        cle = normalize_appui_num(raw_inf, strip_e_prefix=True,
                                  keep_commune=keep_commune)
        if cle:
            all_inf_nums.add(cle)
        if feat_pot.hasGeometry():
            pt = feat_pot.geometry().asPoint()
            coords_qgis[raw_inf] = (pt.x(), pt.y())

    total_pot_in = sum(len(v) for v in dico_etude.values())
    QgsMessageLog.logMessage(
        f"[{context}] RESULTAT: {len(dico_etude)} etudes avec poteaux, "
        f"{total_pot_in} poteaux dans etudes, {len(hors_etude)} hors etude, "
        f"{len(all_inf_nums)} inf_num uniques dans couche, "
        f"{len(coords_qgis)} poteaux avec coordonnees, "
        f"{len(dico_prives)} etudes avec terrains prives, {len(doublons)} doublons",
        "PoleAerien", MSG_INFO
    )

    return doublons, hors_etude, dico_etude, dico_prives, all_inf_nums, coords_qgis

