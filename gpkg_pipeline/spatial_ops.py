# Youcef ADDA | GEODECI
# FR : Plugins QGIS, solutions SIG et administration PostgreSQL/PostGIS ; audit, automatisation et accompagnement.
# EN : QGIS plugins, GIS solutions and PostgreSQL/PostGIS administration; audits, workflow automation and business support.
# AR : تطوير إضافات QGIS وحلول نظم المعلومات الجغرافية وإدارة PostgreSQL/PostGIS، والتدقيق والأتمتة ومرافقة المؤسسات.
# Site / Website / الموقع : https://geodeci.xyz/
# Contact professionnel / Business enquiries / للتواصل المهني : youcef.geodesien@gmail.com | contact@geodeci.xyz
# Détails / Details / التفاصيل : SIGNATURE.md
# ============================================================================
"""
Operations geometriques pures (sans I/O).

Porte les operations PostGIS utilisees par `fddcpiax` :
- ST_ClosestPoint -> shapely.ops.nearest_points
- ST_LineLocatePoint -> LineString.project (normalized)
- ST_Snap -> shapely.ops.snap
- ST_Split -> shapely.ops.split
- ST_DWithin -> STRtree + distance check
- ST_Collect + ST_Dump : geometries + list

Toutes les fonctions sont pures (entree/sortie uniquement, pas d'etat).
"""
from __future__ import annotations

import math
import warnings
from typing import Iterable, List, Optional, Sequence, Tuple

from shapely.geometry import LineString, MultiPoint, Point
from shapely.geometry.base import BaseGeometry
from shapely.ops import nearest_points, snap, split
from shapely.strtree import STRtree


def _has_finite_coords(geom: BaseGeometry) -> bool:
    """Vrai si toutes les coordonnees du geometrie sont finies (pas NaN/inf).

    Shapely considere parfois un Point(NaN, 0) comme is_valid ; ce helper
    empeche que de tels artefacts polluent snap/split et declenchent un
    RuntimeWarning dans line_locate_point.
    """
    try:
        for coord in geom.coords:
            for v in coord:
                if not math.isfinite(v):
                    return False
        return True
    except Exception:
        return False


# -----------------------------------------------------------------------------
# Index spatial
# -----------------------------------------------------------------------------


def build_point_index(points: Sequence[Point]) -> Tuple[STRtree, List[Point]]:
    """Construit un STRtree + garde la liste source (shapely 2 ne donne que des ids).

    Retourne (index, list) : l'index donne des integer ids, la liste permet
    de recuperer le Point original via ids[i].
    """
    if not points:
        return STRtree([]), []
    tree = STRtree(points)
    return tree, list(points)


def points_within(
    tree: STRtree,
    points: Sequence[Point],
    query_geom: BaseGeometry,
    tolerance: float,
) -> List[Point]:
    """Retourne les points du tree dans tolerance de query_geom (ST_DWithin)."""
    if tolerance <= 0 or not points:
        return []
    # STRtree query uses bbox; affine tolerance by filtering on distance
    candidate_ids = tree.query(query_geom.buffer(tolerance))
    result = []
    for idx in candidate_ids:
        pt = points[int(idx)]
        if query_geom.distance(pt) <= tolerance:
            result.append(pt)
    return result


# -----------------------------------------------------------------------------
# Projection et filtrage mid-cable
# -----------------------------------------------------------------------------


def project_point_on_line(line: LineString, point: Point) -> Optional[Point]:
    """Projection perpendiculaire d'un point sur une ligne (ST_ClosestPoint)."""
    if line.is_empty or line.length == 0 or not _has_finite_coords(line) or not _has_finite_coords(point):
        return None
    _, proj = nearest_points(point, line)
    if not _has_finite_coords(proj):
        return None
    return proj


def location_fraction(line: LineString, point: Point) -> float:
    """Position normalisee (0..1) du point projete sur la ligne.

    Equivalent ST_LineLocatePoint. Retourne NaN si geometrie invalide.
    """
    if line.is_empty or line.length == 0 or not _has_finite_coords(line) or not _has_finite_coords(point):
        return float("nan")
    try:
        frac = line.project(point, normalized=True)
    except Exception:
        return float("nan")
    if not math.isfinite(frac):
        return float("nan")
    return max(0.0, min(1.0, frac))


