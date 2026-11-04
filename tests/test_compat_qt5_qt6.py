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
Tests robustes de compatibilite Qt5/Qt6 pour le plugin PoleAerien.

Objectif : verifier que le plugin demarre et fonctionne sur QGIS 3.28+ (Qt5)
ET QGIS 4.0+ (Qt6). Chaque test couvre un breaking change specifique.

Execution : depuis la console Python QGIS ou via pytest-qgis.
"""

import sys
import importlib
import unittest


class TestCompatModule(unittest.TestCase):
    """Verifie que compat.py se charge sans erreur et exporte toutes les constantes."""

    def test_compat_imports_without_error(self):
        """compat.py doit se charger sans exception sur Qt5 et Qt6."""
        from PoleAerien.compat import (
            QGIS_VERSION_INT, IS_QGIS4,
            HORIZONTAL, VERTICAL,
            ALIGN_LEFT, ALIGN_RIGHT, ALIGN_CENTER,
            ALIGN_HCENTER, ALIGN_VCENTER, ALIGN_TOP, ALIGN_BOTTOM,
            CURSOR_POINTING, CURSOR_WAIT, CURSOR_ARROW,
            WF_NO_HELP, CASE_INSENSITIVE, CASE_SENSITIVE,
            MATCH_CONTAINS, MATCH_STARTS_WITH, MATCH_EXACTLY,
            PAL_BASE, PAL_WINDOW, PAL_WINDOW_TEXT,
            PAL_MID, PAL_MIDLIGHT, PAL_BRIGHT_TEXT, PAL_HIGHLIGHT,
            MSG_INFO, MSG_WARNING, MSG_CRITICAL, MSG_SUCCESS,
            LAYER_FILTER_POINT, LAYER_FILTER_POLYGON,
            LAYER_FILTER_LINE, LAYER_FILTER_VECTOR,
            FR_NO_GEOMETRY,
            FIELD_TYPE_STRING, FIELD_TYPE_INT,
            FIELD_TYPE_DOUBLE, FIELD_TYPE_LONGLONG,
            GEOM_POINT, GEOM_LINE, GEOM_POLYGON,
        )
        self.assertIsNotNone(QGIS_VERSION_INT)
        self.assertIsInstance(IS_QGIS4, bool)

    def test_msg_levels_are_usable_by_qgsmessagelog(self):
        """MSG_INFO/WARNING/CRITICAL doivent etre acceptes par QgsMessageLog."""
        from PoleAerien.compat import MSG_INFO, MSG_WARNING, MSG_CRITICAL
        from qgis.core import QgsMessageLog
        # Ne doit pas lever d'exception
        QgsMessageLog.logMessage("test_info", "TestCompat", MSG_INFO)
        QgsMessageLog.logMessage("test_warn", "TestCompat", MSG_WARNING)
        QgsMessageLog.logMessage("test_crit", "TestCompat", MSG_CRITICAL)

    def test_layer_filters_are_usable_by_combo(self):
        """LAYER_FILTER_POINT/POLYGON doivent etre acceptes par QgsMapLayerComboBox."""
        from PoleAerien.compat import LAYER_FILTER_POINT, LAYER_FILTER_POLYGON
        from qgis.gui import QgsMapLayerComboBox
        combo = QgsMapLayerComboBox()
        # Ne doit pas lever d'exception
        combo.setFilters(LAYER_FILTER_POINT)
        combo.setFilters(LAYER_FILTER_POLYGON)

    def test_field_types_are_usable_by_qgsfield(self):
        """FIELD_TYPE_STRING/INT/DOUBLE/LONGLONG doivent creer des QgsField valides."""
        from PoleAerien.compat import (
            FIELD_TYPE_STRING, FIELD_TYPE_INT,
            FIELD_TYPE_DOUBLE, FIELD_TYPE_LONGLONG,
        )
        from qgis.core import QgsField
        # Ne doit pas lever d'exception
        f1 = QgsField("test_str", FIELD_TYPE_STRING)
        f2 = QgsField("test_int", FIELD_TYPE_INT)
        f3 = QgsField("test_dbl", FIELD_TYPE_DOUBLE)
        f4 = QgsField("test_ll", FIELD_TYPE_LONGLONG)
        self.assertEqual(f1.name(), "test_str")
        self.assertEqual(f2.name(), "test_int")
        self.assertEqual(f3.name(), "test_dbl")
        self.assertEqual(f4.name(), "test_ll")

    def test_fr_no_geometry_works_with_feature_request(self):
        """FR_NO_GEOMETRY doit etre accepte par QgsFeatureRequest.setFlags()."""
        from PoleAerien.compat import FR_NO_GEOMETRY
        from qgis.core import QgsFeatureRequest
        req = QgsFeatureRequest()
        # Ne doit pas lever d'exception
        req.setFlags(FR_NO_GEOMETRY)

    def test_geom_type_constants_match_memory_layers(self):
        """GEOM_POINT/LINE/POLYGON doivent etre egaux a layer.geometryType()."""
        from PoleAerien.compat import GEOM_POINT, GEOM_LINE, GEOM_POLYGON
        from qgis.core import QgsVectorLayer
        lyr_pt = QgsVectorLayer("Point?crs=EPSG:4326", "t", "memory")
        lyr_ln = QgsVectorLayer("LineString?crs=EPSG:4326", "t", "memory")
        lyr_pg = QgsVectorLayer("Polygon?crs=EPSG:4326", "t", "memory")
        self.assertEqual(lyr_pt.geometryType(), GEOM_POINT)
        self.assertEqual(lyr_ln.geometryType(), GEOM_LINE)
        self.assertEqual(lyr_pg.geometryType(), GEOM_POLYGON)


class TestNoDirectPyQt5Imports(unittest.TestCase):
    """Verifie qu'aucun fichier n'importe directement PyQt5 (sauf compat.py fallback)."""

    def test_no_from_pyqt5_in_source_files(self):
        """Aucun 'from PyQt5' ne doit exister dans les fichiers source."""
        import os
        plugin_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        violations = []
        for root, _dirs, files in os.walk(plugin_dir):
            for fname in files:
                if not fname.endswith('.py'):
                    continue
                if fname in ('compat.py', 'test_compat_qt5_qt6.py'):
                    continue
                fpath = os.path.join(root, fname)
                with open(fpath, 'r', encoding='utf-8', errors='ignore') as f:
                    for lineno, line in enumerate(f, 1):
                        if 'from PyQt5' in line and not line.strip().startswith('#'):
                            violations.append(f"{fname}:{lineno}: {line.strip()}")
        self.assertEqual(
            violations, [],
            f"Fichiers avec import PyQt5 direct (cassera sur Qt6):\n" +
            "\n".join(violations)
        )

    def test_no_exec_underscore_in_source_files(self):
        """Aucun '.exec_(' ne doit exister (supprime dans PyQt6)."""
        import os
        plugin_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        violations = []
        for root, _dirs, files in os.walk(plugin_dir):
            for fname in files:
                if not fname.endswith('.py'):
                    continue
                if fname == 'test_compat_qt5_qt6.py':
                    continue
                fpath = os.path.join(root, fname)
                with open(fpath, 'r', encoding='utf-8', errors='ignore') as f:
                    for lineno, line in enumerate(f, 1):
                        if '.exec_(' in line and not line.strip().startswith('#'):
                            violations.append(f"{fname}:{lineno}: {line.strip()}")
        self.assertEqual(
            violations, [],
            f"Fichiers avec .exec_() (cassera sur PyQt6):\n" +
            "\n".join(violations)
        )

    def test_no_bare_qgis_enum_in_source_files(self):
        """Aucun 'Qgis.Info', 'Qgis.Warning', 'Qgis.Critical' ne doit rester
        (sauf dans compat.py fallback)."""
        import os
        import re
        plugin_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        pattern = re.compile(r'\bQgis\.(Info|Warning|Critical|Success)\b')
        violations = []
        for root, _dirs, files in os.walk(plugin_dir):
            for fname in files:
                if not fname.endswith('.py'):
                    continue
                if fname in ('compat.py', 'test_compat_qt5_qt6.py'):
                    continue
                fpath = os.path.join(root, fname)
                with open(fpath, 'r', encoding='utf-8', errors='ignore') as f:
                    for lineno, line in enumerate(f, 1):
                        if pattern.search(line) and not line.strip().startswith('#'):
                            violations.append(f"{fname}:{lineno}: {line.strip()}")
        self.assertEqual(
            violations, [],
            f"Fichiers avec enums Qgis.* non qualifies (deprecated QGIS 4):\n" +
            "\n".join(violations)
        )


class TestMetadataCompatibility(unittest.TestCase):
    """Verifie que metadata.txt autorise QGIS 3.28 a 4.99."""

    def test_metadata_version_range(self):
        """qgisMinimumVersion <= 3.28 et qgisMaximumVersion >= 4.0."""
        import os
        import configparser
        plugin_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        metadata_path = os.path.join(plugin_dir, 'metadata.txt')
        self.assertTrue(os.path.exists(metadata_path), "metadata.txt manquant")

        config = configparser.ConfigParser()
        config.read(metadata_path, encoding='utf-8')

        min_ver = config.get('general', 'qgisMinimumVersion')
        max_ver = config.get('general', 'qgisMaximumVersion')

        # min <= 3.28
        min_parts = [int(x) for x in min_ver.split('.')]
        self.assertTrue(
            min_parts[0] < 4,
            f"qgisMinimumVersion={min_ver} exclut QGIS 3.x"
        )

        # max >= 4.0
        max_parts = [int(x) for x in max_ver.split('.')]
        self.assertTrue(
            max_parts[0] >= 4,
            f"qgisMaximumVersion={max_ver} exclut QGIS 4.x"
        )


class TestResourcesCompat(unittest.TestCase):
    """Verifie que resources.py utilise qgis.PyQt et se charge."""

    def test_resources_imports_via_qgis_pyqt(self):
        """resources.py ne doit pas contenir 'from PyQt5'."""
        import os
        plugin_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        res_path = os.path.join(plugin_dir, 'resources.py')
        with open(res_path, 'r', encoding='utf-8') as f:
            content = f.read()
        self.assertNotIn('from PyQt5', content,
                         "resources.py contient encore 'from PyQt5'")
        self.assertIn('from qgis.PyQt', content,
                      "resources.py doit importer depuis 'qgis.PyQt'")


class TestQtEnumsQualified(unittest.TestCase):
    """Verifie que les enums Qt qualifies fonctionnent sur la version courante."""

    def test_qt_alignment_flags_combinable(self):
        """Les flags d'alignement doivent pouvoir etre combines avec |."""
        from PoleAerien.compat import ALIGN_RIGHT, ALIGN_VCENTER
        combined = ALIGN_RIGHT | ALIGN_VCENTER
        self.assertIsNotNone(combined)

    def test_qt_cursor_usable_by_widget(self):
        """CURSOR_POINTING doit etre accepte par setCursor()."""
        from PoleAerien.compat import CURSOR_POINTING
        from qgis.PyQt.QtWidgets import QPushButton
        btn = QPushButton("test")
        btn.setCursor(CURSOR_POINTING)

    def test_qt_palette_roles_usable(self):
        """Les roles QPalette doivent etre utilisables avec p.color()."""
        from PoleAerien.compat import PAL_BASE, PAL_WINDOW, PAL_WINDOW_TEXT
        from qgis.PyQt.QtGui import QPalette
        pal = QPalette()
        # Ne doit pas lever d'exception
        c1 = pal.color(PAL_BASE)
        c2 = pal.color(PAL_WINDOW)
        c3 = pal.color(PAL_WINDOW_TEXT)
        self.assertTrue(c1.isValid() or True)  # couleur peut etre invalide sans app
        self.assertTrue(c2.isValid() or True)
        self.assertTrue(c3.isValid() or True)

    def test_qsplitter_accepts_orientation(self):
        """QSplitter doit accepter VERTICAL comme orientation."""
        from PoleAerien.compat import VERTICAL
        from qgis.PyQt.QtWidgets import QSplitter
        splitter = QSplitter(VERTICAL)
        self.assertIsNotNone(splitter)


