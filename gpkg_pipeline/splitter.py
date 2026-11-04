# Youcef ADDA | GEODECI
# FR : Plugins QGIS, solutions SIG et administration PostgreSQL/PostGIS ; audit, automatisation et accompagnement.
# EN : QGIS plugins, GIS solutions and PostgreSQL/PostGIS administration; audits, workflow automation and business support.
# AR : تطوير إضافات QGIS وحلول نظم المعلومات الجغرافية وإدارة PostgreSQL/PostGIS، والتدقيق والأتمتة ومرافقة المؤسسات.
# Site / Website / الموقع : https://geodeci.xyz/
# Contact professionnel / Business enquiries / للتواصل المهني : youcef.geodesien@gmail.com | contact@geodeci.xyz
# Détails / Details / التفاصيل : SIGNATURE.md
# ============================================================================
"""
Pipeline de decoupage cable -> segments atomiques.

Orchestration des operations geometriques (spatial_ops) pour reproduire
le comportement de `rip_avg_nge.fddcpiax` cote Python, limite au
perimetre aerien (poteaux + BPE + attaches).

Algorithme (port de fddcpiax + composante attaches de fddcpfa) :

    1. Collecte des points de split
       pour chaque cable C :
         pts_C = poteaux + BPE dans tol(C)
               + 2 endpoints de chaque attache dans tol(C)

    2. Projection perpendiculaire (mid-cable uniquement)
       pour chaque pt dans pts_C :
         proj = ClosestPoint(C, pt)
         si 1% <= LineLocate(C, proj) <= 99% :
             on garde proj comme point de split

    3. Snap + Split
       C_snapped = snap(C, ST_Collect(projs), 0.01)
       segments = ST_Dump(ST_Split(C_snapped, ST_Collect(projs)))

    4. Passthrough
       si aucun projs mid-cable mais au moins 1 appui proche :
         on garde le cable entier (pas de split interne)

    5. Orphelin
       si aucun appui dans tolerance_fallback :
         segment unique avec posemode=4

    6. Matching endpoint -> infra (2 tiers + prio non-BPE)
    7. Classification posemode (pot+pot ou pot+bpe = aerien, sinon orphelin)
    8. Deduplication (gid, WKB hash)
"""
from __future__ import annotations

import math
import time
from typing import List, Optional, Sequence, Tuple

from shapely.geometry import LineString, Point
from shapely import wkb as shp_wkb
from shapely.strtree import STRtree

from .dataclasses import (
    AttacheInput,
    BpeInput,
    CableInput,
    CableSegment,
    DEFAULT_MIN_SEGMENT_LENGTH_M,
    DEFAULT_SNAP_TOLERANCE_M,
    ENDPOINT_MATCH_FALLBACK_FACTOR,
    ENDPOINT_MATCH_FALLBACK_MIN_M,
    PoteauInput,
    SNAP_PRECISION_M,
    SPLIT_LOC_MAX,
    SPLIT_LOC_MIN,
    SplitStats,
)
from .posemode import POSEMODE_AERIEN, POSEMODE_ORPHELIN, classify_posemode
from .spatial_ops import (
    attache_endpoints,
    build_point_index,
    dedup_segments,
    end_point,
    location_fraction,
    match_endpoint_to_infra,
    points_within,
    project_point_on_line,
    retract_cable,
    snap_and_split,
    start_point,
)


# -----------------------------------------------------------------------------
# Preparation : transformation des inputs en structures utiles
# -----------------------------------------------------------------------------


def _to_line(wkb: bytes) -> Optional[LineString]:
    """Charge un WKB en LineString shapely. Retourne None si invalide."""
    g = shp_wkb.loads(wkb)
    if not isinstance(g, LineString) or g.is_empty or not g.is_valid:
        return None
    return g


def _to_point(wkb: bytes) -> Optional[Point]:
    """Charge un WKB en Point shapely."""
    g = shp_wkb.loads(wkb)
    if not isinstance(g, Point) or g.is_empty:
        return None
    return g


