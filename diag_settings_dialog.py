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
Dialog de configuration du suivi d'usage (identite sous-traitant + endpoint).
"""

from qgis.PyQt.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QVBoxLayout,
)

from .compat import BTN_CANCEL, BTN_OK
from .ui_theme import (
    chip_qss,
    dialog_qss,
    input_qss,
    primary_button_qss,
    secondary_button_qss,
    svg_icon,
    tokens,
)
from .usage_diag import UsageDiag


class DiagSettingsDialog(QDialog):
    """Prompt entreprise, email et URL endpoint de diagnostic."""

    def __init__(self, parent=None):
        super().__init__(parent)
        tok = tokens()
        self.setWindowTitle("Parametres de diagnostic")
        self.setWindowIcon(svg_icon("settings.svg", tok["accent"]))
        self.setMinimumWidth(380)
        self.setStyleSheet(dialog_qss())

        lay = QVBoxLayout(self)
        lay.setContentsMargins(14, 12, 14, 12)
        lay.setSpacing(10)

        info = QLabel(
            "Ces informations permettent au BE de connaitre l'usage du plugin "
            "et de diagnostiquer les anomalies."
        )
        info.setWordWrap(True)
        info.setStyleSheet(
            f"color: {tok['secondaire'].name()}; font-size: 9pt;"
        )
        lay.addWidget(info)

        badge_row = QHBoxLayout()
        badge_row.setContentsMargins(0, 0, 0, 0)
        auto = QLabel("Envoi automatique : actif")
        auto.setStyleSheet(chip_qss("ok"))
        badge_row.addWidget(auto)
        badge_row.addStretch()
        lay.addLayout(badge_row)

        form = QFormLayout()
        form.setSpacing(8)

        self._company = QLineEdit()
        self._company.setPlaceholderText("Nom de l'entreprise")
        self._company.setStyleSheet(input_qss())
        form.addRow("Entreprise :", self._company)

        self._email = QLineEdit()
        self._email.setPlaceholderText("contact@entreprise.fr")
        self._email.setStyleSheet(input_qss())
        form.addRow("Email :", self._email)

        lay.addLayout(form)

        self._buttons = QDialogButtonBox(BTN_OK | BTN_CANCEL)
        ok_btn = self._buttons.button(BTN_OK)
        if ok_btn is not None:
            ok_btn.setText("Enregistrer")
            ok_btn.setIcon(svg_icon("check.svg", tok["texteAccent"]))
            ok_btn.setStyleSheet(primary_button_qss())
        cancel_btn = self._buttons.button(BTN_CANCEL)
        if cancel_btn is not None:
            cancel_btn.setText("Annuler")
            cancel_btn.setStyleSheet(secondary_button_qss())
        self._buttons.accepted.connect(self._on_ok)
        self._buttons.rejected.connect(self.reject)
        lay.addWidget(self._buttons)

        self._load()

    def _load(self):
        cfg = UsageDiag.get_config()
        self._company.setText(cfg.get("company", ""))
        self._email.setText(cfg.get("email", ""))

    def _on_ok(self):
        cfg = UsageDiag.get_config()
        UsageDiag.set_config(
            company=self._company.text(),
            email=self._email.text(),
            endpoint_url=cfg.get("endpoint_url", ""),
        )
        self.accept()
