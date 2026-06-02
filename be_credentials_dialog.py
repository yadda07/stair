# -*- coding: utf-8 -*-
"""Internal connection setup dialog.

Lets the user enter PostgreSQL credentials for the QGIS stored
connection. Saves through ``QgsAuthManager`` so the
password is encrypted by the QGIS master password.
"""
from __future__ import annotations

import logging
import os
from typing import Optional

from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtWidgets import (
    QCheckBox,
    QDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from .be_export_auth import (
    QGIS_CONN_NAME,
    save_qgis_pg_credentials,
    test_pg_connection,
)

logger = logging.getLogger(__name__)


_FORCED_HOST = os.environ.get("STAIR_PG_HOST", "")
_FORCED_PORT = os.environ.get("STAIR_PG_PORT", "5432")
_FORCED_DBNAME = os.environ.get("STAIR_PG_DB", "")


class CredentialsDialog(QDialog):
    """Modal dialog to capture PG credentials."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Connexion PostgreSQL")
        self.setMinimumWidth(440)
        self.setModal(True)
        self._test_passed: bool = False
        self._build_ui()
        logger.debug("creds_dialog_opened")

    def _build_ui(self) -> None:
        intro = QLabel(
            "Saisis ton identifiant et ton mot de passe PostgreSQL."
        )
        intro.setWordWrap(True)

        form = QFormLayout()

        self._host_edit = QLineEdit(_FORCED_HOST)
        self._host_edit.setReadOnly(True)
        self._host_edit.setEnabled(False)

        self._port_edit = QLineEdit(_FORCED_PORT)
        self._port_edit.setReadOnly(True)
        self._port_edit.setEnabled(False)

        self._db_edit = QLineEdit(_FORCED_DBNAME)
        self._db_edit.setReadOnly(True)
        self._db_edit.setEnabled(False)

        self._user_edit = QLineEdit()
        self._user_edit.setPlaceholderText("identifiant PostgreSQL")
        self._user_edit.textChanged.connect(self._invalidate_test)

        self._pwd_edit = QLineEdit()
        self._pwd_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self._pwd_edit.setPlaceholderText("mot de passe")
        self._pwd_edit.textChanged.connect(self._invalidate_test)

        self._show_pwd = QCheckBox("Afficher le mot de passe")
        self._show_pwd.toggled.connect(self._toggle_pwd_echo)

        form.addRow("Hote :", self._host_edit)
        form.addRow("Port :", self._port_edit)
        form.addRow("Base :", self._db_edit)
        form.addRow("Identifiant :", self._user_edit)
        form.addRow("Mot de passe :", self._pwd_edit)
        form.addRow("", self._show_pwd)

        self._status = QLabel("")
        self._status.setWordWrap(True)
        self._status.setMinimumHeight(36)
        self._status.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        self._status.setStyleSheet("color: #666; padding: 4px;")

        self._test_btn = QPushButton("Tester la connexion")
        self._test_btn.clicked.connect(self._on_test)

        self._save_btn = QPushButton("Enregistrer")
        self._save_btn.setEnabled(False)
        self._save_btn.setDefault(True)
        self._save_btn.clicked.connect(self._on_save)

        self._cancel_btn = QPushButton("Annuler")
        self._cancel_btn.clicked.connect(self.reject)

        btns = QHBoxLayout()
        btns.addWidget(self._test_btn)
        btns.addStretch(1)
        btns.addWidget(self._cancel_btn)
        btns.addWidget(self._save_btn)

        layout = QVBoxLayout(self)
        layout.addWidget(intro)
        layout.addLayout(form)
        layout.addWidget(self._status)
        layout.addLayout(btns)

        self._user_edit.setFocus()

    def _toggle_pwd_echo(self, checked: bool) -> None:
        self._pwd_edit.setEchoMode(
            QLineEdit.EchoMode.Normal if checked
            else QLineEdit.EchoMode.Password
        )

    def _invalidate_test(self) -> None:
        if self._test_passed:
            self._test_passed = False
            self._save_btn.setEnabled(False)
            self._set_status(
                "Modification detectee. Re-tester avant d'enregistrer.",
                level="info",
            )

    def _on_test(self) -> None:
        username = self._user_edit.text().strip()
        password = self._pwd_edit.text()
        if not username or not password:
            self._set_status(
                "Saisis identifiant et mot de passe.", level="info",
            )
            return

        self._test_btn.setEnabled(False)
        self._set_status("Test en cours...", level="info")
        self.repaint()
        try:
            ok, msg = test_pg_connection(
                host=_FORCED_HOST,
                port=_FORCED_PORT,
                dbname=_FORCED_DBNAME,
                username=username,
                password=password,
            )
        finally:
            self._test_btn.setEnabled(True)

        self._test_passed = bool(ok)
        self._save_btn.setEnabled(self._test_passed)
        self._set_status(msg, level=("ok" if ok else "err"))

    def _on_save(self) -> None:
        username = self._user_edit.text().strip()
        password = self._pwd_edit.text()
        if not (username and password and self._test_passed):
            return

        self._save_btn.setEnabled(False)
        self._set_status("Enregistrement...", level="info")
        self.repaint()
        try:
            ok = save_qgis_pg_credentials(
                host=_FORCED_HOST,
                port=_FORCED_PORT,
                dbname=_FORCED_DBNAME,
                username=username,
                password=password,
                conn_name=QGIS_CONN_NAME,
            )
        except Exception as exc:
            logger.error(
                "save_creds_unexpected err_type=%s msg=%s",
                type(exc).__name__, exc,
            )
            ok = False

        if not ok:
            self._save_btn.setEnabled(True)
            self._set_status(
                "Echec de l'enregistrement. Voir les logs QGIS.",
                level="err",
            )
            return

        logger.info("creds_saved username_len=%d", len(username))
        self.accept()

    def _set_status(self, text: str, level: str = "info") -> None:
        if level == "ok":
            color = "#1a7f37"
        elif level == "err":
            color = "#c93131"
        else:
            color = "#555"
        self._status.setStyleSheet(
            "color: %s; padding: 4px;" % color
        )
        self._status.setText(text)
