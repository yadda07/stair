# Youcef ADDA | GEODECI
# FR : Plugins QGIS, solutions SIG et administration PostgreSQL/PostGIS ; audit, automatisation et accompagnement.
# EN : QGIS plugins, GIS solutions and PostgreSQL/PostGIS administration; audits, workflow automation and business support.
# AR : تطوير إضافات QGIS وحلول نظم المعلومات الجغرافية وإدارة PostgreSQL/PostGIS، والتدقيق والأتمتة ومرافقة المؤسسات.
# Site / Website / الموقع : https://geodeci.xyz/
# Contact professionnel / Business enquiries / للتواصل المهني : youcef.geodesien@gmail.com | contact@geodeci.xyz
# Détails / Details / التفاصيل : SIGNATURE.md
# ============================================================================
"""
Orchestrateur du pipeline : GeoPackage -> cables_decoupes dans GeoPackage.

Usage typique (plugin QGIS) :

    from PoleAerien_shp.gpkg_pipeline import split_cables_from_gpkg

    stats = split_cables_from_gpkg(
        input_gpkg="sro_XYZ.gpkg",
        output_gpkg="sro_XYZ_decoupes.gpkg",  # None = ecrit dans input
        tolerance=0.70,
    )
    print(stats.as_dict())

Entree : GeoPackage contenant les couches obligatoires :
    - cables          (LINESTRING)
    - bpe             (POINT)
    - attaches        (LINESTRING)
    - infra_pt_pot    (POINT)
Optionnelles (non utilisees pour le decoupage, simple copie vers sortie) :
    - etude_cap_ft, etude_comac

Sortie : ajout/remplacement de la couche `cables_decoupes` dans le GPKG cible.
"""
from __future__ import annotations

import shutil
from pathlib import Path
from typing import List, Optional

from .dataclasses import (
    AttacheInput,
    BpeInput,
    CableInput,
    CableSegment,
    DEFAULT_MIN_SEGMENT_LENGTH_M,
    DEFAULT_SNAP_TOLERANCE_M,
    PoteauInput,
    SplitStats,
)
from .gpkg_io import (
    iter_features,
    list_layers,
    write_layer,
)
from .splitter import split_cables


# -----------------------------------------------------------------------------
# Chargement
# -----------------------------------------------------------------------------


REQUIRED_LAYERS = ("cables", "bpe", "attaches", "infra_pt_pot")


def _require_layers(gpkg_path: str) -> None:
    """Verifie que toutes les couches obligatoires sont presentes."""
    layers = set(list_layers(gpkg_path))
    missing = [lyr for lyr in REQUIRED_LAYERS if lyr not in layers]
    if missing:
        raise ValueError(
            f"Couches manquantes dans {gpkg_path} : {missing}. "
            f"Couches presentes : {sorted(layers)}"
        )


def _load_cables(gpkg_path: str) -> List[CableInput]:
    """Charge la couche `cables` en CableInput[]."""
    out: List[CableInput] = []
    for attrs, geom in iter_features(gpkg_path, "cables"):
        gid = attrs.pop("gid", None) or attrs.pop("fid", None)
        sro = attrs.pop("sro", "") or ""
        if gid is None:
            continue
        out.append(CableInput(
            gid=int(gid), sro=str(sro), wkb=bytes(geom.wkb), attrs=attrs,
        ))
    return out


def _load_poteaux(gpkg_path: str) -> List[PoteauInput]:
    out: List[PoteauInput] = []
    for attrs, geom in iter_features(gpkg_path, "infra_pt_pot"):
        gid = attrs.get("gid") or attrs.get("fid")
        if gid is None:
            continue
        out.append(PoteauInput(
            gid=int(gid),
            sro=str(attrs.get("sro") or ""),
            wkb=bytes(geom.wkb),
            inf_type=attrs.get("inf_type"),
            inf_num=attrs.get("inf_num"),
        ))
    return out


