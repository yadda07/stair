# Youcef ADDA | GEODECI
# FR : Plugins QGIS, solutions SIG et administration PostgreSQL/PostGIS ; audit, automatisation et accompagnement.
# EN : QGIS plugins, GIS solutions and PostgreSQL/PostGIS administration; audits, workflow automation and business support.
# AR : تطوير إضافات QGIS وحلول نظم المعلومات الجغرافية وإدارة PostgreSQL/PostGIS، والتدقيق والأتمتة ومرافقة المؤسسات.
# Site / Website / الموقع : https://geodeci.xyz/
# Contact professionnel / Business enquiries / للتواصل المهني : youcef.geodesien@gmail.com | contact@geodeci.xyz
# Détails / Details / التفاصيل : SIGNATURE.md
# ============================================================================
"""
Pipeline de decoupage de cables aeriens pour sous-traitants (STAIR).

Porte en Python la fonction SQL `rip_avg_nge.fddcpiax` en version
simplifiee pour le livrable sous-traitant aerien :
- entree : 1 GeoPackage par SRO avec couches `cables`, `bpe`, `attaches`, `infra_pt_pot`
- sortie : couche `cables_decoupes` (segments atomiques entre 2 appuis)

Les chambres (souterrain) et `infra_pt_autres` (facades/adresses) sont
hors perimetre du livrable aerien.

Voir : README.md du dossier pour l'algorithme et les regles metier.
"""
from .dataclasses import (
    CableInput,
    PoteauInput,
    BpeInput,
    AttacheInput,
    CableSegment,
    SplitStats,
)
from .pipeline import split_cables_from_gpkg
from .zip_loader import (
    extract_gpkg_from_zip,
    extract_gpkg_tempdir,
    list_gpkg_in_zip,
    load_gpkg_layers_into_qgis,
)
from .loader import (
    GpkgCaches,
    load_caches_from_gpkg,
    load_caches_from_zip,
)

__all__ = [
    "CableInput",
    "PoteauInput",
    "BpeInput",
    "AttacheInput",
    "CableSegment",
    "SplitStats",
    "split_cables_from_gpkg",
    "extract_gpkg_from_zip",
    "extract_gpkg_tempdir",
    "list_gpkg_in_zip",
    "load_gpkg_layers_into_qgis",
    "GpkgCaches",
    "load_caches_from_gpkg",
    "load_caches_from_zip",
]
