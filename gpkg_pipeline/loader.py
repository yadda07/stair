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
Loader GeoPackage -> caches consommes par les workflows PoleAerien.

Point d'entree unique pour alimenter le batch a partir d'un livrable
GeoPackage (eventuellement zippe). Produit 3 structures :

    cables    : list[cable_segment.CableSegment]  (format historique pivot)
    bpe_list  : list[dict]  {gid, geom_wkt, noe_type}
    attaches  : list[dict]  {gid, geom_wkt}

Ces structures sont les contrats attendus par :
    - async_tasks.ComacTask             (params['fddcpi_cables_cache'])
    - async_tasks.PoliceC6Task          (fddcpi_cables_cache + bpe_list_cache
                                         + attaches_cache)
    - cable_analyzer.compter_cables_par_appui / analyser_charge_appuis

Regles de lecture :
    1. Si la couche `cables_decoupes` existe dans le GPKG (produite par le
       splitter amont), elle est utilisee telle quelle.
    2. Sinon, si `auto_split=True` et les 4 couches obligatoires
       (cables, bpe, attaches, infra_pt_pot) sont presentes, on execute
       `split_cables_from_gpkg` a la volee puis on relit `cables_decoupes`.
    3. Sinon, on lit la couche `cables` brute (1 segment = 1 cable entier,
       degradation gracieuse pour livrables minimaux).

