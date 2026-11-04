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
PoleAerien Dialog V2 - Interface d'analyse par lot alignee sur VTT et RecoGC.

Pure Python, sans fichier .ui. Compatible QGIS 3.28+ (Qt5) et QGIS 4.0+ (Qt6).
Jetons visuels WCAG AA, surfaces Fluent / VTT a repere lateral, ParcoursVisuel
interactif, grille Bento des modules et support complet du glisser-deposer.
"""

import logging
import os
import re

from qgis.core import QgsApplication, QgsProject, QgsSettings
from qgis.gui import QgsCollapsibleGroupBox, QgsMapLayerComboBox
from qgis.PyQt.QtCore import (
    QEasingCurve, QEvent, QObject, QPropertyAnimation, QRectF, QSize,
    Qt, QTimer, pyqtProperty, pyqtSignal,
)
from qgis.PyQt.QtGui import (
    QBrush, QColor, QCursor, QFont, QIcon, QPainter, QPalette,
    QPen, QPixmap, QTextCursor,
)
from qgis.PyQt.QtWidgets import (
    QAbstractButton, QCheckBox, QDialog, QDialogButtonBox, QFileDialog,
    QFormLayout, QFrame, QGridLayout, QHBoxLayout, QLabel, QLineEdit,
    QProgressBar, QPushButton, QSplitter, QTextBrowser, QVBoxLayout, QWidget,
)

from .async_tasks import SmoothProgressController
from .compat import (
    ALIGN_CENTER, ALIGN_LEFT, ALIGN_VCENTER, BTN_CANCEL, BTN_OK,
    CASE_INSENSITIVE, CURSOR_POINTING, DIALOG_ACCEPTED, FRAME_HLINE,
    FRAME_SUNKEN, GEOM_POINT, GEOM_POLYGON, LAYER_FILTER_POINT,
    LAYER_FILTER_POLYGON, MATCH_CONTAINS, PAL_BASE, PAL_BRIGHT_TEXT,
    PAL_HIGHLIGHT, PAL_MID, PAL_MIDLIGHT, PAL_WINDOW, PAL_WINDOW_TEXT,
    VERTICAL, WF_NO_HELP,
)
from .project_detector import DetectionResult, analyse_livrable, detect_project
from . import ui_theme
from .version import get_version

logger = logging.getLogger(__name__)

PLUGIN_DIR = os.path.dirname(__file__)
SVG_DIR = os.path.join(PLUGIN_DIR, 'svg', 'outline')


def _is_dark_theme():
    """Detection rapide du theme sombre QGIS."""
    return ui_theme.is_dark()


def _svg_pixmap(name, size=14, color=None):
    """Rend une icone SVG au trait optique 1.5 px avec teinte optionnelle."""
    return ui_theme.svg_pixmap(name, size=size, color=color)


def _svg_icon(name):
    """Charge une icone SVG teintee selon la palette active."""
    return ui_theme.svg_icon(name, color=ui_theme.tokens()["texte"])


def _svg_icon_colored(name, color):
    """Charge une icone SVG avec une couleur explicite."""
    return ui_theme.svg_icon(name, color=color)


def _svg_hover_color():
    """Couleur d'accentuation au survol des icones d'action."""
    return ui_theme.tokens()["accent"]


def _plugin_icon(name):
    """Charge l'icone principale du plugin depuis images/."""
    path = os.path.join(PLUGIN_DIR, 'images', name)
    if os.path.exists(path):
        return QIcon(path)
    return QIcon()


def _qevent_type(name):
    if hasattr(QEvent, 'Type'):
        return getattr(QEvent.Type, name)
    return getattr(QEvent, name)


class _HoverIconBehavior(QObject):
    """Anime le changement d'icone et de teinte au survol d'un bouton."""

    def __init__(self, button, normal_svg, hover_svg, icon_size, fixed_color=None):
        super().__init__(button)
        self._button = button
        self._normal_svg = normal_svg
        self._hover_svg = hover_svg
        self._icon_size = icon_size
        self._fixed_color = fixed_color
        self._is_hovered = False
        self.refresh()
        button.installEventFilter(self)

    def refresh(self):
        if self._fixed_color is not None:
            col = self._fixed_color() if callable(self._fixed_color) else self._fixed_color
            self._normal_icon = _svg_icon_colored(self._normal_svg, col)
            self._hover_icon = _svg_icon_colored(self._hover_svg, col)
        else:
            self._normal_icon = _svg_icon(self._normal_svg)
            self._hover_icon = _svg_icon_colored(self._hover_svg, _svg_hover_color())
        icon = self._hover_icon if self._is_hovered else self._normal_icon
        self._button.setIcon(icon)
        self._button.setIconSize(QSize(self._icon_size, self._icon_size))

    def eventFilter(self, watched, event):
        if watched is self._button:
            event_type = event.type()
            if event_type == _qevent_type('Enter'):
                self._enter()
            elif event_type == _qevent_type('Leave'):
                self._leave()
        return super().eventFilter(watched, event)

    def _enter(self):
        if not self._button.isEnabled():
            return
        self._is_hovered = True
        self._button.setIcon(self._hover_icon)
        self._button.setIconSize(QSize(self._icon_size, self._icon_size))

    def _leave(self):
        self._is_hovered = False
        self._button.setIcon(self._normal_icon)
        self._button.setIconSize(QSize(self._icon_size, self._icon_size))


# Correspondance des balises de journal vers les icones SVG
_LOG_TAG_ICONS = {
    'GPKG': 'package.svg',
    'BDD': 'database.svg',
    'Diag': 'activity.svg',
    'P-02': 'rocket.svg',
    'INFO': 'info-circle.svg',
    'OK': 'circle-check.svg',
    'WARN': 'alert-triangle.svg',
    'ERR': 'alert-circle.svg',
}


# ---------------------------------------------------------------------------
#  Solveur de couleurs semantiques (aligne sur ui_theme WCAG AA)
# ---------------------------------------------------------------------------

class _ThemeColors:
    """Expose les couleurs semantiques VTT / RecoGC derivees de QPalette."""

    _widget_palette_ref = None

    @staticmethod
    def pal():
        ref = _ThemeColors._widget_palette_ref
        if ref is not None:
            ui_theme.set_palette_ref(ref)
        return ui_theme.active_palette()

    @classmethod
    def _is_dark(cls):
        cls.pal()
        return ui_theme.is_dark()

    @classmethod
    def ok(cls):
        cls.pal()
        return ui_theme.tokens()["ok"]

    @classmethod
    def ok_hover(cls):
        cls.pal()
        return ui_theme.tokens()["ok"]

    @classmethod
    def ok_pressed(cls):
        cls.pal()
        return ui_theme.tokens()["ok"]

    @classmethod
    def error(cls):
        cls.pal()
        return ui_theme.tokens()["error"]

    @classmethod
    def error_hover(cls):
        cls.pal()
        return ui_theme.tokens()["error"]

    @classmethod
    def info(cls):
        cls.pal()
        return ui_theme.tokens()["info"]

    @classmethod
    def warn(cls):
        cls.pal()
        return ui_theme.tokens()["warning"]

    @classmethod
    def dim(cls):
        cls.pal()
        return ui_theme.tokens()["secondaire"]

    @classmethod
    def tag_bg(cls):
        cls.pal()
        return ui_theme.tokens()["accentDoux"]

    @classmethod
    def tag_fg(cls):
        cls.pal()
        return ui_theme.tokens()["accent"]

    @classmethod
    def highlight(cls):
        cls.pal()
        return ui_theme.tokens()["accent"]

    @classmethod
    def progress_chunk(cls):
        cls.pal()
        return ui_theme.tokens()["accent"]


def _c(color):
    """Convertit une QColor en chaine hexadecimale CSS."""
    return color.name()


def _btn_start_style():
    """Feuille de style pour le bouton primaire Demarrer."""
    return ui_theme.start_button_qss()


def _btn_cancel_style():
    """Feuille de style pour le bouton d'arret Annuler."""
    return ui_theme.danger_button_qss()


def _progress_bar_style():
    """Feuille de style pour la barre de progression."""
    return ui_theme.progress_qss()


def _make_font(pt=None, bold=False, family=None):
    """Cree une QFont avec taille, graisse et famille optionnelles."""
    f = QFont()
    if pt:
        f.setPointSizeF(pt)
    if bold:
        f.setBold(True)
    if family:
        f.setFamily(family)
    return f


def _set_foreground(widget, color):
    """Applique une couleur de texte via QPalette et feuille de style."""
    pal = widget.palette()
    pal.setColor(PAL_WINDOW_TEXT, color)
    widget.setPalette(pal)
    if isinstance(widget, QCheckBox):
        widget.setStyleSheet(f"QCheckBox {{ color: {color.name()}; spacing: 5px; }}")
    else:
        widget.setStyleSheet(f"color: {color.name()}; background: transparent;")


def _qt_no_focus():
    return getattr(getattr(Qt, 'FocusPolicy', Qt), 'NoFocus')


def _qt_no_pen():
    return getattr(getattr(Qt, 'PenStyle', Qt), 'NoPen')


def _qt_no_brush():
    return getattr(getattr(Qt, 'BrushStyle', Qt), 'NoBrush')


def _qt_easing_out_cubic():
    return getattr(getattr(QEasingCurve, 'Type', QEasingCurve), 'OutCubic')


def _qt_antialiasing():
    return getattr(getattr(QPainter, 'RenderHint', QPainter), 'Antialiasing')


# ---------------------------------------------------------------------------
#  Champs de saisie avec support Glisser-Deposer (Drag & Drop)
# ---------------------------------------------------------------------------

class _PathDropLineEdit(QLineEdit):
    """Champ de chemin acceptant le glisser-deposer de dossiers ou archives."""

    path_dropped = pyqtSignal(str)

    def __init__(self, accept_dirs=True, accept_archives=False, parent=None):
        super().__init__(parent)
        self._accept_dirs = accept_dirs
        self._accept_archives = accept_archives
        self.setAcceptDrops(True)
        self.setMinimumHeight(ui_theme.HAUTEUR_COMPACTE)
        self.apply_theme(False)

    def apply_theme(self, active_drop=False):
        self.setStyleSheet(ui_theme.input_qss(active_drop=active_drop))

    def _matching_local_path(self, mime_data):
        if not mime_data.hasUrls():
            return ''
        for url in mime_data.urls():
            if not url.isLocalFile():
                continue
            local = url.toLocalFile()
            if not local or not os.path.exists(local):
                continue
            if self._accept_dirs and os.path.isdir(local):
                return local
            if self._accept_archives and os.path.isfile(local):
                if local.lower().endswith(('.zip', '.gpkg')):
                    return local
        return ''

    def dragEnterEvent(self, event):  # noqa: N802
        if self._matching_local_path(event.mimeData()):
            event.acceptProposedAction()
            self.apply_theme(True)
        else:
            event.ignore()

    def dragMoveEvent(self, event):  # noqa: N802
        if self._matching_local_path(event.mimeData()):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragLeaveEvent(self, event):  # noqa: N802
        self.apply_theme(False)
        super().dragLeaveEvent(event)

    def dropEvent(self, event):  # noqa: N802
        self.apply_theme(False)
        matched = self._matching_local_path(event.mimeData())
        if matched:
            event.acceptProposedAction()
            self.setText(matched)
            self.path_dropped.emit(matched)
        else:
            event.ignore()


