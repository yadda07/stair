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

        # Workflows and dialog: lazy init in run() to avoid
        # silent crash at plugin load if pandas/numpy/openpyxl missing.
        self._dlg = None
        self._doc_dock = None
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
        self.add_action(
            icon_path,
            text=self.tr(u'Documentation ST-AIR'),
            callback=self.openDocumentation,
            add_to_menu=True,
            add_to_toolbar=False,
            parent=self.iface.mainWindow())

    def unload(self):
        try:
            if self._batch_orch is not None:
                try:
                    self._batch_orch.shutdown()
                except Exception:
                    pass
                self._batch_orch = None
            self._batch_runner = None
            if self._doc_dock is not None:
                try:
                    self._doc_dock.close()
                    main_win = (
                        self.iface.mainWindow() if self.iface else None
                    )
                    if main_win is not None:
                        main_win.removeDockWidget(self._doc_dock)
                    self._doc_dock.deleteLater()
                except Exception:
                    pass
                self._doc_dock = None
            if self._dlg:
                try:
                    self._dlg.close()
                    self._dlg.deleteLater()
                except Exception:
                    pass
                self._dlg = None
            if hasattr(self, 'translator') and self.translator is not None:
                try:
                    QCoreApplication.removeTranslator(self.translator)
                except Exception:
                    pass
                self.translator = None
            for action in self.actions:
                self.iface.removePluginMenu(
                    self.tr(u'&ST-AIR'), action)
                self.iface.removeToolBarIcon(action)
            self.actions.clear()
            del self.toolbar
        except Exception as e:
            QgsMessageLog.logMessage(
                f'PoleAerien.unload: {e}', 'PoleAerien', MSG_WARNING)

    def openDocumentation(self):
        try:
            from .doc_dock import StairDocDockWidget
            if self._doc_dock is None:
                self._doc_dock = StairDocDockWidget(
                    self.iface,
                    self.plugin_dir,
                    parent=self.iface.mainWindow() if self.iface else None,
                )
            self._doc_dock.ouvrir()
        except Exception as exc:
            QgsMessageLog.logMessage(
                f'PoleAerien.openDocumentation: ouverture dock impossible: {exc}',
                'PoleAerien',
                MSG_WARNING,
            )

    def run(self):
        if self._dlg is None:
            try:
                from .dialog_v2 import PoleAerienDialogV2
                from .batch_runner import BatchRunner
                from .batch_orchestrator import BatchOrchestrator
                from .workflows.comac_workflow import ComacWorkflow
                from .workflows.capft_workflow import CapFtWorkflow
                from .workflows.c6bd_workflow import C6BdWorkflow
                from .workflows.c6c3a_workflow import C6C3AWorkflow
                from .workflows.police_workflow import PoliceWorkflow
                from .workflows.gespot_workflow import GespotWorkflow
            except ImportError as exc:
                from qgis.PyQt.QtWidgets import QMessageBox
                QgsMessageLog.logMessage(
                    f'PoleAerien.run: import error: {exc}',
                    'PoleAerien', MSG_WARNING)
                QMessageBox.critical(
                    self.iface.mainWindow(),
                    'ST-AIR - Dependance manquante',
                    f'Une librairie Python requise est introuvable :\n\n'
                    f'{exc}\n\n'
                    'Contactez le support technique.')
                return
            self._dlg = PoleAerienDialogV2(self.iface.mainWindow())
            self._batch_runner = BatchRunner()
            self._batch_orch = BatchOrchestrator(
                self._dlg, self._batch_runner,
                CapFtWorkflow(), ComacWorkflow(),
                PoliceWorkflow(), C6C3AWorkflow(),
                self.iface,
                gespot_wf=GespotWorkflow(),
                c6bd_wf=C6BdWorkflow(),
            )
            self._dlg.help_requested.connect(self.openDocumentation)
        self._dlg.init_default_layers()
        self._dlg.show()
        self._dlg.raise_()
        self._dlg.activateWindow()