def is_mid_cable(
    fraction: float,
    min_frac: float,
    max_frac: float,
) -> bool:
    """Vrai si la fraction est strictement mid-cable (declenche un split)."""
    return min_frac <= fraction <= max_frac


# -----------------------------------------------------------------------------
# Snap + Split (coeur algo decoupage)
# -----------------------------------------------------------------------------


def snap_and_split(
    line: LineString,
    split_points: Sequence[Point],
    snap_precision: float,
) -> List[LineString]:
    """Snap la ligne aux points puis split.

    Equivalent : ST_Dump(ST_Split(ST_Snap(line, ST_Collect(pts), eps), pts))

    Args:
        line : geometrie LineString a decouper
        split_points : points auxquels splitter (doivent etre sur la ligne)
        snap_precision : tolerance d'alignement (shapely.ops.snap)

    Returns:
        Liste de LineString atomiques. Vide si line invalide.
    """
    if not line.is_valid or line.is_empty or not _has_finite_coords(line) or not split_points:
        return [line] if line.is_valid and not line.is_empty else []

    clean_points = [p for p in split_points if p.is_valid and not p.is_empty and _has_finite_coords(p)]
    if not clean_points:
        return [line]

    # Ne garder que les points reellement sur la ligne (evite NaN dans line_locate_point)
    on_line: List[Point] = []
    max_dist = max(snap_precision * 5.0, 0.05)
    for p in clean_points:
        if line.distance(p) > max_dist:
            continue
        frac = location_fraction(line, p)
        if not math.isfinite(frac) or frac <= 0.0 or frac >= 1.0:
            continue
        on_line.append(p)
    if not on_line:
        return [line]

    multi_pts = MultiPoint(on_line)
    snapped = snap(line, multi_pts, snap_precision)
    if not snapped.is_valid or snapped.is_empty or snapped.length == 0 or not _has_finite_coords(snapped):
        return [line] if line.length > 0 else []

    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        splitted = split(snapped, multi_pts)
    parts: List[LineString] = []
    for g in splitted.geoms:
        if isinstance(g, LineString) and g.is_valid and not g.is_empty:
            parts.append(g)
    return parts if parts else [snapped] if isinstance(snapped, LineString) else []


# -----------------------------------------------------------------------------
# Matching endpoint -> infra
# -----------------------------------------------------------------------------


def match_endpoint_to_infra(
    endpoint: Point,
    tree: STRtree,
    points: Sequence[Point],
    refs: Sequence[str],
    tolerance_primary: float,
    tolerance_fallback: float,
    priority_non_bpe: bool = True,
) -> Optional[str]:
    """Associe une extremite de cable au point infra le plus proche.

    Strategie en 2 tiers (meme regle que fddcpiax SQL LATERAL JOIN) :
      - priorite 1 : points dans tolerance_primary
      - priorite 2 : points dans tolerance_fallback (distance >= primary)
      - priorite intra-niveau : non-BPE (pot_) avant BPE (bpe_) si equidistants
      - fallback final : ST_Distance croissante

    Args:
        endpoint : point extremite du cable (ST_StartPoint / ST_EndPoint)
        tree : STRtree des points infra
        points : liste aligned avec tree
        refs : liste de references (prefixes `pot_123`, `bpe_45`...) aligned
        tolerance_primary : tolerance snap premiere passe
        tolerance_fallback : tolerance elargie si aucun match premiere passe
        priority_non_bpe : favorise pot_ devant bpe_ dans le meme tier

    Returns:
        Reference infra ("pot_123", "bpe_45") ou None si aucun match.
    """
    if not points:
        return None

    candidates_ids = tree.query(endpoint.buffer(tolerance_fallback))
    if len(candidates_ids) == 0:
        return None

    scored: List[Tuple[int, int, float, str]] = []
    for idx in candidates_ids:
        i = int(idx)
        dist = endpoint.distance(points[i])
        if dist > tolerance_fallback:
            continue
        tier = 0 if dist <= tolerance_primary else 1
        type_rank = 0 if (priority_non_bpe and not refs[i].startswith("bpe_")) else 1
        scored.append((tier, type_rank, dist, refs[i]))

    if not scored:
        return None
    scored.sort(key=lambda t: (t[0], t[1], t[2]))
    return scored[0][3]


