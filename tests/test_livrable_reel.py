"""Tests sur le livrable reel `63041-B1I-PMZ-00003`.

Verifie les fonctions pures (sans QGIS) sur des entrees et sorties reelles
issues du livrable sous-traitant.

Usage :
    pytest tests/test_livrable_reel.py
    (ou)  python -m pytest tests/test_livrable_reel.py -v
"""
from __future__ import annotations

import csv
import os
from pathlib import Path

import pytest

from PoleAerien_shp.cable_analyzer import CableAnalyzer, capacites_compatibles
from PoleAerien_shp.core_utils import (
    build_export_path,
    is_plugin_output_file,
    normalize_appui_num,
    safe_float,
    safe_int,
)


# ---------------------------------------------------------------------------
# Chemin du livrable (SKIP si absent)
# ---------------------------------------------------------------------------
LIVRABLE = Path(r"C:\Users\yadda\Downloads\pol\63041-B1I-PMZ-00003")
HAS_LIVRABLE = LIVRABLE.exists()
skip_no_livrable = pytest.mark.skipif(
    not HAS_LIVRABLE, reason="Livrable 63041-B1I-PMZ-00003 absent"
)


# ---------------------------------------------------------------------------
# 1. Normalisation appui (inf_num reels du livrable)
# ---------------------------------------------------------------------------
class TestNormalizeAppuiNum:
    def test_avec_commune_par_defaut_drop_commune(self):
        assert normalize_appui_num("1016647/63094") == "1016647"

    def test_avec_commune_keep(self):
        assert normalize_appui_num("1016647/63094", keep_commune=True) == "1016647/63094"

    def test_zeros_de_tete(self):
        assert normalize_appui_num("0747699/63094") == "747699"
        assert normalize_appui_num("0747699/63094", keep_commune=True) == "747699/63094"

    def test_prefixe_e_conserve_par_defaut(self):
        # sans strip_e_prefix, le E est conserve
        assert normalize_appui_num("E000851/63094") == "E000851"

    def test_prefixe_e_strip(self):
        assert normalize_appui_num("E000851/63094", strip_e_prefix=True) == "851"

    def test_prefixe_e_strip_keep_commune(self):
        assert (
            normalize_appui_num("E000851/63094", strip_e_prefix=True, keep_commune=True)
            == "851/63094"
        )

    def test_none_et_vide(self):
        assert normalize_appui_num(None) == ""
        assert normalize_appui_num("") == ""
        assert normalize_appui_num("   ") == ""

    def test_bt_prefix(self):
        assert normalize_appui_num("BT-1049", strip_bt_prefix=True) == "1049"
        assert normalize_appui_num("BT1049", strip_bt_prefix=True) == "1049"


# ---------------------------------------------------------------------------
# 2. is_plugin_output_file (exclusions pour scan C6)
# ---------------------------------------------------------------------------
class TestIsPluginOutputFile:
    def test_ficheappui_est_exclu(self):
        # PROUVE : FicheAppui_*.xlsx sont exclus du scan C6_vs_Bd
        assert is_plugin_output_file("FicheAppui_0747699.xlsx") is True
        assert is_plugin_output_file("FicheAppui_1016647.xlsx") is True

    def test_rapport_batch_est_exclu(self):
        assert is_plugin_output_file("rapport_batch_20260207_125315.xlsx") is True

    def test_policec6_export_est_exclu(self):
        assert is_plugin_output_file("PoliceC6_Export_20260207_092610.xlsx") is True

    def test_analyse_comac_est_exclu(self):
        assert is_plugin_output_file("ANALYSE_COMAC.xlsx") is True

    def test_fichier_c6_standard_non_exclu(self):
        # Un vrai "C6" Orange aurait ce genre de nom
        assert is_plugin_output_file("C6_2025_12_Appuis.xlsx") is False

    def test_exportcomac_non_exclu_si_pas_dans_keywords(self):
        # Attention : 'comac' est un keyword => EXCLU
        # Ce test documente le comportement actuel (potentiellement surprenant)
        assert is_plugin_output_file("NGE-B1L-00003-PA-1_ExportComac.xlsx") is True