def _build_attache_lines(attaches: Sequence[AttacheInput]) -> List[LineString]:
    """Convertit les attaches en LineString pour le recalage (etape 2b)."""
    lines: List[LineString] = []
    for a in attaches:
        line = _to_line(a.wkb)
        if line is not None:
            lines.append(line)
    return lines


def _collect_split_points(
    poteaux: Sequence[PoteauInput],
    bpes: Sequence[BpeInput],
    attaches: Sequence[AttacheInput],
) -> Tuple[List[Point], List[str]]:
    """Construit la liste des points de split et leurs references prefixees.

    Attaches -> 2 points (start + end) car l'appui reel peut etre du cote
    start comme du cote end (cf note metier).

    Returns:
        (points, refs) alignes. Preserve l'ordre d'insertion.
    """
    points: List[Point] = []
    refs: List[str] = []
    for p in poteaux:
        pt = _to_point(p.wkb)
        if pt is not None:
            points.append(pt)
            refs.append(f"pot_{p.gid}")
    for b in bpes:
        pt = _to_point(b.wkb)
        if pt is not None:
            points.append(pt)
            refs.append(f"bpe_{b.gid}")
    for a in attaches:
        line = _to_line(a.wkb)
        if line is None:
            continue
        sp, ep = attache_endpoints(line)
        points.append(sp)
        refs.append(f"att_{a.gid}_s")
        points.append(ep)
        refs.append(f"att_{a.gid}_e")
    return points, refs


# -----------------------------------------------------------------------------
# Decoupe d'un cable individuel
# -----------------------------------------------------------------------------


def _split_one_cable(
    line: LineString,
    all_points: Sequence[Point],
    tree,
    tolerance: float,
    tolerance_fallback: float,
    stats: SplitStats,
) -> List[LineString]:
    """Retourne la liste des segments atomiques d'un cable.

    Si aucun point de split mid-cable -> passthrough (cable complet).
    Si aucun appui dans tolerance_fallback -> liste vide (orphelin traite
    separement par l'appelant).
    """
    nearby = points_within(tree, all_points, line, tolerance)
    if not nearby:
        # Aucun appui proche : possible orphelin
        nearby_fallback = points_within(tree, all_points, line, tolerance_fallback)
        if not nearby_fallback:
            return []
        return [line]  # passthrough (orphelin attribue ensuite)

    if not line.is_valid or line.is_empty or line.length == 0:
        return []

    # Projection mid-cable uniquement
    projs: List[Point] = []
    for pt in nearby:
        proj = project_point_on_line(line, pt)
        if proj is None:
            continue
        frac = location_fraction(line, proj)
        if not math.isfinite(frac):
            continue
        if SPLIT_LOC_MIN <= frac <= SPLIT_LOC_MAX:
            projs.append(proj)

    if not projs:
        # Appuis uniquement en extremite : passthrough (pas de split mid)
        return [line]

    segments = snap_and_split(line, projs, SNAP_PRECISION_M)
    stats.nb_split_points_total += len(projs)
    return segments


# -----------------------------------------------------------------------------
# Fonction principale : decoupe tous les cables
# -----------------------------------------------------------------------------


