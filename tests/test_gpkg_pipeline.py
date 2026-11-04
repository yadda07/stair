# Youcef ADDA | GEODECI
# FR : Plugins QGIS, solutions SIG et administration PostgreSQL/PostGIS ; audit, automatisation et accompagnement.
# EN : QGIS plugins, GIS solutions and PostgreSQL/PostGIS administration; audits, workflow automation and business support.
# AR : تطوير إضافات QGIS وحلول نظم المعلومات الجغرافية وإدارة PostgreSQL/PostGIS، والتدقيق والأتمتة ومرافقة المؤسسات.
# Site / Website / الموقع : https://geodeci.xyz/
# Contact professionnel / Business enquiries / للتواصل المهني : youcef.geodesien@gmail.com | contact@geodeci.xyz
# Détails / Details / التفاصيل : SIGNATURE.md
# ============================================================================
"""
Tests du pipeline de decoupage GeoPackage (gpkg_pipeline).

Couvre : operations spatiales pures, classification posemode,
I/O GeoPackage, dezippage, integration end-to-end avec fixture programmatique.
"""
from __future__ import annotations

import zipfile
from pathlib import Path

import pytest
from shapely.geometry import LineString, Point

from PoleAerien_shp.gpkg_pipeline import (
    AttacheInput,
    BpeInput,
    CableInput,
    PoteauInput,
    extract_gpkg_from_zip,
    extract_gpkg_tempdir,
    list_gpkg_in_zip,
    load_caches_from_gpkg,
    load_caches_from_zip,
    split_cables_from_gpkg,
)
from PoleAerien_shp.cable_segment import CableSegment as HistoricCableSegment
from PoleAerien_shp.gpkg_pipeline.gpkg_io import (
    blob_to_geometry,
    geometry_to_blob,
    init_gpkg,
    iter_features,
    list_layers,
    write_layer,
)
from PoleAerien_shp.gpkg_pipeline.posemode import (
    POSEMODE_AERIEN,
    POSEMODE_ORPHELIN,
    classify_posemode,
)
from PoleAerien_shp.gpkg_pipeline.spatial_ops import (
    attache_endpoints,
    build_point_index,
    dedup_segments,
    location_fraction,
    match_endpoint_to_infra,
    points_within,
    project_point_on_line,
    retract_cable,
    retract_endpoint_to_pole,
    snap_and_split,
)
from shapely.strtree import STRtree
from PoleAerien_shp.gpkg_pipeline.splitter import split_cables


# =============================================================================
# posemode.py
# =============================================================================


class TestPosemode:
    def test_both_pot_is_aerien(self):
        assert classify_posemode("pot_1", "pot_2") == POSEMODE_AERIEN

    def test_pot_bpe_is_aerien(self):
        assert classify_posemode("pot_1", "bpe_5") == POSEMODE_AERIEN
        assert classify_posemode("bpe_5", "pot_1") == POSEMODE_AERIEN

    def test_both_bpe_is_aerien(self):
        assert classify_posemode("bpe_1", "bpe_2") == POSEMODE_AERIEN

    def test_none_is_orphan(self):
        assert classify_posemode(None, "pot_1") == POSEMODE_ORPHELIN
        assert classify_posemode("pot_1", None) == POSEMODE_ORPHELIN
        assert classify_posemode(None, None) == POSEMODE_ORPHELIN

    def test_unknown_prefix_is_orphan(self):
        assert classify_posemode("chb_1", "pot_2") == POSEMODE_ORPHELIN
        assert classify_posemode("atr_1", "atr_2") == POSEMODE_ORPHELIN

    def test_att_prefix_is_orphan(self):
        # att_* sont des points de split techniques, pas une infra finale
        assert classify_posemode("att_1_s", "pot_2") == POSEMODE_ORPHELIN


# =============================================================================
# spatial_ops.py - operations pures
# =============================================================================