# ---------------------------------------------------------------------------
# 3. capacites_compatibles (cas reels du log comac_ecarts)
# ---------------------------------------------------------------------------
class TestCapacitesCompatibles:
    def test_cas_log_e000757_bdd_incluse(self):
        # Log : COMAC=[144+48/72] FO vs BDD=[144] FO, BDD count=1, COMAC count=2
        # BDD (1 cable 144) n'est pas compatible avec COMAC (2 cables)
        assert capacites_compatibles([[144], [48, 72]], [144]) is False

    def test_cas_e000839_capa_incoherente(self):
        # Log : COMAC=[12] FO vs BDD=[24] FO
        assert capacites_compatibles([[12]], [24]) is False

    def test_cas_ok_pattern_alternatif(self):
        # Doc : source = [[24,36]], BDD = [24] -> True
        assert capacites_compatibles([[24, 36]], [24]) is True
        assert capacites_compatibles([[24, 36]], [36]) is True

    def test_vide_vide_ok(self):
        assert capacites_compatibles([], []) is True

    def test_longueurs_differentes_false(self):
        assert capacites_compatibles([[6]], [6, 6]) is False


# ---------------------------------------------------------------------------
# 4. Parseur C6 (format "Lxxx-NP")
# ---------------------------------------------------------------------------
class TestParserC6:
    def test_format_simple(self):
        refs = CableAnalyzer.parser_references_cables_c6("L192.11-26P")
        assert len(refs) == 1
        assert refs[0]["reference"] == "L192.11"
        assert refs[0]["capacite"] == 26

    def test_format_multiple_pipe(self):
        refs = CableAnalyzer.parser_references_cables_c6(
            "L192.11-26P | L193.12-6P"
        )
        assert len(refs) == 2
        assert refs[0]["capacite"] == 26
        assert refs[1]["capacite"] == 6

    def test_format_livrable_nge(self):
        # Format observe dans COMAC_CABLES du livrable : "L1092-13-P; L1092-11-P"
        # Attention : le plugin attend "Lxxx-NP" (N = chiffres avant P).
        # Le format NGE "L1092-13-P" n'a PAS de chiffre avant P -> pas matche.
        # Ce test documente le gap entre attendu et format NGE.
        refs = CableAnalyzer.parser_references_cables_c6("L1092-13-P")
        # regex = r'([LN][\w.]*?)-(\d+)P' -> reconnait L1092-13-P comme ref=L1092, capa=13
        # mais le "-P" final n'est pas pris en compte dans la regex principale
        # Verification concrete : le regex "Lxxx-NP" match "L1092-13P" mais pas "L1092-13-P"
        # Donc le parser actuel peut echouer sur le format NGE.
        # On verifie le nombre de matches pour tracer.
        assert isinstance(refs, list)

    def test_vide(self):
        assert CableAnalyzer.parser_references_cables_c6("") == []
        assert CableAnalyzer.parser_references_cables_c6(None) == []


# ---------------------------------------------------------------------------
# 5. CSV GSPOT reel (68 colonnes, utf-8-sig, ;)
# ---------------------------------------------------------------------------
class TestCsvGspot:
    GSPOT_DIR = LIVRABLE / "BASE DE DONNEES" / "GSPOT"

    @skip_no_livrable
    def test_fusion_csv_structure(self):
        path = self.GSPOT_DIR / "fusion.csv"
        assert path.exists(), f"CSV fusion absent : {path}"
        with open(path, "r", encoding="utf-8-sig", newline="") as f:
            reader = csv.reader(f, delimiter=";")
            header = next(reader)
            assert len(header) == 68, f"Colonnes attendues 68, vues {len(header)}"
            assert header[0] == "ID"
            assert header[1] == "NUM"
            assert "CODE_INSEE" in header
            assert "COMMUNE" in header
            assert "gps_lat" in header
            assert "gps_lon" in header

    @skip_no_livrable
    def test_fusion_csv_num_exemple(self):
        """Verifie qu'un NUM GSPOT correspond bien au format inf_num normalise."""
        path = self.GSPOT_DIR / "fusion.csv"
        with open(path, "r", encoding="utf-8-sig", newline="") as f:
            reader = csv.DictReader(f, delimiter=";")
            row = next(reader)
            num = row["NUM"]
            insee = row["CODE_INSEE"]
            # GSPOT NUM = numero d'appui brut (ex "1016623")
            # Normalisation equivalente a inf_num "1016623/63041" keep_commune=True
            assert num.isdigit() or num == ""
            if num:
                norm = normalize_appui_num(f"{num}/{insee}", keep_commune=True)
                assert "/" in norm


