# -*- coding: utf-8 -*-
"""
PoleAerien - QGIS Plugin
Controle qualite et mise a jour des poteaux aeriens ENEDIS (FT/BT)

Copyright (C) 2022-2026 NGE ES
License: GNU GPL v2+
"""
from qgis.PyQt.QtCore import QTranslator, QCoreApplication
from qgis.PyQt.QtGui import QIcon
from qgis.PyQt.QtWidgets import QAction
from qgis.core import QgsMessageLog, Qgis, QgsSettings
from .compat import MSG_WARNING
from .resources import *
import os.path

from .workflows.maj_workflow import MajWorkflow
from .workflows.comac_workflow import ComacWorkflow
from .workflows.capft_workflow import CapFtWorkflow
from .workflows.c6bd_workflow import C6BdWorkflow
from .workflows.c6c3a_workflow import C6C3AWorkflow
from .workflows.police_workflow import PoliceWorkflow
from .workflows.gespot_workflow import GespotWorkflow
from .dialog_v2 import PoleAerienDialogV2
from .batch_runner import BatchRunner
from .batch_orchestrator import BatchOrchestrator


class PoleAerien:
    """QGIS Plugin Implementation."""

    def __init__(self, iface):
        self.iface = iface
        self.plugin_dir = os.path.dirname(__file__)

        locale = (QgsSettings().value('locale/userLocale') or 'en')[0:2]
        locale_path = os.path.join(
            self.plugin_dir, 'i18n', f'PoleAerien_{locale}.qm')
        if os.path.exists(locale_path):
            self.translator = QTranslator()
            self.translator.load(locale_path)
            QCoreApplication.installTranslator(self.translator)

        self.toolbar = self.iface.addToolBar(u'&ST-AIR')
        self.toolbar.setObjectName(u'&ST-AIR')
        self.actions = []
        self.menu = self.tr(u'&ST-AIR')

        # Workflows (shared business logic orchestrators)
        self.maj_workflow = MajWorkflow()
        self.comac_workflow = ComacWorkflow()
        self.capft_workflow = CapFtWorkflow()
        self.c6bd_workflow = C6BdWorkflow()
        self.police_workflow = PoliceWorkflow()
        self.c6c3a_workflow = C6C3AWorkflow()
        self.gespot_workflow = GespotWorkflow()

        # Dialog (lazy init in run())
        self._dlg = None
        self._batch_runner = None
        self._batch_orch = None

    # noinspection PyMethodMayBeStatic
    def tr(self, message):
        return QCoreApplication.translate('PoleAerien', message)

    def add_action(self, icon_path, text, callback, enabled_flag=True,
                   add_to_menu=True, add_to_toolbar=True,
                   status_tip=None, whats_this=None, parent=None):
        icon = QIcon(icon_path)
        action = QAction(icon, text, parent)
        action.triggered.connect(callback)
        action.setEnabled(enabled_flag)
        if status_tip is not None:
            action.setStatusTip(status_tip)
        if whats_this is not None:
            action.setWhatsThis(whats_this)
        if add_to_toolbar:
            self.toolbar.addAction(action)
        if add_to_menu:
            self.iface.addPluginToMenu(self.menu, action)
        self.actions.append(action)
        return action

    def initGui(self):
        # ST-AIR : chemin filesystem direct, evite la recompilation resources.qrc
        icon_path = os.path.join(self.plugin_dir, 'images', 'icon.svg')
        self.add_action(
            icon_path,
            text=self.tr(u'ST-AIR'),
            callback=self.run,
            parent=self.iface.mainWindow())

    def unload(self):
        try:
            # Release shared DB connection
            try:
                from .db_connection import close_shared_connection
                close_shared_connection()
            except Exception:
                pass
            if self._dlg:
                try:
                    self._dlg.close()
                except Exception:
                    pass
            for action in self.actions:
                self.iface.removePluginMenu(
                    self.tr(u'&ST-AIR'), action)
                self.iface.removeToolBarIcon(action)
            del self.toolbar
        except Exception as e:
            QgsMessageLog.logMessage(
                f'PoleAerien.unload: {e}', 'PoleAerien', MSG_WARNING)

    def openDocumentation(self):
        import webbrowser
        doc_path = os.path.join(self.plugin_dir, 'docs', 'index.html')
        if os.path.exists(doc_path):
            webbrowser.open(f'file:///{doc_path}')

    def run(self):
        if self._dlg is None:
            self._dlg = PoleAerienDialogV2(self.iface.mainWindow())
            self._batch_runner = BatchRunner()
            self._batch_orch = BatchOrchestrator(
                self._dlg, self._batch_runner,
                self.maj_workflow, self.capft_workflow,
                self.comac_workflow, self.c6bd_workflow,
                self.police_workflow, self.c6c3a_workflow,
                self.iface,
                gespot_wf=self.gespot_workflow,
            )
            self._dlg.help_requested.connect(self.openDocumentation)
        self._dlg.init_default_layers()
        self._dlg.show()
        self._dlg.raise_()
        self._dlg.activateWindow()