def _load_bpes(gpkg_path: str) -> List[BpeInput]:
    out: List[BpeInput] = []
    for attrs, geom in iter_features(gpkg_path, "bpe"):
        gid = attrs.get("gid") or attrs.get("fid")
        if gid is None:
            continue
        out.append(BpeInput(
            gid=int(gid),
            sro=str(attrs.get("sro") or ""),
            wkb=bytes(geom.wkb),
            noe_type=attrs.get("noe_type"),
            noe_codext=attrs.get("noe_codext"),
        ))
    return out


def _load_attaches(gpkg_path: str) -> List[AttacheInput]:
    out: List[AttacheInput] = []
    for attrs, geom in iter_features(gpkg_path, "attaches"):
        gid = attrs.get("gid") or attrs.get("fid")
        if gid is None:
            continue
        out.append(AttacheInput(
            gid=int(gid),
            sro=str(attrs.get("sro") or ""),
            wkb=bytes(geom.wkb),
            noe_usage=attrs.get("noe_usage"),
        ))
    return out


# -----------------------------------------------------------------------------
# Ecriture
# -----------------------------------------------------------------------------


def _segment_to_row(seg: CableSegment) -> dict:
    """Convertit un CableSegment en dict compatible write_layer."""
    from shapely import wkb as shp_wkb
    row = {
        "gid": seg.gid,
        "gid_dc": seg.gid_dc,
        "sro": seg.sro,
        "length": seg.length,
        "start_ref": seg.start_ref,
        "end_ref": seg.end_ref,
        "posemode": seg.posemode,
        "geom": shp_wkb.loads(seg.wkb),
    }
    for k, v in seg.attrs.items():
        if k not in row and isinstance(v, (int, float, str, bool, type(None))):
            row[k] = v
    return row


def _write_output(
    gpkg_path: str,
    segments: List[CableSegment],
    layer_name: str = "cables_decoupes",
) -> int:
    """Ecrit les segments dans la couche cible du GPKG."""
    rows = [_segment_to_row(s) for s in segments]
    return write_layer(
        gpkg_path, layer_name, rows,
        geom_column="geom", geometry_type="LINESTRING",
        replace=True,
    )


# -----------------------------------------------------------------------------
# Fonction principale
# -----------------------------------------------------------------------------


def split_cables_from_gpkg(
    input_gpkg: str,
    output_gpkg: Optional[str] = None,
    tolerance: float = DEFAULT_SNAP_TOLERANCE_M,
    min_segment_length: float = DEFAULT_MIN_SEGMENT_LENGTH_M,
    output_layer: str = "cables_decoupes",
    sro: str = "",
) -> SplitStats:
    """Decoupe les cables d'un GeoPackage SRO et ecrit le resultat.

    Args:
        input_gpkg : chemin GeoPackage en entree
        output_gpkg : chemin sortie. Si None, ecrit dans input_gpkg (en ajout
                      ou remplacement de la couche `cables_decoupes`).
        tolerance : tolerance de snap en metres (defaut 0.70)
        min_segment_length : longueur mini d'un segment retenu (defaut 0.20m)
        output_layer : nom de la couche de sortie (defaut `cables_decoupes`)
        sro : code SRO pour traca dans les stats

    Returns:
        SplitStats avec les indicateurs d'execution.

    Raises:
        ValueError si des couches obligatoires sont absentes.
        FileNotFoundError si input_gpkg n'existe pas.
    """
    input_path = Path(input_gpkg)
    if not input_path.is_file():
        raise FileNotFoundError(f"GeoPackage introuvable : {input_gpkg}")

    _require_layers(str(input_path))

    target = output_gpkg or input_gpkg
    if output_gpkg and not Path(output_gpkg).is_file():
        shutil.copy2(input_path, output_gpkg)

    cables = _load_cables(str(input_path))
    poteaux = _load_poteaux(str(input_path))
    bpes = _load_bpes(str(input_path))
    attaches = _load_attaches(str(input_path))

    segments, stats = split_cables(
        cables=cables, poteaux=poteaux, bpes=bpes, attaches=attaches,
        tolerance=tolerance, min_segment_length=min_segment_length, sro=sro,
    )

    _write_output(target, segments, layer_name=output_layer)
    return stats