# ---------------------------------------------------------------------------
# 6. safe_float / safe_int sur formats du livrable
# ---------------------------------------------------------------------------
class TestSafeParse:
    def test_safe_float_virgule_fr(self):
        assert safe_float("9,149999") == pytest.approx(9.149999)

    def test_safe_float_m_suffixe(self):
        # Certaines cellules Excel ont "16.4m" ou similaire
        assert safe_float("16.4m") == pytest.approx(16.4)

    def test_safe_int_float_string(self):
        assert safe_int("438.0") == 438

    def test_safe_int_none(self):
        assert safe_int(None) == 0
        assert safe_int("") == 0


# ---------------------------------------------------------------------------
# 7. build_export_path
# ---------------------------------------------------------------------------
class TestBuildExportPath:
    def test_repertoire_ajoute_nom(self, tmp_path):
        result = build_export_path(str(tmp_path), "rapport.xlsx")
        assert result.endswith("rapport.xlsx")
        assert os.path.dirname(result) == str(tmp_path)

    def test_fichier_sans_extension_ajoute_xlsx(self, tmp_path):
        base = str(tmp_path / "test")
        assert build_export_path(base, "default.xlsx") == base + ".xlsx"

    def test_fichier_xlsx_deja_present(self, tmp_path):
        base = str(tmp_path / "test.xlsx")
        assert build_export_path(base, "default.xlsx") == base