class TestSpatialOps:
    def test_project_point_perpendicular(self):
        line = LineString([(0, 0), (10, 0)])
        pt = Point(3, 2)
        proj = project_point_on_line(line, pt)
        assert proj is not None
        assert proj.x == pytest.approx(3.0)
        assert proj.y == pytest.approx(0.0)

    def test_location_fraction_middle(self):
        line = LineString([(0, 0), (10, 0)])
        frac = location_fraction(line, Point(5, 0))
        assert frac == pytest.approx(0.5)

    def test_location_fraction_start(self):
        line = LineString([(0, 0), (10, 0)])
        assert location_fraction(line, Point(0, 0)) == pytest.approx(0.0)

    def test_location_fraction_end(self):
        line = LineString([(0, 0), (10, 0)])
        assert location_fraction(line, Point(10, 0)) == pytest.approx(1.0)

    def test_snap_and_split_two_points(self):
        line = LineString([(0, 0), (10, 0)])
        splits = [Point(3, 0), Point(7, 0)]
        parts = snap_and_split(line, splits, snap_precision=0.01)
        assert len(parts) == 3
        lengths = sorted(p.length for p in parts)
        assert lengths == pytest.approx([3.0, 3.0, 4.0])

    def test_snap_and_split_empty_points_returns_original(self):
        line = LineString([(0, 0), (10, 0)])
        parts = snap_and_split(line, [], snap_precision=0.01)
        assert len(parts) == 1
        assert parts[0].length == pytest.approx(10.0)

    def test_snap_and_split_ignores_nan_points_no_warning(self):
        """Point avec NaN dans split_points doit etre ignore sans warning."""
        import warnings

        line = LineString([(0, 0), (10, 0)])
        splits = [Point(3, 0), Point(float("nan"), 0), Point(7, 0)]
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            parts = snap_and_split(line, splits, snap_precision=0.01)
        # Aucun RuntimeWarning de shapely
        assert not any("invalid value encountered" in str(warning.message) for warning in w)
        assert len(parts) == 3
        lengths = sorted(p.length for p in parts)
        assert lengths == pytest.approx([3.0, 3.0, 4.0])

    def test_attache_endpoints_returns_both(self):
        att = LineString([(1, 1), (5, 5), (9, 9)])
        s, e = attache_endpoints(att)
        assert (s.x, s.y) == (1, 1)
        assert (e.x, e.y) == (9, 9)

    def test_points_within_returns_only_in_tolerance(self):
        pts = [Point(0, 0), Point(5, 0), Point(100, 100)]
        tree, _ = build_point_index(pts)
        line = LineString([(0, 0), (10, 0)])
        found = points_within(tree, pts, line, tolerance=0.5)
        assert len(found) == 2  # (0,0) et (5,0), pas (100,100)

    def test_match_endpoint_primary_vs_fallback(self):
        pts = [Point(0.3, 0), Point(5.0, 0)]
        refs = ["pot_1", "pot_2"]
        tree, _ = build_point_index(pts)
        # endpoint a (0,0) : pot_1 en tier1 (0.3m < 0.7), pot_2 en tier2 (5m)
        ref = match_endpoint_to_infra(
            Point(0, 0), tree, pts, refs,
            tolerance_primary=0.7, tolerance_fallback=5.5,
        )
        assert ref == "pot_1"

    def test_match_endpoint_prio_pot_over_bpe(self):
        # 2 candidats equidistants mais 1 bpe + 1 pot : pot gagne
        pts = [Point(0.5, 0), Point(0.5, 0)]
        refs = ["bpe_1", "pot_2"]
        tree, _ = build_point_index(pts)
        ref = match_endpoint_to_infra(
            Point(0, 0), tree, pts, refs,
            tolerance_primary=0.7, tolerance_fallback=5.0,
        )
        assert ref == "pot_2"

    def test_match_endpoint_none_if_too_far(self):
        pts = [Point(100, 100)]
        refs = ["pot_1"]
        tree, _ = build_point_index(pts)
        ref = match_endpoint_to_infra(
            Point(0, 0), tree, pts, refs,
            tolerance_primary=0.7, tolerance_fallback=5.0,
        )
        assert ref is None

    def test_retract_endpoint_to_pole_hit(self):
        # Attache: poteau a (0,0), bout a (5,5). Cable start = (3,3) sur l'attache.
        att = LineString([(0, 0), (5, 5)])
        att_tree = STRtree([att])
        endpoint = Point(3, 3)  # sur la ligne attache
        pole = retract_endpoint_to_pole(endpoint, [att], att_tree, 0.05)
        assert pole is not None
        assert pole.x == pytest.approx(0.0)
        assert pole.y == pytest.approx(0.0)

    def test_retract_endpoint_to_pole_miss(self):
        att = LineString([(0, 0), (5, 5)])
        att_tree = STRtree([att])
        endpoint = Point(10, 10)  # loin de l'attache
        pole = retract_endpoint_to_pole(endpoint, [att], att_tree, 0.05)
        assert pole is None

    def test_retract_cable_modifies_start(self):
        # Cable (3,3)->(10,0), attache (0,0)->(5,5). Start (3,3) tombe sur attache.
        cable = LineString([(3, 3), (10, 0)])
        att = LineString([(0, 0), (5, 5)])
        att_tree = STRtree([att])
        result, modified = retract_cable(cable, [att], att_tree, 0.05)
        assert modified is True
        assert result.coords[0] == pytest.approx((0.0, 0.0), abs=0.01)
        assert result.coords[-1] == pytest.approx((10.0, 0.0), abs=0.01)

    def test_retract_cable_no_change_if_no_attache(self):
        cable = LineString([(1, 1), (10, 0)])
        result, modified = retract_cable(cable, [], STRtree([]), 0.05)
        assert modified is False
        assert result.equals(cable)

    def test_dedup_segments_removes_identical(self):
        l1 = LineString([(0, 0), (5, 0)])
        l2 = LineString([(0, 0), (5, 0)])  # meme geom
        l3 = LineString([(5, 0), (10, 0)])
        out = dedup_segments([(1, l1), (1, l2), (1, l3)])
        assert len(out) == 2


# =============================================================================
# gpkg_io.py : header GPKG binaire + R/W
# =============================================================================


