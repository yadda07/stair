# Youcef ADDA | GEODECI
# FR : Plugins QGIS, solutions SIG et administration PostgreSQL/PostGIS ; audit, automatisation et accompagnement.
# EN : QGIS plugins, GIS solutions and PostgreSQL/PostGIS administration; audits, workflow automation and business support.
# AR : تطوير إضافات QGIS وحلول نظم المعلومات الجغرافية وإدارة PostgreSQL/PostGIS، والتدقيق والأتمتة ومرافقة المؤسسات.
# Site / Website / الموقع : https://geodeci.xyz/
# Contact professionnel / Business enquiries / للتواصل المهني : youcef.geodesien@gmail.com | contact@geodeci.xyz
# Détails / Details / التفاصيل : SIGNATURE.md
# ============================================================================
import unittest
from types import SimpleNamespace

from pcm_drawing import PcmDrawingRenderer


def _support(name, orientation):
    return SimpleNamespace(
        nom=name,
        nature='BT',
        classe='C',
        hauteur=8.0,
        orientation=orientation,
        effort=0.0,
        a_poser=False,
        facade=False,
        portee_molle=False,
        illisible=False,
        non_calcule=False,
    )


def _etude():
    supports = {
        'A': _support('A', 0.0),
        'B': _support('B', 100.0),
        'C': _support('C', 200.0),
    }
    lignes_bt = [
        SimpleNamespace(
            supports=['A', 'B', 'C'],
            conducteur='BT-95',
            a_poser=False,
            armements=[
                SimpleNamespace(support='B', nom_armement='NAPPE', armement='NAPPE', decal_accro=12.0),
            ],
        )
    ]
    lignes_tcf = [
        SimpleNamespace(
            supports=['A', 'B'],
            cable='FO-24',
            a_poser=False,
        )
    ]
    portees_globales = [
        SimpleNamespace(support_gauche='A', support_droit='B', angle=0.0, longueur=32.0, route=False),
        SimpleNamespace(support_gauche='B', support_droit='C', angle=100.0, longueur=41.0, route=True),
    ]
    return SimpleNamespace(
        supports=supports,
        lignes_bt=lignes_bt,
        lignes_tcf=lignes_tcf,
        portees_globales=portees_globales,
    )


class TestPcmDrawingRenderer(unittest.TestCase):
    def test_build_support_entries_indexes_spans_per_support(self):
        renderer = PcmDrawingRenderer(dpi=72)
        entries = renderer.build_support_entries({'ETUDE-1': _etude()})

        self.assertEqual(3, len(entries))
        counts = {entry['support_name']: entry['connections'] for entry in entries}
        self.assertEqual(1, counts['A'])
        self.assertEqual(2, counts['B'])
        self.assertEqual(1, counts['C'])

    def test_render_entries_streams_png_bytes(self):
        renderer = PcmDrawingRenderer(dpi=72)
        entries = renderer.build_support_entries({'ETUDE-1': _etude()})

        diagrams = list(renderer.render_entries(entries))

        self.assertEqual(3, len(diagrams))
        self.assertTrue(all(diagram['image_bytes'] for diagram in diagrams))

    def test_build_support_entries_honors_cancellation(self):
        renderer = PcmDrawingRenderer(dpi=72)
        entries = renderer.build_support_entries({'ETUDE-1': _etude()}, lambda: True)

        self.assertIsNone(entries)


if __name__ == '__main__':
    unittest.main()