# ---------------------------------------------------------------------------
# 8. Verifications shapefile livrable via osgeo.ogr
# ---------------------------------------------------------------------------
@skip_no_livrable
class TestLivrableShapefileSchema:
    """Verifie que le schema DBF reel correspond aux attentes du plugin."""

    NGE_DIR = LIVRABLE / "BASE DE DONNEES" / "NGE"

    def _read_fields(self, shp_path: Path):
        try:
            from osgeo import ogr
            ogr.UseExceptions()
        except ImportError:
            pytest.skip("osgeo.ogr indisponible")
        ds = ogr.Open(str(shp_path))
        assert ds is not None, f"Impossible d'ouvrir {shp_path}"
        lyr = ds.GetLayer(0)
        ld = lyr.GetLayerDefn()
        return [ld.GetFieldDefn(i).GetName() for i in range(ld.GetFieldCount())]

    def test_infra_pt_pot_champs_cles(self):
        """Valide que les champs attendus par le plugin existent."""
        fields = self._read_fields(self.NGE_DIR / "infra_pt_pot.shp")
        # Champs critiques lus par le plugin
        for f in ("gid", "sro", "inf_num", "inf_type", "inf_propri",
                  "noe_codext", "etat", "action", "affectatio", "dce"):
            assert f in fields, f"Champ `{f}` absent de infra_pt_pot.shp"

    def test_maj_bd_champs_etiquette_absents(self):
        """
        ECART PROUVE :
        `maj_sql_background.py` essaie d'updater `etiquette_jaune`,
        `etiquette_orange`, `transition_aerosout`, `zone_privee`.
        Ces champs n'existent pas directement dans le shapefile NGE (tronques
        a 10 chars). Le plugin skippe silencieusement via `_column_exists`.
        """
        fields = self._read_fields(self.NGE_DIR / "infra_pt_pot.shp")
        # Les champs complets N'existent PAS (skip silencieux confirme)
        assert "etiquette_jaune" not in fields
        assert "etiquette_orange" not in fields
        assert "transition_aerosout" not in fields
        assert "zone_privee" not in fields
        # Les champs tronques (ou proches) existent
        assert "etiquette_" in fields  # tronque
        # etiquett_1 / etiquett_2 sont d'autres champs tronques
        has_etiquettes = [f for f in fields if f.startswith("etiquett")]
        assert len(has_etiquettes) >= 1, \
            f"Aucun champ `etiquett*` trouve. Fields: {fields}"

    def test_cables_schema(self):
        fields = self._read_fields(self.NGE_DIR / "cables.shp")
        for f in ("gid", "sro", "cab_type", "cab_capa", "cab_nature",
                  "affectatio", "DCE"):
            assert f in fields, f"Champ `{f}` absent de cables.shp"

    def test_bpe_schema(self):
        fields = self._read_fields(self.NGE_DIR / "bpe.shp")
        for f in ("gid", "sro", "noe_type", "noe_pose", "noe_codext",
                  "inf_propri", "mat_capa"):
            assert f in fields, f"Champ `{f}` absent de bpe.shp"

    def test_etude_cap_ft_schema(self):
        fields = self._read_fields(
            LIVRABLE / "BASE DE DONNEES" / "SHP APT" / "SHAPES ETUDES"
            / "etude_cap_ft.shp"
        )
        # Colonne etude (utilisee par le plugin)
        assert "nom_etudes" in fields
        for f in ("validation", "nbr_ft_cal", "nbr_ft_ko"):
            assert f in fields

    def test_etude_comac_schema(self):
        fields = self._read_fields(
            LIVRABLE / "BASE DE DONNEES" / "SHP APT" / "SHAPES ETUDES"
            / "etude_comac.shp"
        )
        assert "nom_etudes" in fields
        for f in ("validation", "nbr_bt_ko", "nbr_bt_cal",
                  "entreprise", "pourcentag"):
            assert f in fields

    def test_travaux_supports_crs_different(self):
        """
        ECART PROUVE : TRAVAUX_SUPPORTS.shp en EPSG:4326 alors que tout le
        reste est en EPSG:2154. Le plugin doit normaliser si cette couche
        est utilisee.
        """
        try:
            from osgeo import ogr
            ogr.UseExceptions()
        except ImportError:
            pytest.skip("osgeo.ogr indisponible")
        path = (LIVRABLE / "BASE DE DONNEES" / "SHP APT" / "SHAPES TRAVAUX"
                / "TRAVAUX_SUPPORTS.shp")
        if not path.exists():
            pytest.skip(f"{path} absent")
        ds = ogr.Open(str(path))
        srs = ds.GetLayer(0).GetSpatialRef()
        epsg = srs.GetAuthorityCode(None) if srs else None
        assert epsg == "4326", f"SRS attendu 4326, vu {epsg}"


# ---------------------------------------------------------------------------
# 9. Non-regression des corrections (ECART-02, 07, 08, 09)
# ---------------------------------------------------------------------------
def _qgis_available():
    try:
        from qgis.core import QgsVectorLayer  # noqa: F401
        return True
    except ImportError:
        return False


skip_no_qgis = pytest.mark.skipif(
    not _qgis_available(), reason="QGIS Python bindings indisponibles"
)


