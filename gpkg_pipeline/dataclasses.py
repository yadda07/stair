# Youcef ADDA | GEODECI
# FR : Plugins QGIS, solutions SIG et administration PostgreSQL/PostGIS ; audit, automatisation et accompagnement.
# EN : QGIS plugins, GIS solutions and PostgreSQL/PostGIS administration; audits, workflow automation and business support.
# AR : تطوير إضافات QGIS وحلول نظم المعلومات الجغرافية وإدارة PostgreSQL/PostGIS، والتدقيق والأتمتة ومرافقة المؤسسات.
# Site / Website / الموقع : https://geodeci.xyz/
# Contact professionnel / Business enquiries / للتواصل المهني : youcef.geodesien@gmail.com | contact@geodeci.xyz
# Détails / Details / التفاصيل : SIGNATURE.md
# ============================================================================
"""
Dataclasses d'entree/sortie du pipeline de decoupage.

Immuables apres creation (frozen=True) pour garantir la seule responsabilite
lecture/ecriture et eviter les mutations non-tracees.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple


# -----------------------------------------------------------------------------
# Entrees : couches brutes du GeoPackage livre
# -----------------------------------------------------------------------------


@dataclass(frozen=True)
class CableInput:
    """Cable brut non decoupe (1 entite = 1 cable BPE-BPE complet)."""
    gid: int
    sro: str
    wkb: bytes
    attrs: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class PoteauInput:
    """Poteau `infra_pt_pot` (point de split)."""
    gid: int
    sro: str
    wkb: bytes
    inf_type: Optional[str] = None
    inf_num: Optional[str] = None


@dataclass(frozen=True)
class BpeInput:
    """BPE (point de split, extremite naturelle d'un cable)."""
    gid: int
    sro: str
    wkb: bytes
    noe_type: Optional[str] = None
    noe_codext: Optional[str] = None


@dataclass(frozen=True)
class AttacheInput:
    """Attache (ligne) : fournit 2 points de split (ST_StartPoint, ST_EndPoint).

    Necessaire quand le cable passe par une attache dont le BPE/poteau reel
    est decale : sans snap sur les deux extremites de l'attache, le cable
    garderait la geometrie d'origine et le decoupage serait incorrect.
    """
    gid: int
    sro: str
    wkb: bytes
    noe_usage: Optional[str] = None


# -----------------------------------------------------------------------------
# Sortie : segments decoupes
# -----------------------------------------------------------------------------


@dataclass(frozen=True)
class CableSegment:
    """Segment atomique issu du decoupage d'un cable sur les points d'appui.

    gid       : gid du cable parent (reporte)
    gid_dc    : identifiant segment unique apres decoupage (genere)
    sro/nro/attrs : attributs cable heritage
    wkb       : geometrie du segment (LINESTRING 2154)
    length    : longueur du segment en metres
    start_ref : reference infra (prefixe pot_/bpe_/None) a ST_StartPoint
    end_ref   : reference infra a ST_EndPoint
    posemode  : classification (1=aerien, 4=orphelin, autres codes
                reserves pour evolution future si chb_/atr_ ajoutes)
    """
    gid: int
    gid_dc: int
    sro: str
    wkb: bytes
    length: float
    start_ref: Optional[str]
    end_ref: Optional[str]
    posemode: int
    attrs: Dict[str, Any] = field(default_factory=dict)


# -----------------------------------------------------------------------------
# Diagnostic : statistiques de decoupage
# -----------------------------------------------------------------------------


@dataclass
class SplitStats:
    """Statistiques d'execution du pipeline (mutable, mis a jour en cours)."""
    sro: str = ""
    nb_cables_in: int = 0
    nb_cables_ignored_invalid: int = 0
    nb_poteaux: int = 0
    nb_bpe: int = 0
    nb_attaches: int = 0
    nb_cables_retracted: int = 0
    nb_split_points_total: int = 0
    nb_segments_out: int = 0
    nb_segments_orphan: int = 0
    nb_segments_aerien: int = 0
    dropped_too_short: int = 0
    duration_ms: float = 0.0
    warnings: List[str] = field(default_factory=list)

    def as_dict(self) -> Dict[str, Any]:
        """Conversion dict pour logging / export JSON."""
        return {
            "sro": self.sro,
            "nb_cables_in": self.nb_cables_in,
            "nb_cables_ignored_invalid": self.nb_cables_ignored_invalid,
            "nb_poteaux": self.nb_poteaux,
            "nb_bpe": self.nb_bpe,
            "nb_attaches": self.nb_attaches,
            "nb_cables_retracted": self.nb_cables_retracted,
            "nb_split_points_total": self.nb_split_points_total,
            "nb_segments_out": self.nb_segments_out,
            "nb_segments_orphan": self.nb_segments_orphan,
            "nb_segments_aerien": self.nb_segments_aerien,
            "dropped_too_short": self.dropped_too_short,
            "duration_ms": self.duration_ms,
            "warnings": list(self.warnings),
        }


# -----------------------------------------------------------------------------
# Constantes pipeline (valeurs par defaut)
# -----------------------------------------------------------------------------

DEFAULT_SNAP_TOLERANCE_M: float = 0.70
"""Tolerance de snap en metres (identique a fddcpiax SQL)."""

DEFAULT_MIN_SEGMENT_LENGTH_M: float = 0.20
"""Longueur mini d'un segment pour etre conserve (filtre les micro-residus)."""

SPLIT_LOC_MIN: float = 0.01
"""Position min sur le cable (fraction 0..1) pour considerer une projection
`mid-cable` et declencher un split. Evite les micro-segments aux extremites."""

SPLIT_LOC_MAX: float = 0.99
"""Position max sur le cable pour split mid-cable."""

SNAP_PRECISION_M: float = 0.01
"""Precision du snap geometrique (shapely.ops.snap)."""

ENDPOINT_MATCH_FALLBACK_FACTOR: float = 2.0
"""Facteur appliquе a la tolerance pour le fallback endpoint-infra."""

ENDPOINT_MATCH_FALLBACK_MIN_M: float = 5.0
"""Tolerance min du fallback endpoint (max entre facteur*tol et cette valeur)."""

SRID_LAMBERT93: int = 2154
"""SRID Lambert 93 (impose par le livrable)."""


# -----------------------------------------------------------------------------
# Types utilitaires (alias) pour lisibilite
# -----------------------------------------------------------------------------

XY = Tuple[float, float]
"""Coordonnee 2D (x, y) en Lambert 93."""
