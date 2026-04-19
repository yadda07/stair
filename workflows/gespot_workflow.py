# -*- coding: utf-8 -*-
"""
Workflow pour l'analyse GESPOT vs C6.

Orchestre la tâche asynchrone GespotC6Task et relaie les signaux vers l'UI.
Zéro appel QGIS dans le worker - tout le traitement est pur Python.
"""

import os

from qgis.PyQt.QtCore import QObject, pyqtSignal

from ..async_tasks import GespotC6Task, run_async_task


class GespotWorkflow(QObject):
    """Contrôleur pour le flux GESPOT vs C6."""

    progress_changed = pyqtSignal(int)
    message_received = pyqtSignal(str, str)
    analysis_finished = pyqtSignal(dict)
    error_occurred = pyqtSignal(str)

    def __init__(self):
        super().__init__()
        self.current_task = None
        self._cancelled = False

    def cancel(self):
        self._cancelled = True
        if self.current_task:
            self.current_task.cancel()

    def start_analysis(self, gespot_dir: str, c6_dir: str,
                       export_dir: str, etude_map=None) -> None:
        """Lance la comparaison GESPOT vs C6 en tache de fond.

        Args:
            etude_map: {normalized_inf_num: etude_name ou 'HORS ETUDE'} optionnel.
                Construit depuis infra_pt_pot + etude_cap_ft par l'orchestrateur.
        """
        self._cancelled = False

        if not os.path.isdir(gespot_dir):
            self.error_occurred.emit(f"Dossier GESPOT introuvable : {gespot_dir}")
            return
        if not os.path.isdir(c6_dir):
            self.error_occurred.emit(f"Dossier C6 introuvable : {c6_dir}")
            return

        params = {
            'gespot_dir': gespot_dir,
            'c6_dir': c6_dir,
            'export_dir': export_dir,
            'etude_map': etude_map or {},
        }

        self.current_task = GespotC6Task(params)
        self.current_task.signals.progress.connect(self.progress_changed)
        self.current_task.signals.message.connect(self.message_received)
        self.current_task.signals.finished.connect(self._on_finished)
        self.current_task.signals.error.connect(self.error_occurred)
        run_async_task(self.current_task)

    def _on_finished(self, result: dict) -> None:
        self.analysis_finished.emit(result)