def split_cables(
    cables: Sequence[CableInput],
    poteaux: Sequence[PoteauInput],
    bpes: Sequence[BpeInput],
    attaches: Sequence[AttacheInput],
    tolerance: float = DEFAULT_SNAP_TOLERANCE_M,
    min_segment_length: float = DEFAULT_MIN_SEGMENT_LENGTH_M,
    sro: str = "",
) -> Tuple[List[CableSegment], SplitStats]:
    """Decoupe les cables aux appuis + endpoints attaches.

    Args:
        cables : cables bruts (BPE-BPE complets)
        poteaux : poteaux d'infrastructure
        bpes : boites (BPE) de distribution
        attaches : attaches (lignes) - 2 endpoints chacune servent de split
        tolerance : distance snap en metres (defaut 0.70m)
        min_segment_length : longueur mini d'un segment conserve (defaut 0.20m)
        sro : code SRO (copie dans stats et segments.sro)

    Returns:
        (liste de CableSegment, stats).
    """
    t0 = time.time()
    stats = SplitStats(
        sro=sro,
        nb_cables_in=len(cables),
        nb_poteaux=len(poteaux),
        nb_bpe=len(bpes),
        nb_attaches=len(attaches),
    )

    # 2b. Recalage cable -> poteau via attaches (port fddcpiax etape 2b)
    attache_geoms = _build_attache_lines(attaches)
    att_tree = STRtree(attache_geoms) if attache_geoms else None

    split_points, split_refs = _collect_split_points(poteaux, bpes, attaches)
    tree, _ = build_point_index(split_points)

    # Index separe pour matching endpoint (utilise aussi pour refs)
    endpoint_tolerance_fallback = max(
        ENDPOINT_MATCH_FALLBACK_MIN_M, tolerance * ENDPOINT_MATCH_FALLBACK_FACTOR
    )

    # Pour matching endpoint, on veut uniquement pot_ et bpe_ (pas les att_*)
    infra_points: List[Point] = []
    infra_refs: List[str] = []
    for pt, ref in zip(split_points, split_refs):
        if ref.startswith("pot_") or ref.startswith("bpe_"):
            infra_points.append(pt)
            infra_refs.append(ref)
    infra_tree, _ = build_point_index(infra_points)

    out: List[CableSegment] = []
    gid_dc_counter = 0
    raw_pairs: List[Tuple[CableInput, LineString]] = []

    for cable in cables:
        line = _to_line(cable.wkb)
        if line is None:
            stats.nb_cables_ignored_invalid += 1
            continue
        if att_tree is not None and attache_geoms:
            line, retracted = retract_cable(
                line, attache_geoms, att_tree,
            )
            if retracted:
                stats.nb_cables_retracted += 1
                if line.length < min_segment_length:
                    stats.dropped_too_short += 1
                    continue
        segs = _split_one_cable(
            line, split_points, tree,
            tolerance, endpoint_tolerance_fallback, stats,
        )
        if not segs:
            # Orphelin : cable complet sans appui proche
            raw_pairs.append((cable, line))
            continue
        for seg in segs:
            if seg.length < min_segment_length:
                stats.dropped_too_short += 1
                continue
            raw_pairs.append((cable, seg))

    # Dedup (gid_cable, geom)
    dedup_keys = [(c.gid, g) for c, g in raw_pairs]
    deduped = dedup_segments(dedup_keys)
    dedup_set = {(gid, bytes(g.wkb)) for gid, g in deduped}

    for cable, seg in raw_pairs:
        key = (cable.gid, bytes(seg.wkb))
        if key not in dedup_set:
            continue
        dedup_set.discard(key)

        gid_dc_counter += 1
        start_ref = match_endpoint_to_infra(
            start_point(seg), infra_tree, infra_points, infra_refs,
            tolerance, endpoint_tolerance_fallback,
        )
        end_ref = match_endpoint_to_infra(
            end_point(seg), infra_tree, infra_points, infra_refs,
            tolerance, endpoint_tolerance_fallback,
        )
        pm = classify_posemode(start_ref, end_ref)
        segment = CableSegment(
            gid=cable.gid,
            gid_dc=gid_dc_counter,
            sro=cable.sro,
            wkb=bytes(seg.wkb),
            length=float(seg.length),
            start_ref=start_ref,
            end_ref=end_ref,
            posemode=pm,
            attrs=dict(cable.attrs),
        )
        out.append(segment)
        if pm == POSEMODE_AERIEN:
            stats.nb_segments_aerien += 1
        elif pm == POSEMODE_ORPHELIN:
            stats.nb_segments_orphan += 1

    stats.nb_segments_out = len(out)
    stats.duration_ms = (time.time() - t0) * 1000.0
    return out, stats
