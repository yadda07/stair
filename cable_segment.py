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
Dataclass CableSegment : format pivot des cables decoupes.

Ce dataclass servait a transporter les resultats de l'ancienne fonction SQL
`fddcpiax` vers les modules d'analyse (Comac, PoliceC6, cable_analyzer...).
Depuis la suppression de la BDD PostgreSQL, il sert toujours de contrat
pivot mais les segments sont desormais produits par le pipeline Python
`gpkg_pipeline` a partir d'un GeoPackage.

Les champs sont conserves identiques pour maintenir la compatibilite avec
tous les consommateurs existants (champs presents dans le livrable GPKG
ou vides si non fournis).
"""
from dataclasses import dataclass


@dataclass
class CableSegment:
    """Segment de cable decoupe par l'infrastructure.

    Format issu historiquement de `fddcpi2`/`fddcpiax`, maintenant produit
    par le pipeline Python `gpkg_pipeline` a partir d'un GeoPackage.

    Tous les champs ont des valeurs par defaut pour supporter un livrable
    GeoPackage partiel : seuls `gid`, `sro`, `cab_capa`, `posemode`,
    `geom_wkt` sont strictement necessaires aux consommateurs.
    """
    gid_dc2: int = 0          # ID segment unique
    gid_dc: int = 0           # ID cable original
    gid: int = 0              # GID de reference
    sro: str = ''
    nro: str = ''
    length: float = 0.0
    cab_type: str = ''
    cab_capa: int = 0         # Capacite FO
    cab_modulo: int = 0
    isole: str = ''
    date_modif: str = ''
    modif_par: str = ''
    cab_nature: str = ''
    commentaire: str = ''
    collecte: str = ''
    cb_etiquet: str = ''
    fon: str = ''
    projet: str = ''
    dce: str = ''
    dist_type: str = ''
    affectation: str = ''
    posemode: int = 0         # 0=souterrain, 1=aerien, 2=facade, 4=orphelin
    geom_wkt: str = ''        # Geometrie en WKT