class TestGpkgIo:
    def test_header_roundtrip_point(self):
        pt = Point(700000, 6600000)
        blob = geometry_to_blob(pt)
        assert blob[:2] == b"GP"
        g = blob_to_geometry(blob)
        assert g.equals(pt)

    def test_header_roundtrip_line(self):
        line = LineString([(0, 0), (100, 50), (200, 0)])
        blob = geometry_to_blob(line)
        g = blob_to_geometry(blob)
        assert g.equals(line)

    def test_init_gpkg_creates_system_tables(self, tmp_path):
        gpkg = str(tmp_path / "new.gpkg")
        init_gpkg(gpkg)
        assert Path(gpkg).is_file()
        # La liste est vide (aucune couche features encore), mais les tables systeme
        # existent
        layers = list_layers(gpkg)
        assert layers == []

    def test_write_and_read_layer(self, tmp_path):
        gpkg = str(tmp_path / "out.gpkg")
        rows = [
            {"gid": 1, "sro": "SRO1", "geom": Point(0, 0)},
            {"gid": 2, "sro": "SRO1", "geom": Point(10, 5)},
        ]
        n = write_layer(
            gpkg, "my_points", rows,
            geom_column="geom", geometry_type="POINT",
        )
        assert n == 2
        assert "my_points" in list_layers(gpkg)
        back = list(iter_features(gpkg, "my_points"))
        assert len(back) == 2
        attrs_by_gid = {r[0]["gid"]: r for r in back}
        assert attrs_by_gid[1][1].equals(Point(0, 0))
        assert attrs_by_gid[2][1].equals(Point(10, 5))
        assert attrs_by_gid[1][0]["sro"] == "SRO1"


# =============================================================================
# splitter.py : pipeline en memoire
# =============================================================================


def _line_wkb(coords):
    return bytes(LineString(coords).wkb)


def _point_wkb(x, y):
    return bytes(Point(x, y).wkb)


