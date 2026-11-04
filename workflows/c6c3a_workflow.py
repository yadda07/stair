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
Workflow pour la comparaison C6 vs C3A vs BD.
Orchestre la comparaison entre les fichiers Excel C6, C3A, C7 et les données QGIS.
"""

from qgis.PyQt.QtCore import QObject, pyqtSignal
from qgis.core import Qgis, QgsMessageLog, QgsApplication
from ..compat import MSG_CRITICAL
from ..C6_vs_C3A_vs_Bd import C6_vs_C3A_vs_Bd
from ..async_tasks import C6C3ATask, ExcelExportTask, run_async_task
import os
import pandas as pd
import datetime
import traceback

class C6C3AWorkflow(QObject):
    """
    Contrôleur pour le flux de comparaison C6 vs C3A vs BD.
    
    Gère:
    1. Extraction des données QGIS (Main Thread)
    2. Lecture asynchrone des fichiers Excel (C6, C3A, C7) et fusion (Worker Thread)
    3. Export Excel asynchrone (Worker Thread)
    """
    
    # Signaux
    progress_changed = pyqtSignal(int)
    message_received = pyqtSignal(str, str)  # message, couleur
    analysis_finished = pyqtSignal(dict)
    export_finished = pyqtSignal(dict)
    error_occurred = pyqtSignal(str)
    
    def __init__(self):
        super().__init__()
        self.logic = C6_vs_C3A_vs_Bd()
        self.current_task = None

    def cancel(self):
        if self.current_task:
            self.current_task.cancel()

    def start_analysis(self, params):
        """
        Lance l'analyse complète:
        1. Extraction des couches QGIS sur le thread principal
        2. Lecture des fichiers Excel C6/C3A/C7 et fusion pandas dans C6C3ATask
        """
        self.progress_changed.emit(5)
        self.message_received.emit("Démarrage de l'analyse C6 vs C3A vs BD...", "black")

        try:
            mode_c3a = params.get('mode_c3a', 'QGIS')

            # 1. Extraction BD QGIS (Main Thread)
            self.message_received.emit("Extraction de la Base de Données QGIS...", "black")
            df_bd, df_bd_rempl = self.logic.liste_poteau_etudes(
                params['table_infra'],
                params['table_decoupage'],
                params['champs_dcp'],
                params['valeur_dcp'],
            )
            self.progress_changed.emit(10)

            df_c3a_qgis, df_c3a_rempl_qgis = pd.DataFrame(), pd.DataFrame()
            if mode_c3a == 'QGIS' and params.get('table_cmd'):
                self.message_received.emit("Extraction C3A via QGIS...", "black")
                df_c3a_qgis, df_c3a_rempl_qgis = self.logic.liste_poteau_c3a_qgis(
                    params['table_cmd'],
                    params['table_decoupage'],
                    params['champs_dcp'],
                    params['valeur_dcp'],
                )

            qgis_data = {
                'df_bd': df_bd,
                'df_bd_rempl': df_bd_rempl,
                'df_c3a': df_c3a_qgis,
                'df_c3a_rempl': df_c3a_rempl_qgis,
            }

            # 2. Lancement de la tâche asynchrone (Excel C6/C3A/C7 + fusion pandas)
            self.current_task = C6C3ATask(params, qgis_data)
            self.current_task.signals.progress.connect(self.progress_changed)
            self.current_task.signals.message.connect(self.message_received)
            self.current_task.signals.finished.connect(self.analysis_finished)
            self.current_task.signals.error.connect(self.error_occurred)
            run_async_task(self.current_task)

        except Exception as e:
            QgsMessageLog.logMessage(f"Erreur C6C3AWorkflow: {traceback.format_exc()}", "PoleAerien", MSG_CRITICAL)
            self.error_occurred.emit(str(e))

    def start_export(self, result):
        """
        Lance l'export Excel (tâche asynchrone).
        """
        df_final = result.get('df_final')
        df_final_rempl = result.get('df_final_rempl')
        chemin_export = result.get('chemin_export')
        
        if df_final is None or df_final_rempl is None:
            self.error_occurred.emit("Données manquantes pour l'export")
            return
            
        # On définit une fonction wrapper pour l'export qui fait les 2 exports
        def run_export(df_final, df_final_rempl, chemin_export):
            # 1. Export Analyse Globale
            date_str = datetime.datetime.now().strftime("%d-%m-%Y_%H_%M")
            file_name_1 = f"{chemin_export}{os.sep}ANALYSE_C6_C3A_BD_{date_str}.xlsx"
            nb_err_1 = self.logic.ecrictureExcel(df_final, file_name_1)
            
            # 2. Export Analyse Remplacement (avec C7)
            file_name_2 = f"{chemin_export}{os.sep}ANALYSE_C6_C7_C3A_BD_{date_str}.xlsx"
            nb_err_2 = self.logic.ecrictureExcelC6C7C3aBd(df_final_rempl, file_name_2)
            
            return {
                'file_1': file_name_1,
                'nb_err_1': nb_err_1,
                'file_2': file_name_2,
                'nb_err_2': nb_err_2
            }

        self.current_task = ExcelExportTask(
            "Export C6-C3A-BD",
            run_export,
            args=[df_final, df_final_rempl, chemin_export],
            payload={}
        )
        
        self.current_task.signals.progress.connect(self.progress_changed)
        self.current_task.signals.message.connect(self.message_received)
        self.current_task.signals.finished.connect(self.export_finished)
        self.current_task.signals.error.connect(self.error_occurred)
        
        run_async_task(self.current_task)
