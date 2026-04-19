# -*- coding: utf-8 -*-
"""
PoleAerien Dialog V2 - Streamlined batch interface.

Pure Python, no .ui file. Native QGIS 3.28+ compatible.
Uses QgsApplication.getThemeIcon() for all icons.
Single project folder -> auto-detect -> select modules -> execute all.
"""

import os
import re

from qgis.PyQt.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QFormLayout,
    QLabel, QPushButton, QLineEdit, QCheckBox, QFrame,
    QTextBrowser, QProgressBar, QFileDialog,
    QWidget, QSplitter, QDialogButtonBox,
)
from qgis.PyQt.QtCore import Qt, QSize, QEvent, pyqtSignal
from qgis.PyQt.QtGui import QTextCursor, QFont, QPalette, QColor
from qgis.core import QgsApplication, QgsProject, QgsSettings
from qgis.gui import QgsMapLayerComboBox, QgsCollapsibleGroupBox

from .compat import (
    VERTICAL,
    CURSOR_POINTING,
    WF_NO_HELP, CASE_INSENSITIVE, MATCH_CONTAINS,
    PAL_BASE, PAL_WINDOW, PAL_WINDOW_TEXT, PAL_MID, PAL_MIDLIGHT,
    PAL_BRIGHT_TEXT, PAL_HIGHLIGHT,
    FRAME_HLINE, FRAME_SUNKEN, BTN_OK, BTN_CANCEL,
    LAYER_FILTER_POINT, LAYER_FILTER_POLYGON,
    GEOM_POINT, GEOM_POLYGON,
)
from .project_detector import detect_project, DetectionResult, analyse_livrable
from .async_tasks import SmoothProgressController


PLUGIN_DIR = os.path.dirname(__file__)


def _qgs_icon(name):
    """Get a native QGIS theme icon by name."""
    return QgsApplication.getThemeIcon(name)


def _plugin_icon(name):
    """Load SVG icon from plugin images folder (fallback)."""
    path = os.path.join(PLUGIN_DIR, 'images', name)
    if os.path.exists(path):
        from qgis.PyQt.QtGui import QIcon
        return QIcon(path)
    from qgis.PyQt.QtGui import QIcon
    return QIcon()


# ---------------------------------------------------------------------------
#  Theme-aware color resolver
# ---------------------------------------------------------------------------

class _ThemeColors:
    """Resolves semantic colors from QPalette so the UI follows the
    active QGIS theme (light, dark, or custom).

    _widget_palette_ref: when set to a widget (e.g. the log QTextBrowser),
    pal() uses that widget's synthesized palette instead of the application
    palette. Qt synthesizes widget palettes from QSS stylesheets, so this
    correctly reflects QGIS dark themes even when the app-level palette is
    unchanged.
    """

    _widget_palette_ref = None

    @staticmethod
    def pal():
        ref = _ThemeColors._widget_palette_ref
        if ref is not None:
            try:
                return ref.palette()
            except RuntimeError:
                _ThemeColors._widget_palette_ref = None
        return QgsApplication.instance().palette()

    @classmethod
    def _is_dark(cls):
        p = cls.pal()
        # QPalette.Base is the text-widget background - Qt synthesizes it from
        # QSS stylesheets, so it reflects QGIS dark themes reliably.
        base = p.color(PAL_BASE)
        if base.isValid() and base.lightnessF() < 0.5:
            return True
        # Fallback: main window background
        return p.color(PAL_WINDOW).lightnessF() < 0.5

    # --- Semantic colors (adapt to light/dark) ---

    @classmethod
    def ok(cls):
        """Success / positive - green tones."""
        return QColor(76, 175, 80) if cls._is_dark() else QColor(46, 125, 50)

    @classmethod
    def ok_hover(cls):
        return QColor(56, 142, 60) if cls._is_dark() else QColor(27, 94, 32)

    @classmethod
    def ok_pressed(cls):
        return QColor(46, 125, 50) if cls._is_dark() else QColor(23, 79, 26)

    @classmethod
    def error(cls):
        """Error / destructive - red tones."""
        return QColor(239, 83, 80) if cls._is_dark() else QColor(198, 40, 40)

    @classmethod
    def error_hover(cls):
        return QColor(229, 57, 53) if cls._is_dark() else QColor(183, 28, 28)

    @classmethod
    def info(cls):
        """Information / accent - blue tones."""
        return QColor(100, 181, 246) if cls._is_dark() else QColor(21, 101, 192)

    @classmethod
    def warn(cls):
        """Warning - orange tones."""
        return QColor(255, 167, 38) if cls._is_dark() else QColor(230, 81, 0)

    @classmethod
    def dim(cls):
        """Dimmed / secondary text."""
        p = cls.pal()
        c = p.color(PAL_WINDOW_TEXT)
        c.setAlphaF(0.55)
        return c

    @classmethod
    def tag_bg(cls):
        """Background for the project tag pill."""
        p = cls.pal()
        if cls._is_dark():
            return p.color(PAL_MIDLIGHT)
        return p.color(PAL_MID)

    @classmethod
    def tag_fg(cls):
        """Foreground for the project tag pill."""
        p = cls.pal()
        if cls._is_dark():
            return p.color(PAL_WINDOW_TEXT)
        return p.color(PAL_BRIGHT_TEXT)

    @classmethod
    def highlight(cls):
        """The native highlight color from the palette."""
        return cls.pal().color(PAL_HIGHLIGHT)

    @classmethod
    def progress_chunk(cls):
        """Progress bar fill - uses the highlight or ok color."""
        return cls.highlight()


def _c(color):
    """Convert QColor to CSS hex string."""
    return color.name()


# ---------------------------------------------------------------------------
#  Targeted styles - only for action buttons; everything else is native Qt
# ---------------------------------------------------------------------------

def _btn_start_style():
    """Stylesheet for the green Start button only."""
    ok = _c(_ThemeColors.ok())
    ok_h = _c(_ThemeColors.ok_hover())
    ok_p = _c(_ThemeColors.ok_pressed())
    mid = _c(_ThemeColors.pal().color(PAL_MID))
    midl = _c(_ThemeColors.pal().color(PAL_MIDLIGHT))
    return (
        f"QPushButton {{ background-color: {ok}; color: white; border: none;"
        f" border-radius: 6px; padding: 7px 24px; font-weight: bold; font-size: 10pt; }}"
        f" QPushButton:hover {{ background-color: {ok_h}; }}"
        f" QPushButton:pressed {{ background-color: {ok_p}; }}"
        f" QPushButton:disabled {{ background-color: {mid}; color: {midl}; }}"
    )


def _btn_cancel_style():
    """Stylesheet for the red Cancel button only."""
    err = _c(_ThemeColors.error())
    err_h = _c(_ThemeColors.error_hover())
    return (
        f"QPushButton {{ background-color: {err}; color: white; border: none;"
        f" border-radius: 6px; padding: 7px 24px; font-weight: bold; font-size: 10pt; }}"
        f" QPushButton:hover {{ background-color: {err_h}; }}"
    )


def _progress_bar_style():
    """Themed QSS for the progress bar chunk color."""
    chunk = _c(_ThemeColors.progress_chunk())
    bg = _c(_ThemeColors.pal().color(PAL_MID))
    return (
        f"QProgressBar {{ border: 1px solid {bg}; border-radius: 4px;"
        f" text-align: center; font-size: 8pt; }}"
        f" QProgressBar::chunk {{ background-color: {chunk}; border-radius: 3px; }}"
    )


def _make_font(pt=None, bold=False, family=None):
    """Create a QFont with optional overrides."""
    f = QFont()
    if pt:
        f.setPointSizeF(pt)
    if bold:
        f.setBold(True)
    if family:
        f.setFamily(family)
    return f


def _set_foreground(widget, color):
    """Set a widget's foreground color via QPalette (theme-safe)."""
    pal = widget.palette()
    pal.setColor(PAL_WINDOW_TEXT, color)
    widget.setPalette(pal)


# ---------------------------------------------------------------------------
#  SRO manual input dialog
# ---------------------------------------------------------------------------

_SRO_REGEX = re.compile(
    r'^\d{5}/[A-Z0-9]{2,3}/[A-Z]{2,3}/\d{5}$',
    re.IGNORECASE
)