class TestSplitter:
    def test_cable_sans_appui_est_orphelin(self):
        cable = CableInput(gid=1, sro="S", wkb=_line_wkb([(0, 0), (10, 0)]))
        segs, stats = split_cables([cable], [], [], [])
        assert len(segs) == 1
        assert segs[0].posemode == POSEMODE_ORPHELIN
        assert stats.nb_segments_orphan == 1
        assert stats.nb_segments_aerien == 0

    def test_cable_entre_deux_poteaux_sans_split_mid(self):
        """Poteaux aux extremites exactes -> passthrough sans split."""
        cable = CableInput(gid=1, sro="S", wkb=_line_wkb([(0, 0), (10, 0)]))
        poteaux = [
            PoteauInput(gid=1, sro="S", wkb=_point_wkb(0, 0)),
            PoteauInput(gid=2, sro="S", wkb=_point_wkb(10, 0)),
        ]
        segs, _stats = split_cables([cable], poteaux, [], [])
        assert len(segs) == 1
        assert segs[0].posemode == POSEMODE_AERIEN
        assert segs[0].start_ref in ("pot_1", "pot_2")
        assert segs[0].end_ref in ("pot_1", "pot_2")

    def test_cable_decoupe_par_poteau_intermediaire(self):
        """Poteau au milieu -> 2 segments."""
        cable = CableInput(gid=1, sro="S", wkb=_line_wkb([(0, 0), (20, 0)]))
        poteaux = [
            PoteauInput(gid=1, sro="S", wkb=_point_wkb(0, 0)),
            PoteauInput(gid=2, sro="S", wkb=_point_wkb(10, 0)),  # milieu
            PoteauInput(gid=3, sro="S", wkb=_point_wkb(20, 0)),
        ]
        segs, _stats = split_cables([cable], poteaux, [], [])
        assert len(segs) == 2
        lengths = sorted(s.length for s in segs)
        assert lengths == pytest.approx([10.0, 10.0])
        for s in segs:
            assert s.posemode == POSEMODE_AERIEN

    def test_cable_decoupe_par_plusieurs_poteaux(self):
        """3 poteaux intermediaires -> 4 segments."""
        cable = CableInput(gid=1, sro="S", wkb=_line_wkb([(0, 0), (40, 0)]))
        poteaux = [
            PoteauInput(gid=i, sro="S", wkb=_point_wkb(x, 0))
            for i, x in enumerate([0, 10, 20, 30, 40])
        ]
        segs, _ = split_cables([cable], poteaux, [], [])
        assert len(segs) == 4
        assert all(s.length == pytest.approx(10.0) for s in segs)

    def test_poteau_decale_tolerance(self):
        """Poteau legerement decale (<tol) reste point de split."""
        cable = CableInput(gid=1, sro="S", wkb=_line_wkb([(0, 0), (20, 0)]))
        poteaux = [
            PoteauInput(gid=1, sro="S", wkb=_point_wkb(10, 0.3)),  # 0.3m off
        ]
        segs, _ = split_cables([cable], poteaux, [], [], tolerance=0.70)
        assert len(segs) == 2

    def test_poteau_trop_loin_ignore(self):
        """Poteau au-dela du fallback -> pas de split."""
        cable = CableInput(gid=1, sro="S", wkb=_line_wkb([(0, 0), (20, 0)]))
        poteaux = [
            PoteauInput(gid=1, sro="S", wkb=_point_wkb(10, 50)),  # 50m off
        ]
        segs, _ = split_cables([cable], poteaux, [], [])
        assert len(segs) == 1
        assert segs[0].posemode == POSEMODE_ORPHELIN

    def test_attache_decoupe_sur_endpoint(self):
        """Le cable passe par une attache dont l'extremite START
        sert de point de split (le poteau reel est cote END)."""
        cable = CableInput(gid=1, sro="S", wkb=_line_wkb([(0, 0), (30, 0)]))
        # Attache [(10,0), (12,2)] : start sur le cable, end off
        attaches = [
            AttacheInput(gid=1, sro="S", wkb=_line_wkb([(10, 0), (12, 2)])),
        ]
        # Poteaux aux extremites du cable + 1 poteau cote end de l'attache
        poteaux = [
            PoteauInput(gid=1, sro="S", wkb=_point_wkb(0, 0)),
            PoteauInput(gid=2, sro="S", wkb=_point_wkb(12, 2)),  # cote end attache
            PoteauInput(gid=3, sro="S", wkb=_point_wkb(30, 0)),
        ]
        segs, _ = split_cables([cable], poteaux, [], attaches)
        # On doit avoir 2 segments (split au x=10 grace a l'attache)
        assert len(segs) == 2
        lengths = sorted(s.length for s in segs)
        assert lengths[0] == pytest.approx(10.0)
        assert lengths[1] == pytest.approx(20.0)

    def test_bpe_aux_extremites(self):
        """BPE sert de point d'extremite valide (posemode aerien)."""
        cable = CableInput(gid=1, sro="S", wkb=_line_wkb([(0, 0), (10, 0)]))
        bpes = [
            BpeInput(gid=1, sro="S", wkb=_point_wkb(0, 0)),
            BpeInput(gid=2, sro="S", wkb=_point_wkb(10, 0)),
        ]
        segs, _ = split_cables([cable], [], bpes, [])
        assert len(segs) == 1
        assert segs[0].posemode == POSEMODE_AERIEN
        assert segs[0].start_ref in ("bpe_1", "bpe_2")
        assert segs[0].end_ref in ("bpe_1", "bpe_2")

    def test_prio_poteau_sur_bpe_si_equidistants(self):
        """Si poteau et BPE equidistants, poteau prioritaire (match_endpoint)."""
        cable = CableInput(gid=1, sro="S", wkb=_line_wkb([(0, 0), (10, 0)]))
        poteaux = [PoteauInput(gid=7, sro="S", wkb=_point_wkb(0.5, 0))]
        bpes = [BpeInput(gid=9, sro="S", wkb=_point_wkb(0.5, 0))]
        segs, _ = split_cables([cable], poteaux, bpes, [])
        assert segs[0].start_ref == "pot_7"

    def test_cable_invalide_ignore(self):
        """Cable avec WKB invalide -> compte dans ignored."""
        cable = CableInput(gid=1, sro="S", wkb=_point_wkb(0, 0))  # point au lieu de ligne
        segs, stats = split_cables([cable], [], [], [])
        assert len(segs) == 0
        assert stats.nb_cables_ignored_invalid == 1

    def test_segments_trop_courts_dropped(self):
        """Segments < min_segment_length exclus."""
        cable = CableInput(gid=1, sro="S", wkb=_line_wkb([(0, 0), (10, 0)]))
        poteaux = [
            PoteauInput(gid=1, sro="S", wkb=_point_wkb(0, 0)),
            PoteauInput(gid=2, sro="S", wkb=_point_wkb(0.1, 0)),  # 0.1m from start
            PoteauInput(gid=3, sro="S", wkb=_point_wkb(10, 0)),
        ]
        # min=0.2m : le micro-segment doit etre filtre
        _, stats = split_cables(
            [cable], poteaux, [], [], min_segment_length=0.2,
        )
        assert stats.dropped_too_short >= 1

    def test_recalage_retracte_start_vers_poteau(self):
        """Cable dont le start tombe sur une attache -> retracte vers le poteau.

        Attache = (0,0)->(3,3). Cable = (2,2)->(20,0).
        Le start (2,2) est sur l'attache -> retracte vers (0,0) = poteau.
        Poteau a (0,0) et (20,0) -> posemode aerien.
        """
        cable = CableInput(gid=1, sro="S", wkb=_line_wkb([(2, 2), (20, 0)]))
        attaches = [
            AttacheInput(gid=1, sro="S", wkb=_line_wkb([(0, 0), (3, 3)])),
        ]
        poteaux = [
            PoteauInput(gid=1, sro="S", wkb=_point_wkb(0, 0)),
            PoteauInput(gid=2, sro="S", wkb=_point_wkb(20, 0)),
        ]
        segs, stats = split_cables([cable], poteaux, [], attaches)
        assert stats.nb_cables_retracted == 1
        assert len(segs) == 1
        assert segs[0].posemode == POSEMODE_AERIEN
        assert segs[0].start_ref == "pot_1"

    def test_recalage_no_attache_no_retraction(self):
        """Sans attache, nb_cables_retracted reste a 0."""
        cable = CableInput(gid=1, sro="S", wkb=_line_wkb([(0, 0), (10, 0)]))
        poteaux = [
            PoteauInput(gid=1, sro="S", wkb=_point_wkb(0, 0)),
            PoteauInput(gid=2, sro="S", wkb=_point_wkb(10, 0)),
        ]
        _, stats = split_cables([cable], poteaux, [], [])
        assert stats.nb_cables_retracted == 0

    def test_stats_populees(self):
        cable = CableInput(gid=1, sro="SRO-X", wkb=_line_wkb([(0, 0), (10, 0)]))
        poteaux = [
            PoteauInput(gid=1, sro="SRO-X", wkb=_point_wkb(0, 0)),
            PoteauInput(gid=2, sro="SRO-X", wkb=_point_wkb(10, 0)),
        ]
        _, stats = split_cables(
            [cable], poteaux, [], [], sro="SRO-X",
        )
        assert stats.sro == "SRO-X"
        assert stats.nb_cables_in == 1
        assert stats.nb_poteaux == 2
        assert stats.duration_ms >= 0.0


# =============================================================================
# Integration : pipeline complet avec GeoPackage fixture
# =============================================================================


