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
Workflow pour l'analyse COMAC.
Orchestre l'extraction des données, l'analyse asynchrone et l'export Excel.
"""

from qgis.PyQt.QtCore import QObject, pyqtSignal
from qgis.PyQt.QtWidgets import QApplication
from qgis.core import Qgis, QgsMessageLog
from ..compat import MSG_INFO, MSG_WARNING, MSG_CRITICAL, FIELD_TYPE_STRING, FIELD_TYPE_INT, FIELD_TYPE_DOUBLE, FIELD_TYPE_LONGLONG
from ..Comac import Comac
from ..async_tasks import ComacTask, ExcelExportTask, run_async_task
from ..core_utils import build_export_path
from ..qgis_utils import extract_sro_from_layer
from ..cable_analyzer import extraire_appuis_wkb
from ..qgis_utils import show_feature_count
from ..perf_logger import PerfLogger
import os
import time

class ComacWorkflow(QObject):
    """
    Contrôleur pour le flux d'analyse COMAC.
    Gère:
    1. L'extraction des données QGIS (Main Thread)
    2. Le lancement de la tâche asynchrone d'analyse (Worker Thread)
    3. Le lancement de la tâche asynchrone d'export (Worker Thread)
    4. La communication des résultats via signaux
    """
    
    # Signaux relayés vers l'UI
    progress_changed = pyqtSignal(int)
    message_received = pyqtSignal(str, str)  # message, couleur
    analysis_finished = pyqtSignal(dict)
    export_finished = pyqtSignal(dict)
    error_occurred = pyqtSignal(str)
    
    def __init__(self):
        super().__init__()
        self.comac_logic = Comac()
        self.current_task = None

    def cancel(self):
        if self.current_task:
            self.current_task.cancel()

    def start_analysis(self, lyr_pot, lyr_comac, col_comac, chemin_comac, chemin_export,
                        fddcpi_cache=None, sro_appuis_cache=None,
                        sro=None, spatial_tolerance=7.5):
        """
        Lance l'analyse COMAC.
        
        Args:
            lyr_pot (QgsVectorLayer): Couche Poteaux (infra_pt_pot)
            lyr_comac (QgsVectorLayer): Couche Etude COMAC
            col_comac (str): Colonne identifiant l'etude dans la couche COMAC
            chemin_comac (str): Repertoire des fichiers COMAC
            chemin_export (str): Chemin pour le fichier Excel de sortie
            fddcpi_cache (list|None): CableSegment list from previous fddcpi2 call (batch optimization)
            sro_appuis_cache (dict|None): {'sro': str, 'appuis_wkb': list} from another module (batch optimization)
        """
        if not lyr_pot or not lyr_comac:
            self.error_occurred.emit("Couches invalides ou manquantes")
            return

        if not os.path.isdir(chemin_comac):
            self.error_occurred.emit("Repertoire COMAC invalide")
            return

        # 1. Extraction des donnees (Main Thread) - passe unique
        _t0 = time.perf_counter()
        try:
            doublons, hors_etude, dico_qgis, dico_poteaux_prives, all_inf_nums, coords_qgis = (
                self.comac_logic.extraire_donnees_comac(
                    lyr_pot.name(), lyr_comac.name(), col_comac
                )
            )
        except ValueError as e:
            self.error_occurred.emit(str(e))
            return
        except Exception as e:
            QgsMessageLog.logMessage(f"Erreur extraction COMAC: {e}", "PoleAerien", MSG_CRITICAL)
            self.error_occurred.emit(f"Erreur technique extraction: {e}")
            return
        _n_pot = sum(len(v) for v in dico_qgis.values())
        PerfLogger.record('comac', 'extraction_qgis',
                          (time.perf_counter() - _t0) * 1000, sro=sro or '', feature_count=_n_pot)
        self.message_received.emit(
            f"[PERF] COMAC extraction_qgis: {int((time.perf_counter()-_t0)*1000)}ms "
            f"({_n_pot} poteaux)", "grey"
        )

        # 2. Extraction SRO + appuis WKB pour verif cables (Main Thread)
        # SRO: reutiliser le cache batch si disponible
        if sro_appuis_cache:
            sro = sro_appuis_cache.get('sro') or sro
        if not sro:
            sro = extract_sro_from_layer(lyr_pot)
        # Appuis WKB: reutiliser appuis_wkb_commune si pre-extrait en passe unique
        appuis_wkb = []
        if sro_appuis_cache and sro_appuis_cache.get('appuis_wkb_commune') is not None:
            appuis_wkb = sro_appuis_cache['appuis_wkb_commune']
        elif sro:
            _t1 = time.perf_counter()
            appuis_wkb = extraire_appuis_wkb(lyr_pot, keep_commune=True)
            PerfLogger.record('comac', 'appuis_wkb_extract',
                              (time.perf_counter() - _t1) * 1000, sro=sro, feature_count=len(appuis_wkb))
        else:
            QgsMessageLog.logMessage(
                "[COMAC] SRO non trouve - verif cables desactivee",
                "PoleAerien", MSG_WARNING
            )

        # 3. Preparation des donnees pour la Task (lecture Excel COMAC dans le worker QgsTask)
        fichier_export = build_export_path(chemin_export, "ANALYSE_COMAC.xlsx")

        params = {
            'chemin_comac': chemin_comac,
            'fichier_export': fichier_export,
            'zone_climatique': 'ZVN',
            'sro': sro,
            'fddcpi_cables_cache': fddcpi_cache,
            'spatial_tolerance': spatial_tolerance,
        }

        qgis_data = {
            'doublons': list(doublons),
            'hors_etude': list(hors_etude),
            'dico_qgis': dico_qgis,
            'dico_poteaux_prives': dico_poteaux_prives,
            'appuis': appuis_wkb,
            'all_inf_nums': all_inf_nums,
            'coords_qgis': coords_qgis,
        }

        # Lancement Task Asynchrone (fusion / comparaison uniquement)
        self.current_task = ComacTask(params, qgis_data)
        
        self.current_task.signals.progress.connect(self.progress_changed)
        self.current_task.signals.message.connect(self.message_received)
        self.current_task.signals.finished.connect(self._on_task_finished)
        self.current_task.signals.error.connect(self.error_occurred)
        
        # Utilisation de run_async_task pour compatibilité avec async_tasks.py
        run_async_task(self.current_task)

    def _on_task_finished(self, result):
        """Charge la couche cables fddcpi2 dans QGIS puis emet analysis_finished."""
        cables_all = result.get('fddcpi_cables_all')
        sro = result.get('fddcpi_sro', '')

        if cables_all:
            cables_aerial = [
                c for c in cables_all
                if c.posemode in (1, 2) and c.cab_capa >= 6
            ]
            cables_for_layer = [
                {
                    'gid_dc2': c.gid_dc2,
                    'gid_dc': c.gid_dc,
                    'cab_capa': c.cab_capa,
                    'cab_type': c.cab_type,
                    'cb_etiquet': c.cb_etiquet,
                    'posemode': c.posemode,
                    'length': c.length,
                    'geom_wkt': c.geom_wkt,
                }
                for c in cables_aerial
            ]
            if cables_for_layer:
                try:
                    self._load_cables_layer(cables_for_layer, sro)
                except Exception as e:
                    self.message_received.emit(
                        f"[!] Erreur chargement couche cables: {e}", "orange"
                    )

        self.analysis_finished.emit(result)

    def _load_cables_layer(self, cables, sro):
        """Charge les cables recales comme couche temporaire dans QGIS."""
        import warnings
        from qgis.core import (
            QgsVectorLayer, QgsFeature, QgsGeometry, QgsField, QgsProject
        )

        sro_safe = sro.replace('/', '_')
        layer_name = f"cables_recales_{sro_safe}"

        layer = QgsVectorLayer(
            "LineString?crs=EPSG:2154",
            layer_name,
            "memory"
        )
        provider = layer.dataProvider()

        with warnings.catch_warnings():
            warnings.filterwarnings("ignore", category=DeprecationWarning)
            provider.addAttributes([
                QgsField("gid_dc2", FIELD_TYPE_LONGLONG),
                QgsField("gid_dc", FIELD_TYPE_LONGLONG),
                QgsField("cab_capa", FIELD_TYPE_INT),
                QgsField("cab_type", FIELD_TYPE_STRING),
                QgsField("cb_etiquet", FIELD_TYPE_STRING),
                QgsField("posemode", FIELD_TYPE_INT),
                QgsField("length", FIELD_TYPE_DOUBLE),
            ])
        layer.updateFields()

        features = []
        for cable in cables:
            wkt = cable.get('geom_wkt')
            if not wkt:
                continue
            geom = QgsGeometry.fromWkt(wkt)
            if geom.isNull() or geom.isEmpty():
                continue
            feat = QgsFeature(layer.fields())
            feat.setGeometry(geom)
            feat.setAttribute("gid_dc2", cable.get('gid_dc2'))
            feat.setAttribute("gid_dc", cable.get('gid_dc'))
            feat.setAttribute("cab_capa", cable.get('cab_capa'))
            feat.setAttribute("cab_type", cable.get('cab_type', ''))
            feat.setAttribute("cb_etiquet", cable.get('cb_etiquet', ''))
            feat.setAttribute("posemode", cable.get('posemode'))
            feat.setAttribute("length", cable.get('length'))
            features.append(feat)

        if not features:
            self.message_received.emit(
                "Couche cables: 0 features valides (WKT absent ou invalide)", "orange"
            )
            return

        provider.addFeatures(features)
        layer.updateExtents()

        QgsProject.instance().addMapLayer(layer)

        root = QgsProject.instance().layerTreeRoot()
        node = root.findLayer(layer.id())
        if node:
            node.setItemVisibilityChecked(True)
            node.setCustomProperty("showFeatureCount", True)

        style_path = os.path.join(
            os.path.dirname(os.path.dirname(__file__)),
            'styles', 'cables_dc.qml'
        )
        if os.path.exists(style_path):
            layer.loadNamedStyle(style_path)
            layer.triggerRepaint()

        try:
            from qgis.PyQt.QtCore import QTimer
            from qgis.utils import iface as _iface
            if _iface:
                QTimer.singleShot(200, _iface.mapCanvas().refresh)
        except Exception:
            pass

        self.message_received.emit(
            f"Couche '{layer.name()}' chargee: {len(features)} cables aeriens/facade",
            "green"
        )

    def start_export(self, result):
        """
        Lance l'export Excel suite à l'analyse.
        
        Args:
            result (dict): Résultat de l'analyse COMAC contenant les données à exporter
        """
        if not result.get('pending_export'):
            return

        self.current_task = ExcelExportTask(
            "Export COMAC",
            self.comac_logic.ecrireResultatsAnalyseExcels,
            args=[
                result['resultats'],
                result['fichier_export'],
                result.get('dico_verif_secu'),
                result.get('verif_cables'),
                result.get('verif_boitiers'),
            ],
            payload=result,
        )
        
        self.current_task.signals.progress.connect(self.progress_changed)
        self.current_task.signals.message.connect(self.message_received)
        self.current_task.signals.finished.connect(self.export_finished) # Signal spécifique export
        self.current_task.signals.error.connect(self.error_occurred)
        
        run_async_task(self.current_task)