class SroInputDialog(QDialog):
    """Compact native-looking dialog for manual SRO entry with DB autocomplete."""

    def __init__(self, current_sro='', project_name='', sro_list=None,
                 parent=None):
        super().__init__(parent)
        self.setWindowTitle('SRO')
        self.setWindowFlags(
            self.windowFlags() & ~WF_NO_HELP
        )
        self.setFixedWidth(340)

        self._result_sro = ''

        lay = QFormLayout(self)
        lay.setContentsMargins(10, 10, 10, 10)
        lay.setSpacing(6)

        # Hint
        hint = QLabel('ex: 63041/B1I/PMZ/00003')
        hint.setFont(_make_font(pt=8))
        _set_foreground(hint, _ThemeColors.dim())

        # Input
        self._edit = QLineEdit(current_sro)
        self._edit.setPlaceholderText('XXXXX/YYY/ZZZ/NNNNN')
        self._edit.setMaxLength(25)
        self._edit.setFont(QFont('Consolas', 9))
        self._edit.textChanged.connect(self._on_text_changed)

        # Autocomplete from DB if available
        if sro_list:
            from qgis.PyQt.QtWidgets import QCompleter
            completer = QCompleter(sro_list, self)
            completer.setCaseSensitivity(CASE_INSENSITIVE)
            completer.setFilterMode(MATCH_CONTAINS)
            completer.setMaxVisibleItems(12)
            self._edit.setCompleter(completer)

        # Validation
        self._validation = QLabel()
        self._validation.setFont(_make_font(pt=8))

        lay.addRow('SRO :', self._edit)
        lay.addRow('', hint)
        lay.addRow('', self._validation)

        # Standard button box (native OK/Cancel)
        self._btn_box = QDialogButtonBox(
            BTN_OK | BTN_CANCEL
        )
        self._btn_box.accepted.connect(self._accept)
        self._btn_box.rejected.connect(self.reject)
        lay.addRow(self._btn_box)

        self._on_text_changed(self._edit.text())

    def _on_text_changed(self, text):
        text = text.strip().upper()
        if not text:
            self._validation.setText('')
            self._btn_box.button(BTN_OK).setEnabled(False)
            return
        normalized = text.replace('-', '/').replace('_', '/')
        if _SRO_REGEX.match(normalized):
            self._validation.setText('[OK]')
            _set_foreground(self._validation, _ThemeColors.ok())
            self._btn_box.button(BTN_OK).setEnabled(True)
        else:
            self._validation.setText('Format: XXXXX/YYY/ZZZ/NNNNN')
            _set_foreground(self._validation, _ThemeColors.error())
            self._btn_box.button(BTN_OK).setEnabled(False)

    def _accept(self):
        raw = self._edit.text().strip().upper()
        normalized = raw.replace('-', '/').replace('_', '/')
        if _SRO_REGEX.match(normalized):
            parts = normalized.split('/')
            self._result_sro = f'{parts[0]}/{parts[1]}/{parts[2]}/{parts[3]}'
            self.accept()

    def get_sro(self) -> str:
        return self._result_sro


# ---------------------------------------------------------------------------
#  Module row widget
# ---------------------------------------------------------------------------
class _ModuleRow(QWidget):
    """Module row: main line [checkbox] [status] + detail line [resource or reason]."""

    toggled = pyqtSignal(str, bool)

    def __init__(self, key, label, parent=None, tooltip=''):
        super().__init__(parent)
        self.key = key
        self._found = False
        self._prereq_tooltip = tooltip

        outer = QVBoxLayout(self)
        outer.setContentsMargins(4, 1, 4, 1)
        outer.setSpacing(0)

        # Main row
        main_row = QHBoxLayout()
        main_row.setContentsMargins(0, 0, 0, 0)
        main_row.setSpacing(6)

        self.checkbox = QCheckBox(label)
        self.checkbox.setEnabled(False)
        if tooltip:
            self.checkbox.setToolTip(tooltip)
        self.checkbox.toggled.connect(lambda c: self.toggled.emit(self.key, c))
        main_row.addWidget(self.checkbox, 1)

        self.status_icon = QLabel()
        self.status_icon.setFixedWidth(28)
        self.status_icon.setToolTip("Non detecte")
        main_row.addWidget(self.status_icon)

        outer.addLayout(main_row)

        # Detail row (UI-012: sub-state)
        self._detail_label = QLabel()
        self._detail_label.setFont(_make_font(pt=7.5))
        _set_foreground(self._detail_label, _ThemeColors.dim())
        self._detail_label.setContentsMargins(22, 0, 4, 0)
        self._detail_label.setVisible(False)
        outer.addWidget(self._detail_label)

        self._update_status_icon(False)

    def set_detected(self, found, path='', diagnostic=''):
        self._found = found
        self.checkbox.setEnabled(found)
        self.checkbox.setChecked(found)
        self._update_status_icon(found, diagnostic)

        if found:
            display = os.path.basename(path) if path else ''
            self._detail_label.setText(display)
            self._detail_label.setToolTip(path)
            _set_foreground(self._detail_label, _ThemeColors.dim())
            self._detail_label.setVisible(bool(display))
        else:
            reason = diagnostic or ''
            if reason:
                first_line = reason.split('\n')[0]
                self._detail_label.setText(first_line)
                _set_foreground(self._detail_label, _ThemeColors.warn())
                self._detail_label.setVisible(True)
            else:
                self._detail_label.setVisible(False)
            self._detail_label.setToolTip(diagnostic or self._prereq_tooltip)

    def _update_status_icon(self, found, diagnostic=''):
        self.status_icon.clear()
        self.status_icon.setFont(_make_font(pt=8, bold=True))
        if found:
            self.status_icon.setText("[OK]")
            _set_foreground(self.status_icon, _ThemeColors.ok())
            self.status_icon.setToolTip("Detecte")
        else:
            self.status_icon.setText("[--]")
            _set_foreground(self.status_icon, _ThemeColors.error())
            tip = "Non detecte"
            if diagnostic:
                tip += "\n" + diagnostic
            elif self._prereq_tooltip:
                tip += "\n" + self._prereq_tooltip
            self.status_icon.setToolTip(tip)

    @property
    def is_selected(self):
        return self.checkbox.isChecked() and self.checkbox.isEnabled()


