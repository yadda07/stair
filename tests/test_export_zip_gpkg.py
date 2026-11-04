# Youcef ADDA | GEODECI
# FR : Plugins QGIS, solutions SIG et administration PostgreSQL/PostGIS ; audit, automatisation et accompagnement.
# EN : QGIS plugins, GIS solutions and PostgreSQL/PostGIS administration; audits, workflow automation and business support.
# AR : تطوير إضافات QGIS وحلول نظم المعلومات الجغرافية وإدارة PostgreSQL/PostGIS، والتدقيق والأتمتة ومرافقة المؤسسات.
# Site / Website / الموقع : https://geodeci.xyz/
# Contact professionnel / Business enquiries / للتواصل المهني : youcef.geodesien@gmail.com | contact@geodeci.xyz
# Détails / Details / التفاصيل : SIGNATURE.md
# ============================================================================
"""Tests unitaires des fonctions pures de `export_zip_gpkg.py`.

Portee : uniquement ce qui est testable hors QGIS (pas de connexion PG,
pas de prompt, pas de QgsTask). Objectif : garantir que le SRO saisi
produit le bon nom de fichier et que les filtres metier appliques a
chaque table correspondent a ce que le plugin ST-AIR sait consommer.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType
from unittest.mock import MagicMock


# ---------------------------------------------------------------------------
# Mock qgis.* pour permettre l'import hors QGIS
# ---------------------------------------------------------------------------
def _install_qgis_mocks() -> None:
    for name in ("qgis", "qgis.core", "qgis.PyQt", "qgis.PyQt.QtWidgets"):
        if name not in sys.modules:
            sys.modules[name] = ModuleType(name)
    core = sys.modules["qgis.core"]
    core.QgsApplication = MagicMock()
    core.QgsDataSourceUri = MagicMock()
    core.QgsProject = MagicMock()
    core.QgsProviderRegistry = MagicMock()
    # QgsTask est utilise comme classe de base -> fournir un vrai type
    class _FakeTask:
        class CanCancel:
            pass
        def __init__(self, *args, **kwargs):
            pass
    core.QgsTask = _FakeTask
    core.QgsVectorFileWriter = MagicMock()
    core.QgsVectorLayer = MagicMock()
    widgets = sys.modules["qgis.PyQt.QtWidgets"]
    widgets.QInputDialog = MagicMock()
    widgets.QLineEdit = MagicMock()
    widgets.QDialog = MagicMock()
    widgets.QVBoxLayout = MagicMock()
    widgets.QHBoxLayout = MagicMock()
    widgets.QLabel = MagicMock()
    widgets.QPushButton = MagicMock()
    widgets.QListWidget = MagicMock()
    widgets.QListWidgetItem = MagicMock()
    widgets.QFileDialog = MagicMock()
    widgets.QDialogButtonBox = MagicMock()
    widgets.QCheckBox = MagicMock()

    pyqt = sys.modules["qgis.PyQt"]
    if not hasattr(pyqt, "QtCore"):
        qtcore = ModuleType("qgis.PyQt.QtCore")
        sys.modules["qgis.PyQt.QtCore"] = qtcore
        pyqt.QtCore = qtcore
    qtcore = sys.modules["qgis.PyQt.QtCore"]
    qtcore.Qt = MagicMock()


_install_qgis_mocks()

# Import du script cible sans declencher son auto-run (__name__ neutre).
_script_path = Path(__file__).parent.parent / "export_zip_gpkg.py"
_spec = importlib.util.spec_from_file_location("stair_export_under_test", _script_path)
export_mod = importlib.util.module_from_spec(_spec)
export_mod.__name__ = "stair_export_under_test"  # != '__main__' ni '__console__'
_spec.loader.exec_module(export_mod)


# ===========================================================================
# _validate_sro : accepte les 3 separateurs, normalise en slash/dash uppercase
# ===========================================================================
class TestValidateSro:
    EXPECTED = ("63041/B1I/PMZ/00003", "63041-B1I-PMZ-00003")

    def test_slash_format(self):
        assert export_mod._validate_sro("63041/B1I/PMZ/00003") == self.EXPECTED

    def test_dash_format_normalized(self):
        assert export_mod._validate_sro("63041-B1I-PMZ-00003") == self.EXPECTED

    def test_underscore_format_normalized(self):
        assert export_mod._validate_sro("63041_B1I_PMZ_00003") == self.EXPECTED

    def test_lowercase_uppercased(self):
        assert export_mod._validate_sro("63041/b1i/pmz/00003") == self.EXPECTED

    def test_whitespace_trimmed(self):
        assert export_mod._validate_sro("  63041/B1I/PMZ/00003  ") == self.EXPECTED

    def test_empty_rejected(self):
        assert export_mod._validate_sro("") is None
        assert export_mod._validate_sro(None) is None

    def test_missing_segment_rejected(self):
        assert export_mod._validate_sro("63041/B1I/PMZ") is None

    def test_wrong_digit_count_rejected(self):
        assert export_mod._validate_sro("6304/B1I/PMZ/00003") is None  # 4 chiffres
        assert export_mod._validate_sro("63041/B1I/PMZ/3") is None  # 1 chiffre

    def test_extra_suffix_rejected(self):
        # Le pattern est ancre : pas de suffixe apres le NUMERO
        assert export_mod._validate_sro("63041/B1I/PMZ/00003_CDC") is None

    def test_s05_middle_code_accepted(self):
        # Type intermediaire peut contenir des chiffres (ex: S05)
        assert export_mod._validate_sro("63471/S05/PMZ/49785") == (
            "63471/S05/PMZ/49785",
            "63471-S05-PMZ-49785",
        )


# ===========================================================================
# _sro_clause : construction du predicat SQL avec echappement
# ===========================================================================
class TestSroClause:
    def test_basic(self):
        clause = export_mod._sro_clause("63041/B1I/PMZ/00003")
        assert clause == "\"sro\" = '63041/B1I/PMZ/00003'"

    def test_escapes_single_quote(self):
        # Defense en profondeur contre injection (le SRO est deja valide,
        # mais _sro_clause reste sur en lui-meme).
        clause = export_mod._sro_clause("abc' OR 1=1--")
        assert "''" in clause  # quote echappe en ''
        assert "OR 1=1" in clause  # present tel quel, mais dans une string

    def test_custom_field(self):
        clause = export_mod._sro_clause("X", field="code_sro")
        assert clause == "\"code_sro\" = 'X'"


# ===========================================================================
# TABLE_FILTERS : ordre, presence, filtres metier
# ===========================================================================
class TestTableFilters:
    EXPECTED_TABLES = [
        "cables",
        "infra_pt_pot",
        "etude_cap_ft",
        "etude_comac",
        "bpe",
        "attaches",
        "data_gespot",
    ]

    def test_tables_list_exact(self):
        # Garantit que l'export ne livre ni plus ni moins que ces tables
        assert export_mod.TABLES == self.EXPECTED_TABLES

    def test_infra_pt_autres_not_exported(self):
        # Jamais lu par le plugin -> doit rester hors de l'export
        assert "infra_pt_autres" not in export_mod.TABLES

    def test_attaches_is_exported(self):
        # Obligatoire pour Police C6 / COMAC
        assert "attaches" in export_mod.TABLES

    def test_data_gespot_is_exported(self):
        # GESPOT integre dans le GPKG pour les STT
        assert "data_gespot" in export_mod.TABLES

    def test_singular_names_for_etude_tables(self):
        # Plugin attend `etude_cap_ft` et `etude_comac` (pas le pluriel BDD)
        assert "etude_cap_ft" in export_mod.TABLES
        assert "etude_comac" in export_mod.TABLES
        assert "etudes_cap_ft" not in export_mod.TABLES
        assert "etudes_comac" not in export_mod.TABLES

    def test_cables_filter_only_sro(self):
        where = export_mod.TABLE_FILTERS["cables"]("63041/B1I/PMZ/00003")
        assert where == "\"sro\" = '63041/B1I/PMZ/00003'"

    def test_infra_pt_pot_filter_has_three_clauses(self):
        where = export_mod.TABLE_FILTERS["infra_pt_pot"]("63041/B1I/PMZ/00003")
        assert "\"sro\" = '63041/B1I/PMZ/00003'" in where
        assert "'POT-FT'" in where
        assert "'POT-BT'" in where
        assert "IN" in where
        assert "\"affectation\"" in where
        assert "<> '3'" in where
        assert "IS NULL" in where  # tolere les affectation NULL

    def test_bpe_attaches_etudes_only_sro_filter(self):
        sro = "63041/B1I/PMZ/00003"
        for table in ("bpe", "attaches", "etude_cap_ft", "etude_comac"):
            where = export_mod.TABLE_FILTERS[table](sro)
            assert where == f"\"sro\" = '{sro}'", f"{table} doit avoir SRO uniquement"

    def test_data_gespot_spatial_filter(self):
        where = export_mod.TABLE_FILTERS["data_gespot"]("63041/B1I/PMZ/00003")
        assert "ST_Intersects" in where
        assert "za_sro" in where
        assert "\"sro\" = '63041/B1I/PMZ/00003'" in where
        assert "\"geom\"" in where

    def test_data_gespot_uses_gespot_schema(self):
        assert export_mod.TABLE_SCHEMAS["data_gespot"] == "gespot"

    def test_other_tables_use_rip_avg_nge_schema(self):
        for table in ("cables", "infra_pt_pot", "bpe", "attaches",
                       "etude_cap_ft", "etude_comac"):
            assert export_mod.TABLE_SCHEMAS[table] == "rip_avg_nge", \
                f"{table} should be in rip_avg_nge"


# ===========================================================================
# _sanitize_ident : anti-injection sur les identifiants SQL
# ===========================================================================
class TestSanitizeIdent:
    def test_preserves_safe_chars(self):
        assert export_mod._sanitize_ident("infra_pt_pot") == "infra_pt_pot"
        assert export_mod._sanitize_ident("rip_avg_nge") == "rip_avg_nge"

    def test_strips_semicolons(self):
        assert export_mod._sanitize_ident("a;DROP TABLE x") == "aDROPTABLEx"

    def test_strips_quotes_and_spaces(self):
        assert export_mod._sanitize_ident("foo' OR 1=1") == "fooOR11"