Les attributs metier (cab_type, cab_capa, cb_etiquet...) sont lus depuis
les colonnes du GeoPackage. Les colonnes manquantes sont laissees aux
valeurs par defaut de `CableSegment` (chaine vide / 0).
"""
from __future__ import annotations

import os
import shutil
import tempfile
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from shapely.geometry.base import BaseGeometry

from ..cable_segment import CableSegment
from .gpkg_io import iter_features, list_layers
from .zip_loader import extract_gpkg_from_zip


# -----------------------------------------------------------------------------
# Conteneur de retour
# -----------------------------------------------------------------------------


@dataclass
class GpkgCaches:
    """Caches charges depuis un GeoPackage livre, prets a injecter
    dans les params de workflows (`fddcpi_cables_cache`, `bpe_list_cache`,
    `attaches_cache`).
    """
    cables: List[CableSegment] = field(default_factory=list)
    bpe_list: List[Dict[str, Any]] = field(default_factory=list)
    attaches: List[Dict[str, Any]] = field(default_factory=list)
    sro: str = ""
    source_gpkg: str = ""
    segments_layer: str = ""   # 'cables_decoupes' ou 'cables'
    split_on_the_fly: bool = False


# -----------------------------------------------------------------------------
# Helpers de conversion
# -----------------------------------------------------------------------------


def _as_int(value: Any, default: int = 0) -> int:
    """Cast tolerant vers int (gere None, str vide, float)."""
    if value is None or value == "":
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        try:
            return int(float(value))
        except (TypeError, ValueError):
            return default


def _as_float(value: Any, default: float = 0.0) -> float:
    """Cast tolerant vers float."""
    if value is None or value == "":
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _as_str(value: Any) -> str:
    """Cast tolerant vers str ; None -> ''."""
    if value is None:
        return ""
    return str(value)


def _geom_to_wkt(geom: Optional[BaseGeometry]) -> str:
    """Shapely geometry -> WKT string, '' si None/vide."""
    if geom is None or geom.is_empty:
        return ""
    return geom.wkt


# -----------------------------------------------------------------------------
# Construction des CableSegment pivot
# -----------------------------------------------------------------------------


def _segment_from_row(
    attrs: Dict[str, Any],
    geom: Optional[BaseGeometry],
) -> CableSegment:
    """Construit un CableSegment historique a partir d'une ligne GPKG.

    Attributs lus (tous optionnels) :
        gid, gid_dc, gid_dc2, sro, nro, length, posemode,
        cab_type, cab_capa, cab_modulo, cab_nature, cb_etiquet,
        fon, projet, dce, dist_type, affectation, collecte,
        commentaire, isole, date_modif, modif_par
    """
    gid = _as_int(attrs.get("gid") or attrs.get("fid"))
    gid_dc = _as_int(attrs.get("gid_dc"), default=gid)
    gid_dc2 = _as_int(attrs.get("gid_dc2"), default=gid_dc)
    length = _as_float(attrs.get("length"))
    if length == 0.0 and geom is not None and not geom.is_empty:
        try:
            length = float(geom.length)
        except Exception:
            length = 0.0

    return CableSegment(
        gid_dc2=gid_dc2,
        gid_dc=gid_dc,
        gid=gid,
        sro=_as_str(attrs.get("sro")),
        nro=_as_str(attrs.get("nro")),
        length=length,
        cab_type=_as_str(attrs.get("cab_type")),
        cab_capa=_as_int(attrs.get("cab_capa")),
        cab_modulo=_as_int(attrs.get("cab_modulo")),
        isole=_as_str(attrs.get("isole")),
        date_modif=_as_str(attrs.get("date_modif")),
        modif_par=_as_str(attrs.get("modif_par")),
        cab_nature=_as_str(attrs.get("cab_nature")),
        commentaire=_as_str(attrs.get("commentaire")),
        collecte=_as_str(attrs.get("collecte")),
        cb_etiquet=_as_str(attrs.get("cb_etiquet")),
        fon=_as_str(attrs.get("fon")),
        projet=_as_str(attrs.get("projet")),
        dce=_as_str(attrs.get("dce")),
        dist_type=_as_str(attrs.get("dist_type")),
        affectation=_as_str(attrs.get("affectation")),
        posemode=_as_int(attrs.get("posemode"), default=1),
        geom_wkt=_geom_to_wkt(geom),
    )


# -----------------------------------------------------------------------------
# Lecteurs de couches
# -----------------------------------------------------------------------------


def _read_cables(gpkg_path: str, layer_name: str) -> List[CableSegment]:
    """Lit la couche cables (ou cables_decoupes) et produit la liste pivot."""
    out: List[CableSegment] = []
    for attrs, geom in iter_features(gpkg_path, layer_name):
        out.append(_segment_from_row(attrs, geom))
    return out


def _read_bpes(gpkg_path: str) -> List[Dict[str, Any]]:
    """Lit la couche `bpe` -> list[{gid, geom_wkt, noe_type}]."""
    out: List[Dict[str, Any]] = []
    for attrs, geom in iter_features(gpkg_path, "bpe"):
        out.append({
            "gid": _as_int(attrs.get("gid") or attrs.get("fid")),
            "geom_wkt": _geom_to_wkt(geom),
            "noe_type": _as_str(attrs.get("noe_type")),
            "noe_codext": _as_str(attrs.get("noe_codext")),
            "sro": _as_str(attrs.get("sro")),
        })
    return out


def _read_attaches(gpkg_path: str) -> List[Dict[str, Any]]:
    """Lit la couche `attaches` -> list[{gid, geom_wkt}]."""
    out: List[Dict[str, Any]] = []
    for attrs, geom in iter_features(gpkg_path, "attaches"):
        out.append({
            "gid": _as_int(attrs.get("gid") or attrs.get("fid")),
            "geom_wkt": _geom_to_wkt(geom),
            "noe_usage": _as_str(attrs.get("noe_usage")),
            "sro": _as_str(attrs.get("sro")),
        })
    return out


def _infer_sro(cables: List[CableSegment], bpe_list: List[Dict[str, Any]], hint: str = "") -> str:
    """Determine le SRO de reference : hint explicite, sinon 1er SRO non vide."""
    if hint:
        return hint
    for c in cables:
        if c.sro:
            return c.sro
    for b in bpe_list:
        if b.get("sro"):
            return str(b["sro"])
    return ""


# -----------------------------------------------------------------------------
# API publique
# -----------------------------------------------------------------------------


_SPLIT_LAYERS = ("cables", "bpe", "attaches", "infra_pt_pot")


def load_caches_from_gpkg(
    gpkg_path: str,
    *,
    auto_split: bool = True,
    sro_hint: str = "",
) -> GpkgCaches:
    """Charge les caches (cables / bpe / attaches / sro) depuis un GeoPackage.

    Args:
        gpkg_path   : chemin absolu du .gpkg livre.
        auto_split  : si True, declenche le splitter si `cables_decoupes`
                      est absente mais les 4 couches de split sont la.
        sro_hint    : code SRO force (sinon infere depuis les attributs).

    Returns:
        GpkgCaches prepeuples. Listes vides si la couche correspondante
        est absente du GPKG (pas d'erreur : degradation gracieuse).

    Raises:
        FileNotFoundError : GPKG inexistant.
        ValueError        : aucune couche `cables` ni `cables_decoupes`.
    """
    if not os.path.isfile(gpkg_path):
        raise FileNotFoundError(f"GeoPackage introuvable : {gpkg_path}")

    layers = set(list_layers(gpkg_path))

    # 1. Choix de la couche source des cables
    segments_layer = ""
    split_on_the_fly = False
    if "cables_decoupes" in layers:
        segments_layer = "cables_decoupes"
    elif auto_split and all(lyr in layers for lyr in _SPLIT_LAYERS):
        # Import local pour eviter un cycle (pipeline importe loader indirectement
        # via __init__.py)
        from .pipeline import split_cables_from_gpkg
        split_cables_from_gpkg(gpkg_path, sro=sro_hint)
        layers = set(list_layers(gpkg_path))
        if "cables_decoupes" in layers:
            segments_layer = "cables_decoupes"
            split_on_the_fly = True
    if not segments_layer:
        if "cables" in layers:
            segments_layer = "cables"
        else:
            raise ValueError(
                f"Aucune couche `cables` ni `cables_decoupes` dans {gpkg_path}. "
                f"Couches presentes : {sorted(layers)}"
            )

    # 2. Lecture des trois structures
    cables = _read_cables(gpkg_path, segments_layer)
    bpe_list = _read_bpes(gpkg_path) if "bpe" in layers else []
    attaches = _read_attaches(gpkg_path) if "attaches" in layers else []

    # 3. Resolution du SRO
    sro = _infer_sro(cables, bpe_list, hint=sro_hint)

    return GpkgCaches(
        cables=cables,
        bpe_list=bpe_list,
        attaches=attaches,
        sro=sro,
        source_gpkg=os.path.abspath(gpkg_path),
        segments_layer=segments_layer,
        split_on_the_fly=split_on_the_fly,
    )


def load_caches_from_zip(
    zip_path: str,
    *,
    auto_split: bool = True,
    sro_hint: str = "",
    extract_dir: Optional[str] = None,
    keep_extracted: bool = False,
    gpkg_member: Optional[str] = None,
) -> GpkgCaches:
    """Dezippe un livrable puis charge les caches.

    Args:
        zip_path       : chemin absolu du .zip livre.
        auto_split     : propage a `load_caches_from_gpkg`.
        sro_hint       : code SRO force.
        extract_dir    : dossier d'extraction. Si None, un temp est cree.
        keep_extracted : si True et extract_dir fourni, garde le GPKG extrait.
                         Sinon, un dossier temporaire est supprime a la fin.
        gpkg_member    : nom exact du .gpkg a extraire (si plusieurs dans le ZIP).

    Returns:
        GpkgCaches. `source_gpkg` pointe sur le chemin extrait (supprime en
        sortie si temp dir).

    Raises:
        FileNotFoundError : ZIP absent.
        zipfile.BadZipFile : archive corrompue.
        ValueError        : aucun GPKG, ou GPKG sans couche cables.
    """
    if not os.path.isfile(zip_path):
        raise FileNotFoundError(f"ZIP introuvable : {zip_path}")

    tmp_created = extract_dir is None
    target_dir = extract_dir or tempfile.mkdtemp(prefix="stair_gpkg_")
    try:
        gpkg_path = extract_gpkg_from_zip(zip_path, target_dir, gpkg_member)
        caches = load_caches_from_gpkg(
            gpkg_path, auto_split=auto_split, sro_hint=sro_hint,
        )
        return caches
    finally:
        # Nettoyage : seulement si on a cree le temp dir et keep=False
        if tmp_created and not keep_extracted:
            shutil.rmtree(target_dir, ignore_errors=True)


__all__ = [
    "GpkgCaches",
    "load_caches_from_gpkg",
    "load_caches_from_zip",
]
