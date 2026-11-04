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
Workflow pour l'analyse CAP_FT.
Orchestre l'extraction des données, l'analyse asynchrone et l'export Excel.
"""

from qgis.PyQt.QtCore import QObject, pyqtSignal
from qgis.core import Qgis, QgsMessageLog
from ..compat import MSG_CRITICAL
from ..CapFt import CapFt
from ..async_tasks import CapFtTask, ExcelExportTask, run_async_task
from ..core_utils import build_export_path
from ..perf_logger import PerfLogger
import os
import time

class CapFtWorkflow(QObject):
    """
    Contrôleur pour le flux d'analyse CAP_FT.
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
        self.cap_logic = CapFt()
        self.current_task = None

    def cancel(self):
        if self.current_task:
            self.current_task.cancel()

    def start_analysis(self, lyr_pot, lyr_cap, col_cap, chemin_cap, chemin_export):
        """
        Lance l'analyse CAP_FT.
        
        Args:
            lyr_pot (QgsVectorLayer): Couche Poteaux (infra_pt_pot)
            lyr_cap (QgsVectorLayer): Couche Etude CAP_FT
            col_cap (str): Colonne identifiant l'étude dans la couche CAP_FT
            chemin_cap (str): Répertoire des fichiers CAP_FT (Fiches Appuis)
            chemin_export (str): Chemin pour le fichier Excel de sortie
        """
        if not lyr_pot or not lyr_cap:
            self.error_occurred.emit("Couches invalides ou manquantes")
            return

        if not os.path.isdir(chemin_cap):
            self.error_occurred.emit("Répertoire CAP_FT invalide")
            return

        fichier_export = build_export_path(chemin_export, "analyse_cap_ft.xlsx")

        # 1. Extraction des données (Main Thread) - passe unique
        _t0 = time.perf_counter()
        try:
            doublons, hors_etude, dico_qgis, dico_poteaux_prives, all_inf_nums, coords_qgis = (
                self.cap_logic.extraire_donnees_capft(
                    lyr_pot.name(), lyr_cap.name(), col_cap
                )
            )
        except ValueError as e:
            self.error_occurred.emit(str(e))
            return
        except Exception as e:
            QgsMessageLog.logMessage(f"Erreur extraction CAP_FT: {e}", "PoleAerien", MSG_CRITICAL)
            self.error_occurred.emit(f"Erreur technique extraction: {e}")
            return
        _n_pot = sum(len(v) for v in dico_qgis.values())
        PerfLogger.record('capft', 'extraction_qgis',
                          (time.perf_counter() - _t0) * 1000, feature_count=_n_pot)
        self.message_received.emit(
            f"[PERF] CAP_FT extraction_qgis: {int((time.perf_counter()-_t0)*1000)}ms "
            f"({_n_pot} poteaux)", "grey"
        )

        # 2. Préparation des données pour la Task
        params = {
            'chemin_cap_ft': chemin_cap,
            'fichier_export': fichier_export
        }
        
        qgis_data = {
            'doublons': list(doublons),
            'hors_etude': list(hors_etude),
            'dico_qgis': dico_qgis,
            'dico_poteaux_prives': dico_poteaux_prives,
            'all_inf_nums': all_inf_nums,
            'coords_qgis': coords_qgis,
        }

        # 3. Lancement Task Asynchrone
        self.current_task = CapFtTask(params, qgis_data)
        
        self.current_task.signals.progress.connect(self.progress_changed)
        self.current_task.signals.message.connect(self.message_received)
        self.current_task.signals.finished.connect(self.analysis_finished)
        self.current_task.signals.error.connect(self.error_occurred)
        
        run_async_task(self.current_task)

    def start_export(self, result):
        """
        Lance l'export Excel suite à l'analyse.
        
        Args:
            result (dict): Résultat de l'analyse CAP_FT contenant les données à exporter
        """
        if not result.get('pending_export'):
            return

        self.current_task = ExcelExportTask(
            "Export CAP_FT",
            self.cap_logic.ecrireResultatsAnalyseExcelsCapFt,
            args=[result['resultats'], result['fichier_export']],
            payload=result,
        )
        
        self.current_task.signals.progress.connect(self.progress_changed)
        self.current_task.signals.message.connect(self.message_received)
        self.current_task.signals.finished.connect(self.export_finished)
        self.current_task.signals.error.connect(self.error_occurred)
        
        run_async_task(self.current_task)