def _build_fixture_gpkg(path: str) -> None:
    """Cree un GPKG SRO de test avec cables + appuis + attaches."""
    # Cable 1 : (0,0) -> (40,0), doit etre decoupe en 4 segments de 10m
    # Cable 2 : (100,0) -> (110,0), 1 seul segment (aerien pot-pot)
    cables_rows = [
        {"gid": 1, "sro": "SRO_TEST", "cab_type": "DI",
         "geom": LineString([(0, 0), (40, 0)])},
        {"gid": 2, "sro": "SRO_TEST", "cab_type": "DI",
         "geom": LineString([(100, 0), (110, 0)])},
    ]
    poteaux_rows = [
        {"gid": 1, "sro": "SRO_TEST", "geom": Point(0, 0)},
        {"gid": 2, "sro": "SRO_TEST", "geom": Point(10, 0)},
        {"gid": 3, "sro": "SRO_TEST", "geom": Point(20, 0)},
        {"gid": 4, "sro": "SRO_TEST", "geom": Point(30, 0)},
        {"gid": 5, "sro": "SRO_TEST", "geom": Point(40, 0)},
        {"gid": 6, "sro": "SRO_TEST", "geom": Point(100, 0)},
        {"gid": 7, "sro": "SRO_TEST", "geom": Point(110, 0)},
    ]
    bpe_rows = [
        {"gid": 1, "sro": "SRO_TEST", "geom": Point(0, 0)},
    ]
    attaches_rows = [
        {"gid": 1, "sro": "SRO_TEST", "geom": LineString([(105, 0), (107, 2)])},
    ]

    write_layer(path, "cables", cables_rows,
                geom_column="geom", geometry_type="LINESTRING")
    write_layer(path, "bpe", bpe_rows,
                geom_column="geom", geometry_type="POINT")
    write_layer(path, "attaches", attaches_rows,
                geom_column="geom", geometry_type="LINESTRING")
    write_layer(path, "infra_pt_pot", poteaux_rows,
                geom_column="geom", geometry_type="POINT")