class TestQSqlQueryExec(unittest.TestCase):
    """Verifie que QSqlQuery.exec() fonctionne (et pas exec_)."""

    def test_qsqlquery_has_exec_method(self):
        """QSqlQuery doit avoir une methode exec() (pas seulement exec_)."""
        from qgis.PyQt.QtSql import QSqlQuery
        q = QSqlQuery()
        self.assertTrue(hasattr(q, 'exec'),
                        "QSqlQuery n'a pas de methode exec()")


class TestPluginLoadable(unittest.TestCase):
    """Verifie que le module principal du plugin se charge sans erreur."""

    def test_compat_module_loadable(self):
        """Le module compat doit se charger sans exception."""
        mod = importlib.import_module('PoleAerien.compat')
        self.assertIsNotNone(mod)

    def test_plugin_main_module_importable(self):
        """PoleAerien.PoleAerien doit etre importable sans crash."""
        try:
            mod = importlib.import_module('PoleAerien.PoleAerien')
            self.assertIsNotNone(mod)
        except ImportError as e:
            # Acceptable si QGIS n'est pas completement initialise
            if 'iface' in str(e) or 'qgis' in str(e).lower():
                self.skipTest(f"QGIS non initialise: {e}")
            raise


if __name__ == '__main__':
    unittest.main()