# ---------------------------------------------------------------------------
#  Main Dialog
# ---------------------------------------------------------------------------
class PoleAerienDialogV2(QDialog):
    """Streamlined batch analysis dialog - professional native QGIS look."""

    start_requested = pyqtSignal(list)
    cancel_requested = pyqtSignal()
    help_requested = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("PoleAerienBatch")
        self._detection = DetectionResult()
        self._is_running = False
        self._module_rows = {}
        self._chk_comac_drawings = None
        self._chk_data_dictionary = None
        self._sro_source = ''
        self._export_status = None
        self._layers_status = None
        self._readiness_label = None
        self._splitter = None
        self._progress_step = None
        self.smooth_progress = SmoothProgressController(interval_ms=25)

        self._build_ui()
        self._restore_ui_state()
        self._connect_signals()

    # ==================================================================
    #  UI CONSTRUCTION
    # ==================================================================

    def _build_ui(self):
        self.setWindowTitle("ST-AIR - Analyse par lot")
        self.setWindowIcon(_plugin_icon('icon.svg'))
        self.setMinimumSize(700, 750)
        self.resize(740, 850)

        root = QVBoxLayout(self)
        root.setSpacing(6)
        root.setContentsMargins(8, 8, 8, 8)

        # --- Header bar ---
        root.addLayout(self._build_header())

        # --- Main splitter: config (top) / log (bottom) ---
        self._splitter = QSplitter(VERTICAL)
        splitter = self._splitter
        splitter.setChildrenCollapsible(False)
        splitter.setHandleWidth(2)

        # Top: config (no scroll)
        top_widget = QWidget()
        top_lay = QVBoxLayout(top_widget)
        top_lay.setSpacing(4)
        top_lay.setContentsMargins(0, 0, 0, 0)

        self._build_project_group(top_lay)
        self._build_sro_group(top_lay)
        self._build_layers_group(top_lay)
        self._build_modules_group(top_lay)
        splitter.addWidget(top_widget)

        # Bottom: log
        log_widget = self._build_log()
        splitter.addWidget(log_widget)

        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        root.addWidget(splitter, 1)

        # --- Readiness summary (UI-009) ---
        self._readiness_label = QLabel()
        self._readiness_label.setFont(_make_font(pt=8.5, bold=True))
        self._readiness_label.setContentsMargins(4, 2, 4, 2)
        self._readiness_label.setVisible(False)
        root.addWidget(self._readiness_label)

        # --- Footer ---
        root.addLayout(self._build_footer())

    # ---- Header ----
    def _build_header(self):
        lay = QHBoxLayout()
        lay.setSpacing(8)

        icon_lbl = QLabel()
        icon_lbl.setPixmap(_plugin_icon('icon.svg').pixmap(22, 22))
        lay.addWidget(icon_lbl)

        title = QLabel("ST-AIR")
        title_font = QFont()
        title_font.setPointSize(12)
        title_font.setBold(True)
        title.setFont(title_font)
        lay.addWidget(title)

        self._subtitle = QLabel("  -  Selectionnez un dossier projet")
        self._subtitle.setFont(_make_font(pt=9))
        _set_foreground(self._subtitle, _ThemeColors.dim())
        lay.addWidget(self._subtitle)

        lay.addStretch()

        self._project_tag = QLabel()
        self._project_tag.setVisible(False)
        self._project_tag.setFont(_make_font(pt=9, bold=True))
        self._project_tag.setAutoFillBackground(True)
        tag_pal = self._project_tag.palette()
        tag_pal.setColor(PAL_WINDOW, _ThemeColors.tag_bg())
        tag_pal.setColor(PAL_WINDOW_TEXT, _ThemeColors.tag_fg())
        self._project_tag.setPalette(tag_pal)
        self._project_tag.setContentsMargins(10, 2, 10, 2)
        lay.addWidget(self._project_tag)

        sep = QFrame()
        sep.setFrameShape(FRAME_HLINE)
        sep.setFrameShadow(FRAME_SUNKEN)

        outer = QVBoxLayout()
        outer.setSpacing(4)
        outer.addLayout(lay)
        outer.addWidget(sep)
        return outer

    # ---- Project group ----
    def _build_project_group(self, parent):
        grp = QgsCollapsibleGroupBox("Dossier projet")
        grp.setObjectName("PoleAerien_grp_project")
        grp.setSettingGroup("PoleAerien/dialog_v2")
        lay = QVBoxLayout()
        lay.setSpacing(4)
        lay.setContentsMargins(6, 4, 6, 4)

        # Project path
        row1 = QHBoxLayout()
        row1.setSpacing(4)

        lbl = QLabel("Projet :")
        lbl.setFixedWidth(52)
        lbl.setFont(_make_font(pt=9, bold=True))
        row1.addWidget(lbl)

        self.projectPathEdit = QLineEdit()
        self.projectPathEdit.setPlaceholderText("Chemin du dossier projet...")
        row1.addWidget(self.projectPathEdit)

        self.browseProjectBtn = QPushButton()
        self.browseProjectBtn.setIcon(_qgs_icon('mActionFileOpen.svg'))
        self.browseProjectBtn.setIconSize(QSize(16, 16))
        self.browseProjectBtn.setToolTip("Parcourir...")
        self.browseProjectBtn.setCursor(CURSOR_POINTING)
        row1.addWidget(self.browseProjectBtn)

        self.refreshBtn = QPushButton()
        self.refreshBtn.setIcon(_qgs_icon('mActionRefresh.svg'))
        self.refreshBtn.setIconSize(QSize(16, 16))
        self.refreshBtn.setToolTip("Re-detecter les dossiers (synchroniser)")
        self.refreshBtn.setCursor(CURSOR_POINTING)
        row1.addWidget(self.refreshBtn)

        lay.addLayout(row1)

        # Export path
        row2 = QHBoxLayout()
        row2.setSpacing(4)

        lbl2 = QLabel("Export :")
        lbl2.setFixedWidth(52)
        lbl2.setFont(_make_font(pt=9, bold=True))
        row2.addWidget(lbl2)

        self.exportPathEdit = QLineEdit()
        self.exportPathEdit.setPlaceholderText("Dossier d'export (défaut = projet)")
        row2.addWidget(self.exportPathEdit)

        self.browseExportBtn = QPushButton()
        self.browseExportBtn.setIcon(_qgs_icon('mActionFileSaveAs.svg'))
        self.browseExportBtn.setIconSize(QSize(16, 16))
        self.browseExportBtn.setToolTip("Changer le dossier d'export")
        self.browseExportBtn.setCursor(CURSOR_POINTING)
        row2.addWidget(self.browseExportBtn)

        lay.addLayout(row2)

        # Export status inline (UI-006)
        self._export_status = QLabel()
        self._export_status.setFont(_make_font(pt=8))
        self._export_status.setContentsMargins(56, 0, 0, 0)
        self._export_status.setVisible(False)
        lay.addWidget(self._export_status)

        # Detection summary label
        self._det_summary = QLabel()
        self._det_summary.setWordWrap(True)
        self._det_summary.setFont(_make_font(pt=8))
        _set_foreground(self._det_summary, _ThemeColors.dim())
        self._det_summary.setContentsMargins(0, 2, 0, 2)
        self._det_summary.setVisible(False)
        lay.addWidget(self._det_summary)

        grp.setLayout(lay)
        parent.addWidget(grp)

    # ---- SRO group (ST-AIR : plus de mode BDD, toujours sur les couches QGIS) ----
    def _build_sro_group(self, parent):
        grp = QgsCollapsibleGroupBox("SRO du projet")
        grp.setObjectName("PoleAerien_grp_sro")
        grp.setSettingGroup("PoleAerien/dialog_v2")
        lay = QVBoxLayout()
        lay.setSpacing(4)
        lay.setContentsMargins(6, 4, 6, 4)

        sro_lay = QHBoxLayout()
        sro_lay.setContentsMargins(0, 2, 0, 0)
        sro_lay.setSpacing(6)

        sro_lbl = QLabel("SRO :")
        sro_lbl.setFixedWidth(52)
        sro_lbl.setFont(_make_font(pt=9, bold=True))
        sro_lay.addWidget(sro_lbl)

        self._sro_value = QLineEdit()
        self._sro_value.setPlaceholderText('XXXXX/YYY/ZZZ/NNNNN')
        self._sro_value.setMaxLength(25)
        self._sro_value.setFont(_make_font(pt=9, bold=True, family='Consolas'))
        self._sro_value.setMinimumWidth(180)
        self._sro_value.setMaximumWidth(220)
        self._sro_value.textChanged.connect(self._on_sro_text_changed)
        sro_lay.addWidget(self._sro_value)
        sro_lay.addStretch()

        self._sro_status = QLabel()
        self._sro_status.setFont(_make_font(pt=8))
        sro_lay.addWidget(self._sro_status)

        lay.addLayout(sro_lay)

        hint = QLabel(
            "Le SRO est detecte automatiquement a partir du nom de dossier "
            "(ex. 63041-B1I-PMZ-00003). Corrigez-le si necessaire."
        )
        hint.setFont(_make_font(pt=8))
        hint.setWordWrap(True)
        _set_foreground(hint, _ThemeColors.dim())
        lay.addWidget(hint)

        grp.setLayout(lay)
        parent.addWidget(grp)

    # ---- Layers group ----
    def _build_layers_group(self, parent):
        self._layers_grp = QgsCollapsibleGroupBox("Couches QGIS")
        self._layers_grp.setObjectName("PoleAerien_grp_layers")
        self._layers_grp.setSettingGroup("PoleAerien/dialog_v2")
        grp = self._layers_grp
        form = QFormLayout()
        form.setHorizontalSpacing(12)
        form.setVerticalSpacing(4)
        form.setContentsMargins(6, 4, 6, 4)

        self.comboInfraPtPot = QgsMapLayerComboBox()
        self.comboInfraPtPot.setFilters(LAYER_FILTER_POINT)
        self.comboInfraPtPot.setShowCrs(True)
        form.addRow("infra_pt_pot :", self.comboInfraPtPot)

        self.comboEtudeCapFt = QgsMapLayerComboBox()
        self.comboEtudeCapFt.setFilters(LAYER_FILTER_POLYGON)
        self.comboEtudeCapFt.setShowCrs(True)
        form.addRow("etude_cap_ft :", self.comboEtudeCapFt)

        self.comboEtudeComac = QgsMapLayerComboBox()
        self.comboEtudeComac.setFilters(LAYER_FILTER_POLYGON)
        self.comboEtudeComac.setShowCrs(True)
        form.addRow("etude_comac :", self.comboEtudeComac)

        # Layers status inline (UI-007)
        self._layers_status = QLabel()
        self._layers_status.setFont(_make_font(pt=8))
        self._layers_status.setContentsMargins(6, 2, 6, 0)
        self._layers_status.setVisible(False)
        form.addRow("", self._layers_status)

        grp.setLayout(form)
        parent.addWidget(grp)

    # ---- Modules group ----
    def _build_modules_group(self, parent):
        grp = QgsCollapsibleGroupBox("Modules d'analyse")
        grp.setObjectName("PoleAerien_grp_modules")
        grp.setSettingGroup("PoleAerien/dialog_v2")
        lay = QVBoxLayout()
        lay.setSpacing(1)
        lay.setContentsMargins(6, 4, 6, 4)

        # Actions row
        act_row = QHBoxLayout()
        act_row.setSpacing(6)

        self.selectAllBtn = QPushButton("Tout cocher")
        self.selectAllBtn.setIcon(_qgs_icon('mActionSelectAll.svg'))
        self.selectAllBtn.setIconSize(QSize(14, 14))
        self.selectAllBtn.setCursor(CURSOR_POINTING)
        act_row.addWidget(self.selectAllBtn)

        self.deselectAllBtn = QPushButton("Tout décocher")
        self.deselectAllBtn.setIcon(_qgs_icon('mActionDeselectAll.svg'))
        self.deselectAllBtn.setIconSize(QSize(14, 14))
        self.deselectAllBtn.setCursor(CURSOR_POINTING)
        act_row.addWidget(self.deselectAllBtn)

        act_row.addStretch()

        self._mod_count = QLabel("0/7")
        self._mod_count.setFont(_make_font(pt=8.5))
        _set_foreground(self._mod_count, _ThemeColors.dim())
        act_row.addWidget(self._mod_count)

        lay.addLayout(act_row)

        # Separator
        sep = QFrame()
        sep.setFrameShape(FRAME_HLINE)
        sep.setFrameShadow(FRAME_SUNKEN)
        lay.addWidget(sep)

        # Module rows with prerequisite tooltips
        modules_def = [
            ('maj',       'MAJ BD - Import FT-BT KO',
             "Prerequis: fichier FT-BT KO (.xlsx) a la racine\n"
             "Couches: infra_pt_pot, etude_cap_ft, etude_comac"),
            ('capft',     'VERIF CAP_FT - Poteaux vs fiches appuis',
             "Prerequis: dossier CAP FT avec fiches appuis\n"
             "Couches: infra_pt_pot, etude_cap_ft"),
            ('comac',     'VERIF COMAC - Poteaux BT vs ExportComac',
             "Prerequis: dossier COMAC (ExportComac .xlsx)\n"
             "Couches: infra_pt_pot, etude_comac\n"
             "Connexion PostgreSQL (verif cables)"),
            ('c6bd',      'C6 vs BD - Annexe C6 vs base de donnees',
             "Prerequis: dossier CAP FT (annexes C6 = etudes .xlsx)\n"
             "Couches: infra_pt_pot, etude_cap_ft"),
            ('police_c6', 'POLICE C6 - Analyse complete',
             "Prerequis: dossier CAP FT (annexes C6)\n"
             "Couches: infra_pt_pot\n"
             "Connexion PostgreSQL (fddcpi2)"),
            ('c6c3a',     'C6-C3A-C7-BD - Croisement annexes',
             "Prerequis: dossier CAP FT + fichier C7 et/ou C3A\n"
             "Couches: infra_pt_pot, etude_cap_ft"),
            ('gespot_c6', 'GESPOT vs C6 - Comparaison referentiel terrain',
             "Prerequis: dossier GSPOT ou GESPOT (CSV) + dossier CAP FT (annexes C6)\n"
             "Aucune couche QGIS requise"),
        ]
        for key, label, tooltip in modules_def:
            row = _ModuleRow(key, label, tooltip=tooltip)
            row.toggled.connect(self._on_module_toggled)
            self._module_rows[key] = row
            lay.addWidget(row)

        sep_opts = QFrame()
        sep_opts.setFrameShape(FRAME_HLINE)
        sep_opts.setFrameShadow(FRAME_SUNKEN)
        lay.addWidget(sep_opts)

        opts_row = QHBoxLayout()
        opts_row.setSpacing(16)

        self._chk_comac_drawings = QCheckBox("Schema poteau (COMAC, plus lent)")
        self._chk_comac_drawings.setToolTip(
            "Ajoute les feuilles COMAC_DESSIN au rapport Excel unifie.\n"
            "Desactive par defaut car le rendu des schemas peut etre long."
        )
        self._chk_comac_drawings.setChecked(False)
        opts_row.addWidget(self._chk_comac_drawings)

        self._chk_data_dictionary = QCheckBox("Dictionnaire de donnees")
        self._chk_data_dictionary.setToolTip(
            "Ajoute les feuilles GLOSSAIRE et DICTIONNAIRE au rapport Excel unifie.\n"
            "Decrit chaque colonne, chaque valeur et chaque code couleur du rapport."
        )
        self._chk_data_dictionary.setChecked(False)
        opts_row.addWidget(self._chk_data_dictionary)

        opts_row.addStretch()
        lay.addLayout(opts_row)

        grp.setLayout(lay)
        parent.addWidget(grp)

    # ---- Progress ----
    def _build_progress(self, parent):
        self._progress_step = QLabel()
        self._progress_step.setFont(_make_font(pt=8))
        _set_foreground(self._progress_step, _ThemeColors.dim())
        self._progress_step.setVisible(False)
        parent.addWidget(self._progress_step)

        self.progressBar = QProgressBar()
        self.progressBar.setRange(0, 100)
        self.progressBar.setValue(0)
        self.progressBar.setTextVisible(True)
        self.progressBar.setFormat("%p%")
        self.progressBar.setMinimumHeight(20)
        self.progressBar.setStyleSheet(_progress_bar_style())
        parent.addWidget(self.progressBar)
        self.smooth_progress.set_progress_bar(self.progressBar)

    # ---- Log ----
    def _build_log(self):
        widget = QWidget()
        lay = QVBoxLayout(widget)
        lay.setSpacing(3)
        lay.setContentsMargins(0, 0, 0, 0)

        # Header
        hdr = QHBoxLayout()
        hdr.setSpacing(6)

        lbl = QLabel("Journal")
        lbl.setFont(_make_font(pt=9, bold=True))
        hdr.addWidget(lbl)
        hdr.addStretch()

        self.clearLogBtn = QPushButton("Effacer")
        self.clearLogBtn.setIcon(_qgs_icon('mActionDeleteSelected.svg'))
        self.clearLogBtn.setIconSize(QSize(12, 12))
        self.clearLogBtn.setCursor(CURSOR_POINTING)
        hdr.addWidget(self.clearLogBtn)

        self.exportLogBtn = QPushButton("Exporter")
        self.exportLogBtn.setIcon(_qgs_icon('mActionFileSaveAs.svg'))
        self.exportLogBtn.setIconSize(QSize(12, 12))
        self.exportLogBtn.setCursor(CURSOR_POINTING)
        hdr.addWidget(self.exportLogBtn)

        lay.addLayout(hdr)

        self.textBrowser = QTextBrowser()
        self.textBrowser.setFont(_make_font(pt=8.5, family='Consolas'))
        self.textBrowser.setOpenExternalLinks(False)
        self.textBrowser.setOpenLinks(False)
        lay.addWidget(self.textBrowser, 1)

        # Point _ThemeColors at this widget so dark-mode detection uses its
        # synthesized palette (Qt derives it from the applied QSS stylesheet)
        # rather than the application palette which QGIS never makes dark.
        _ThemeColors._widget_palette_ref = self.textBrowser

        self._build_progress(lay)

        return widget

    # ---- Footer ----
    def _build_footer(self):
        lay = QHBoxLayout()
        lay.setSpacing(8)

        self.helpButton = QPushButton("Aide")
        self.helpButton.setIcon(_qgs_icon('mActionHelpContents.svg'))
        self.helpButton.setIconSize(QSize(16, 16))
        self.helpButton.setCursor(CURSOR_POINTING)
        lay.addWidget(self.helpButton)

        self.diagButton = QPushButton("Diagnostic")
        self.diagButton.setIcon(_qgs_icon('mActionPropertiesWidget.svg'))
        self.diagButton.setIconSize(QSize(16, 16))
        self.diagButton.setCursor(CURSOR_POINTING)
        self.diagButton.setToolTip(
            "Scanner le projet et afficher un rapport complet\n"
            "des ressources detectees, manquantes et couches QGIS."
        )
        lay.addWidget(self.diagButton)

        lay.addStretch()

        self.cancelBtn = QPushButton("  Annuler")
        self.cancelBtn.setStyleSheet(_btn_cancel_style())
        self.cancelBtn.setIcon(_qgs_icon('mTaskCancel.svg'))
        self.cancelBtn.setIconSize(QSize(16, 16))
        self.cancelBtn.setCursor(CURSOR_POINTING)
        self.cancelBtn.setVisible(False)
        lay.addWidget(self.cancelBtn)

        self.startBtn = QPushButton("  Démarrer l'analyse")
        self.startBtn.setStyleSheet(_btn_start_style())
        self.startBtn.setIcon(_qgs_icon('mActionStart.svg'))
        self.startBtn.setIconSize(QSize(16, 16))
        self.startBtn.setCursor(CURSOR_POINTING)
        self.startBtn.setEnabled(False)
        self.startBtn.setMinimumWidth(180)
        lay.addWidget(self.startBtn)

        sep = QFrame()
        sep.setFrameShape(FRAME_HLINE)
        sep.setFrameShadow(FRAME_SUNKEN)

        outer = QVBoxLayout()
        outer.setSpacing(4)
        outer.addWidget(sep)
        outer.addLayout(lay)
        return outer

    # ==================================================================
    #  SIGNAL CONNECTIONS
    # ==================================================================

    def _connect_signals(self):
        self.browseProjectBtn.clicked.connect(self._browse_project)
        self.refreshBtn.clicked.connect(self._refresh_detection)
        self.browseExportBtn.clicked.connect(self._browse_export)
        self.projectPathEdit.textChanged.connect(self._on_project_path_changed)
        self.selectAllBtn.clicked.connect(self._select_all)
        self.deselectAllBtn.clicked.connect(self._deselect_all)
        self.clearLogBtn.clicked.connect(self.textBrowser.clear)
        self.exportLogBtn.clicked.connect(self._export_log)
        self.startBtn.clicked.connect(self._on_start_clicked)
        self.cancelBtn.clicked.connect(self._on_cancel_clicked)
        self.helpButton.clicked.connect(lambda: self.help_requested.emit())
        self.diagButton.clicked.connect(self._run_diagnostic)
        self.textBrowser.anchorClicked.connect(self._on_link_clicked)
        self._chk_comac_drawings.toggled.connect(lambda _: self._save_ui_state())
        self._chk_data_dictionary.toggled.connect(lambda _: self._save_ui_state())
        self.comboInfraPtPot.layerChanged.connect(lambda _: self._refresh_readiness())
        self.comboEtudeCapFt.layerChanged.connect(lambda _: self._refresh_readiness())
        self.comboEtudeComac.layerChanged.connect(lambda _: self._refresh_readiness())
        self.exportPathEdit.textChanged.connect(lambda _: self._refresh_readiness())

    def _save_ui_state(self):
        settings = QgsSettings()
        settings.setValue(
            "PoleAerien/dialog_v2/include_comac_drawings",
            self._chk_comac_drawings.isChecked()
        )
        settings.setValue(
            "PoleAerien/dialog_v2/include_data_dictionary",
            self._chk_data_dictionary.isChecked()
        )
        settings.setValue(
            "PoleAerien/dialog_v2/splitter_state",
            self._splitter.saveState()
        )

    def _restore_ui_state(self):
        settings = QgsSettings()
        checked = settings.value(
            "PoleAerien/dialog_v2/include_comac_drawings",
            False,
            type=bool
        )
        self._chk_comac_drawings.setChecked(bool(checked))
        checked_dict = settings.value(
            "PoleAerien/dialog_v2/include_data_dictionary",
            False,
            type=bool
        )
        self._chk_data_dictionary.setChecked(bool(checked_dict))
        splitter_state = settings.value(
            "PoleAerien/dialog_v2/splitter_state"
        )
        if splitter_state:
            try:
                self._splitter.restoreState(splitter_state)
            except (TypeError, AttributeError):
                pass

    # ==================================================================
    #  PROJECT DETECTION
    # ==================================================================

    def _browse_project(self):
        path = QFileDialog.getExistingDirectory(
            self, "Sélectionner le dossier projet",
            self.projectPathEdit.text() or ""
        )
        if path:
            self.projectPathEdit.setText(path)

    def _browse_export(self):
        path = QFileDialog.getExistingDirectory(
            self, "Sélectionner le dossier d'export",
            self.exportPathEdit.text() or ""
        )
        if path:
            self.exportPathEdit.setText(path)

    def _refresh_detection(self):
        """Re-run detection on the current project path (resync after folder rename)."""
        path = self.projectPathEdit.text()
        if path and os.path.isdir(path):
            self._run_detection(path)
            self._log_info("Detection resynchronisee.")

    def _on_sro_text_changed(self, text):
        """Validate SRO as user types in the editable field."""
        text = text.strip()
        if not text:
            self._sro_status.setText("[--] Saisissez le SRO")
            _set_foreground(self._sro_status, _ThemeColors.warn())
            self._detection.sro = ''
            self._sro_source = ''
            self._validate_start()
            return
        normalized = text.replace('-', '/').replace('_', '/')
        if _SRO_REGEX.match(normalized):
            parts = normalized.split('/')
            sro = f'{parts[0]}/{parts[1]}/{parts[2]}/{parts[3]}'
            self._detection.sro = sro
            if self._sro_source != 'detecte':
                self._sro_source = 'manuel'
            self._sro_status.setText(
                f"[OK] SRO {self._sro_source}"
            )
            _set_foreground(self._sro_status, _ThemeColors.ok())
        else:
            self._sro_status.setText("[--] Format invalide")
            _set_foreground(self._sro_status, _ThemeColors.warn())
            self._detection.sro = ''
        self._validate_start()


    def _on_project_path_changed(self, path):
        if not path or not os.path.isdir(path):
            self._reset_detection()
            return
        self._run_detection(path)

    def _run_detection(self, path):
        self._detection = detect_project(path)
        d = self._detection

        # Header
        self._project_tag.setText(d.project_name)
        self._project_tag.setVisible(bool(d.project_name))
        self._subtitle.setText(f"  -  {d.project_name}")

        # SRO display
        if d.sro:
            self._sro_value.setText(d.sro)
            self._sro_source = 'detecte'
            self._sro_status.setText("[OK] SRO detecte")
            _set_foreground(self._sro_status, _ThemeColors.ok())
        else:
            self._sro_value.clear()
            self._sro_source = ''
            self._sro_status.setText(
                "[--] Saisissez le SRO manuellement"
            )
            _set_foreground(self._sro_status, _ThemeColors.warn())

        # Export default
        if not self.exportPathEdit.text():
            self.exportPathEdit.setText(d.export_dir)

        # Summary (UI-010: multi-line)
        lines = []
        ok_parts = []
        ko_parts = []
        for label, p, found in d.summary_lines():
            if found:
                ok_parts.append(label)
            else:
                ko_parts.append(label)
        if ok_parts:
            lines.append(f"Detecte : {', '.join(ok_parts)}")
        if ko_parts:
            lines.append(f"Absent : {', '.join(ko_parts)}")
        if d.sro:
            lines.append(f"SRO : {d.sro}")
        if d.has_gracethd:
            lines.append("BE : Axione (GraceTHD)")
        self._det_summary.setText("\n".join(lines))
        self._det_summary.setVisible(bool(lines))

        # Module rows with diagnostic hints
        module_map = {
            'maj':       (d.has_ftbt,  d.ftbt_excel,  'FT-BT KO'),
            'capft':     (d.has_capft, d.capft_dir,   'CAP FT'),
            'comac':     (d.has_comac, d.comac_dir,   'COMAC'),
            'c6bd':      (d.has_c6,    d.c6_dir,      'C6 (via CAP FT)'),
            'police_c6': (d.has_c6,    d.c6_dir,      'C6 (via CAP FT)'),
            'c6c3a':     ((d.has_c6 or d.has_c6_annexe) and (d.has_c7 or d.has_c3a),
                          d.c6_annexe_file or d.c6_dir, 'C6 annexe'),
            'gespot_c6': (d.has_gespot and d.has_c6,
                          d.gespot_dir, 'GESPOT'),
        }
        for key, row in self._module_rows.items():
            found, mp, res_label = module_map.get(key, (False, '', ''))
            diag = '' if found else d.get_diagnostic(res_label)
            row.set_detected(found, mp, diagnostic=diag)

        self._update_mod_count()
        self._validate_start()

        # Log with diagnostics for missing resources
        self._log_info(f"Projet : {d.project_name}")
        if d.sro:
            self._log_ok(f"  SRO : {d.sro}")
        if d.has_gracethd:
            self._log_ok(f"  BE : Axione (GraceTHD detecte)")
        has_missing = False
        for label, p, found in d.summary_lines():
            if found:
                self._log_ok(f"  {label} : {os.path.basename(p)}")
            else:
                self._log_dim(f"  {label} : non detecte")
                has_missing = True
                diag = d.get_diagnostic(label)
                if diag:
                    for line in diag.split('\n'):
                        self._log_dim(f"      {line}")
        if has_missing:
            self._log_warn(
                "Conseil : verifiez le nommage des dossiers/fichiers "
                "ou utilisez le bouton Rafraichir apres renommage."
            )

        # Show deduplication warnings
        for label, hint in d.diagnostics:
            if label == 'Doublon potentiel':
                self._log_warn(f"  {label} : {hint}")

    def _reset_detection(self):
        self._detection = DetectionResult()
        self._project_tag.setVisible(False)
        self._subtitle.setText("  -  Selectionnez un dossier projet")
        self._det_summary.setVisible(False)
        self._sro_value.clear()
        self._sro_status.setText("")
        for row in self._module_rows.values():
            row.set_detected(False)
        self._update_mod_count()
        self._validate_start()

    # ==================================================================
    #  MODULE SELECTION
    # ==================================================================

    def _on_module_toggled(self, key, checked):
        self._update_mod_count()
        self._validate_start()

    def _select_all(self):
        for row in self._module_rows.values():
            if row.checkbox.isEnabled():
                row.checkbox.setChecked(True)

    def _deselect_all(self):
        for row in self._module_rows.values():
            row.checkbox.setChecked(False)

    def _update_mod_count(self):
        sel = sum(1 for r in self._module_rows.values() if r.is_selected)
        self._mod_count.setText(f"{sel}/{len(self._module_rows)} sélectionné(s)")

    def _refresh_readiness(self):
        """Recompute readiness and update all inline indicators."""
        state = self._compute_readiness()
        self.startBtn.setEnabled(state['ready'] and not self._is_running)
        self._update_readiness_label(state)
        self._update_export_status(state)
        self._update_layers_status(state)

    def _compute_readiness(self):
        """Central readiness model: returns a dict with all blocking reasons."""
        reasons = []
        proj_path = self.projectPathEdit.text() or ''
        has_proj = bool(proj_path) and os.path.isdir(proj_path)
        if not has_proj:
            reasons.append("Aucun dossier projet valide")

        has_mod = any(r.is_selected for r in self._module_rows.values())
        if not has_mod:
            reasons.append("Aucun module selectionne")

        # SRO obligatoire en mode ST-AIR
        sro_ok = bool(self._detection.sro)
        if not sro_ok:
            reasons.append("SRO requis (saisir ou detecter via le nom de dossier)")

        export_dir = self.exportPathEdit.text() or self._detection.export_dir
        export_ok = bool(export_dir) and os.path.isdir(export_dir)
        if not export_ok and export_dir:
            reasons.append("Dossier export introuvable")

        layers_ok = True
        missing_layers = []
        if not self.comboInfraPtPot.currentLayer():
            missing_layers.append("infra_pt_pot")
        if not self.comboEtudeCapFt.currentLayer():
            missing_layers.append("etude_cap_ft")
        if not self.comboEtudeComac.currentLayer():
            missing_layers.append("etude_comac")
        if missing_layers:
            layers_ok = False
            reasons.append(
                f"Couche(s) manquante(s) : {', '.join(missing_layers)}"
            )

        # CRS mismatch warning (non-blocking)
        if layers_ok:
            crs_set = set()
            for combo in (self.comboInfraPtPot, self.comboEtudeCapFt,
                          self.comboEtudeComac):
                layer = combo.currentLayer()
                if layer and layer.crs().isValid():
                    crs_set.add(layer.crs().authid())
            if len(crs_set) > 1:
                reasons.append(
                    f"Attention : CRS divergents ({', '.join(sorted(crs_set))})"
                )

        sel_count = sum(1 for r in self._module_rows.values() if r.is_selected)
        mode_label = "Couches QGIS"

        ready = (has_proj and has_mod and sro_ok and layers_ok
                 and not self._is_running)

        return {
            'ready': ready,
            'reasons': reasons,
            'has_project': has_proj,
            'has_modules': has_mod,
            'sro_ok': sro_ok,
            'export_ok': export_ok,
            'export_dir': export_dir,
            'layers_ok': layers_ok,
            'missing_layers': missing_layers,
            'selected_count': sel_count,
            'total_count': len(self._module_rows),
            'mode': mode_label,
        }

    def _update_readiness_label(self, state):
        """Update the inline readiness summary before the footer."""
        if self._is_running:
            self._readiness_label.setText("Analyse en cours...")
            _set_foreground(self._readiness_label, _ThemeColors.info())
            self._readiness_label.setVisible(True)
            return
        if not state['has_project']:
            self._readiness_label.setVisible(False)
            return
        if state['ready']:
            txt = (f"Pret : {state['selected_count']}/{state['total_count']} "
                   f"module(s) | {state['mode']}")
            src = self._sro_source or '?'
            txt += f" | SRO {src}"
            if not state['export_ok']:
                txt += " | Export: defaut projet"
            self._readiness_label.setText(txt)
            _set_foreground(self._readiness_label, _ThemeColors.ok())
        else:
            parts = state['reasons'][:3]
            self._readiness_label.setText("Incomplet : " + " ; ".join(parts))
            _set_foreground(self._readiness_label, _ThemeColors.warn())
        self._readiness_label.setVisible(True)

    def _update_export_status(self, state):
        """Update the inline export status label (UI-006)."""
        if not state['has_project']:
            self._export_status.setVisible(False)
            return
        export_dir = state.get('export_dir', '')
        if not export_dir:
            self._export_status.setText("Export : racine projet (par defaut)")
            _set_foreground(self._export_status, _ThemeColors.dim())
        elif not os.path.isdir(export_dir):
            self._export_status.setText("Export : dossier introuvable")
            _set_foreground(self._export_status, _ThemeColors.error())
        else:
            try:
                test_path = os.path.join(export_dir, '.poleaerien_write_test')
                with open(test_path, 'w', encoding='utf-8') as _f:
                    pass
                os.remove(test_path)
                self._export_status.setText("Export : OK")
                _set_foreground(self._export_status, _ThemeColors.ok())
            except OSError:
                self._export_status.setText("Export : non inscriptible")
                _set_foreground(self._export_status, _ThemeColors.error())
        self._export_status.setVisible(True)

    def _update_layers_status(self, state):
        """Update the inline layers status label (UI-007)."""
        if not state['has_project']:
            self._layers_status.setVisible(False)
            return
        missing = state.get('missing_layers', [])
        if missing:
            self._layers_status.setText(
                f"{3 - len(missing)}/3 couches | manquante(s) : {', '.join(missing)}"
            )
            _set_foreground(self._layers_status, _ThemeColors.warn())
        else:
            # Check CRS divergence
            crs_set = set()
            for combo in (self.comboInfraPtPot, self.comboEtudeCapFt,
                          self.comboEtudeComac):
                layer = combo.currentLayer()
                if layer and layer.crs().isValid():
                    crs_set.add(layer.crs().authid())
            if len(crs_set) > 1:
                self._layers_status.setText(
                    f"3/3 couches | CRS divergents : {', '.join(sorted(crs_set))}"
                )
                _set_foreground(self._layers_status, _ThemeColors.warn())
            else:
                crs_txt = f" [{next(iter(crs_set))}]" if crs_set else ""
                self._layers_status.setText(
                    f"3/3 couches selectionnees{crs_txt}"
                )
                _set_foreground(self._layers_status, _ThemeColors.ok())
        self._layers_status.setVisible(True)

    def _validate_start(self):
        """Legacy compat: delegates to centralized readiness."""
        self._refresh_readiness()

    def selected_modules(self):
        order = ['maj', 'capft', 'comac', 'c6bd', 'police_c6', 'c6c3a', 'gespot_c6']
        return [k for k in order if k in self._module_rows and self._module_rows[k].is_selected]

    # ==================================================================
    #  EXECUTION STATE
    # ==================================================================

    def _on_start_clicked(self):
        mods = self.selected_modules()
        if mods:
            self.start_requested.emit(mods)

    def _on_cancel_clicked(self):
        self.cancel_requested.emit()

    def set_running(self, running):
        self._is_running = running
        self.startBtn.setVisible(not running)
        self.cancelBtn.setVisible(running)
        self.browseProjectBtn.setEnabled(not running)
        self.browseExportBtn.setEnabled(not running)
        self.projectPathEdit.setReadOnly(running)
        self.exportPathEdit.setReadOnly(running)
        self.selectAllBtn.setEnabled(not running)
        self.deselectAllBtn.setEnabled(not running)
        self._chk_comac_drawings.setEnabled(not running)
        self._chk_data_dictionary.setEnabled(not running)
        self._sro_value.setReadOnly(running)
        self.refreshBtn.setEnabled(not running)

        for row in self._module_rows.values():
            row.checkbox.setEnabled(not running and row._found)

        if running:
            self.smooth_progress.reset()
            self.smooth_progress.set_target(2)
        self._validate_start()

    def set_progress(self, percent):
        self.smooth_progress.set_target(percent)

    def set_progress_step(self, text):
        """Show a short step label above the progress bar during batch."""
        if self._progress_step is None:
            return
        if text:
            self._progress_step.setText(text)
            self._progress_step.setVisible(True)
        else:
            self._progress_step.setVisible(False)

    def reset_after_batch(self):
        self._is_running = False
        self.smooth_progress.set_target(100)
        self.set_progress_step('')
        self.startBtn.setVisible(True)
        self.cancelBtn.setVisible(False)
        self.browseProjectBtn.setEnabled(True)
        self.browseExportBtn.setEnabled(True)
        self.projectPathEdit.setReadOnly(False)
        self.exportPathEdit.setReadOnly(False)
        self.selectAllBtn.setEnabled(True)
        self.deselectAllBtn.setEnabled(True)
        self._chk_comac_drawings.setEnabled(True)
        self._chk_data_dictionary.setEnabled(True)
        self._sro_value.setReadOnly(False)
        self.refreshBtn.setEnabled(True)
        for row in self._module_rows.values():
            row.checkbox.setEnabled(row._found)
        self._validate_start()

    # ==================================================================
    #  DIAGNOSTIC
    # ==================================================================

    def _run_diagnostic(self):
        """Pre-diagnostic intelligent du livrable et de l'environnement."""
        self.textBrowser.clear()

        path = self.projectPathEdit.text()
        if not path or not os.path.isdir(path):
            self._log_err("Aucun dossier projet selectionne.")
            return

        # Ensure detection is up-to-date
        d = self._detection
        if not d.project_name:
            self._run_detection(path)
            d = self._detection

        # Deep analysis
        analysis = analyse_livrable(d)

        # ── Header ──
        self._log_info(f"<b>=== PRE-DIAGNOSTIC : {d.project_name or os.path.basename(path)} ===</b>")
        if d.sro:
            self._log_ok(f"  SRO : {d.sro}")
        else:
            self._log_warn(
                "  SRO : non reconnu (format attendu : XXXXX-YYY-ZZZ-NNNNN)"
            )

        # ── 1. Contenu du livrable ──
        self._log_info("")
        self._log_info("<b>--- Contenu du livrable ---</b>")

        # CAP FT
        if d.has_capft:
            cap = analysis.get('capft', {})
            parts = []
            if cap.get('cmd'):
                parts.append(f"{cap['cmd']} CMD")
            if cap.get('etudes'):
                parts.append(f"{cap['etudes']} etude(s)")
            if cap.get('excels'):
                parts.append(f"{cap['excels']} fichiers Excel")
            self._log_ok(f"  CAP FT ........... {' | '.join(parts) if parts else 'detecte'}")
        else:
            self._log_warn("  CAP FT ........... Non livre")

        # COMAC
        if d.has_comac:
            com = analysis.get('comac', {})
            parts = []
            if com.get('sous_dossiers'):
                parts.append(f"{com['sous_dossiers']} sous-dossier(s)")
            if com.get('excels'):
                parts.append(f"{com['excels']} fichiers Excel")
            self._log_ok(f"  COMAC ............ {' | '.join(parts) if parts else 'detecte'}")
        else:
            self._log_warn("  COMAC ............ Non livre")

        # FT-BT KO
        if d.has_ftbt:
            nom = analysis.get('ftbt', {}).get('fichier', '?')
            self._log_ok(f"  FT-BT KO ......... {nom}")
        else:
            self._log_warn("  FT-BT KO ......... Non livre")

        # C7 / C3A
        if d.has_c7:
            self._log_ok(f"  C7 ............... {analysis.get('c7', {}).get('fichier', '?')}")
        else:
            self._log_dim("  C7 ............... Non livre")
        if d.has_c3a:
            self._log_ok(f"  C3A .............. {analysis.get('c3a', {}).get('fichier', '?')}")
        else:
            self._log_dim("  C3A .............. Non livre")

        # ── 2. Environnement QGIS ──
        self._log_info("")
        self._log_info("<b>--- Environnement QGIS ---</b>")

        self._log_dim("  Mode : Couches QGIS (ST-AIR, 100% local)")
        pg_ok = False  # plus de connexion PG en mode sous-traitant

        # QGIS layers - check selected layers
        layer_checks = [
            (self.comboInfraPtPot.currentLayer(), 'infra_pt_pot',
             'Poteaux (Point)'),
            (self.comboEtudeCapFt.currentLayer(), 'etude_cap_ft',
             'Zonage CAP FT (Polygone)'),
            (self.comboEtudeComac.currentLayer(), 'etude_comac',
             'Zonage COMAC (Polygone)'),
        ]
        crs_set = set()
        for lyr, key, desc in layer_checks:
            if lyr:
                fc = lyr.featureCount()
                crs = lyr.crs().authid() if lyr.crs().isValid() else '?'
                crs_set.add(crs)
                if fc > 0:
                    self._log_ok(
                        f"  {key} : {lyr.name()} ({fc:,} entites, {crs})")
                else:
                    self._log_warn(
                        f"  {key} : {lyr.name()} (VIDE - 0 entite)")
            else:
                self._log_warn(f"  {key} : non selectionnee ({desc})")

        if len(crs_set) > 1:
            self._log_warn(
                f"  CRS : heterogenes ({', '.join(crs_set)}) - detail dans Qualite donnees")

        # Export
        export_dir = self.exportPathEdit.text() or d.export_dir
        if export_dir and os.path.isdir(export_dir):
            try:
                test_path = os.path.join(export_dir, '.poleaerien_diag_test')
                with open(test_path, 'w') as _f:
                    pass
                os.remove(test_path)
                self._log_ok(f"  Export : {export_dir}")
            except OSError:
                self._log_err(f"  Export : {export_dir} (NON inscriptible)")
        elif export_dir:
            self._log_err(f"  Export : {export_dir} (dossier introuvable)")
        else:
            self._log_dim("  Export : racine projet (par defaut)")

        # ── 3. Qualite donnees ──
        self._log_info("")
        self._log_info("<b>--- Qualite donnees ---</b>")

        from .preflight_checks import run_data_quality_checks, format_check_log
        from .qgis_utils import detect_etude_field as _detect_etude_field

        _lyr_pot_dq = self.comboInfraPtPot.currentLayer()
        _lyr_cap_dq = self.comboEtudeCapFt.currentLayer()
        _lyr_com_dq = self.comboEtudeComac.currentLayer()

        _be_type_dq = 'axione' if d.has_gracethd else 'nge'
        if not d.has_gracethd and d.sro:
            self._log_dim(
                "  Type BE : infere 'NGE' (dossier GraceTHD absent). "
                "Pour un SRO Axione, livrer le dossier GraceTHD pour activer PF-09/10."
            )
        _mods_dq = self.selected_modules() or [
            'maj', 'capft', 'comac', 'c6bd', 'police_c6', 'c6c3a'
        ]

        def _auto_field_diag(lyr):
            result = _detect_etude_field(lyr, context="Diagnostic")
            if result:
                return result
            if lyr and lyr.isValid():
                for _fld in lyr.fields():
                    if _fld.typeName().lower() in ('string', 'text', 'varchar'):
                        return _fld.name()
            return None

        self._log_dim("  Analyse en cours...")
        try:
            _dq_checks = run_data_quality_checks(
                _lyr_pot_dq, _lyr_cap_dq, _lyr_com_dq,
                _mods_dq,
                auto_field_fn=_auto_field_diag,
                be_type=_be_type_dq,
                gracethd_dir=d.gracethd_dir if d.has_gracethd else '',
                comac_dir=d.comac_dir if d.has_comac else '',
                capft_dir=d.capft_dir if d.has_capft else '',
            )
            if not _dq_checks:
                self._log_dim("  (aucun check applicable - selectionner des modules et/ou des couches QGIS)")
            else:
                _n_block = 0
                _n_warn = 0
                for _chk in _dq_checks:
                    _msg, _lvl = format_check_log(_chk)
                    if _lvl == 'error':
                        self._log_err(_msg)
                        _n_block += 1
                    elif _lvl == 'warning':
                        self._log_warn(_msg)
                        _n_warn += 1
                    else:
                        self._log_ok(_msg)
                if _n_block:
                    self._log_err(
                        f"  => {_n_block} probleme(s) BLOQUANT(S) detecte(s) - "
                        f"corriger avant de lancer."
                    )
                elif _n_warn:
                    self._log_warn(
                        f"  => {_n_warn} avertissement(s) - "
                        f"verifier avant de lancer."
                    )
                else:
                    self._log_ok("  => Qualite donnees : OK")
        except Exception as _dq_exc:
            self._log_warn(f"  Qualite donnees: erreur inattendue ({_dq_exc})")

        # ── 4. Modules ──
        self._log_info("")
        self._log_info("<b>--- Modules ---</b>")

        detected = d.detected_modules()

        module_defs = [
            ('maj',       'MAJ BD',       self._diag_prereq_maj),
            ('capft',     'CAP_FT',       self._diag_prereq_capft),
            ('comac',     'COMAC',        self._diag_prereq_comac),
            ('c6bd',      'C6 vs BD',     self._diag_prereq_c6),
            ('police_c6', 'POLICE C6',    self._diag_prereq_c6),
            ('c6c3a',     'C6-C3A-C7-BD', self._diag_prereq_c6c3a),
            ('gespot_c6', 'GESPOT vs C6',  self._diag_prereq_gespot_c6),
        ]

        n_ready = 0
        for key, name, prereq_fn in module_defs:
            if key in detected:
                detail = prereq_fn(analysis)
                self._log_ok(f"  PRET    {name:.<16s} {detail}")
                n_ready += 1
            else:
                raison = prereq_fn(analysis, missing=True)
                self._log_warn(f"  BLOQUE  {name:.<16s} {raison}")

        # ── 4. Recommandations ──
        recommandations = self._build_recommandations(d, detected, pg_ok)
        if recommandations:
            self._log_info("")
            self._log_info("<b>--- Recommandations ---</b>")
            for reco in recommandations:
                self._log_dim(f"  > {reco}")

        # ── Verdict ──
        self._log_info("")
        total = len(module_defs)
        if n_ready == total:
            self._log_ok(f"<b>Verdict : {n_ready}/{total} modules prets. Livrable complet.</b>")
        elif n_ready > 0:
            self._log_warn(
                f"<b>Verdict : {n_ready}/{total} modules prets. "
                f"Livrable partiel.</b>")
        else:
            self._log_err(f"<b>Verdict : 0/{total} modules prets.</b>")

    # --- Diagnostic helpers ---

    def _diag_prereq_maj(self, analysis, missing=False):
        if missing:
            return "fichier FT-BT KO manquant a la racine"
        return analysis.get('ftbt', {}).get('fichier', '')

    def _diag_prereq_capft(self, analysis, missing=False):
        if missing:
            return "dossier CAP FT non detecte"
        cap = analysis.get('capft', {})
        parts = []
        if cap.get('cmd'):
            parts.append(f"{cap['cmd']} CMD")
        if cap.get('etudes'):
            parts.append(f"{cap['etudes']} etudes")
        return ', '.join(parts) if parts else 'detecte'

    def _diag_prereq_comac(self, analysis, missing=False):
        if missing:
            return "dossier COMAC non detecte"
        com = analysis.get('comac', {})
        n = com.get('sous_dossiers', 0)
        return f"{n} etude(s)" if n else 'detecte'

    def _diag_prereq_c6(self, analysis, missing=False):
        if missing:
            return "dossier CAP FT requis (C6 = etudes dans CAP FT)"
        cap = analysis.get('capft', {})
        n = cap.get('etudes', 0)
        return f"via CAP FT ({n} etudes)" if n else 'via CAP FT'

    def _diag_prereq_c6c3a(self, analysis, missing=False):
        if missing:
            return "C7 et/ou C3A requis en complement de C6"
        parts = []
        if 'c7' in analysis:
            parts.append('C7')
        if 'c3a' in analysis:
            parts.append('C3A')
        return f"C6 + {' + '.join(parts)}" if parts else 'detecte'

    def _diag_prereq_gespot_c6(self, analysis, missing=False):
        if missing:
            return "dossier GSPOT/GESPOT et dossier CAP FT requis"
        d = self._detection
        csv_count = 0
        import os
        if d.gespot_dir and os.path.isdir(d.gespot_dir):
            csv_count = sum(1 for f in os.listdir(d.gespot_dir)
                            if f.lower().endswith('.csv'))
        return f"GESPOT ({csv_count} CSV) + CAP FT"

    def _build_recommandations(self, d, detected, pg_ok):
        recos = []
        if 'maj' not in detected:
            recos.append(
                'MAJ BD : placer le fichier Excel FT-BT KO '
                'a la racine du projet')
        if 'capft' not in detected:
            recos.append(
                'CAP FT / C6 / Police : creer le dossier CAP FT '
                '(ou ETUDE_CAPFT) avec les CMD a l\'interieur')
        if 'comac' not in detected:
            recos.append(
                'COMAC : creer le dossier COMAC '
                '(ou ETUDE_COMAC) avec les sous-dossiers d\'etude')
        if 'c6c3a' not in detected and 'c6bd' in detected:
            missing = []
            if not d.has_c7:
                missing.append('C7')
            if not d.has_c3a:
                missing.append('C3A')
            if missing:
                recos.append(
                    f"C6-C3A-C7-BD : ajouter les annexes "
                    f"{' et '.join(missing)}")
        any_missing = not all([
            self.comboInfraPtPot.currentLayer(),
            self.comboEtudeCapFt.currentLayer(),
            self.comboEtudeComac.currentLayer(),
        ])
        if any_missing:
            recos.append(
                'Couches QGIS : selectionner les 3 couches '
                '(infra_pt_pot, etude_cap_ft, etude_comac)')
        return recos

    # ==================================================================
    #  LOGGING
    # ==================================================================

    def _log_html(self, html):
        self.textBrowser.append(html)
        c = self.textBrowser.textCursor()
        c.movePosition(QTextCursor.MoveOperation.End)
        self.textBrowser.setTextCursor(c)

    def _log_info(self, msg):
        self._log_html(f'<span style="color:{_c(_ThemeColors.info())}">{msg}</span>')

    def _log_ok(self, msg):
        self._log_html(f'<span style="color:{_c(_ThemeColors.ok())}">{msg}</span>')

    def _log_warn(self, msg):
        self._log_html(f'<span style="color:{_c(_ThemeColors.warn())}">{msg}</span>')

    def _log_err(self, msg):
        self._log_html(f'<span style="color:{_c(_ThemeColors.error())}"><b>{msg}</b></span>')

    def _log_dim(self, msg):
        self._log_html(f'<span style="color:{_c(_ThemeColors.dim())}">{msg}</span>')

    def log_result_link(self, filepath):
        """Log a clickable link to open a result file."""
        fname = os.path.basename(filepath)
        url = filepath.replace('\\', '/')
        self._log_html(
            f'<span style="color:{_c(_ThemeColors.ok())}">Rapport : '
            f'<a href="file:///{url}" style="color:{_c(_ThemeColors.info())}; '
            f'text-decoration:underline;">{fname}</a></span>'
        )

    def _on_link_clicked(self, url):
        """Open a file link clicked in the log browser."""
        path = url.toLocalFile()
        if path and os.path.isfile(path):
            os.startfile(path)
        else:
            self._log_warn(f"Fichier introuvable : {path}")

    def log_message(self, msg, level='info'):
        dispatch = {
            'info': self._log_info,
            'success': self._log_ok,
            'warning': self._log_warn,
            'error': self._log_err,
        }
        dispatch.get(level, self._log_info)(msg)

    def _export_log(self):
        path, _ = QFileDialog.getSaveFileName(
            self, "Exporter le journal", "", "Fichier texte (*.txt)"
        )
        if path:
            try:
                with open(path, 'w', encoding='utf-8') as f:
                    f.write(self.textBrowser.toPlainText())
                self._log_ok(f"Journal exporté : {path}")
            except OSError as exc:
                self._log_err(f"Erreur export : {exc}")

    # ==================================================================
    #  PUBLIC API
    # ==================================================================

    @property
    def detection(self):
        return self._detection

    @property
    def is_project_mode(self):
        """ST-AIR : toujours False, pas de mode Projet BDD."""
        return False

    @property
    def load_layers_in_qgis(self):
        """ST-AIR : toujours False (pas de chargement BDD)."""
        return False

    @property
    def include_comac_drawings(self):
        return self._chk_comac_drawings.isChecked()

    @property
    def include_data_dictionary(self):
        return self._chk_data_dictionary.isChecked()

    @property
    def spatial_tolerance(self):
        """Spatial matching tolerance (m) - ST-AIR utilise la valeur par defaut."""
        return 7.5

    @property
    def sro(self):
        """SRO derived from project name or manually entered."""
        return self._detection.sro

    def get_export_dir(self):
        return self.exportPathEdit.text() or self._detection.export_dir

    def init_default_layers(self):
        # Collect candidates with priority (higher = better match)
        best_pot = (None, 0)
        best_cap = (None, 0)
        best_com = (None, 0)

        for layer in QgsProject.instance().mapLayers().values():
            name = layer.name().lower()
            geom = layer.geometryType() if hasattr(layer, 'geometryType') else -1

            # infra_pt_pot: Point layers only
            if 'infra_pt_pot' in name and geom == GEOM_POINT:
                best_pot = (layer, 1)

            # etude_cap_ft: Polygon layers, prefer exact match
            if geom == GEOM_POLYGON:
                if 'etude_cap_ft' in name:
                    best_cap = (layer, 3)
                elif ('cap_ft' in name or 'capft' in name) and best_cap[1] < 2:
                    best_cap = (layer, 2)

            # etude_comac: Polygon layers, prefer exact match
            if geom == GEOM_POLYGON:
                if 'etude_comac' in name:
                    best_com = (layer, 3)
                elif 'comac' in name and best_com[1] < 2:
                    best_com = (layer, 2)

        if best_pot[0]:
            self.comboInfraPtPot.setLayer(best_pot[0])
        if best_cap[0]:
            self.comboEtudeCapFt.setLayer(best_cap[0])
        if best_com[0]:
            self.comboEtudeComac.setLayer(best_com[0])

    def keyPressEvent(self, event):
        """UI-024: Keyboard shortcuts for batch actions."""
        key = event.key()
        mods = event.modifiers()
        if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            if mods & Qt.KeyboardModifier.ControlModifier:
                if self.startBtn.isEnabled() and self.startBtn.isVisible():
                    self._on_start_clicked()
                    return
        if key == Qt.Key.Key_Escape:
            if self._is_running:
                self._on_cancel_clicked()
                return
        super().keyPressEvent(event)

    def changeEvent(self, event):
        """Refresh targeted stylesheets when the QGIS theme changes."""
        super().changeEvent(event)
        if event.type() == QEvent.Type.PaletteChange:
            self.startBtn.setStyleSheet(_btn_start_style())
            self.cancelBtn.setStyleSheet(_btn_cancel_style())
            self.progressBar.setStyleSheet(_progress_bar_style())
            self._refresh_readiness()

    def closeEvent(self, event):
        _ThemeColors._widget_palette_ref = None
        if self._is_running:
            self.cancel_requested.emit()
        self._save_ui_state()
        if self.smooth_progress:
            self.smooth_progress.reset()
        super().closeEvent(event)