class TestPipelineEndToEnd:
    def test_decoupe_gpkg_complet(self, tmp_path):
        gpkg = str(tmp_path / "SRO_TEST.gpkg")
        _build_fixture_gpkg(gpkg)

        stats = split_cables_from_gpkg(
            input_gpkg=gpkg,
            output_gpkg=None,
            tolerance=0.70,
            sro="SRO_TEST",
        )

        assert stats.nb_cables_in == 2
        assert stats.nb_poteaux == 7
        assert stats.nb_bpe == 1
        assert stats.nb_attaches == 1
        # cable 1 : 4 segments de 10m, cable 2 : 1 segment de 10m = 5 total
        # + attache(105,0)-(107,2) : start (105,0) est sur cable 2 -> split
        # donc cable 2 est decoupe en 2 a x=105 -> 2 segments
        # Total : 4 + 2 = 6 segments
        assert stats.nb_segments_out == 6
        assert stats.nb_segments_aerien == 6

        # Verifier la couche de sortie
        out_layers = list_layers(gpkg)
        assert "cables_decoupes" in out_layers

        out = list(iter_features(gpkg, "cables_decoupes"))
        assert len(out) == 6
        lengths = sorted(attrs["length"] for attrs, _ in out)
        # 4 x 10m (cable1) + 5m + 5m (cable2 split a x=105)
        assert lengths[0] == pytest.approx(5.0)
        assert lengths[1] == pytest.approx(5.0)
        assert all(l == pytest.approx(10.0) for l in lengths[2:])

    def test_missing_layer_raises(self, tmp_path):
        gpkg = str(tmp_path / "partial.gpkg")
        # Seulement cables, sans bpe/attaches/infra_pt_pot
        write_layer(gpkg, "cables",
                    [{"gid": 1, "sro": "X", "geom": LineString([(0, 0), (5, 0)])}],
                    geom_column="geom", geometry_type="LINESTRING")
        with pytest.raises(ValueError, match="Couches manquantes"):
            split_cables_from_gpkg(gpkg)

    def test_file_not_found_raises(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            split_cables_from_gpkg(str(tmp_path / "ghost.gpkg"))

    def test_sortie_separee(self, tmp_path):
        """output_gpkg different de input : copie + ecriture couche."""
        src = str(tmp_path / "src.gpkg")
        dst = str(tmp_path / "dst.gpkg")
        _build_fixture_gpkg(src)
        stats = split_cables_from_gpkg(src, output_gpkg=dst)
        assert Path(dst).is_file()
        assert "cables_decoupes" in list_layers(dst)
        # La source reste intouchee (pas de couche cables_decoupes dans src)
        assert "cables_decoupes" not in list_layers(src)
        assert stats.nb_segments_out > 0


# =============================================================================
# zip_loader.py : dezippage d'un livrable GeoPackage
# =============================================================================


def _make_zip_with(members: dict, zip_path: str) -> None:
    """Utility : cree un ZIP a partir d'un dict {arcname: bytes|src_path}."""
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for arcname, content in members.items():
            if isinstance(content, (bytes, bytearray)):
                zf.writestr(arcname, bytes(content))
            else:
                zf.write(content, arcname=arcname)


class TestZipLoader:
    def test_extract_single_gpkg(self, tmp_path):
        """ZIP contenant un seul GPKG -> extraction directe."""
        src_gpkg = str(tmp_path / "src.gpkg")
        _build_fixture_gpkg(src_gpkg)
        zip_path = str(tmp_path / "livrable.zip")
        _make_zip_with({"SRO_TEST.gpkg": src_gpkg}, zip_path)

        dest = tmp_path / "out"
        dest.mkdir()
        extracted = extract_gpkg_from_zip(zip_path, str(dest))
        assert Path(extracted).is_file()
        assert Path(extracted).name == "SRO_TEST.gpkg"
        # Le GPKG extrait est fonctionnel : ses couches sont lisibles
        layers = list_layers(extracted)
        assert "cables" in layers
        assert "infra_pt_pot" in layers

    def test_list_gpkg_in_zip(self, tmp_path):
        """Liste les GPKG sans extraction."""
        src1 = str(tmp_path / "a.gpkg")
        src2 = str(tmp_path / "b.gpkg")
        _build_fixture_gpkg(src1)
        _build_fixture_gpkg(src2)
        zip_path = str(tmp_path / "multi.zip")
        _make_zip_with({"a.gpkg": src1, "nested/b.gpkg": src2}, zip_path)

        names = list_gpkg_in_zip(zip_path)
        assert set(names) == {"a.gpkg", "nested/b.gpkg"}

    def test_multi_gpkg_without_member_raises(self, tmp_path):
        """Plusieurs GPKG sans precision -> erreur explicite."""
        src1 = str(tmp_path / "a.gpkg")
        src2 = str(tmp_path / "b.gpkg")
        _build_fixture_gpkg(src1)
        _build_fixture_gpkg(src2)
        zip_path = str(tmp_path / "multi.zip")
        _make_zip_with({"a.gpkg": src1, "b.gpkg": src2}, zip_path)

        with pytest.raises(ValueError, match="2 .gpkg"):
            extract_gpkg_from_zip(zip_path, str(tmp_path))

    def test_multi_gpkg_with_member_ok(self, tmp_path):
        """Plusieurs GPKG + gpkg_member precise -> OK."""
        src1 = str(tmp_path / "a.gpkg")
        src2 = str(tmp_path / "b.gpkg")
        _build_fixture_gpkg(src1)
        _build_fixture_gpkg(src2)
        zip_path = str(tmp_path / "multi.zip")
        _make_zip_with({"a.gpkg": src1, "b.gpkg": src2}, zip_path)

        dest = tmp_path / "out"
        dest.mkdir()
        extracted = extract_gpkg_from_zip(
            zip_path, str(dest), gpkg_member="b.gpkg",
        )
        assert Path(extracted).name == "b.gpkg"

    def test_zip_avec_aucun_gpkg_raises(self, tmp_path):
        """ZIP sans GPKG -> ValueError."""
        zip_path = str(tmp_path / "no_gpkg.zip")
        _make_zip_with({"readme.txt": b"hello"}, zip_path)
        with pytest.raises(ValueError, match="Aucun .gpkg"):
            extract_gpkg_from_zip(zip_path, str(tmp_path))

    def test_zip_corrompu_raises(self, tmp_path):
        """Fichier non-ZIP -> BadZipFile."""
        fake = tmp_path / "bidon.zip"
        fake.write_bytes(b"ceci n'est pas un zip")
        with pytest.raises(zipfile.BadZipFile):
            extract_gpkg_from_zip(str(fake), str(tmp_path))

    def test_zip_introuvable_raises(self, tmp_path):
        """Fichier ZIP absent -> FileNotFoundError."""
        with pytest.raises(FileNotFoundError):
            extract_gpkg_from_zip(str(tmp_path / "ghost.zip"))

    def test_zip_slip_refuse(self, tmp_path):
        """Protection contre Zip-Slip (path traversal)."""
        # On force un arcname malicieux qui tenterait d'ecrire hors dest_dir.
        # Avec _flatten_name, ce cas est inoffensif ; on valide juste que
        # l'extraction reste dans le dossier cible.
        src = str(tmp_path / "src.gpkg")
        _build_fixture_gpkg(src)
        zip_path = str(tmp_path / "tricky.zip")
        _make_zip_with({"../evil.gpkg": src}, zip_path)

        dest = tmp_path / "sandbox"
        dest.mkdir()
        extracted = extract_gpkg_from_zip(zip_path, str(dest))
        # Le fichier doit etre dans dest (grace au _flatten_name), pas au-dessus
        assert Path(extracted).resolve().is_relative_to(dest.resolve())

    def test_tempdir_cleanup(self, tmp_path):
        """Context manager : dossier temp cree puis supprime."""
        src = str(tmp_path / "src.gpkg")
        _build_fixture_gpkg(src)
        zip_path = str(tmp_path / "livrable.zip")
        _make_zip_with({"SRO.gpkg": src}, zip_path)

        extracted_path_holder = []
        with extract_gpkg_tempdir(zip_path) as gpkg_path:
            assert Path(gpkg_path).is_file()
            extracted_path_holder.append(gpkg_path)
            # Le GPKG reste utilisable pendant le with
            assert "cables" in list_layers(gpkg_path)

        # Apres le with : le fichier et son dossier doivent avoir ete nettoyes
        assert not Path(extracted_path_holder[0]).exists()
        assert not Path(extracted_path_holder[0]).parent.exists()

    def test_extract_then_split(self, tmp_path):
        """Flux complet : dezipper -> decouper les cables -> couche de sortie."""
        src = str(tmp_path / "src.gpkg")
        _build_fixture_gpkg(src)
        zip_path = str(tmp_path / "livrable.zip")
        _make_zip_with({"SRO_TEST.gpkg": src}, zip_path)

        with extract_gpkg_tempdir(zip_path) as gpkg_path:
            stats = split_cables_from_gpkg(gpkg_path, sro="SRO_TEST")
            assert stats.nb_segments_out > 0
            assert "cables_decoupes" in list_layers(gpkg_path)


# =============================================================================
# loader.py : alimentation des caches batch depuis un livrable GPKG/ZIP
# =============================================================================


def _build_fixture_gpkg_with_attrs(path: str) -> None:
    """Comme _build_fixture_gpkg mais avec les attributs metier cable.

    Ces colonnes simulent un vrai livrable BE : cab_type, cab_capa, posemode,
    cb_etiquet sont indispensables pour les consommateurs Comac / Police C6.
    """
    cables_rows = [
        {"gid": 1, "sro": "SRO_TEST",
         "cab_type": "CDI", "cab_capa": 12, "cab_modulo": 1,
         "cb_etiquet": "CAB-001", "posemode": 1,
         "geom": LineString([(0, 0), (40, 0)])},
        {"gid": 2, "sro": "SRO_TEST",
         "cab_type": "TRA", "cab_capa": 48, "cab_modulo": 1,
         "cb_etiquet": "CAB-002", "posemode": 1,
         "geom": LineString([(100, 0), (110, 0)])},
    ]
    poteaux_rows = [
        {"gid": 1, "sro": "SRO_TEST", "geom": Point(0, 0)},
        {"gid": 2, "sro": "SRO_TEST", "geom": Point(10, 0)},
        {"gid": 3, "sro": "SRO_TEST", "geom": Point(20, 0)},
        {"gid": 4, "sro": "SRO_TEST", "geom": Point(30, 0)},
        {"gid": 5, "sro": "SRO_TEST", "geom": Point(40, 0)},
        {"gid": 6, "sro": "SRO_TEST", "geom": Point(100, 0)},
        {"gid": 7, "sro": "SRO_TEST", "geom": Point(110, 0)},
    ]
    bpe_rows = [
        {"gid": 1, "sro": "SRO_TEST", "noe_type": "BPE",
         "noe_codext": "BPE-A", "geom": Point(0, 0)},
        {"gid": 2, "sro": "SRO_TEST", "noe_type": "BPE",
         "noe_codext": "BPE-B", "geom": Point(40, 0)},
    ]
    attaches_rows = [
        {"gid": 1, "sro": "SRO_TEST", "noe_usage": "ATT",
         "geom": LineString([(105, 0), (107, 2)])},
    ]

    write_layer(path, "cables", cables_rows,
                geom_column="geom", geometry_type="LINESTRING")
    write_layer(path, "bpe", bpe_rows,
                geom_column="geom", geometry_type="POINT")
    write_layer(path, "attaches", attaches_rows,
                geom_column="geom", geometry_type="LINESTRING")
    write_layer(path, "infra_pt_pot", poteaux_rows,
                geom_column="geom", geometry_type="POINT")


class TestLoadCaches:
    def test_auto_split_produces_decoupes_layer(self, tmp_path):
        """Si `cables_decoupes` absent, auto_split le produit et le lit."""
        gpkg = str(tmp_path / "SRO_TEST.gpkg")
        _build_fixture_gpkg_with_attrs(gpkg)

        caches = load_caches_from_gpkg(gpkg, sro_hint="SRO_TEST")

        assert caches.segments_layer == "cables_decoupes"
        assert caches.split_on_the_fly is True
        assert "cables_decoupes" in list_layers(gpkg)
        assert caches.sro == "SRO_TEST"
        assert len(caches.cables) > 0
        assert all(isinstance(c, HistoricCableSegment) for c in caches.cables)

    def test_existing_decoupes_layer_used_as_is(self, tmp_path):
        """Si `cables_decoupes` deja present, pas de re-split."""
        gpkg = str(tmp_path / "pre_split.gpkg")
        _build_fixture_gpkg_with_attrs(gpkg)
        # Pre-split : genere cables_decoupes a partir du splitter
        split_cables_from_gpkg(gpkg, sro="SRO_TEST")
        assert "cables_decoupes" in list_layers(gpkg)

        caches = load_caches_from_gpkg(gpkg)

        assert caches.segments_layer == "cables_decoupes"
        assert caches.split_on_the_fly is False
        assert len(caches.cables) > 0

    def test_cable_attrs_preserved(self, tmp_path):
        """Verifie que cab_type, cab_capa, cb_etiquet, posemode sont propages."""
        gpkg = str(tmp_path / "SRO_TEST.gpkg")
        _build_fixture_gpkg_with_attrs(gpkg)

        caches = load_caches_from_gpkg(gpkg, sro_hint="SRO_TEST")

        # Cable 1 (CDI 12FO) doit etre present parmi les segments
        cdi = [c for c in caches.cables if c.cab_type == "CDI"]
        tra = [c for c in caches.cables if c.cab_type == "TRA"]
        assert len(cdi) >= 1
        assert len(tra) >= 1
        assert all(c.cab_capa == 12 for c in cdi)
        assert all(c.cab_capa == 48 for c in tra)
        assert all(c.posemode in (1, 4) for c in caches.cables)
        # Tous les segments ont un WKT non vide (indispensable pour Qgs.fromWkt)
        assert all(c.geom_wkt.startswith("LINESTRING") for c in caches.cables)

    def test_bpe_and_attaches_shape(self, tmp_path):
        """bpe_list et attaches doivent etre des list[dict] avec `geom_wkt`."""
        gpkg = str(tmp_path / "SRO_TEST.gpkg")
        _build_fixture_gpkg_with_attrs(gpkg)

        caches = load_caches_from_gpkg(gpkg, sro_hint="SRO_TEST")

        assert len(caches.bpe_list) == 2
        for bpe in caches.bpe_list:
            assert set(bpe.keys()) >= {"gid", "geom_wkt", "noe_type"}
            assert bpe["geom_wkt"].startswith("POINT")
        assert len(caches.attaches) == 1
        for att in caches.attaches:
            assert set(att.keys()) >= {"gid", "geom_wkt"}
            assert att["geom_wkt"].startswith("LINESTRING")

    def test_gpkg_sans_bpe_ni_attaches_ok(self, tmp_path):
        """Livrable minimal : cables + cables_decoupes sans bpe/attaches."""
        gpkg = str(tmp_path / "minimal.gpkg")
        # Cree seulement `cables_decoupes` (format post-split ou simplifie)
        write_layer(
            gpkg, "cables_decoupes",
            [{"gid": 1, "gid_dc": 1, "sro": "MIN", "length": 10.0,
              "cab_type": "CDI", "cab_capa": 6, "posemode": 1,
              "geom": LineString([(0, 0), (10, 0)])}],
            geom_column="geom", geometry_type="LINESTRING",
        )

        caches = load_caches_from_gpkg(gpkg, auto_split=False)

        assert len(caches.cables) == 1
        assert caches.cables[0].cab_type == "CDI"
        assert caches.bpe_list == []
        assert caches.attaches == []
        assert caches.sro == "MIN"

    def test_raise_si_aucune_couche_cables(self, tmp_path):
        """Ni `cables` ni `cables_decoupes` -> ValueError explicite."""
        gpkg = str(tmp_path / "bad.gpkg")
        init_gpkg(gpkg)
        # Couche arbitraire non relative aux cables
        write_layer(
            gpkg, "infra_pt_pot",
            [{"gid": 1, "geom": Point(0, 0)}],
            geom_column="geom", geometry_type="POINT",
        )
        with pytest.raises(ValueError, match="Aucune couche"):
            load_caches_from_gpkg(gpkg)

    def test_load_from_zip(self, tmp_path):
        """Cycle complet : ZIP -> dezipper -> charger caches -> cleanup."""
        src = str(tmp_path / "src.gpkg")
        _build_fixture_gpkg_with_attrs(src)
        zip_path = str(tmp_path / "livrable.zip")
        _make_zip_with({"SRO_TEST.gpkg": src}, zip_path)

        caches = load_caches_from_zip(zip_path, sro_hint="SRO_TEST")

        assert caches.sro == "SRO_TEST"
        assert len(caches.cables) > 0
        assert len(caches.bpe_list) == 2
        assert len(caches.attaches) == 1
        # Le chemin extrait est dans un tempdir supprime : le fichier ne doit
        # plus exister apres retour (cleanup auto)
        assert not Path(caches.source_gpkg).exists()

    def test_load_from_zip_keeps_extracted_if_requested(self, tmp_path):
        """keep_extracted=True preserve le GPKG dans extract_dir fourni."""
        src = str(tmp_path / "src.gpkg")
        _build_fixture_gpkg_with_attrs(src)
        zip_path = str(tmp_path / "livrable.zip")
        _make_zip_with({"SRO_TEST.gpkg": src}, zip_path)

        out_dir = tmp_path / "extracted"
        out_dir.mkdir()
        caches = load_caches_from_zip(
            zip_path, extract_dir=str(out_dir), keep_extracted=True,
        )
        assert Path(caches.source_gpkg).exists()
        assert Path(caches.source_gpkg).parent == out_dir.resolve() or \
               Path(caches.source_gpkg).parent.resolve() == out_dir.resolve()

    def test_caches_match_task_consumer_contract(self, tmp_path):
        """Garantit la compatibilite des caches avec les consommateurs :

        - ComacTask filtre `c.posemode in (1, 2) and c.cab_capa >= 6` puis
          distingue par `c.cab_type in ('CDI', 'TRA', 'RAC')`.
        - PoliceC6Task filtre `c.cab_type == 'CDI' and c.posemode in (1, 2)`.
        - Les BPE exposent `geom_wkt`, `noe_type`, `gid` (dict-access).
        - Les attaches exposent `geom_wkt` non vide (consomme par
          cable_analyzer._parse_attaches_geoms).
        """
        gpkg = str(tmp_path / "SRO_TEST.gpkg")
        _build_fixture_gpkg_with_attrs(gpkg)

        caches = load_caches_from_gpkg(gpkg, sro_hint="SRO_TEST")

        comac_filter = [c for c in caches.cables
                        if c.posemode in (1, 2) and c.cab_capa >= 6]
        assert comac_filter, "ComacTask doit trouver au moins 1 cable"
        police_filter = [c for c in caches.cables
                         if c.cab_type == 'CDI' and c.posemode in (1, 2)]
        assert police_filter, "PoliceC6Task doit trouver au moins 1 CDI aerien"

        for bpe in caches.bpe_list:
            # Acces par cle (dict), pas par attribut
            assert bpe['geom_wkt']
            _ = bpe['noe_type']
            _ = bpe['gid']

        for att in caches.attaches:
            assert att.get('geom_wkt', '')