class _HeaderLogoCard(QWidget):
    """Medaillon d'en-tete 34x34 peignant le logo du plugin sur surface VTT."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(34, 34)
        self._icon = _plugin_icon('icon.svg')

    def paintEvent(self, _event):  # noqa: N802
        tok = ui_theme.tokens()
        painter = QPainter(self)
        painter.setRenderHint(_qt_antialiasing(), True)
        rect = QRectF(1.0, 1.0, float(self.width() - 2), float(self.height() - 2))
        border = QColor(tok["accent"])
        border.setAlphaF(0.42)
        ui_theme.paint_card_surface(
            painter,
            rect,
            {
                "bg": tok["accentDoux"],
                "border": border,
                "radius": float(ui_theme.RAYON_CONTROLE),
                "border_width": 1.0,
            },
        )
        pix = self._icon.pixmap(20, 20)
        if not pix.isNull():
            px = int((self.width() - pix.width()) / 2)
            py = int((self.height() - pix.height()) / 2)
            painter.drawPixmap(px, py, pix)
        painter.end()


class _AppleToggle(QAbstractButton):
    """Interrupteur glissant BDD / GPKG anime en OutCubic."""

    def __init__(self, text='', parent=None):
        super().__init__(parent)
        self.setText(text)
        self.setCheckable(True)
        self.setCursor(CURSOR_POINTING)
        self.setFocusPolicy(_qt_no_focus())
        self._offset = 0.0
        self._anim = QPropertyAnimation(self, b"offset", self)
        self._anim.setDuration(130)
        self._anim.setEasingCurve(_qt_easing_out_cubic())
        self.toggled.connect(self._animate)
        self.setFixedSize(self.sizeHint())

    def setText(self, text):
        super().setText(text)
        self.updateGeometry()
        self.update()

    def sizeHint(self):
        return QSize(62, 26)

    def minimumSizeHint(self):
        return self.sizeHint()

    def _offset_value(self):
        return self._offset

    def _set_offset(self, value):
        self._offset = max(0.0, min(1.0, float(value)))
        self.update()

    offset = pyqtProperty(float, fget=_offset_value, fset=_set_offset)

    def _animate(self, checked):
        self._anim.stop()
        self._anim.setStartValue(self._offset)
        self._anim.setEndValue(1.0 if checked else 0.0)
        self._anim.start()

    def paintEvent(self, event):
        tok = ui_theme.tokens()
        painter = QPainter(self)
        painter.setRenderHint(_qt_antialiasing(), True)
        track_w = 58.0
        track_h = 22.0
        x = (self.width() - track_w) / 2.0
        y = (self.height() - track_h) / 2.0
        track = QRectF(x, y, track_w, track_h)
        knob_d = 18.0
        knob_margin = (track_h - knob_d) / 2.0
        on_color = QColor(tok["ok"])
        off_color = QColor(tok["surfaceDouce"])
        track_color = on_color if self.isChecked() else off_color
        border_color = QColor(tok["ok"] if self.isChecked() else tok["ligneControle"])

        painter.setPen(QPen(border_color, 1.0))
        painter.setBrush(QBrush(track_color))
        painter.drawRoundedRect(track, 11.0, 11.0)

        label_font = QFont(self.font())
        label_font.setPointSizeF(7.0)
        label_font.setBold(True)
        painter.setFont(label_font)
        label_color = QColor("#ffffff") if self.isChecked() else tok["secondaire"]
        painter.setPen(QPen(label_color))
        if self.isChecked():
            label_rect = QRectF(x + 5.0, y, track_w - knob_d - 8.0, track_h)
        else:
            label_rect = QRectF(x + knob_d + 4.0, y, track_w - knob_d - 6.0, track_h)
        painter.drawText(label_rect, ALIGN_CENTER, self.text())

        knob_x = x + knob_margin + self._offset * (
            track_w - knob_d - 2.0 * knob_margin
        )
        knob_y = y + knob_margin
        shadow = QRectF(knob_x, knob_y + 0.8, knob_d, knob_d)
        knob = QRectF(knob_x, knob_y, knob_d, knob_d)
        shadow_color = QColor(0, 0, 0, 60 if tok["sombre"] else 35)
        painter.setPen(_qt_no_pen())
        painter.setBrush(QBrush(shadow_color))
        painter.drawEllipse(shadow)
        painter.setBrush(QBrush(QColor(255, 255, 255)))
        painter.drawEllipse(knob)
        painter.end()


# ---------------------------------------------------------------------------
#  Dialogue de saisie SRO avec autocompletion BDD
# ---------------------------------------------------------------------------

_SRO_REGEX = re.compile(
    r'^\d{5}/[A-Z0-9]{2,3}/[A-Z]{2,3}/\d{5}$',
    re.IGNORECASE
)


class SroInputDialog(QDialog):
    """Dialogue compact de saisie SRO avec autocompletion et jetons VTT."""

    def __init__(self, current_sro='', project_name='', sro_list=None,
                 parent=None):
        super().__init__(parent)
        self.setWindowTitle('Code Zone SRO')
        self.setWindowFlags(
            self.windowFlags() & ~WF_NO_HELP
        )
        self.setFixedWidth(360)
        self.setStyleSheet(ui_theme.dialog_qss())

        self._result_sro = ''

        lay = QFormLayout(self)
        lay.setContentsMargins(12, 12, 12, 12)
        lay.setSpacing(8)

        hint = QLabel('Ex. : 63041/B1I/PMZ/00003')
        hint.setFont(_make_font(pt=8))
        _set_foreground(hint, _ThemeColors.dim())

        self._edit = QLineEdit(current_sro)
        self._edit.setPlaceholderText('XXXXX/YYY/ZZZ/NNNNN')
        self._edit.setMaxLength(25)
        self._edit.setMinimumHeight(ui_theme.HAUTEUR_COMPACTE)
        self._edit.setFont(QFont('Consolas', 9))
        self._edit.setStyleSheet(ui_theme.input_qss())
        self._edit.textChanged.connect(self._on_text_changed)

        if sro_list:
            from qgis.PyQt.QtWidgets import QCompleter
            completer = QCompleter(sro_list, self)
            completer.setCaseSensitivity(CASE_INSENSITIVE)
            completer.setFilterMode(MATCH_CONTAINS)
            completer.setMaxVisibleItems(12)
            self._edit.setCompleter(completer)

        self._validation = QLabel()
        self._validation.setFont(_make_font(pt=8, bold=True))

        lay.addRow('SRO :', self._edit)
        lay.addRow('', hint)
        lay.addRow('', self._validation)

        self._btn_box = QDialogButtonBox(
            BTN_OK | BTN_CANCEL
        )
        ok_btn = self._btn_box.button(BTN_OK)
        cancel_btn = self._btn_box.button(BTN_CANCEL)
        if ok_btn is not None:
            ok_btn.setStyleSheet(ui_theme.primary_button_qss())
        if cancel_btn is not None:
            cancel_btn.setStyleSheet(ui_theme.secondary_button_qss())
        self._btn_box.accepted.connect(self._accept)
        self._btn_box.rejected.connect(self.reject)
        lay.addRow(self._btn_box)

        self._on_text_changed(self._edit.text())

    def _on_text_changed(self, text):
        text = text.strip().upper()
        if not text:
            self._validation.setText('')
            self._edit.setStyleSheet(ui_theme.input_qss())
            self._btn_box.button(BTN_OK).setEnabled(False)
            return
        normalized = text.replace('-', '/').replace('_', '/')
        if _SRO_REGEX.match(normalized):
            self._validation.setText('Format SRO valide')
            _set_foreground(self._validation, _ThemeColors.ok())
            self._edit.setStyleSheet(ui_theme.input_qss(valid=True))
            self._btn_box.button(BTN_OK).setEnabled(True)
        else:
            self._validation.setText('Format attendu : XXXXX/YYY/ZZZ/NNNNN')
            _set_foreground(self._validation, _ThemeColors.error())
            self._edit.setStyleSheet(ui_theme.input_qss(valid=False))
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
#  Carte de module d'analyse (style Bento VTT / RecoGC a repere lateral)
# ---------------------------------------------------------------------------

class _ModuleRow(QWidget):
    """Carte de module interactive avec repere lateral 3 px et sous-ligne de ressource."""

    toggled = pyqtSignal(str, bool)

    def __init__(self, key, label, parent=None, tooltip=''):
        super().__init__(parent)
        self.key = key
        self._found = False
        self._prereq_tooltip = tooltip
        self._hovered = False
        self._hover_t = 0.0
        self.setMinimumHeight(40)
        self.setCursor(CURSOR_POINTING)

        if ' - ' in label:
            short_title, subtitle = label.split(' - ', 1)
        else:
            short_title, subtitle = label, ''
        self._short_title = short_title.strip()
        self._subtitle_default = subtitle.strip()

        outer = QVBoxLayout(self)
        outer.setContentsMargins(10, 4, 8, 4)
        outer.setSpacing(0)

        main_row = QHBoxLayout()
        main_row.setContentsMargins(0, 0, 0, 0)
        main_row.setSpacing(6)

        self.checkbox = QCheckBox(self._short_title, self)
        self.checkbox.setFont(_make_font(pt=8.5, bold=True))
        self.checkbox.setEnabled(False)
        self.checkbox.setCursor(CURSOR_POINTING)
        self.checkbox.setStyleSheet("QCheckBox { spacing: 5px; }")
        if tooltip:
            self.checkbox.setToolTip(tooltip)
            self.setToolTip(tooltip)
        self.checkbox.toggled.connect(self._on_chk_toggled)
        main_row.addWidget(self.checkbox, 1)

        self._state_badge = QLabel("En attente", self)
        self._state_badge.setStyleSheet(ui_theme.chip_qss("neutral"))
        main_row.addWidget(self._state_badge)

        self.status_icon = QLabel(self)
        self.status_icon.setFixedWidth(20)
        self.status_icon.setAlignment(ALIGN_CENTER)
        self.status_icon.setStyleSheet("background: transparent; border: none;")
        self.status_icon.setToolTip("Non detecte")
        main_row.addWidget(self.status_icon)

        outer.addLayout(main_row)

        self._detail_label = QLabel(self._subtitle_default, self)
        self._detail_label.setFont(_make_font(pt=7.5))
        self._detail_label.setStyleSheet("background: transparent; border: none;")
        _set_foreground(self._detail_label, _ThemeColors.dim())
        self._detail_label.setContentsMargins(20, 0, 2, 0)
        outer.addWidget(self._detail_label)
        self._detail_label.setVisible(bool(self._subtitle_default))

        self._anim_timer = QTimer(self)
        self._anim_timer.setInterval(30)
        self._anim_timer.timeout.connect(self._on_anim_tick)

        self._update_status_icon(False)

    def _on_chk_toggled(self, checked):
        self._refresh_visuals()
        self.toggled.emit(self.key, checked)

    def _on_anim_tick(self):
        target = 1.0 if self._hovered else 0.0
        self._hover_t += (target - self._hover_t) * 0.28
        if abs(self._hover_t - target) < 0.02:
            self._hover_t = target
            self._anim_timer.stop()
        self.update()

    def enterEvent(self, event):  # noqa: N802
        self._hovered = True
        if not self._anim_timer.isActive():
            self._anim_timer.start()
        super().enterEvent(event)

    def leaveEvent(self, event):  # noqa: N802
        self._hovered = False
        if not self._anim_timer.isActive():
            self._anim_timer.start()
        super().leaveEvent(event)

    def mouseReleaseEvent(self, event):  # noqa: N802
        if (
            event.button() == Qt.MouseButton.LeftButton
            and self.checkbox.isEnabled()
            and self.rect().contains(event.pos())
        ):
            chk_rect = self.checkbox.geometry()
            if not chk_rect.contains(event.pos()):
                self.checkbox.setChecked(not self.checkbox.isChecked())
                event.accept()
                return
        super().mouseReleaseEvent(event)

    def set_detected(self, found, path='', diagnostic=''):
        self._found = found
        self.checkbox.setEnabled(found)
        self.checkbox.setChecked(found)
        self._update_status_icon(found, diagnostic)

        if found:
            display = os.path.basename(path) if path else ''
            sub = f"{self._subtitle_default}  ·  {display}" if (display and self._subtitle_default) else (display or self._subtitle_default)
            self._detail_label.setText(sub)
            self._detail_label.setToolTip(path or self._prereq_tooltip)
            _set_foreground(self._detail_label, _ThemeColors.dim())
            self._detail_label.setVisible(bool(sub))
        else:
            reason = diagnostic or ''
            if reason:
                first_line = reason.split('\n')[0]
                self._detail_label.setText(first_line)
                _set_foreground(self._detail_label, _ThemeColors.warn())
                self._detail_label.setVisible(True)
            else:
                self._detail_label.setText(self._subtitle_default)
                _set_foreground(self._detail_label, _ThemeColors.dim())
                self._detail_label.setVisible(bool(self._subtitle_default))
            self._detail_label.setToolTip(diagnostic or self._prereq_tooltip)
        self._refresh_visuals()

    def _update_status_icon(self, found, diagnostic=''):
        self.status_icon.clear()
        if found:
            pix = _svg_pixmap('circle-check.svg', size=16, color=_ThemeColors.ok())
            self.status_icon.setPixmap(pix)
            self.status_icon.setToolTip("Detecte")
        else:
            pix = _svg_pixmap('circle-x.svg', size=16, color=_ThemeColors.error())
            self.status_icon.setPixmap(pix)
            tip = "Non detecte"
            if diagnostic:
                tip += "\n" + diagnostic
            elif self._prereq_tooltip:
                tip += "\n" + self._prereq_tooltip
            self.status_icon.setToolTip(tip)

    def _refresh_visuals(self):
        tok = ui_theme.tokens()
        if not self._found:
            self._state_badge.setText("Indisponible")
            self._state_badge.setStyleSheet(ui_theme.chip_qss("optional"))
            _set_foreground(self.checkbox, tok["tertiaire"])
        elif self.checkbox.isChecked():
            self._state_badge.setText("Actif")
            self._state_badge.setStyleSheet(ui_theme.chip_qss("present"))
            _set_foreground(self.checkbox, tok["texte"])
        else:
            self._state_badge.setText("Ignoré")
            self._state_badge.setStyleSheet(ui_theme.chip_qss("neutral"))
            _set_foreground(self.checkbox, tok["secondaire"])
        self.update()

    def apply_theme(self):
        self._update_status_icon(self._found)
        self._refresh_visuals()

    def paintEvent(self, _event):  # noqa: N802
        tok = ui_theme.tokens()
        checked = self._found and self.checkbox.isChecked()
        if checked:
            base_bg = ui_theme.lerp_color(tok["surface"], tok["okDoux"], 0.60)
            left_col = tok["ok"]
            border_base = ui_theme.lerp_color(tok["ligne"], tok["ok"], 0.45)
        elif self._found:
            base_bg = tok["surface"]
            left_col = tok["accent"]
            border_base = tok["ligneControle"]
        else:
            base_bg = tok["surfaceDouce"]
            left_col = tok["ligne"]
            border_base = tok["ligne"]

        bg = ui_theme.lerp_color(base_bg, tok["surfaceSurvol"], self._hover_t * 0.55)
        border = ui_theme.lerp_color(border_base, tok["accent"], self._hover_t * 0.70)

        painter = QPainter(self)
        painter.setRenderHint(_qt_antialiasing(), True)
        rect = QRectF(1.0, 1.0, float(self.width() - 2), float(self.height() - 2))
        ui_theme.paint_card_surface(
            painter,
            rect,
            {
                "bg": bg,
                "border": border,
                "radius": float(ui_theme.RAYON_CONTROLE),
                "border_width": 1.2 if checked else 1.0,
                "left_accent": left_col,
            },
        )
        painter.end()

    @property
    def is_selected(self):
        return self.checkbox.isChecked() and self.checkbox.isEnabled()


# ---------------------------------------------------------------------------
#  Dialogue Principal ST-AIR
# ---------------------------------------------------------------------------

class PoleAerienDialogV2(QDialog):
    """Dialogue principal d'analyse par lot ST-AIR au design VTT / RecoGC."""

    start_requested = pyqtSignal(list)
    cancel_requested = pyqtSignal()
    help_requested = pyqtSignal()
    gpkg_delivery_requested = pyqtSignal(str)  # chemin .zip ou .gpkg

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("PoleAerienBatch")
        self.setAcceptDrops(True)
        self._detection = DetectionResult()
        self._is_running = False
        self._module_rows = {}
        self._locked_to_gpkg = False
        self._gpkg_caches = None
        self._gpkg_summary = None
        self._diag_timer = None
        self._chk_show_layers = None
        self._gpkg_sro_label = ''
        self._chk_comac_drawings = None
        self._chk_data_dictionary = None
        self._chk_bdd_mode = None
        self._bdd_connected = False
        self._sro_source = ''
        self._export_status = None
        self._layers_status = None
        self._readiness_label = None
        self._splitter = None
        self._progress_step = None
        self._last_report_path = ''
        self._reopenReportBtn = None
        self._settingsBtn = None
        self._parcours = None
        self._sro_medallion = None
        self._resource_chips = {}
        self._resource_paths = {}
        self._layer_badges = {}
        self.smooth_progress = SmoothProgressController(interval_ms=25)

        self._auth_probe = None
        self._exportBtn = None
        self._exportProgress = None
        self._export_tasks = None
        self._export_timer = None
        self._hover_icon_behaviors = []
        self._clear_feedback = None

        self._build_ui()
        self._restore_ui_state()
        self._connect_signals()
        self._install_credentials_shortcut()
        self._start_auth_probe()
        self._apply_full_theme()

    # ==================================================================
    #  CONSTRUCTION DE L'INTERFACE
    # ==================================================================

    def _add_hover_icon(self, button, normal_svg, hover_svg, icon_size, fixed_color=None):
        behavior = _HoverIconBehavior(
            button, normal_svg, hover_svg, icon_size, fixed_color=fixed_color
        )
        self._hover_icon_behaviors.append(behavior)

    def _build_ui(self):
        self.setWindowTitle(f"ST-AIR v{get_version()} · Contrôle qualité FTTH aérien")
        self.setWindowIcon(_plugin_icon('icon.svg'))
        self.setMinimumSize(780, 760)
        self.resize(860, 860)

        root = QVBoxLayout(self)
        root.setSpacing(6)
        root.setContentsMargins(10, 8, 10, 8)

        # --- En-tete VTT / RecoGC ---
        root.addLayout(self._build_header())

        # --- Parcours Visuel 4 jalons ---
        self._parcours = ui_theme.ParcoursVisuelBar(self)
        self._parcours.step_activated.connect(self._on_parcours_step_clicked)
        root.addWidget(self._parcours)

        # --- Splitter principal : Configuration (haut) / Journal (bas) ---
        self._splitter = QSplitter(VERTICAL)
        splitter = self._splitter
        splitter.setChildrenCollapsible(False)
        splitter.setHandleWidth(4)

        top_widget = QWidget()
        top_widget.setObjectName("stairTopPane")
        top_lay = QVBoxLayout(top_widget)
        top_lay.setSpacing(5)
        top_lay.setContentsMargins(0, 0, 0, 0)

        self._build_project_group(top_lay)
        self._build_sro_group(top_lay)
        self._build_layers_group(top_lay)
        self._build_modules_group(top_lay)

        for grp in top_widget.findChildren(QgsCollapsibleGroupBox):
            grp.setCollapsed(False)

        splitter.addWidget(top_widget)

        log_widget = self._build_log()
        log_widget.setObjectName("stairLogPane")
        splitter.addWidget(log_widget)

        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        root.addWidget(splitter, 1)

        # --- Bandeau de synthese de preparation (UI-009) ---
        self._readiness_label = QLabel()
        self._readiness_label.setFont(_make_font(pt=8.5, bold=True))
        self._readiness_label.setContentsMargins(6, 2, 6, 2)
        self._readiness_label.setVisible(False)
        root.addWidget(self._readiness_label)

        # --- Barre de commandes / Pied ---
        root.addLayout(self._build_footer())

    # ---- En-tete ----
    def _build_header(self):
        lay = QHBoxLayout()
        lay.setContentsMargins(0, 0, 0, 2)
        lay.setSpacing(8)

        self._logo_card = _HeaderLogoCard(self)
        lay.addWidget(self._logo_card)

        title_col = QVBoxLayout()
        title_col.setContentsMargins(0, 0, 0, 0)
        title_col.setSpacing(0)

        title_row = QHBoxLayout()
        title_row.setContentsMargins(0, 0, 0, 0)
        title_row.setSpacing(6)

        title = QLabel("ST-AIR")
        title_font = QFont()
        title_font.setPointSize(12)
        title_font.setBold(True)
        title.setFont(title_font)
        title_row.addWidget(title)

        ver_lbl = QLabel(f"v{get_version()}")
        ver_lbl.setStyleSheet(ui_theme.badge_qss("neutral"))
        title_row.addWidget(ver_lbl)
        self._ver_badge = ver_lbl
        title_row.addStretch()

        title_col.addLayout(title_row)

        self._subtitle = QLabel("Contrôle qualité des livrables sous-traitants FTTH aérien")
        self._subtitle.setFont(_make_font(pt=8.5))
        _set_foreground(self._subtitle, _ThemeColors.dim())
        title_col.addWidget(self._subtitle)

        lay.addLayout(title_col)
        lay.addStretch()

        self._project_tag = QLabel()
        self._project_tag.setVisible(False)
        self._project_tag.setFont(_make_font(pt=8.5, bold=True))
        self._project_tag.setStyleSheet(ui_theme.badge_qss("accent"))
        lay.addWidget(self._project_tag)

        self._reopenReportBtn = QPushButton("Ouvrir le rapport")
        self._reopenReportBtn.setMinimumHeight(ui_theme.HAUTEUR_COMPACTE)
        self._reopenReportBtn.setCursor(CURSOR_POINTING)
        self._reopenReportBtn.setToolTip("Ouvrir le dernier rapport Excel généré")
        self._reopenReportBtn.setStyleSheet(ui_theme.secondary_button_qss())
        self._add_hover_icon(
            self._reopenReportBtn, 'file-export.svg', 'file-export.svg', 15
        )
        self._reopenReportBtn.setVisible(False)
        lay.addWidget(self._reopenReportBtn)

        self._settingsBtn = QPushButton()
        self._settingsBtn.setFixedSize(
            ui_theme.HAUTEUR_COMPACTE, ui_theme.HAUTEUR_COMPACTE
        )
        self._settingsBtn.setCursor(CURSOR_POINTING)
        self._settingsBtn.setToolTip("Paramètres d'identification et de suivi diagnostic")
        self._settingsBtn.setStyleSheet(ui_theme.tool_button_qss())
        self._add_hover_icon(
            self._settingsBtn, 'settings.svg', 'settings.svg', 16
        )
        lay.addWidget(self._settingsBtn)

        outer = QVBoxLayout()
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(2)
        outer.addLayout(lay)
        return outer

    # ---- 1. Groupe Dossier projet & Livrables ----
    def _build_project_group(self, parent):
        grp = QgsCollapsibleGroupBox("1. Dossier projet et livrables")
        grp.setObjectName("PoleAerien_grp_project")
        grp.setSettingGroup("PoleAerien/dialog_v2")
        lay = QVBoxLayout()
        lay.setSpacing(4)
        lay.setContentsMargins(8, 6, 8, 6)

        # Ligne 1 : Dossier projet
        row1 = QHBoxLayout()
        row1.setSpacing(6)

        lbl = QLabel("Projet :")
        lbl.setFixedWidth(56)
        lbl.setFont(_make_font(pt=8.5, bold=True))
        row1.addWidget(lbl)

        self.projectPathEdit = _PathDropLineEdit(
            accept_dirs=True, accept_archives=False, parent=self
        )
        self.projectPathEdit.setClearButtonEnabled(True)
        self.projectPathEdit.setPlaceholderText(
            "Glissez un dossier projet ici ou cliquez sur Parcourir..."
        )
        row1.addWidget(self.projectPathEdit, 1)

        self.browseProjectBtn = QPushButton("Parcourir")
        self.browseProjectBtn.setMinimumHeight(ui_theme.HAUTEUR_COMPACTE)
        self.browseProjectBtn.setToolTip("Sélectionner le dossier projet sous-traitant")
        self.browseProjectBtn.setCursor(CURSOR_POINTING)
        self.browseProjectBtn.setStyleSheet(ui_theme.secondary_button_qss())
        self._add_hover_icon(
            self.browseProjectBtn, 'folder-open.svg', 'folder-open.svg', 15
        )
        row1.addWidget(self.browseProjectBtn)

        self.refreshBtn = QPushButton()
        self.refreshBtn.setFixedSize(
            ui_theme.HAUTEUR_COMPACTE, ui_theme.HAUTEUR_COMPACTE
        )
        self.refreshBtn.setToolTip("Re-détecter les sous-dossiers et fichiers (synchroniser)")
        self.refreshBtn.setCursor(CURSOR_POINTING)
        self.refreshBtn.setStyleSheet(ui_theme.secondary_button_qss())
        self._add_hover_icon(self.refreshBtn, 'refresh.svg', 'refresh.svg', 15)
        row1.addWidget(self.refreshBtn)

        lay.addLayout(row1)

        # Ligne 2 : Livrable GeoPackage (.zip / .gpkg) + Dossier d'export
        row_gpkg = QHBoxLayout()
        row_gpkg.setSpacing(6)

        lbl_gpkg = QLabel("GPKG :")
        lbl_gpkg.setFixedWidth(56)
        lbl_gpkg.setFont(_make_font(pt=8.5, bold=True))
        row_gpkg.addWidget(lbl_gpkg)

        self.gpkgPathEdit = _PathDropLineEdit(
            accept_dirs=False, accept_archives=True, parent=self
        )
        self.gpkgPathEdit.setReadOnly(True)
        self.gpkgPathEdit.setPlaceholderText(
            "Archive BDD SRO (.zip ou .gpkg) : glisser-déposer ou Charger..."
        )
        row_gpkg.addWidget(self.gpkgPathEdit, 1)

        self.loadGpkgBtn = QPushButton("Charger GPKG")
        self.loadGpkgBtn.setMinimumHeight(ui_theme.HAUTEUR_COMPACTE)
        self.loadGpkgBtn.setToolTip(
            "Charger un livrable GeoPackage (.zip ou .gpkg).\n"
            "Alimente les caches câbles / BPE / attaches consommés par\n"
            "les modules COMAC et Police C6."
        )
        self.loadGpkgBtn.setCursor(CURSOR_POINTING)
        self.loadGpkgBtn.setStyleSheet(ui_theme.secondary_button_qss())
        self._add_hover_icon(
            self.loadGpkgBtn, 'package.svg', 'package-import.svg', 15
        )
        row_gpkg.addWidget(self.loadGpkgBtn)

        lbl2 = QLabel("Export :")
        lbl2.setFont(_make_font(pt=8.5, bold=True))
        row_gpkg.addWidget(lbl2)

        self.exportPathEdit = _PathDropLineEdit(
            accept_dirs=True, accept_archives=False, parent=self
        )
        self.exportPathEdit.setPlaceholderText("Dossier d'export (défaut = projet)")
        row_gpkg.addWidget(self.exportPathEdit, 1)

        self.browseExportBtn = QPushButton()
        self.browseExportBtn.setFixedSize(
            ui_theme.HAUTEUR_COMPACTE, ui_theme.HAUTEUR_COMPACTE
        )
        self.browseExportBtn.setToolTip("Changer le dossier d'export du rapport Excel")
        self.browseExportBtn.setCursor(CURSOR_POINTING)
        self.browseExportBtn.setStyleSheet(ui_theme.secondary_button_qss())
        self._add_hover_icon(
            self.browseExportBtn, 'download.svg', 'download.svg', 15
        )
        row_gpkg.addWidget(self.browseExportBtn)

        self._export_status = QLabel()
        self._export_status.setFont(_make_font(pt=8))
        self._export_status.setStyleSheet(ui_theme.chip_qss("neutral"))
        self._export_status.setVisible(False)
        row_gpkg.addWidget(self._export_status)

        lay.addLayout(row_gpkg)

        # Ligne 3 : Pastilles interactives des pieces du livrable (style RecoGC)
        chips_row = QHBoxLayout()
        chips_row.setContentsMargins(56, 1, 0, 0)
        chips_row.setSpacing(5)

        pieces_spec = (
            ("CAP FT", "Dossier d'études CAP FT (fiches appuis)"),
            ("COMAC", "Dossier d'études COMAC (ExportComac .xlsx)"),
            ("C6", "Annexes C6 (.xlsx)"),
            ("C7", "Annexe C7 (.xlsx / .csv)"),
            ("C3A", "Annexe C3A (.xlsx / .csv)"),
            ("GPKG", "Paquet de référence GeoPackage (.zip / .gpkg)"),
        )
        for key, tip in pieces_spec:
            chip = QPushButton(key)
            chip.setFlat(True)
            chip.setCursor(CURSOR_POINTING)
            chip.setToolTip(tip)
            chip.setStyleSheet(ui_theme.chip_qss("neutral"))
            chip.clicked.connect(lambda _=False, k=key: self._on_resource_chip_clicked(k))
            self._resource_chips[key] = chip
            chips_row.addWidget(chip)

        chips_row.addStretch(1)

        self._det_summary = QLabel()
        self._det_summary.setWordWrap(True)
        self._det_summary.setFont(_make_font(pt=7.5))
        _set_foreground(self._det_summary, _ThemeColors.dim())
        self._det_summary.setVisible(False)
        chips_row.addWidget(self._det_summary)

        lay.addLayout(chips_row)

        grp.setLayout(lay)
        parent.addWidget(grp)

    # ---- 2. Groupe SRO du projet ----
    def _build_sro_group(self, parent):
        grp = QgsCollapsibleGroupBox("2. Zone SRO du projet")
        grp.setObjectName("PoleAerien_grp_sro")
        grp.setSettingGroup("PoleAerien/dialog_v2")
        lay = QHBoxLayout()
        lay.setSpacing(6)
        lay.setContentsMargins(8, 5, 8, 5)

        self._sro_medallion = ui_theme.SroMedallion("database.svg", self)
        self._sro_medallion.setToolTip("Identifiant de la zone SRO étudiée")
        lay.addWidget(self._sro_medallion)

        sro_lbl = QLabel("Code SRO :")
        sro_lbl.setFont(_make_font(pt=8.5, bold=True))
        lay.addWidget(sro_lbl)

        self._sro_value = QLineEdit()
        self._sro_value.setPlaceholderText('XXXXX/YYY/ZZZ/NNNNN')
        self._sro_value.setMaxLength(25)
        self._sro_value.setClearButtonEnabled(True)
        self._sro_value.setMinimumHeight(ui_theme.HAUTEUR_COMPACTE)
        self._sro_value.setFont(_make_font(pt=9, bold=True, family='Consolas'))
        self._sro_value.setMinimumWidth(200)
        self._sro_value.setMaximumWidth(235)
        self._sro_value.setStyleSheet(ui_theme.input_qss())
        self._sro_value.textChanged.connect(self._on_sro_text_changed)
        lay.addWidget(self._sro_value)

        self._sro_status = QLabel("SRO à renseigner")
        self._sro_status.setFont(_make_font(pt=8, bold=True))
        self._sro_status.setStyleSheet(ui_theme.chip_qss("neutral"))
        lay.addWidget(self._sro_status)

        hint = QLabel(
            "Détecté automatiquement depuis le dossier ou le GPKG (modifiable)."
        )
        hint.setFont(_make_font(pt=7.5))
        _set_foreground(hint, _ThemeColors.dim())
        lay.addWidget(hint, 1)

        grp.setLayout(lay)
        parent.addWidget(grp)

    # ---- 3. Groupe Couches QGIS ----
    def _build_layers_group(self, parent):
        self._layers_grp = QgsCollapsibleGroupBox("3. Couches géographiques QGIS")
        self._layers_grp.setObjectName("PoleAerien_grp_layers")
        self._layers_grp.setSettingGroup("PoleAerien/dialog_v2")
        grp = self._layers_grp
        lay = QVBoxLayout()
        lay.setSpacing(4)
        lay.setContentsMargins(8, 5, 8, 5)

        grid = QGridLayout()
        grid.setHorizontalSpacing(8)
        grid.setVerticalSpacing(4)

        self.comboInfraPtPot = QgsMapLayerComboBox()
        self.comboInfraPtPot.setFilters(LAYER_FILTER_POINT)
        self.comboInfraPtPot.setShowCrs(True)

        self.comboEtudeCapFt = QgsMapLayerComboBox()
        self.comboEtudeCapFt.setFilters(LAYER_FILTER_POLYGON)
        self.comboEtudeCapFt.setShowCrs(True)

        self.comboEtudeComac = QgsMapLayerComboBox()
        self.comboEtudeComac.setFilters(LAYER_FILTER_POLYGON)
        self.comboEtudeComac.setShowCrs(True)

        rows_spec = (
            (0, "infra_pt_pot :", "Poteaux (Points)", self.comboInfraPtPot, "pot"),
            (1, "etude_cap_ft :", "Zonage CAP FT (Polygones)", self.comboEtudeCapFt, "cap"),
            (2, "etude_comac :", "Zonage COMAC (Polygones)", self.comboEtudeComac, "com"),
        )
        for idx, title, tip, combo, key in rows_spec:
            lbl = QLabel(title)
            lbl.setFont(_make_font(pt=8.5, bold=True))
            lbl.setToolTip(tip)
            grid.addWidget(lbl, idx, 0)
            grid.addWidget(combo, idx, 1)

            badge = QLabel("Non sélectionnée")
            badge.setAlignment(ALIGN_CENTER)
            badge.setMinimumWidth(145)
            badge.setStyleSheet(ui_theme.chip_qss("neutral"))
            self._layer_badges[key] = badge
            grid.addWidget(badge, idx, 2)

        grid.setColumnStretch(1, 1)
        lay.addLayout(grid)

        status_row = QHBoxLayout()
        status_row.setContentsMargins(0, 2, 0, 0)
        status_row.setSpacing(8)

        self._layers_status = QLabel()
        self._layers_status.setFont(_make_font(pt=8, bold=True))
        self._layers_status.setVisible(False)
        status_row.addWidget(self._layers_status)
        status_row.addStretch(1)

        self.layersUnlockBtn = QPushButton("Modifier manuellement")
        self.layersUnlockBtn.setMinimumHeight(26)
        self.layersUnlockBtn.setFont(_make_font(pt=8))
        self.layersUnlockBtn.setCursor(CURSOR_POINTING)
        self.layersUnlockBtn.setStyleSheet(ui_theme.secondary_button_qss())
        self.layersUnlockBtn.setToolTip(
            "Déverrouiller les listes déroulantes et choisir les couches manuellement\n"
            "(par défaut, les couches sont liées au GeoPackage chargé)."
        )
        self.layersUnlockBtn.setVisible(False)
        status_row.addWidget(self.layersUnlockBtn)

        lay.addLayout(status_row)

        grp.setLayout(lay)
        parent.addWidget(grp)

    # ---- 4. Groupe Modules d'analyse (Grille Bento 2 colonnes) ----
    def _build_modules_group(self, parent):
        grp = QgsCollapsibleGroupBox("4. Modules de contrôle et options du rapport")
        grp.setObjectName("PoleAerien_grp_modules")
        grp.setSettingGroup("PoleAerien/dialog_v2")
        lay = QVBoxLayout()
        lay.setSpacing(6)
        lay.setContentsMargins(8, 6, 8, 6)

        # Barre d'actions de selection
        act_row = QHBoxLayout()
        act_row.setSpacing(6)

        self.selectAllBtn = QPushButton("Tout cocher")
        self.selectAllBtn.setMinimumHeight(28)
        self.selectAllBtn.setCursor(CURSOR_POINTING)
        self.selectAllBtn.setStyleSheet(ui_theme.secondary_button_qss())
        self._add_hover_icon(
            self.selectAllBtn, 'check.svg', 'check.svg', 14
        )
        act_row.addWidget(self.selectAllBtn)

        self.deselectAllBtn = QPushButton("Tout décocher")
        self.deselectAllBtn.setMinimumHeight(28)
        self.deselectAllBtn.setCursor(CURSOR_POINTING)
        self.deselectAllBtn.setStyleSheet(ui_theme.secondary_button_qss())
        self._add_hover_icon(
            self.deselectAllBtn, 'square.svg', 'square.svg', 14
        )
        act_row.addWidget(self.deselectAllBtn)

        act_row.addSpacing(12)

        self._chk_comac_drawings = QCheckBox("Schémas COMAC")
        self._chk_comac_drawings.setToolTip(
            "Ajoute les feuilles COMAC_DESSIN au rapport Excel unifié.\n"
            "Désactivé par défaut car le rendu des schémas peut être long."
        )
        self._chk_comac_drawings.setChecked(False)
        act_row.addWidget(self._chk_comac_drawings)

        self._chk_data_dictionary = QCheckBox("Glossaire Excel")
        self._chk_data_dictionary.setToolTip(
            "Ajoute les feuilles GLOSSAIRE et DICTIONNAIRE au rapport Excel unifié.\n"
            "Décrit chaque colonne, chaque valeur et chaque code couleur du rapport."
        )
        self._chk_data_dictionary.setChecked(False)
        act_row.addWidget(self._chk_data_dictionary)

        act_row.addStretch()

        self._mod_count = QLabel("0/6 sélectionné(s)")
        self._mod_count.setFont(_make_font(pt=8, bold=True))
        self._mod_count.setStyleSheet(ui_theme.badge_qss("neutral"))
        act_row.addWidget(self._mod_count)

        lay.addLayout(act_row)

        # Grille Bento 2 colonnes x 3 lignes pour les 6 modules
        grid = QGridLayout()
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(6)
        grid.setVerticalSpacing(5)

        modules_def = [
            ('capft',     'VERIF CAP_FT - Poteaux vs fiches appuis',
             "Prérequis : dossier CAP FT avec fiches appuis\n"
             "Couches : infra_pt_pot, etude_cap_ft"),
            ('comac',     'VERIF COMAC - Poteaux BT vs ExportComac',
             "Prérequis : dossier COMAC (ExportComac .xlsx)\n"
             "Couches : infra_pt_pot, etude_comac\n"
             "GeoPackage câbles (cache)"),
            ('c6bd',      'C6 vs Données Projet - Annexe C6 vs poteaux',
             "Prérequis : dossier CAP FT (annexes C6 = études .xlsx)\n"
             "Couches : infra_pt_pot, etude_cap_ft"),
            ('police_c6', 'POLICE C6 - Analyse complète des câbles',
             "Prérequis : dossier CAP FT (annexes C6)\n"
             "Couches : infra_pt_pot\n"
             "GeoPackage câbles (cache)"),
            ('c6c3a',     'C6-C3A-C7-BD - Croisement multi-annexes',
             "Prérequis : dossier CAP FT + fichier C7 et/ou C3A\n"
             "Couches : infra_pt_pot, etude_cap_ft"),
            ('gespot_c6', 'GESPOT vs C6 - Comparaison référentiel terrain',
             "Prérequis : couche data_gespot (via GPKG ou BDD) + dossier CAP FT (annexes C6)\n"
             "GESPOT provient du GeoPackage livrable ou de la BDD"),
        ]
        for idx, (key, label, tooltip) in enumerate(modules_def):
            row = _ModuleRow(key, label, parent=grp, tooltip=tooltip)
            row.toggled.connect(self._on_module_toggled)
            self._module_rows[key] = row
            grid.addWidget(row, idx // 2, idx % 2)

        grid.setColumnStretch(0, 1)
        grid.setColumnStretch(1, 1)
        lay.addLayout(grid)

        grp.setLayout(lay)
        parent.addWidget(grp)

    # ---- Barre de progression ----
    def _build_progress(self, parent):
        prog_row = QHBoxLayout()
        prog_row.setContentsMargins(0, 0, 0, 0)
        prog_row.setSpacing(8)

        self._progress_step = QLabel()
        self._progress_step.setFont(_make_font(pt=8, bold=True))
        _set_foreground(self._progress_step, _ThemeColors.info())
        self._progress_step.setVisible(False)
        prog_row.addWidget(self._progress_step)

        self.progressBar = QProgressBar()
        self.progressBar.setRange(0, 100)
        self.progressBar.setValue(0)
        self.progressBar.setTextVisible(True)
        self.progressBar.setFormat("%p%")
        self.progressBar.setMinimumHeight(18)
        self.progressBar.setMaximumHeight(20)
        self.progressBar.setStyleSheet(_progress_bar_style())
        prog_row.addWidget(self.progressBar, 1)

        parent.addLayout(prog_row)
        self.smooth_progress.set_progress_bar(self.progressBar)

    # ---- Journal d'execution ----
    def _build_log(self):
        widget = QWidget()
        lay = QVBoxLayout(widget)
        lay.setSpacing(4)
        lay.setContentsMargins(0, 2, 0, 0)

        hdr = QHBoxLayout()
        hdr.setSpacing(6)

        lbl = QLabel("Journal d'exécution et pré-diagnostic")
        lbl.setFont(_make_font(pt=9, bold=True))
        hdr.addWidget(lbl)
        hdr.addStretch()

        self.clearLogBtn = QPushButton("Effacer")
        self.clearLogBtn.setMinimumHeight(26)
        self.clearLogBtn.setCursor(CURSOR_POINTING)
        self.clearLogBtn.setStyleSheet(ui_theme.secondary_button_qss())
        self._add_hover_icon(self.clearLogBtn, 'trash.svg', 'trash-x.svg', 13)
        self._clear_feedback = ui_theme.ButtonFeedbackController(self.clearLogBtn)
        hdr.addWidget(self.clearLogBtn)

        self.exportLogBtn = QPushButton("Exporter")
        self.exportLogBtn.setMinimumHeight(26)
        self.exportLogBtn.setCursor(CURSOR_POINTING)
        self.exportLogBtn.setStyleSheet(ui_theme.secondary_button_qss())
        self._add_hover_icon(self.exportLogBtn, 'file-export.svg', 'send.svg', 13)
        hdr.addWidget(self.exportLogBtn)

        lay.addLayout(hdr)

        self.textBrowser = QTextBrowser()
        self.textBrowser.setFont(_make_font(pt=8.5, family='Consolas'))
        self.textBrowser.setOpenExternalLinks(False)
        self.textBrowser.setOpenLinks(False)
        lay.addWidget(self.textBrowser, 1)

        _ThemeColors._widget_palette_ref = self.textBrowser
        ui_theme.set_palette_ref(self.textBrowser)

        self._build_progress(lay)

        return widget

    # ---- Pied de dialogue / Barre de commandes ----
    def _build_footer(self):
        lay = QHBoxLayout()
        lay.setSpacing(8)

        self.helpButton = QPushButton("Guide")
        self.helpButton.setMinimumHeight(ui_theme.HAUTEUR_CONTROLE)
        self.helpButton.setCursor(CURSOR_POINTING)
        self.helpButton.setToolTip("Ouvrir la documentation complète ST-AIR")
        self.helpButton.setStyleSheet(ui_theme.secondary_button_qss())
        self._add_hover_icon(self.helpButton, 'help.svg', 'bulb.svg', 16)
        lay.addWidget(self.helpButton)

        self.diagButton = QPushButton("Pré-diagnostic")
        self.diagButton.setMinimumHeight(ui_theme.HAUTEUR_CONTROLE)
        self.diagButton.setCursor(CURSOR_POINTING)
        self.diagButton.setStyleSheet(ui_theme.secondary_button_qss())
        self._add_hover_icon(
            self.diagButton, 'stethoscope.svg', 'stethoscope.svg', 16
        )
        self.diagButton.setToolTip(
            "Scanner le projet et afficher un bilan complet\n"
            "des ressources détectées, manquantes et couches QGIS."
        )
        lay.addWidget(self.diagButton)

        self._chk_bdd_mode = _AppleToggle("GPKG")
        self._chk_bdd_mode.setToolTip(
            "Active le chargement des couches depuis PostgreSQL (connexion AUVERGNE).\n"
            "Coche cette option si tu es connecté au réseau interne BE.\n"
            "Décoche pour utiliser le mode GeoPackage livrable (défaut)."
        )
        self._chk_bdd_mode.setChecked(False)
        self._detect_bdd_connection()
        self._chk_bdd_mode.setVisible(False)
        self._chk_bdd_mode.toggled.connect(lambda _: self._on_bdd_mode_toggled())
        lay.addWidget(self._chk_bdd_mode)

        self._exportBtn = QPushButton("Extraire BDD (GPKG)")
        self._exportBtn.setMinimumHeight(ui_theme.HAUTEUR_CONTROLE)
        self._exportBtn.setCursor(CURSOR_POINTING)
        self._exportBtn.setToolTip(
            "Bureau d'études : extraire un paquet GeoPackage (.zip) depuis PostgreSQL AUVERGNE (Ctrl+Shift+B)"
        )
        self._exportBtn.setStyleSheet(ui_theme.secondary_button_qss())
        self._add_hover_icon(
            self._exportBtn, 'package.svg', 'package-export.svg', 16
        )
        self._exportBtn.setVisible(False)
        lay.addWidget(self._exportBtn)

        self._exportProgress = QProgressBar()
        self._exportProgress.setFixedWidth(120)
        self._exportProgress.setFixedHeight(18)
        self._exportProgress.setTextVisible(False)
        self._exportProgress.setRange(0, 100)
        self._exportProgress.setValue(0)
        self._exportProgress.setStyleSheet(_progress_bar_style())
        self._exportProgress.setVisible(False)
        lay.addWidget(self._exportProgress)

        lay.addStretch()

        self.cancelBtn = QPushButton("  Annuler l'analyse")
        self.cancelBtn.setMinimumHeight(ui_theme.HAUTEUR_CONTROLE)
        self.cancelBtn.setStyleSheet(_btn_cancel_style())
        self.cancelBtn.setCursor(CURSOR_POINTING)
        self.cancelBtn.setVisible(False)
        self._add_hover_icon(
            self.cancelBtn, 'x.svg', 'x.svg', 16,
            fixed_color=lambda: QColor("#ffffff"),
        )
        lay.addWidget(self.cancelBtn)

        self.startBtn = QPushButton("  Démarrer l'analyse")
        self.startBtn.setMinimumHeight(ui_theme.HAUTEUR_CONTROLE)
        self.startBtn.setStyleSheet(_btn_start_style())
        self.startBtn.setCursor(CURSOR_POINTING)
        self.startBtn.setEnabled(False)
        self.startBtn.setMinimumWidth(195)
        self.startBtn.setToolTip("Lancer les modules sélectionnés (Ctrl+Entrée)")
        self._add_hover_icon(
            self.startBtn, 'player-play.svg', 'player-play.svg', 16,
            fixed_color=lambda: (
                QColor("#ffffff")
                if self.startBtn.isEnabled()
                else ui_theme.tokens()["tertiaire"]
            ),
        )
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
    #  CONNEXIONS DES SIGNAUX ET INTERACTIONS
    # ==================================================================

    def _connect_signals(self):
        self.browseProjectBtn.clicked.connect(self._browse_project)
        self.refreshBtn.clicked.connect(self._refresh_detection)
        self.browseExportBtn.clicked.connect(self._browse_export)
        self.projectPathEdit.textChanged.connect(self._on_project_path_changed)
        self.gpkgPathEdit.path_dropped.connect(self._on_gpkg_dropped)
        self.selectAllBtn.clicked.connect(self._select_all)
        self.deselectAllBtn.clicked.connect(self._deselect_all)
        self.clearLogBtn.clicked.connect(self._on_clear_log)
        self.exportLogBtn.clicked.connect(self._export_log)
        self.startBtn.clicked.connect(self._on_start_clicked)
        self.cancelBtn.clicked.connect(self._on_cancel_clicked)
        self.loadGpkgBtn.clicked.connect(self._on_load_gpkg_clicked)
        self.helpButton.clicked.connect(lambda: self.help_requested.emit())
        self.diagButton.clicked.connect(self._run_diagnostic)
        self._exportBtn.clicked.connect(self._on_export_clicked)
        self._reopenReportBtn.clicked.connect(self._on_reopen_last_report)
        self._settingsBtn.clicked.connect(self._open_diag_settings)
        self.textBrowser.anchorClicked.connect(self._on_link_clicked)
        self._chk_comac_drawings.toggled.connect(lambda _: self._save_ui_state())
        self._chk_data_dictionary.toggled.connect(lambda _: self._save_ui_state())
        self.comboInfraPtPot.layerChanged.connect(lambda _: self._refresh_readiness())
        self.comboEtudeCapFt.layerChanged.connect(lambda _: self._refresh_readiness())
        self.comboEtudeComac.layerChanged.connect(lambda _: self._refresh_readiness())
        self.exportPathEdit.textChanged.connect(lambda _: self._refresh_readiness())
        self._sro_value.textChanged.connect(lambda _: self._refresh_readiness())
        self.layersUnlockBtn.clicked.connect(self._on_unlock_layers)

    def _on_clear_log(self):
        self.textBrowser.clear()
        if self._clear_feedback is not None:
            self._clear_feedback.flash("Effacé")

    def _open_diag_settings(self):
        try:
            from .diag_settings_dialog import DiagSettingsDialog
            dlg = DiagSettingsDialog(self)
            dlg.exec()
        except Exception:
            logger.exception("diag_settings_dialog_failed")

    def _on_reopen_last_report(self):
        if self._last_report_path and os.path.isfile(self._last_report_path):
            os.startfile(self._last_report_path)
        else:
            export_dir = self.get_export_dir()
            if export_dir and os.path.isdir(export_dir):
                os.startfile(export_dir)

    def _on_parcours_step_clicked(self, step_key):
        if step_key == "projet":
            p = self.projectPathEdit.text().strip()
            if p and os.path.isdir(p):
                os.startfile(p)
            else:
                self._browse_project()
        elif step_key == "referentiel":
            if not self._sro_value.text().strip():
                self._sro_value.setFocus()
            elif not self._gpkg_caches and not self.is_project_mode:
                self._on_load_gpkg_clicked()
            else:
                self._sro_value.setFocus()
        elif step_key == "controle":
            if self.startBtn.isEnabled() and not self._is_running:
                self._on_start_clicked()
            elif not self._is_running and os.path.isdir(self.projectPathEdit.text() or ''):
                self._run_diagnostic()
        elif step_key == "rapport":
            self._on_reopen_last_report()

    def _on_resource_chip_clicked(self, key):
        target = self._resource_paths.get(key, '')
        if target and os.path.exists(target):
            os.startfile(target)
        elif key == "GPKG":
            self._on_load_gpkg_clicked()
        elif not self.projectPathEdit.text().strip():
            self._browse_project()

    def _on_gpkg_dropped(self, path):
        if path and os.path.isfile(path):
            self.gpkg_delivery_requested.emit(path)

    # Support Glisser-Deposer au niveau du dialogue entier
    def dragEnterEvent(self, event):  # noqa: N802
        if event.mimeData().hasUrls():
            for url in event.mimeData().urls():
                if url.isLocalFile():
                    local = url.toLocalFile()
                    if os.path.isdir(local) or local.lower().endswith(('.zip', '.gpkg')):
                        event.acceptProposedAction()
                        return
        event.ignore()

    def dropEvent(self, event):  # noqa: N802
        if not event.mimeData().hasUrls():
            event.ignore()
            return
        for url in event.mimeData().urls():
            if not url.isLocalFile():
                continue
            local = url.toLocalFile()
            if os.path.isdir(local):
                event.acceptProposedAction()
                self.projectPathEdit.setText(local)
                return
            if os.path.isfile(local) and local.lower().endswith(('.zip', '.gpkg')):
                event.acceptProposedAction()
                self.gpkgPathEdit.setText(local)
                self.gpkg_delivery_requested.emit(local)
                return
        event.ignore()

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
    #  DETECTION DU PROJET ET PASTILLES DE RESSOURCES
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
        """Relance la detection sur le dossier projet courant."""
        path = self.projectPathEdit.text()
        if path and os.path.isdir(path):
            self._run_detection(path)
            self._log_info("Detection resynchronisee.")

    def _on_sro_text_changed(self, text):
        """Valide le code SRO a la volee et met a jour la pastille et le medaillon."""
        text = text.strip()
        if not text:
            self._sro_status.setText("[--] Saisissez le SRO")
            self._sro_status.setStyleSheet(ui_theme.chip_qss("warning"))
            self._sro_value.setStyleSheet(ui_theme.input_qss())
            if self._sro_medallion is not None:
                self._sro_medallion.set_state("warning", "database.svg")
            self._detection.sro = ''
            self._sro_source = ''
            self._validate_start()
            return
        normalized = text.replace('-', '/').replace('_', '/')
        if _SRO_REGEX.match(normalized):
            parts = normalized.split('/')
            sro = f'{parts[0]}/{parts[1]}/{parts[2]}/{parts[3]}'
            self._detection.sro = sro
            if self._sro_source not in ('detecte', 'gpkg'):
                self._sro_source = 'manuel'
            self._sro_status.setText(f"[OK] SRO {self._sro_source}")
            self._sro_status.setStyleSheet(ui_theme.chip_qss("ok"))
            self._sro_value.setStyleSheet(ui_theme.input_qss(valid=True))
            if self._sro_medallion is not None:
                ic = "package.svg" if self._sro_source == 'gpkg' else "database.svg"
                self._sro_medallion.set_state("ok", ic)
        else:
            self._sro_status.setText("[--] Format invalide")
            self._sro_status.setStyleSheet(ui_theme.chip_qss("warning"))
            self._sro_value.setStyleSheet(ui_theme.input_qss(valid=False))
            if self._sro_medallion is not None:
                self._sro_medallion.set_state("warning", "database.svg")
            self._detection.sro = ''
        self._validate_start()

    def _on_project_path_changed(self, path):
        if not path or not os.path.isdir(path):
            self._reset_detection()
            return
        self._run_detection(path)

    def _update_resource_chips(self):
        """Met a jour les pastilles de pieces du livrable (CAP FT, COMAC, C6, C7, C3A, GPKG)."""
        d = self._detection
        has_proj = bool(d and d.project_root and os.path.isdir(d.project_root))
        gpkg_ok = bool(self._gpkg_caches or (self.gpkgPathEdit.text() and os.path.isfile(self.gpkgPathEdit.text())))
        gpkg_path = self.gpkgPathEdit.text().strip() if gpkg_ok else ''

        if not has_proj:
            self._resource_paths = {"GPKG": gpkg_path} if gpkg_ok else {}
            for key, chip in self._resource_chips.items():
                if key == "GPKG" and gpkg_ok:
                    chip.setStyleSheet(ui_theme.chip_qss("present"))
                    chip.setToolTip(f"GeoPackage chargé : {os.path.basename(gpkg_path)}")
                else:
                    chip.setStyleSheet(ui_theme.chip_qss("neutral"))
            return

        spec = {
            "CAP FT": (d.has_capft, d.capft_dir, True),
            "COMAC": (d.has_comac, d.comac_dir, True),
            "C6": (d.has_c6 or d.has_c6_annexe, d.c6_annexe_file or d.c6_dir, True),
            "C7": (d.has_c7, d.c7_file, False),
            "C3A": (d.has_c3a, d.c3a_file, False),
            "GPKG": (gpkg_ok, gpkg_path, False),
        }
        self._resource_paths = {}
        for key, (found, path, primary) in spec.items():
            chip = self._resource_chips.get(key)
            if chip is None:
                continue
            if found:
                self._resource_paths[key] = path
                chip.setStyleSheet(ui_theme.chip_qss("present"))
                chip.setToolTip(f"{key} détecté : {path}\n(Cliquer pour ouvrir)")
            elif primary:
                chip.setStyleSheet(ui_theme.chip_qss("warning"))
                chip.setToolTip(f"{key} non détecté dans ce dossier")
            else:
                chip.setStyleSheet(ui_theme.chip_qss("optional"))
                chip.setToolTip(f"{key} : optionnel / non fourni")

    def _run_detection(self, path):
        self._detection = detect_project(path)
        d = self._detection

        self._project_tag.setText(d.project_name)
        self._project_tag.setVisible(bool(d.project_name))
        self._subtitle.setText(f"Projet actif : {d.project_name}")

        if d.sro:
            self._sro_source = 'detecte'
            self._sro_value.setText(d.sro)
            self._sro_status.setText("[OK] SRO detecte")
            self._sro_status.setStyleSheet(ui_theme.chip_qss("ok"))
            self._sro_value.setStyleSheet(ui_theme.input_qss(valid=True))
            if self._sro_medallion is not None:
                self._sro_medallion.set_state("ok", "database.svg")
        elif not self._locked_to_gpkg:
            self._sro_value.clear()
            self._sro_source = ''
            self._sro_status.setText("[--] Saisissez le SRO manuellement")
            self._sro_status.setStyleSheet(ui_theme.chip_qss("warning"))
            self._sro_value.setStyleSheet(ui_theme.input_qss())
            if self._sro_medallion is not None:
                self._sro_medallion.set_state("warning", "database.svg")

        if not self.exportPathEdit.text():
            self.exportPathEdit.setText(d.export_dir)

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
        self._det_summary.setText("  |  ".join(lines[:2]))
        self._det_summary.setToolTip("\n".join(lines))
        self._det_summary.setVisible(bool(lines))
        self._update_resource_chips()

        module_map = {
            'capft':     (d.has_capft, d.capft_dir,   'CAP FT'),
            'comac':     (d.has_comac, d.comac_dir,   'COMAC'),
            'c6bd':      (d.has_c6,    d.c6_dir,      'C6 (via CAP FT)'),
            'police_c6': (d.has_c6,    d.c6_dir,      'C6 (via CAP FT)'),
            'c6c3a':     ((d.has_c6 or d.has_c6_annexe) and (d.has_c7 or d.has_c3a),
                          d.c6_annexe_file or d.c6_dir, 'C6 annexe'),
            'gespot_c6': (d.has_c6,
                          '(via GPKG/BDD)', 'GESPOT'),
        }
        settings = QgsSettings()
        for key, row in self._module_rows.items():
            found, mp, res_label = module_map.get(key, (False, '', ''))
            diag = '' if found else d.get_diagnostic(res_label)
            row.set_detected(found, mp, diagnostic=diag)
            if found:
                saved = settings.value(
                    f"PoleAerien/dialog_v2/module_checked/{key}",
                    True, type=bool
                )
                if not saved:
                    row.checkbox.setChecked(False)
                    logger.debug(
                        "module_restored key=%s checked=False "
                        "from saved preference", key
                    )

        self._update_mod_count()
        self._validate_start()

        self._log_info(f"Projet : {d.project_name}")
        if d.sro:
            self._log_ok(f"  SRO : {d.sro}")
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

        for label, hint in d.diagnostics:
            if label == 'Doublon potentiel':
                self._log_warn(f"  {label} : {hint}")

    def _reset_detection(self):
        self._detection = DetectionResult()
        self._project_tag.setVisible(False)
        self._subtitle.setText(
            "Contrôle qualité des livrables sous-traitants FTTH aérien"
        )
        self._det_summary.setVisible(False)
        if not self._locked_to_gpkg:
            self._sro_value.clear()
            self._sro_status.setText("SRO à renseigner")
            self._sro_status.setStyleSheet(ui_theme.chip_qss("neutral"))
            if self._sro_medallion is not None:
                self._sro_medallion.set_state("neutral", "database.svg")
        for row in self._module_rows.values():
            row.set_detected(False)
        self._update_resource_chips()
        self._update_mod_count()
        self._validate_start()

    # ==================================================================
    #  SELECTION DES MODULES ET ETAT DE PREPARATION (READINESS)
    # ==================================================================

    def _on_module_toggled(self, key, checked):
        settings = QgsSettings()
        settings.setValue(
            f"PoleAerien/dialog_v2/module_checked/{key}",
            checked
        )
        logger.debug(
            "module_toggled key=%s checked=%s saved", key, checked
        )
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
        total = len(self._module_rows)
        self._mod_count.setText(f"{sel}/{total} sélectionné(s)")
        self._mod_count.setStyleSheet(
            ui_theme.badge_qss("accent" if sel > 0 else "neutral")
        )

    def _refresh_readiness(self):
        """Recalcule l'etat de preparation et met a jour tous les temoins visuels."""
        state = self._compute_readiness()
        self.startBtn.setEnabled(state['ready'] and not self._is_running)
        for behavior in self._hover_icon_behaviors:
            if behavior._button is self.startBtn:
                behavior.refresh()
        self._update_readiness_label(state)
        self._update_export_status(state)
        self._update_layers_status(state)
        self._update_parcours_visuel(state)

    def _compute_readiness(self):
        """Modele central de preparation retournant les causes de blocage."""
        reasons = []
        proj_path = self.projectPathEdit.text() or ''
        has_proj = bool(proj_path) and os.path.isdir(proj_path)
        if not has_proj:
            reasons.append("Aucun dossier projet valide")

        has_mod = any(r.is_selected for r in self._module_rows.values())
        if not has_mod:
            reasons.append("Aucun module selectionne")

        sro_ok = bool(self._sro_value.text().strip() or self._detection.sro)
        if not sro_ok:
            reasons.append("SRO requis (saisir ou detecter via le nom de dossier)")

        export_dir = self.exportPathEdit.text() or self._detection.export_dir
        export_ok = bool(export_dir) and os.path.isdir(export_dir)
        if not export_ok and export_dir:
            reasons.append("Dossier export introuvable")

        is_bdd = self.is_project_mode
        layers_ok = True
        missing_layers = []
        if not is_bdd:
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
        mode_label = "BDD" if is_bdd else "Couches QGIS"

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

    def _update_parcours_visuel(self, state):
        """Synchronise la barre ParcoursVisuel avec l'etat reel du controleur."""
        if self._parcours is None:
            return
        d = self._detection
        # Jalon 1 : Dossier projet
        if state['has_project']:
            n_det = len(d.detected_modules()) if d else 0
            self._parcours.set_step(
                "projet",
                "termine",
                f"{d.project_name or 'Détecté'} ({n_det} mod.)",
                listening=False,
                tooltip="Cliquer pour ouvrir le dossier projet dans l'Explorateur",
            )
        else:
            self._parcours.set_step(
                "projet",
                "attente",
                "À choisir",
                listening=True,
                tooltip="Cliquer pour sélectionner un dossier projet",
            )

        # Jalon 2 : SRO et Couches
        if state['sro_ok'] and state['layers_ok']:
            ref_lbl = (
                "Mode BDD actif"
                if self.is_project_mode
                else ("GPKG lié · 3/3" if self._locked_to_gpkg else "SRO · 3/3 couches")
            )
            self._parcours.set_step(
                "referentiel",
                "termine",
                ref_lbl,
                listening=False,
                tooltip="Référentiel SRO et couches prêts (cliquer pour modifier)",
            )
        elif state['has_project']:
            missing_txt = "SRO manquant" if not state['sro_ok'] else f"{3 - len(state['missing_layers'])}/3 couches"
            self._parcours.set_step(
                "referentiel",
                "attention",
                missing_txt,
                listening=True,
                tooltip="Cliquer pour compléter le SRO ou charger un livrable GPKG",
            )
        else:
            self._parcours.set_step(
                "referentiel",
                "attente",
                "En attente",
                listening=False,
                tooltip="Charger un livrable GeoPackage ou saisir le SRO",
            )

        # Jalon 3 : Controle par lot
        if self._is_running:
            step_lbl = self._progress_step.text() if (self._progress_step and self._progress_step.isVisible()) else "Analyse en cours…"
            self._parcours.set_step(
                "controle",
                "actif",
                step_lbl,
                listening=False,
                tooltip="Analyse par lot en cours d'exécution",
            )
        elif state['ready']:
            self._parcours.set_step(
                "controle",
                "attente",
                f"Prêt ({state['selected_count']}/{state['total_count']})",
                listening=True,
                tooltip="Cliquer pour démarrer l'analyse par lot",
            )
        else:
            self._parcours.set_step(
                "controle",
                "attente",
                "En attente",
                listening=False,
                tooltip="Sélectionnez un projet et les prérequis pour lancer",
            )

        # Jalon 4 : Rapport Excel
        if self._last_report_path and os.path.isfile(self._last_report_path):
            self._parcours.set_step(
                "rapport",
                "termine",
                "Disponible · Ouvrir",
                listening=False,
                tooltip=f"Cliquer pour ouvrir {os.path.basename(self._last_report_path)}",
            )
        else:
            self._parcours.set_step(
                "rapport",
                "attente",
                "En attente",
                listening=False,
                tooltip="",
            )

    def _update_readiness_label(self, state):
        """Met a jour le bandeau de synthese de preparation."""
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
        """Met a jour la pastille d'etat du dossier d'export (UI-006)."""
        if not state['has_project']:
            self._export_status.setVisible(False)
            return
        export_dir = state.get('export_dir', '')
        if not export_dir:
            self._export_status.setText("Export : racine projet (par defaut)")
            self._export_status.setStyleSheet(ui_theme.chip_qss("neutral"))
            _set_foreground(self._export_status, _ThemeColors.dim())
        elif not os.path.isdir(export_dir):
            self._export_status.setText("Export : dossier introuvable")
            self._export_status.setStyleSheet(ui_theme.chip_qss("error"))
            _set_foreground(self._export_status, _ThemeColors.error())
        else:
            try:
                test_path = os.path.join(export_dir, '.poleaerien_write_test')
                with open(test_path, 'w', encoding='utf-8') as _f:
                    pass
                os.remove(test_path)
                self._export_status.setText("Export : OK")
                self._export_status.setStyleSheet(ui_theme.chip_qss("ok"))
                _set_foreground(self._export_status, _ThemeColors.ok())
            except OSError:
                self._export_status.setText("Export : non inscriptible")
                self._export_status.setStyleSheet(ui_theme.chip_qss("error"))
                _set_foreground(self._export_status, _ThemeColors.error())
        self._export_status.setVisible(True)

    def _update_single_layer_badge(self, key, layer):
        badge = self._layer_badges.get(key)
        if badge is None:
            return
        if not layer or not layer.isValid():
            badge.setText("Non sélectionnée")
            badge.setStyleSheet(ui_theme.chip_qss("warning"))
            return
        fc = layer.featureCount()
        crs = layer.crs().authid() if layer.crs().isValid() else "?"
        if fc > 0:
            badge.setText(f"{fc:,} entités · {crs}".replace(",", " "))
            badge.setStyleSheet(ui_theme.chip_qss("present"))
        else:
            badge.setText(f"0 entité · {crs}")
            badge.setStyleSheet(ui_theme.chip_qss("warning"))

    def _update_layers_status(self, state):
        """Met a jour les pastilles par couche et le statut global des couches (UI-007)."""
        self._update_single_layer_badge("pot", self.comboInfraPtPot.currentLayer())
        self._update_single_layer_badge("cap", self.comboEtudeCapFt.currentLayer())
        self._update_single_layer_badge("com", self.comboEtudeComac.currentLayer())

        if self.is_project_mode:
            self._layers_status.setVisible(False)
            return
        if self._locked_to_gpkg:
            sro = self._gpkg_sro_label or '?'
            self._layers_status.setText(
                f"Couches liees au livrable GPKG (SRO {sro})"
            )
            _set_foreground(self._layers_status, _ThemeColors.ok())
            self._layers_status.setVisible(True)
            return
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

    # ==================================================================
    #  LIAISON AUTOMATIQUE AUX COUCHES GPKG
    # ==================================================================

    def _gpkg_combo_map(self):
        """Retourne la liste [(combo, nom_attendu), ...] pour les 3 couches."""
        return [
            (self.comboInfraPtPot, 'infra_pt_pot'),
            (self.comboEtudeCapFt, 'etude_cap_ft'),
            (self.comboEtudeComac, 'etude_comac'),
        ]

    def bind_gpkg_layers(self, sro_label=''):
        """Selectionne automatiquement les 3 couches du GPKG et verrouille les combos."""
        project = QgsProject.instance()
        bound = []
        missing = []
        for combo, name in self._gpkg_combo_map():
            matches = project.mapLayersByName(name)
            if matches:
                combo.setLayer(matches[-1])
                combo.setEnabled(False)
                bound.append(name)
            else:
                combo.setEnabled(False)
                missing.append(name)

        if not bound:
            for combo, _ in self._gpkg_combo_map():
                combo.setEnabled(True)
            self.log_message(
                "[GPKG] Aucune des 3 couches attendues (infra_pt_pot, "
                "etude_cap_ft, etude_comac) n'a ete trouvee dans le projet "
                "QGIS apres chargement. Selection manuelle requise.",
                'warning',
            )
            return

        self._locked_to_gpkg = True
        self._gpkg_sro_label = sro_label or ''
        self.layersUnlockBtn.setVisible(True)

        if sro_label:
            self._sro_source = 'gpkg'
            self._sro_value.setText(sro_label)
            self._sro_status.setText("[OK] SRO depuis GPKG")
            self._sro_status.setStyleSheet(ui_theme.chip_qss("ok"))
            self._sro_value.setStyleSheet(ui_theme.input_qss(valid=True))
            if self._sro_medallion is not None:
                self._sro_medallion.set_state("ok", "package.svg")

        if missing:
            self.log_message(
                f"[GPKG] Couches liees : {', '.join(bound)}. "
                f"Manquante(s) dans le GPKG : {', '.join(missing)}.",
                'warning',
            )
        else:
            self.log_message(
                f"[GPKG] 3 couches liees au livrable : {', '.join(bound)}.",
                'success',
            )
        self._update_resource_chips()
        self._refresh_readiness()

    def _on_unlock_layers(self):
        """Deverrouille les combos pour selection manuelle."""
        self._locked_to_gpkg = False
        self._gpkg_sro_label = ''
        for combo, _ in self._gpkg_combo_map():
            combo.setEnabled(True)
        self.layersUnlockBtn.setVisible(False)
        self.log_message(
            "[GPKG] Mode manuel : les combos sont modifiables.",
            'info',
        )
        self._refresh_readiness()

    def _validate_start(self):
        """Delegue au modele central de preparation."""
        self._refresh_readiness()

    def selected_modules(self):
        order = ['capft', 'comac', 'c6bd', 'police_c6', 'c6c3a', 'gespot_c6']
        return [
            k for k in order
            if k in self._module_rows and self._module_rows[k].is_selected
        ]

    # ==================================================================
    #  ETAT D'EXECUTION
    # ==================================================================

    def _on_start_clicked(self):
        mods = self.selected_modules()
        if mods:
            self.start_requested.emit(mods)

    def _on_cancel_clicked(self):
        self.cancel_requested.emit()

    def _on_load_gpkg_clicked(self):
        """Ouvre un selecteur de fichier pour charger un livrable GPKG."""
        d = self._detection
        start_dir = (
            d.project_root if d and d.project_root else os.path.expanduser('~')
        )
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Choisir un livrable GeoPackage",
            start_dir,
            "Livrables GeoPackage (*.zip *.gpkg);;Tous fichiers (*.*)",
        )
        if not path:
            return
        self.gpkgPathEdit.setText(path)
        self.gpkg_delivery_requested.emit(path)

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
        """Affiche l'etape en cours a cote de la barre de progression et dans le parcours."""
        if self._progress_step is None:
            return
        if text:
            self._progress_step.setText(text)
            self._progress_step.setVisible(True)
        else:
            self._progress_step.setVisible(False)
        if self._is_running and self._parcours is not None:
            self._parcours.set_step(
                "controle",
                "actif",
                text or "Analyse en cours…",
                listening=False,
            )

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
    #  ANIMATEUR DU PRE-DIAGNOSTIC
    # ==================================================================

    _DIAG_SPEED_LINE = 45
    _DIAG_SPEED_CHAR = 12
    _DIAG_PAUSE_SECTION = 250
    _DIAG_PAUSE_REVEAL = 400

    def _diag_animate(self, lines):
        """Lance l'animation typewriter sur une liste de lignes de diagnostic."""
        if self._diag_timer is not None and self._diag_timer.isActive():
            self._diag_timer.stop()
        self._diag_queue = list(lines)
        self._diag_char_buf = ''
        self._diag_char_idx = 0
        self._diag_phase = 'idle'
        self._diag_timer = QTimer(self)
        self._diag_timer.timeout.connect(self._diag_tick)
        self.diagButton.setEnabled(False)
        self._diag_timer.start(self._DIAG_SPEED_LINE)

    def _diag_tick(self):
        if self._diag_phase == 'typing':
            self._diag_type_step()
            return
        if self._diag_phase == 'reveal_pause':
            self._diag_phase = 'reveal'
            self._diag_timer.setInterval(self._DIAG_SPEED_LINE)
            return
        if self._diag_phase == 'reveal':
            self._diag_do_reveal()
            self._diag_phase = 'idle'
            return

        if not self._diag_queue:
            self._diag_timer.stop()
            self.diagButton.setEnabled(True)
            return

        entry = self._diag_queue.pop(0)
        kind = entry[0]

        if kind == 'section':
            self._diag_timer.setInterval(self._DIAG_PAUSE_SECTION)
            self._log_html(entry[1])
            return

        if kind == 'line':
            html = entry[1]
            self._diag_start_typing(html)
            return

        if kind == 'correction':
            probe_html, final_html = entry[1], entry[2]
            self._diag_reveal_final = final_html
            self._diag_start_typing(probe_html)
            self._diag_phase_after_typing = 'reveal_pause'
            return

    def _diag_start_typing(self, html):
        import re as _re
        self._diag_full_html = html
        self._diag_plain = _re.sub(r'<[^>]+>', '', html)
        self._diag_char_idx = 0
        self._diag_phase = 'typing'
        self._diag_phase_after_typing = 'idle'
        self._diag_timer.setInterval(self._DIAG_SPEED_CHAR)
        self._log_html('')

    def _diag_type_step(self):
        idx = self._diag_char_idx
        plain = self._diag_plain
        if idx >= len(plain):
            cursor = self.textBrowser.textCursor()
            cursor.movePosition(QTextCursor.MoveOperation.End)
            cursor.movePosition(
                QTextCursor.MoveOperation.StartOfBlock,
                QTextCursor.MoveMode.KeepAnchor,
            )
            cursor.removeSelectedText()
            cursor.insertHtml(self._diag_full_html)
            self.textBrowser.setTextCursor(cursor)
            self._diag_phase = self._diag_phase_after_typing
            if self._diag_phase == 'reveal_pause':
                self._diag_timer.setInterval(self._DIAG_PAUSE_REVEAL)
            else:
                self._diag_timer.setInterval(self._DIAG_SPEED_LINE)
            return
        self._diag_char_idx += 1
        cursor = self.textBrowser.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        cursor.insertText(plain[idx])
        self.textBrowser.setTextCursor(cursor)

    def _diag_do_reveal(self):
        cursor = self.textBrowser.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        cursor.movePosition(
            QTextCursor.MoveOperation.StartOfBlock,
            QTextCursor.MoveMode.KeepAnchor,
        )
        cursor.removeSelectedText()
        cursor.insertHtml(self._diag_reveal_final)
        self.textBrowser.setTextCursor(cursor)

    # ==================================================================
    #  PRE-DIAGNOSTIC : COLLECTE DES LIGNES
    # ==================================================================

    def _run_diagnostic(self):
        """Pre-diagnostic intelligent du livrable et de l'environnement."""
        self.textBrowser.clear()
        lines = []

        def _sec(html):
            lines.append(('section', html))

        def _line(html):
            lines.append(('line', html))

        def _correct(probe_html, final_html):
            lines.append(('correction', probe_html, final_html))

        def _info(msg):
            _line(f'<span style="color:{_c(_ThemeColors.info())}">{msg}</span>')

        def _ok(msg):
            _line(f'<span style="color:{_c(_ThemeColors.ok())}">{msg}</span>')

        def _warn(msg):
            probe = f'<span style="color:{_c(_ThemeColors.dim())}">  Verification...</span>'
            final = f'<span style="color:{_c(_ThemeColors.warn())}">{msg}</span>'
            _correct(probe, final)

        def _err(msg):
            probe = f'<span style="color:{_c(_ThemeColors.dim())}">  Analyse...</span>'
            final = f'<span style="color:{_c(_ThemeColors.error())}"><b>{msg}</b></span>'
            _correct(probe, final)

        def _dim(msg):
            _line(f'<span style="color:{_c(_ThemeColors.dim())}">{msg}</span>')

        path = self.projectPathEdit.text()
        if not path or not os.path.isdir(path):
            self._log_err("Aucun dossier projet selectionne.")
            return

        d = self._detection
        if not d.project_name:
            self._run_detection(path)
            d = self._detection

        analysis = analyse_livrable(d)

        _info(f"<b>=== PRE-DIAGNOSTIC : {d.project_name or os.path.basename(path)} ===</b>")
        if d.sro:
            _ok(f"  SRO : {d.sro}")
        else:
            _warn("  SRO : non reconnu (format attendu : XXXXX-YYY-ZZZ-NNNNN)")

        _sec(f'<span style="color:{_c(_ThemeColors.info())}"><b>--- Contenu du livrable ---</b></span>')

        if d.has_capft:
            cap = analysis.get('capft', {})
            parts = []
            if cap.get('cmd'):
                parts.append(f"{cap['cmd']} CMD")
            if cap.get('etudes'):
                parts.append(f"{cap['etudes']} etude(s)")
            if cap.get('excels'):
                parts.append(f"{cap['excels']} fichiers Excel")
            _ok(f"  CAP FT ........... {' | '.join(parts) if parts else 'detecte'}")
        else:
            _warn("  CAP FT ........... Non livre")

        if d.has_comac:
            com = analysis.get('comac', {})
            parts = []
            if com.get('sous_dossiers'):
                parts.append(f"{com['sous_dossiers']} sous-dossier(s)")
            if com.get('excels'):
                parts.append(f"{com['excels']} fichiers Excel")
            _ok(f"  COMAC ............ {' | '.join(parts) if parts else 'detecte'}")
        else:
            _warn("  COMAC ............ Non livre")

        if d.has_c7:
            _ok(f"  C7 ............... {analysis.get('c7', {}).get('fichier', '?')}")
        else:
            _dim("  C7 ............... Non livre")
        if d.has_c3a:
            _ok(f"  C3A .............. {analysis.get('c3a', {}).get('fichier', '?')}")
        else:
            _dim("  C3A .............. Non livre")

        _sec(f'<span style="color:{_c(_ThemeColors.info())}"><b>--- Environnement QGIS ---</b></span>')

        _dim("  Mode : Couches QGIS (ST-AIR, 100% local)")
        pg_ok = False

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
                    _ok(f"  {key} : {lyr.name()} ({fc:,} entites, {crs})")
                else:
                    _warn(f"  {key} : {lyr.name()} (VIDE - 0 entite)")
            else:
                _warn(f"  {key} : non selectionnee ({desc})")

        if len(crs_set) > 1:
            _warn(f"  CRS : heterogenes ({', '.join(crs_set)})")

        export_dir = self.exportPathEdit.text() or d.export_dir
        if export_dir and os.path.isdir(export_dir):
            try:
                test_path = os.path.join(export_dir, '.poleaerien_diag_test')
                with open(test_path, 'w') as _f:
                    pass
                os.remove(test_path)
                _ok(f"  Export : {export_dir}")
            except OSError:
                _err(f"  Export : {export_dir} (NON inscriptible)")
        elif export_dir:
            _err(f"  Export : {export_dir} (dossier introuvable)")
        else:
            _dim("  Export : racine projet (par defaut)")

        _sec(f'<span style="color:{_c(_ThemeColors.info())}"><b>--- Qualite donnees ---</b></span>')

        from .preflight_checks import (
            estimate_analysis_time, format_check_log,
            run_cross_validation, run_data_quality_checks,
            run_gpkg_intelligence,
        )
        from .qgis_utils import detect_etude_field as _detect_etude_field

        _lyr_pot_dq = self.comboInfraPtPot.currentLayer()
        _lyr_cap_dq = self.comboEtudeCapFt.currentLayer()
        _lyr_com_dq = self.comboEtudeComac.currentLayer()

        _mods_dq = self.selected_modules() or [
            'capft', 'comac', 'c6bd', 'police_c6', 'c6c3a'
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

        _dq_checks = []
        try:
            _dq_checks = run_data_quality_checks(
                _lyr_pot_dq, _lyr_cap_dq, _lyr_com_dq,
                _mods_dq,
                auto_field_fn=_auto_field_diag,
                comac_dir=d.comac_dir if d.has_comac else '',
                capft_dir=d.capft_dir if d.has_capft else '',
            )
            if not _dq_checks:
                _dim("  (aucun check applicable)")
            else:
                _n_block = 0
                _n_warn_c = 0
                for _chk in _dq_checks:
                    _msg, _lvl = format_check_log(_chk)
                    if _lvl == 'error':
                        _err(_msg)
                        _n_block += 1
                    elif _lvl == 'warning':
                        _warn(_msg)
                        _n_warn_c += 1
                    else:
                        _ok(_msg)
                if _n_block:
                    _err(f"  => {_n_block} probleme(s) BLOQUANT(S)")
                elif _n_warn_c:
                    _warn(f"  => {_n_warn_c} avertissement(s)")
                else:
                    _ok("  => Qualite donnees : OK")
        except Exception as _dq_exc:
            _warn(f"  Qualite donnees: erreur ({_dq_exc})")

        _sec(f'<span style="color:{_c(_ThemeColors.info())}"><b>--- Modules ---</b></span>')

        detected = d.detected_modules()
        module_defs = [
            ('capft',     'CAP_FT',              self._diag_prereq_capft),
            ('comac',     'COMAC',               self._diag_prereq_comac),
            ('c6bd',      'C6 vs Donnees Projet', self._diag_prereq_c6bd),
            ('police_c6', 'POLICE C6',           self._diag_prereq_c6),
            ('c6c3a',     'C6-C3A-C7-BD',        self._diag_prereq_c6c3a),
            ('gespot_c6', 'GESPOT vs C6',         self._diag_prereq_gespot_c6),
        ]

        n_ready = 0
        for key, name, prereq_fn in module_defs:
            if key in detected:
                detail = prereq_fn(analysis)
                _ok(f"  PRET    {name:.<16s} {detail}")
                n_ready += 1
            else:
                raison = prereq_fn(analysis, missing=True)
                _warn(f"  BLOQUE  {name:.<16s} {raison}")

        gpkg = self._gpkg_caches
        gi_checks = []
        xv_checks = []
        if gpkg and gpkg.cables:
            _sec(f'<span style="color:{_c(_ThemeColors.info())}"><b>--- Intelligence GPKG ---</b></span>')
            gi_checks = run_gpkg_intelligence(
                gpkg.cables, gpkg.bpe_list, gpkg.attaches,
            )
            for chk in gi_checks:
                _msg, _lvl = format_check_log(chk)
                if _lvl == 'warning':
                    _warn(_msg)
                else:
                    _ok(_msg)

        _sec(f'<span style="color:{_c(_ThemeColors.info())}"><b>--- Validation croisee ---</b></span>')
        try:
            xv_checks = run_cross_validation(
                _lyr_pot_dq, _lyr_cap_dq, _lyr_com_dq,
            )
            if xv_checks:
                for chk in xv_checks:
                    _msg, _lvl = format_check_log(chk)
                    if _lvl == 'error':
                        _err(_msg)
                    elif _lvl == 'warning':
                        _warn(_msg)
                    else:
                        _ok(_msg)
            else:
                _dim("  (couches insuffisantes pour la validation croisee)")
        except Exception as xv_exc:
            _warn(f"  Validation croisee: erreur ({xv_exc})")

        recommandations = self._build_recommandations(d, detected, pg_ok)
        if recommandations:
            _sec(f'<span style="color:{_c(_ThemeColors.info())}"><b>--- Recommandations ---</b></span>')
            for reco in recommandations:
                _dim(f"  > {reco}")

        total = len(module_defs)

        _pot_c = _lyr_pot_dq.featureCount() if _lyr_pot_dq else 0
        _cap_c = _lyr_cap_dq.featureCount() if _lyr_cap_dq else 0
        _com_c = _lyr_com_dq.featureCount() if _lyr_com_dq else 0
        _cab_c = len(gpkg.cables) if gpkg else 0
        _sel_mods = self.selected_modules() or []
        if _sel_mods and _pot_c > 0:
            est = estimate_analysis_time(_pot_c, _cap_c, _com_c, _cab_c, _sel_mods)
            if est < 60:
                _dim(f"  Duree estimee : ~{est:.0f}s")
            else:
                _dim(f"  Duree estimee : ~{est / 60:.1f}min")

        n_total_checks = 0
        n_ok_checks = 0
        for chk_list in [_dq_checks or []]:
            for chk in chk_list:
                n_total_checks += 1
                if chk['level'] == 'OK':
                    n_ok_checks += 1
        if gpkg and gpkg.cables:
            for chk in gi_checks:
                n_total_checks += 1
                if chk['level'] == 'OK':
                    n_ok_checks += 1
        for chk in xv_checks:
            n_total_checks += 1
            if chk['level'] == 'OK':
                n_ok_checks += 1

        health_pct = (n_ok_checks / n_total_checks * 100) if n_total_checks else 0

        if n_ready == total and health_pct >= 90:
            _ok(
                f"<b>Verdict : {n_ready}/{total} modules prets | "
                f"Sante donnees : {health_pct:.0f}% | Livrable complet.</b>"
            )
        elif n_ready > 0:
            _warn(
                f"<b>Verdict : {n_ready}/{total} modules prets | "
                f"Sante donnees : {health_pct:.0f}% | Livrable partiel.</b>"
            )
        else:
            _err(
                f"<b>Verdict : 0/{total} modules prets | "
                f"Sante donnees : {health_pct:.0f}%</b>"
            )

        self._diag_animate(lines)

    # --- Helpers de diagnostic ---

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

    def _diag_prereq_c6bd(self, analysis, missing=False):
        if missing:
            return "dossier CAP FT requis (annexes C6)"
        cap = analysis.get('capft', {})
        n = cap.get('etudes', 0)
        e = cap.get('excels', 0)
        return f"{n} etudes, {e} fichiers C6" if n else 'via CAP FT'

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
            return "couche data_gespot (GPKG/BDD) et dossier CAP FT requis"
        has_gespot_layer = False
        try:
            layers = QgsProject.instance().mapLayersByName('data_gespot')
            has_gespot_layer = bool(layers and layers[0].isValid())
        except Exception:
            pass
        src = 'couche data_gespot' if has_gespot_layer else 'couche data_gespot manquante'
        return f"GESPOT ({src}) + CAP FT"

    def _build_recommandations(self, d, detected, pg_ok):
        recos = []
        if 'capft' not in detected:
            recos.append(
                'CAP FT / C6 / Police : creer le dossier CAP FT '
                '(ou ETUDE_CAPFT) avec les CMD a l\'interieur')
        if 'comac' not in detected:
            recos.append(
                'COMAC : creer le dossier COMAC '
                '(ou ETUDE_COMAC) avec les sous-dossiers d\'etude')
        if 'c6c3a' not in detected and 'police_c6' in detected:
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

    def _detect_bdd_connection(self):
        """Detecte si une connexion PostgreSQL QGIS 'AUVERGNE' est configuree."""
        try:
            from .be_export_auth import read_qgis_pg_params
            params = read_qgis_pg_params()
            self._bdd_connected = bool(params and params.get('host') and params.get('user'))
        except Exception:
            self._bdd_connected = False

    def _on_bdd_mode_toggled(self):
        """Bascule entre le mode BDD PostgreSQL et le mode GPKG local."""
        if self._chk_bdd_mode is None:
            return
        is_bdd = self._chk_bdd_mode.isChecked()
        if is_bdd:
            self._chk_bdd_mode.setText('BDD')
            self.log_message(
                "[BDD] Mode BDD active : les couches seront chargees "
                "depuis PostgreSQL (AUVERGNE).", 'info',
            )
        else:
            self._chk_bdd_mode.setText('GPKG')
            self.log_message(
                "[BDD] Mode GPKG active : utiliser le livrable GeoPackage.", 'info',
            )
        if hasattr(self, '_layers_grp'):
            self._layers_grp.setVisible(not is_bdd)
        self._refresh_readiness()

    # ==================================================================
    #  EXPORT GPKG BUREAU D'ETUDES
    # ==================================================================

    def _start_auth_probe(self):
        try:
            from .be_export_auth import AuthProbe
            if self._auth_probe is not None:
                try:
                    self._auth_probe.cancel()
                except Exception:
                    logger.exception("auth_probe_cancel_failed")
            self._auth_probe = AuthProbe(parent=self)
            self._auth_probe.authorized.connect(self._on_auth_result)
            self._auth_probe.prompt_credentials.connect(
                self._on_prompt_credentials,
            )
            self._auth_probe.start()
        except Exception:
            logger.exception("auth_probe_start_failed")

    def _on_auth_result(self, authorized):
        if not authorized:
            return
        try:
            self._bdd_connected = True
            if self._exportBtn is not None:
                self._exportBtn.setVisible(True)
            if self._chk_bdd_mode is not None:
                self._chk_bdd_mode.setChecked(True)
                self._chk_bdd_mode.setText('BDD')
                self._chk_bdd_mode.setVisible(True)
            if hasattr(self, '_layers_grp'):
                self._layers_grp.setVisible(False)
            self._refresh_readiness()
        except RuntimeError:
            return

    def _on_prompt_credentials(self):
        self._open_credentials_dialog()

    def _install_credentials_shortcut(self):
        try:
            from qgis.PyQt.QtGui import QKeySequence
            try:
                from qgis.PyQt.QtGui import QShortcut
            except ImportError:
                from qgis.PyQt.QtWidgets import QShortcut
            sc = QShortcut(QKeySequence("Ctrl+Shift+B"), self)
            sc.activated.connect(self._open_credentials_dialog)
        except Exception:
            logger.exception("shortcut_install_failed")

    def _open_credentials_dialog(self):
        try:
            from .be_credentials_dialog import CredentialsDialog
        except Exception:
            logger.exception("credentials_dialog_import_failed")
            return
        dlg = CredentialsDialog(parent=self)
        if dlg.exec() == DIALOG_ACCEPTED:
            self._start_auth_probe()

    def _on_export_clicked(self):
        """Declenche l'extraction du paquet GeoPackage depuis PostgreSQL."""
        try:
            from . import export_zip_gpkg
            export_zip_gpkg.run()
            tasks = export_zip_gpkg.__dict__.get('_stair_extract_tasks')
            if tasks:
                self._export_tasks = tasks
                self._exportBtn.setEnabled(False)
                self._exportProgress.setValue(0)
                self._exportProgress.setVisible(True)
                self._export_timer = QTimer(self)
                self._export_timer.setInterval(250)
                self._export_timer.timeout.connect(self._poll_export)
                self._export_timer.start()
        except Exception as exc:
            self._log_err(f"Export: {exc}")

    def _poll_export(self):
        tasks = getattr(self, '_export_tasks', None)
        if not tasks:
            self._finish_export()
            return
        from qgis.core import QgsTask
        total = 0.0
        all_done = True
        for t in tasks:
            try:
                s = t.status()
                if s in (QgsTask.Complete, QgsTask.Terminated):
                    total += 100.0
                else:
                    total += t.progress()
                    all_done = False
            except RuntimeError:
                total += 100.0
        self._exportProgress.setValue(int(max(0, min(total / len(tasks), 100))))
        if all_done:
            self._finish_export()

    def _finish_export(self):
        if self._export_timer:
            self._export_timer.stop()
            self._export_timer = None
        self._export_tasks = None
        self._exportProgress.setValue(100)
        QTimer.singleShot(1500, self._hide_export_progress)

    def _hide_export_progress(self):
        self._exportProgress.setVisible(False)
        self._exportProgress.setValue(0)
        self._exportBtn.setEnabled(True)

    # ==================================================================
    #  JOURNALISATION ET LIENS DE RAPPORT
    # ==================================================================

    _LOG_TAG_RE = re.compile(r'\[([A-Za-z0-9_-]+)\]')

    def _inject_tag_icons(self, msg, color):
        """Remplace les balises [TAG] par leur icone SVG teintee en ligne."""
        from qgis.PyQt.QtCore import QUrl
        doc = self.textBrowser.document()

        def _replace_tag(m):
            tag = m.group(1)
            svg_name = _LOG_TAG_ICONS.get(tag)
            if not svg_name:
                return m.group(0)
            res_key = f'tag_{tag}_{_c(color)}'
            pix = _svg_pixmap(svg_name, size=13, color=color)
            if pix.isNull():
                return m.group(0)
            doc.addResource(2, QUrl(f'icon://{res_key}'), pix)
            return (
                f'<img src="icon://{res_key}" width="13" height="13" '
                f'style="vertical-align:middle;"/> '
            )

        return self._LOG_TAG_RE.sub(_replace_tag, msg)

    def _log_html(self, html):
        self.textBrowser.append(html)
        c = self.textBrowser.textCursor()
        c.movePosition(QTextCursor.MoveOperation.End)
        self.textBrowser.setTextCursor(c)

    def _log_info(self, msg):
        color = _ThemeColors.info()
        msg = self._inject_tag_icons(msg, color)
        self._log_html(f'<span style="color:{_c(color)}">{msg}</span>')

    def _log_ok(self, msg):
        color = _ThemeColors.ok()
        msg = self._inject_tag_icons(msg, color)
        self._log_html(f'<span style="color:{_c(color)}">{msg}</span>')

    def _log_warn(self, msg):
        color = _ThemeColors.warn()
        msg = self._inject_tag_icons(msg, color)
        self._log_html(f'<span style="color:{_c(color)}">{msg}</span>')

    def _log_err(self, msg):
        color = _ThemeColors.error()
        msg = self._inject_tag_icons(msg, color)
        self._log_html(f'<span style="color:{_c(color)}"><b>{msg}</b></span>')

    def _log_dim(self, msg):
        self._log_html(f'<span style="color:{_c(_ThemeColors.dim())}">{msg}</span>')

    def log_result_link(self, filepath):
        """Affiche le lien vers le rapport Excel et active l'ouverture directe."""
        self._last_report_path = filepath or ''
        if self._reopenReportBtn is not None and filepath and os.path.isfile(filepath):
            self._reopenReportBtn.setVisible(True)
            self._reopenReportBtn.setToolTip(f"Ouvrir {os.path.basename(filepath)}")
        fname = os.path.basename(filepath)
        url = filepath.replace('\\', '/')
        self._log_html(
            f'<span style="color:{_c(_ThemeColors.ok())}">Rapport : '
            f'<a href="file:///{url}" style="color:{_c(_ThemeColors.info())}; '
            f'text-decoration:underline; font-weight:bold;">{fname}</a></span>'
        )
        self._refresh_readiness()

    def _on_link_clicked(self, url):
        """Ouvre le fichier de rapport clique dans le journal."""
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
    #  PROPRIETES ET API PUBLIQUE
    # ==================================================================

    @property
    def detection(self):
        return self._detection

    @property
    def is_project_mode(self):
        """Mode BDD : True si connexion BDD detectee et toggle active."""
        return self._bdd_connected and self._chk_bdd_mode is not None \
            and self._chk_bdd_mode.isChecked()

    @property
    def is_bdd_connected(self):
        """True si une connexion PostgreSQL QGIS 'AUVERGNE' est configuree."""
        return self._bdd_connected

    @property
    def force_gpkg_mode(self):
        """True si le mode GPKG est force (toggle BDD decoche ou absent)."""
        if self._chk_bdd_mode is None:
            return True
        return not self._chk_bdd_mode.isChecked()

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
        """Tolerance spatiale d'appariement (m)."""
        return 7.5

    @property
    def sro(self):
        """SRO : valeur du champ UI (GPKG, detection ou saisie manuelle)."""
        return self._sro_value.text().strip() or self._detection.sro

    def get_export_dir(self):
        return self.exportPathEdit.text() or self._detection.export_dir

    def init_default_layers(self):
        best_pot = (None, 0)
        best_cap = (None, 0)
        best_com = (None, 0)

        for layer in QgsProject.instance().mapLayers().values():
            name = layer.name().lower()
            geom = layer.geometryType() if hasattr(layer, 'geometryType') else -1

            if 'infra_pt_pot' in name and geom == GEOM_POINT:
                best_pot = (layer, 1)

            if geom == GEOM_POLYGON:
                if 'etude_cap_ft' in name:
                    best_cap = (layer, 3)
                elif ('cap_ft' in name or 'capft' in name) and best_cap[1] < 2:
                    best_cap = (layer, 2)

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
        self._refresh_readiness()

    def _apply_full_theme(self):
        """Applique l'ensemble des jetons et feuilles de style VTT / RecoGC."""
        if getattr(self, "_applying_theme", False):
            return
        self._applying_theme = True
        try:
            ui_theme.invalidate_tokens()
            self.setStyleSheet(ui_theme.dialog_qss())
            self.startBtn.setStyleSheet(_btn_start_style())
            self.cancelBtn.setStyleSheet(_btn_cancel_style())
            self.progressBar.setStyleSheet(_progress_bar_style())
            self._exportProgress.setStyleSheet(_progress_bar_style())
            for btn in (
                self.browseProjectBtn, self.refreshBtn, self.loadGpkgBtn,
                self.browseExportBtn, self.layersUnlockBtn, self.selectAllBtn,
                self.deselectAllBtn, self.clearLogBtn, self.exportLogBtn,
                self.helpButton, self.diagButton, self._exportBtn,
                self._reopenReportBtn,
            ):
                if btn is not None:
                    btn.setStyleSheet(ui_theme.secondary_button_qss())
            if self._settingsBtn is not None:
                self._settingsBtn.setStyleSheet(ui_theme.tool_button_qss())
            if self._ver_badge is not None:
                self._ver_badge.setStyleSheet(ui_theme.badge_qss("neutral"))
            if self._project_tag is not None:
                self._project_tag.setStyleSheet(ui_theme.badge_qss("accent"))
            for edit in (self.projectPathEdit, self.gpkgPathEdit, self.exportPathEdit):
                edit.apply_theme(False)
            for row in self._module_rows.values():
                row.apply_theme()
            for behavior in self._hover_icon_behaviors:
                behavior.refresh()
            if self._parcours is not None:
                self._parcours.apply_theme()
            if self._subtitle is not None:
                _set_foreground(self._subtitle, _ThemeColors.dim())
            if self._det_summary is not None:
                _set_foreground(self._det_summary, _ThemeColors.dim())
            if self._progress_step is not None:
                _set_foreground(self._progress_step, _ThemeColors.info())
            self._on_sro_text_changed(self._sro_value.text())
            self._update_resource_chips()
            self._update_mod_count()
            self._refresh_readiness()
        finally:
            self._applying_theme = False

    def keyPressEvent(self, event):
        """Raccourcis clavier (Ctrl+Entree pour demarrer, Echap pour annuler)."""
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
        """Rafraichit tous les jetons visuels lorsque le theme QGIS change."""
        super().changeEvent(event)
        if event.type() == _qevent_type('PaletteChange'):
            self._apply_full_theme()

    def closeEvent(self, event):
        _ThemeColors._widget_palette_ref = None
        ui_theme.set_palette_ref(None)
        if self._diag_timer is not None and self._diag_timer.isActive():
            self._diag_timer.stop()
        if self._is_running:
            self.cancel_requested.emit()
        self._save_ui_state()
        if self.smooth_progress:
            self.smooth_progress.reset()
        super().closeEvent(event)