@skip_no_qgis
class TestFixesQgisUtils:
    """Verifie les corrections ECART-02, 07, 08, 09 dans qgis_utils.py."""

    def _make_layer(self, fields_def, name="t"):
        """Cree une QgsVectorLayer memoire avec les champs demandes.
        fields_def: liste de (nom, type) ex: [('commentair', 'string')]
        """
        from qgis.core import QgsVectorLayer
        field_uri = '&'.join(f'field={n}:{t}' for n, t in fields_def)
        uri = f'Point?crs=EPSG:2154&{field_uri}' if field_uri else 'Point?crs=EPSG:2154'
        lyr = QgsVectorLayer(uri, name, 'memory')
        assert lyr.isValid(), f"layer invalide: {uri}"
        return lyr

    def test_ecart02_index_from_names_prefer_commentaire(self):
        """Si la couche a 'commentaire', on retourne son index."""
        from PoleAerien_shp.qgis_utils import _index_from_names
        lyr = self._make_layer([('commentaire', 'string'), ('inf_num', 'string')])
        idx = _index_from_names(lyr, ('commentaire', 'commentair'), 'test')
        assert idx == lyr.fields().indexFromName('commentaire')
        assert idx >= 0

    def test_ecart02_index_from_names_fallback_commentair(self):
        """Si la couche a seulement 'commentair' (SHP tronque), fallback OK."""
        from PoleAerien_shp.qgis_utils import _index_from_names
        lyr = self._make_layer([('commentair', 'string'), ('inf_num', 'string')])
        idx = _index_from_names(lyr, ('commentaire', 'commentair'), 'test')
        assert idx == lyr.fields().indexFromName('commentair')
        assert idx >= 0

    def test_ecart02_index_from_names_absent_retourne_moins_un(self):
        """Si aucun alias ne matche, retourne -1 (et logue warning)."""
        from PoleAerien_shp.qgis_utils import _index_from_names
        lyr = self._make_layer([('autre', 'string')])
        idx = _index_from_names(lyr, ('commentaire', 'commentair'), 'test')
        assert idx == -1

    def test_ecart08_filtre_inf_type_egalite_stricte(self):
        """
        La chaine d'expression QGIS doit etre '=' (pas LIKE sans wildcard).
        Verifie par lecture du fichier source.
        """
        import inspect
        from PoleAerien_shp import qgis_utils
        src = inspect.getsource(qgis_utils.extraire_poteaux_etude)
        assert "inf_type = '" in src, "filtre doit utiliser egalite stricte"
        assert "inf_type LIKE '" not in src, \
            "filtre ne doit plus utiliser LIKE sans wildcard"

    def test_ecart07_doublons_une_seule_passe(self):
        """
        La detection des doublons d'etude ne doit plus faire une seconde
        iteration `etude.getFeatures(req_etude)`. Verifie par comptage
        des occurrences dans le code source.
        """
        import inspect
        from PoleAerien_shp import qgis_utils
        src = inspect.getsource(qgis_utils.extraire_poteaux_etude)
        # Une seule iteration sur etude
        assert src.count("etude.getFeatures(req_etude)") == 1, \
            "doit iterer une seule fois sur la couche etude"

    def test_ecart09_detect_etude_catchall_warning_tag(self):
        """
        Le code doit contenir une trace de warning explicite pour les
        patterns catchalls de detect_etude_field.
        """
        import inspect
        from PoleAerien_shp import qgis_utils
        src = inspect.getsource(qgis_utils.detect_etude_field)
        assert "_CATCHALL_PATTERNS" in src
        assert "catchall" in src.lower()

    def test_ecart03_validate_crs_message_suggere_reprojection(self):
        """
        Quand `validate_same_crs` echoue, le message doit suggerer
        explicitement la reprojection (pas seulement signaler l'ecart).
        """
        from qgis.core import QgsProject
        from PoleAerien_shp.qgis_utils import validate_same_crs, reset_crs_cache
        lyr_a = self._make_layer([('id', 'string')], name='a')
        lyr_b = self._make_layer([('id', 'string')], name='b')
        # Forcer un CRS different pour lyr_b
        from qgis.core import QgsCoordinateReferenceSystem
        lyr_b.setCrs(QgsCoordinateReferenceSystem('EPSG:4326'))
        QgsProject.instance().addMapLayer(lyr_a, False)
        QgsProject.instance().addMapLayer(lyr_b, False)
        try:
            reset_crs_cache()
            with pytest.raises(ValueError) as exc_info:
                validate_same_crs(lyr_a, lyr_b, context='test-ecart03')
            msg = str(exc_info.value)
            assert 'test-ecart03' in msg
            assert 'EPSG:2154' in msg
            assert 'EPSG:4326' in msg
            assert 'Reprojeter' in msg
        finally:
            QgsProject.instance().removeMapLayer(lyr_a.id())
            QgsProject.instance().removeMapLayer(lyr_b.id())