# -----------------------------------------------------------------------------
# Deduplication
# -----------------------------------------------------------------------------


def dedup_segments(
    segments: Iterable[Tuple[int, LineString]],
) -> List[Tuple[int, LineString]]:
    """Deduplique (gid, geom) par hash WKB.

    Equivalent : DISTINCT ON (gid, md5(ST_AsBinary(geom))).
    """
    seen = set()
    out: List[Tuple[int, LineString]] = []
    for gid, geom in segments:
        key = (gid, bytes(geom.wkb))
        if key in seen:
            continue
        seen.add(key)
        out.append((gid, geom))
    return out


# -----------------------------------------------------------------------------
# Helpers endpoints
# -----------------------------------------------------------------------------


def start_point(line: LineString) -> Point:
    """Premier sommet de la ligne."""
    return Point(line.coords[0])


def end_point(line: LineString) -> Point:
    """Dernier sommet de la ligne."""
    return Point(line.coords[-1])


# -----------------------------------------------------------------------------
# Recalage cable -> poteau via attaches (port de fddcpiax etape 2b)
# -----------------------------------------------------------------------------


def retract_endpoint_to_pole(
    endpoint: Point,
    attache_lines: Sequence[LineString],
    attache_tree: STRtree,
    intersect_tolerance: float = 0.05,
) -> Optional[Point]:
    """Si endpoint tombe sur une attache, retourne ST_StartPoint(attache) = poteau.

    Port de fddcpiax etape 2b :
      ST_SetPoint(cable, 0, ST_StartPoint(attache))
      WHERE ST_Intersects(ST_StartPoint(cable), attache)

    PostGIS ST_Intersects sur point/line = distance ~0. On utilise une micro-
    tolerance (5cm) pour absorber les imprecisions flottantes du GPKG.

    Args:
        endpoint: extremite du cable (start ou end)
        attache_lines: lignes des attaches (index-aligned avec attache_tree)
        attache_tree: STRtree des attaches
        intersect_tolerance: distance max pour considerer une intersection

    Returns:
        Point du poteau (start de l'attache) ou None si pas d'intersection.
    """
    if not attache_lines:
        return None
    candidates = attache_tree.query(endpoint.buffer(intersect_tolerance))
    best_dist = intersect_tolerance + 1.0
    best_pole: Optional[Point] = None
    for idx in candidates:
        att = attache_lines[int(idx)]
        dist = endpoint.distance(att)
        if dist <= intersect_tolerance and dist < best_dist:
            best_dist = dist
            best_pole = Point(att.coords[0])  # ST_StartPoint = pole
    return best_pole


def retract_cable(
    line: LineString,
    attache_lines: Sequence[LineString],
    attache_tree: STRtree,
    intersect_tolerance: float = 0.05,
) -> Tuple[LineString, bool]:
    """Retracte les extremites d'un cable vers les poteaux via attaches.

    Modifie le premier et/ou dernier vertex si l'extremite intersecte une
    attache. Retourne (line_modifiee, a_ete_modifie).
    """
    coords = list(line.coords)
    modified = False

    sp = Point(coords[0])
    pole_start = retract_endpoint_to_pole(
        sp, attache_lines, attache_tree, intersect_tolerance,
    )
    if pole_start is not None:
        coords[0] = (pole_start.x, pole_start.y)
        modified = True

    ep = Point(coords[-1])
    pole_end = retract_endpoint_to_pole(
        ep, attache_lines, attache_tree, intersect_tolerance,
    )
    if pole_end is not None:
        coords[-1] = (pole_end.x, pole_end.y)
        modified = True

    if not modified:
        return line, False
    retracted = LineString(coords)
    if retracted.is_empty or not retracted.is_valid or retracted.length < 0.01:
        return line, False
    return retracted, True


def attache_endpoints(attache_line: LineString) -> Tuple[Point, Point]:
    """Extrait les 2 endpoints d'une attache (start + end).

    Critique : le SQL utilisait seulement `ST_EndPoint(attache)`, mais
    le plugin doit snap sur les DEUX extremites (parfois l'appui reel
    se trouve cote StartPoint). Cette fonction centralise la regle.
    """
    return start_point(attache_line), end_point(attache_line)
