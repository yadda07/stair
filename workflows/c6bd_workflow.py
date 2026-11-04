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
Workflow pour l'analyse C6 vs BD.

Orchestre l'extraction des données, l'analyse asynchrone et l'export Excel.
Architecture non-bloquante avec QTimer pour UI fluide.

Fonctionnalités:
1. Extraction des poteaux FT couverts par polygones CAP FT (IN)
2. Extraction des poteaux FT NON couverts (OUT)
3. Vérification que les noms d'études CAP FT existent dans le répertoire C6
4. Auto-détection du champ étude dans la couche CAP FT
5. Export Excel multi-feuilles
"""

from qgis.PyQt.QtCore import QObject, pyqtSignal, QTimer
from qgis.PyQt.QtWidgets import QApplication
from qgis.core import Qgis, QgsMessageLog
from ..compat import MSG_WARNING, MSG_CRITICAL
from ..C6_vs_Bd import C6_vs_Bd
from ..async_tasks import C6BdTask, ExcelExportTask, run_async_task
from ..core_utils import build_export_path
import os
import pandas as pd


class C6BdWorkflow(QObject):
    """
    Contrôleur pour le flux d'analyse C6 vs BD.
    
    Architecture non-bloquante:
    - Extraction QGIS découpée en étapes avec QTimer
    - UI reste fluide entre chaque étape
    - Lecture Excel sur thread principal ; fusion pandas en QgsTask
    """
    
    # Signaux relayés vers l'UI
    progress_changed = pyqtSignal(int)
    message_received = pyqtSignal(str, str)  # message, couleur
    analysis_finished = pyqtSignal(dict)
    export_finished = pyqtSignal(dict)
    error_occurred = pyqtSignal(str)
    
    def __init__(self):
        super().__init__()
        self.c6bd_logic = C6_vs_Bd()
        self.current_task = None
        self._cancelled = False
        self._extraction_state = {}

    def detect_etude_field(self, layer):
        """
        Auto-détecte le champ étude dans une couche CAP FT.
        
        Args:
            layer: QgsVectorLayer
            
        Returns:
            str: Nom du champ détecté ou None
        """
        return self.c6bd_logic.detect_etude_field(layer)

    def cancel(self):
        """Annule l'extraction en cours."""
        self._cancelled = True
        if self.current_task:
            self.current_task.cancel()

    def start_analysis(self, lyr_pot, lyr_cap, col_cap, chemin_c6, chemin_export):
        """
        Lance l'analyse C6 vs BD de manière non-bloquante.
        
        L'extraction est découpée en étapes avec QTimer pour garder l'UI fluide.
        """
        self._cancelled = False
        
        if not lyr_pot or not lyr_cap:
            self.error_occurred.emit("Couches invalides ou manquantes")
            return

        if not os.path.isdir(chemin_c6):
            self.error_occurred.emit("Répertoire C6 invalide")
            return

        # Auto-détection du champ étude si non fourni
        if not col_cap:
            col_cap = self.detect_etude_field(lyr_cap)
            if not col_cap:
                self.error_occurred.emit(
                    "Impossible de détecter le champ étude dans la couche CAP FT. "
                    "Champs attendus: nom_etude, etudes, nom, decoupage, zone"
                )
                return

        # Stocker l'état pour l'extraction incrémentale
        self._extraction_state = {
            'lyr_pot_name': lyr_pot.name(),
            'lyr_cap_name': lyr_cap.name(),
            'col_cap': col_cap,
            'chemin_c6': chemin_c6,
            'chemin_export': chemin_export,
            'df_qgis': None,
            'df_poteaux_out': None,
            'verif_etudes': None
        }

        self.progress_changed.emit(5)
        self.message_received.emit("Extraction des poteaux FT (IN + OUT)...", "blue")
        
        # Etape 1: Extraction poteaux IN + OUT en une seule passe
        QTimer.singleShot(0, self._step1_extract_poteaux)

    def _step1_extract_poteaux(self):
        """Etape 1: Extraction poteaux FT IN + OUT en une seule passe."""
        if self._cancelled:
            return
        
        try:
            state = self._extraction_state
            df_qgis, df_out = self.c6bd_logic.extraire_poteaux_in_out(
                state['lyr_pot_name'], state['lyr_cap_name'], state['col_cap']
            )
            state['df_qgis'] = df_qgis
            state['df_poteaux_out'] = df_out
        except Exception as e:
            QgsMessageLog.logMessage(f"Erreur extraction poteaux: {e}", "PoleAerien", MSG_CRITICAL)
            self.error_occurred.emit(f"Erreur extraction: {e}")
            return

        self.progress_changed.emit(40)
        self.message_received.emit("Verification etudes vs fichiers C6...", "blue")
        
        # Etape 2 via QTimer
        QTimer.singleShot(0, self._step3_verify_etudes)

    def _step3_verify_etudes(self):
        """Étape 3: Vérification études vs fichiers C6."""
        if self._cancelled:
            return
        
        try:
            state = self._extraction_state
            verif = self.c6bd_logic.verifier_etudes_c6(
                state['lyr_cap_name'], state['col_cap'], state['chemin_c6']
            )
            state['verif_etudes'] = verif
        except Exception as e:
            QgsMessageLog.logMessage(f"Erreur vérification études: {e}", "PoleAerien", MSG_WARNING)
            state['verif_etudes'] = None

        self.progress_changed.emit(50)
        QTimer.singleShot(0, self._step4_launch_async_task)

    def _step4_launch_async_task(self):
        """Etape 4: Lancer la tache asynchrone (lecture Excel C6 + fusion pandas)."""
        if self._cancelled:
            return

        state = self._extraction_state
        chemin_export = state['chemin_export']

        fichier_export = build_export_path(chemin_export, "analyse_c6_bd.xlsx")

        params = {
            'repertoire_c6': state['chemin_c6'],
            'fichier_export': fichier_export
        }

        df_qgis = state['df_qgis']
        df_out = state['df_poteaux_out']

        qgis_data = {
            'df_qgis': df_qgis.copy() if df_qgis is not None else None,
            'df_poteaux_out': df_out.copy() if df_out is not None else None,
            'verif_etudes': state['verif_etudes'],
        }

        # Lancement Task Asynchrone (fusion pandas uniquement)
        self.current_task = C6BdTask(params, qgis_data)
        
        self.current_task.signals.progress.connect(self.progress_changed)
        self.current_task.signals.message.connect(self.message_received)
        self.current_task.signals.finished.connect(self.analysis_finished)
        self.current_task.signals.error.connect(self.error_occurred)
        
        run_async_task(self.current_task)

    def start_export(self, result):
        """
        Lance l'export Excel multi-feuilles suite à l'analyse.
        
        Feuilles générées:
        1. ANALYSE C6 BD - Comparaison principale
        2. POTEAUX HORS PERIMETRE - Poteaux FT non couverts
        3. VERIF ETUDES - Études sans C6 / C6 sans étude
        
        Args:
            result (dict): Résultat de l'analyse contenant les données à exporter
        """
        if not result.get('pending_export'):
            return
            
        final_df = result.get('final_df')
        fichier_export = result.get('fichier_export')
        poteaux_out = result.get('df_poteaux_out')
        verif_etudes = result.get('verif_etudes')

        self.current_task = ExcelExportTask(
            "Export C6 BD",
            self.c6bd_logic.ecrictureExcel,
            args=[final_df, fichier_export, poteaux_out, verif_etudes],
            payload=result,
        )
        
        self.current_task.signals.progress.connect(self.progress_changed)
        self.current_task.signals.message.connect(self.message_received)
        self.current_task.signals.finished.connect(self.export_finished)
        self.current_task.signals.error.connect(self.error_occurred)
        
        run_async_task(self.current_task)
