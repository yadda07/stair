# -*- coding: utf-8 -*-
# Youcef ADDA | GEODECI
# FR : Plugins QGIS, solutions SIG et administration PostgreSQL/PostGIS ; audit, automatisation et accompagnement.
# EN : QGIS plugins, GIS solutions and PostgreSQL/PostGIS administration; audits, workflow automation and business support.
# AR : تطوير إضافات QGIS وحلول نظم المعلومات الجغرافية وإدارة PostgreSQL/PostGIS، والتدقيق والأتمتة ومرافقة المؤسسات.
# Site / Website / الموقع : https://geodeci.xyz/
# Contact professionnel / Business enquiries / للتواصل المهني : youcef.geodesien@gmail.com | contact@geodeci.xyz
# Détails / Details / التفاصيل : SIGNATURE.md
# ============================================================================
from .async_tasks import AsyncTaskBase
from .unified_report import generate_unified_report
from .version import get_version


class UnifiedReportExportTask(AsyncTaskBase):
    def __init__(self, request):
        super().__init__("Export rapport batch", request)

    def execute(self):
        if self.isCanceled():
            return False

        # Inject plugin version into batch_results for unified report
        batch_results = self.params.get('batch_results') or {}
        if 'metadata' not in batch_results:
            batch_results['metadata'] = {}
        batch_results['metadata']['plugin_version'] = get_version()

        filepath = generate_unified_report(
            batch_results,
            self.params.get('export_dir', ''),
            {
                'include_comac_drawings': bool(self.params.get('include_comac_drawings')),
                'include_data_dictionary': bool(self.params.get('include_data_dictionary')),
                'progress': self.emit_progress,
                'message': self.emit_message,
                'is_cancelled': self.isCanceled,
                'sro': self.params.get('sro', ''),
            }
        )

        if self.isCanceled() or not filepath:
            return False

        self.result = {
            'export_done': True,
            'filepath': filepath,
        }
        return True
