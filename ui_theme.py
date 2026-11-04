# -*- coding: utf-8 -*-
# Youcef ADDA | GEODECI
# FR : Plugins QGIS, solutions SIG et administration PostgreSQL/PostGIS ; audit, automatisation et accompagnement.
# EN : QGIS plugins, GIS solutions and PostgreSQL/PostGIS administration; audits, workflow automation and business support.
# AR : تطوير إضافات QGIS وحلول نظم المعلومات الجغرافية وإدارة PostgreSQL/PostGIS، والتدقيق والأتمتة ومرافقة المؤسسات.
# Site / Website / الموقع : https://geodeci.xyz/
# Contact professionnel / Business enquiries / للتواصل المهني : youcef.geodesien@gmail.com | contact@geodeci.xyz
# Détails / Details / التفاصيل : SIGNATURE.md
# ============================================================================
"""Jetons visuels, icones optiques et primitives VTT / RecoGC pour ST-AIR."""

from collections import OrderedDict
import logging
import math
import os
import re
from typing import Dict, Optional, Tuple

from qgis.core import QgsApplication
from qgis.PyQt.QtCore import (
    QByteArray, QObject, QPointF, QRectF, Qt, QTimer, pyqtSignal,
)
from qgis.PyQt.QtGui import (
    QColor, QCursor, QFont, QIcon, QImage, QPainter, QPainterPath,
    QPalette, QPen, QPixmap,
)
from qgis.PyQt.QtSvg import QSvgRenderer
from qgis.PyQt.QtWidgets import QHBoxLayout, QPushButton, QWidget

from .compat import (
    ALIGN_LEFT, ALIGN_VCENTER, CURSOR_ARROW, CURSOR_POINTING,
    PAL_BASE, PAL_HIGHLIGHT, PAL_WINDOW, PAL_WINDOW_TEXT,
)

logger = logging.getLogger(__name__)

PLUGIN_DIR = os.path.dirname(__file__)
SVG_DIR = os.path.join(PLUGIN_DIR, "svg", "outline")

ICONE_XS, ICONE_SM, ICONE_MD, ICONE_LG = 16, 18, 20, 24
TRAIT_ICONE = 1.5
HAUTEUR_COMPACTE, HAUTEUR_CONTROLE, HAUTEUR_CONFORTABLE, HAUTEUR_PARCOURS = (
    30, 34, 40, 46
)
ESPACE_XS, ESPACE_SM, ESPACE_MD, ESPACE_GROUPE = 4, 8, 12, 16
RAYON_PANNEAU, RAYON_CONTROLE = 8, 6

_ANTIALIAS = (
    QPainter.RenderHint.Antialiasing
    if hasattr(QPainter, "RenderHint")
    else QPainter.Antialiasing
)
_TEXT_ANTIALIAS = (
    QPainter.RenderHint.TextAntialiasing
    if hasattr(QPainter, "RenderHint")
    else QPainter.TextAntialiasing
)
_NO_PEN = Qt.PenStyle.NoPen if hasattr(Qt, "PenStyle") else Qt.NoPen
_TRANSPARENT = (
    Qt.GlobalColor.transparent
    if hasattr(Qt, "GlobalColor")
    else Qt.transparent
)
_ELIDE_RIGHT = (
    Qt.TextElideMode.ElideRight
    if hasattr(Qt, "TextElideMode")
    else Qt.ElideRight
)
_FORMAT_PREMULTIPLIED = (
    QImage.Format.Format_ARGB32_Premultiplied
    if hasattr(QImage, "Format")
    else QImage.Format_ARGB32_Premultiplied
)
_COMPOSITION_SOURCE_IN = (
    QPainter.CompositionMode.CompositionMode_SourceIn
    if hasattr(QPainter, "CompositionMode")
    else QPainter.CompositionMode_SourceIn
)

_WIDGET_PAL_REF = None
_KEY_CACHE = None
_SIGNATURE_CACHE = None
_TOKENS_CACHE: dict = {}
_ICON_CACHE: "OrderedDict[tuple, QIcon]" = OrderedDict()
_PIX_CACHE: "OrderedDict[tuple, QPixmap]" = OrderedDict()
_SVG_SOURCES: Dict[str, str] = {}
_CACHE_LIMIT = 256


# ---------------------------------------------------------------------------
#  Palette et contraste WCAG AA
# ---------------------------------------------------------------------------

def set_palette_ref(widget) -> None:
    """Assigne un widget de reference pour detecter les themes sombres QSS."""
    global _WIDGET_PAL_REF
    if _WIDGET_PAL_REF is not widget:
        _WIDGET_PAL_REF = widget
        invalidate_tokens()


def active_palette() -> QPalette:
    """Retourne la palette synthetisee du widget de reference ou de QGIS."""
    global _WIDGET_PAL_REF
    if _WIDGET_PAL_REF is not None:
        try:
            return _WIDGET_PAL_REF.palette()
        except RuntimeError:
            _WIDGET_PAL_REF = None
    app = QgsApplication.instance()
    return app.palette() if app is not None else QPalette()


def _luminance(color: QColor) -> float:
    def _lin(val: float) -> float:
        return val / 12.92 if val <= 0.04045 else ((val + 0.055) / 1.055) ** 2.4

    return (
        0.2126 * _lin(color.redF())
        + 0.7152 * _lin(color.greenF())
        + 0.0722 * _lin(color.blueF())
    )


def _contrast(a: QColor, b: QColor) -> float:
    light, dark = sorted((_luminance(a), _luminance(b)), reverse=True)
    return (light + 0.05) / (dark + 0.05)


def _mix(a: QColor, b: QColor, ratio: float) -> QColor:
    part = max(0.0, min(1.0, ratio))
    return QColor.fromRgbF(
        a.redF() * (1.0 - part) + b.redF() * part,
        a.greenF() * (1.0 - part) + b.greenF() * part,
        a.blueF() * (1.0 - part) + b.blueF() * part,
    )


def _readable(color: QColor, bg: QColor, threshold: float = 4.55) -> QColor:
    pole = max(
        (QColor("#000000"), QColor("#ffffff")),
        key=lambda c: _contrast(c, bg),
    )
    for step in range(101):
        cand = _mix(color, pole, step / 100.0)
        if _contrast(cand, bg) >= threshold:
            return cand
    return pole


def _readable_on_all(
    color: QColor, backgrounds: tuple, threshold: float = 4.55
) -> QColor:
    pole = max(
        (QColor("#000000"), QColor("#ffffff")),
        key=lambda c: min(_contrast(c, bg) for bg in backgrounds),
    )
    for step in range(101):
        cand = _mix(color, pole, step / 100.0)
        if all(_contrast(cand, bg) >= threshold for bg in backgrounds):
            return cand
    return pole


def rgba_css(color: QColor, alpha: float) -> str:
    """Formate une QColor en rgba() CSS."""
    clamped = max(0.0, min(1.0, alpha))
    return f"rgba({color.red()}, {color.green()}, {color.blue()}, {clamped:.2f})"


def _compute_tokens() -> dict:
    pal = active_palette()
    fond = pal.color(PAL_WINDOW)
    surface = pal.color(PAL_BASE)
    if not surface.isValid():
        surface = fond
    sombre = (
        _luminance(surface) < 0.30
        or surface.lightnessF() < 0.5
        or _luminance(fond) < 0.30
        or fond.lightnessF() < 0.5
    )
    if sombre and fond.lightnessF() >= 0.5:
        fond = _mix(surface, QColor("#181a1f"), 0.35)
    raw_txt = pal.color(PAL_WINDOW_TEXT)
    texte = _readable(raw_txt, fond)
    secondaire = _readable(_readable(_mix(texte, fond, 0.26), fond), surface)
    tertiaire = _readable(_readable(_mix(texte, fond, 0.44), fond), surface)
    raw_hl = pal.color(PAL_HIGHLIGHT)
    if not raw_hl.isValid() or _contrast(raw_hl, surface) < 1.3:
        raw_hl = QColor("#3b82f6") if sombre else QColor("#1d4ed8")
    accent = _readable(raw_hl, surface, 5.0)
    texte_accent = max(
        (QColor("#ffffff"), QColor("#000000")),
        key=lambda c: _contrast(c, accent),
    )
    tok = {
        "fond": fond,
        "surface": surface,
        "surfaceDouce": _mix(surface, texte, 0.055),
        "surfaceSurvol": _mix(surface, texte, 0.085),
        "surfacePressee": _mix(surface, texte, 0.135),
        "texte": _readable(texte, surface),
        "secondaire": secondaire,
        "tertiaire": tertiaire,
        "ligne": _mix(fond, texte, 0.20),
        "accent": accent,
        "texteAccent": texte_accent,
        "accentSurvol": _readable(_mix(accent, texte_accent, 0.08), texte_accent),
        "accentPresse": _readable(_mix(accent, texte_accent, 0.15), texte_accent),
    }
    raw_sem = {
        "warning": "#f6c177" if sombre else "#956000",
        "error": "#ff7b86" if sombre else "#b32639",
        "ok": "#74d99f" if sombre else "#237843",
        "info": "#64b5f6" if sombre else "#1565c0",
    }
    for key, hex_val in raw_sem.items():
        tok[key] = _readable(QColor(hex_val), surface, 5.0)
    for key in ("accent", "warning", "error", "ok", "info"):
        soft = _mix(surface, tok[key], 0.10)
        tok[key] = _readable(tok[key], soft, 4.55)
        tok[f"{key}Doux"] = soft
    bgs = tuple(
        tok[k]
        for k in (
            "fond", "surface", "surfaceDouce", "surfaceSurvol",
            "surfacePressee", "accentDoux", "warningDoux",
            "errorDoux", "okDoux",
        )
    )
    for key in ("texte", "secondaire", "tertiaire", "accent", "warning", "error", "ok", "info"):
        tok[key] = _readable_on_all(tok[key], bgs)
    tok["ligneControle"] = _readable_on_all(tok["ligne"], bgs, 3.0)
    tok["sombre"] = sombre
    return tok


def tokens() -> dict:
    """Retourne les jetons visuels actifs avec cache de signature."""
    global _KEY_CACHE, _SIGNATURE_CACHE, _TOKENS_CACHE
    pal = active_palette()
    cache_key = pal.cacheKey()
    sig = (
        cache_key,
        pal.color(PAL_WINDOW).rgba(),
        pal.color(PAL_BASE).rgba(),
        pal.color(PAL_WINDOW_TEXT).rgba(),
    )
    if _TOKENS_CACHE and sig == _SIGNATURE_CACHE:
        return _TOKENS_CACHE
    _SIGNATURE_CACHE = sig
    _KEY_CACHE = cache_key
    _TOKENS_CACHE = _compute_tokens()
    return _TOKENS_CACHE


def invalidate_tokens() -> dict:
    """Vide les caches de jetons et d'icones lors d'un changement de theme."""
    global _KEY_CACHE, _SIGNATURE_CACHE
    _KEY_CACHE = None
    _SIGNATURE_CACHE = None
    _ICON_CACHE.clear()
    _PIX_CACHE.clear()
    return tokens()


def is_dark() -> bool:
    """Indique si la palette active est sombre."""
    return bool(tokens()["sombre"])


def tabular_font(base_font: Optional[QFont] = None, pt_delta: float = 0.0) -> QFont:
    """Police sans-serif a chasse tabulaire pour compteurs et codes."""
    f = QFont(base_font) if base_font is not None else QFont()
    if pt_delta:
        base_pt = f.pointSizeF() if f.pointSizeF() > 0 else 9.0
        f.setPointSizeF(max(7.5, base_pt + pt_delta))
    if hasattr(QFont, "StyleHint"):
        f.setStyleHint(QFont.StyleHint.SansSerif)
    return f


# ---------------------------------------------------------------------------
#  Rendu optique des icones SVG (trait 1.5 px + cache LRU)
# ---------------------------------------------------------------------------

def _load_svg_xml(name: str) -> str:
    fname = name if name.endswith(".svg") else f"{name}.svg"
    cached = _SVG_SOURCES.get(fname)
    if cached is not None:
        return cached
    path = os.path.join(SVG_DIR, fname)
    if not os.path.exists(path):
        return ""
    try:
        with open(path, "r", encoding="utf-8-sig") as fh:
            raw = fh.read()
    except OSError:
        return ""
    if 'viewBox="0 0 24 24"' in raw:
        raw = re.sub(
            r'(<svg\b[^>]*?)\bstroke-width="[0-9.]+"',
            rf'\1stroke-width="{TRAIT_ICONE:.4g}"',
            raw,
            count=1,
        )
    _SVG_SOURCES[fname] = raw
    return raw


def svg_pixmap(name: str, size: int = 16, color: Optional[QColor] = None) -> QPixmap:
    """Rend une icone SVG au trait optique 1.5 px teintee avec antialiasing."""
    tint = color if color is not None else tokens()["texte"]
    key = (name, int(size), tint.rgba())
    cached = _PIX_CACHE.get(key)
    if cached is not None:
        _PIX_CACHE.move_to_end(key)
        return cached
    xml = _load_svg_xml(name)
    if not xml:
        return QPixmap()
    renderer = QSvgRenderer(QByteArray(xml.encode("utf-8")))
    img = QImage(size, size, _FORMAT_PREMULTIPLIED)
    img.fill(_TRANSPARENT)
    painter = QPainter(img)
    painter.setRenderHint(_ANTIALIAS, True)
    renderer.render(painter, QRectF(0.0, 0.0, float(size), float(size)))
    painter.setCompositionMode(_COMPOSITION_SOURCE_IN)
    painter.fillRect(img.rect(), tint)
    painter.end()
    pix = QPixmap.fromImage(img)
    while len(_PIX_CACHE) >= _CACHE_LIMIT:
        _PIX_CACHE.popitem(last=False)
    _PIX_CACHE[key] = pix
    return pix


def svg_icon(name: str, color: Optional[QColor] = None) -> QIcon:
    """Construit un QIcon multi-resolution teinte selon le theme VTT / RecoGC."""
    tint = color if color is not None else tokens()["texte"]
    key = (name, tint.rgba())
    cached = _ICON_CACHE.get(key)
    if cached is not None:
        _ICON_CACHE.move_to_end(key)
        return cached
    xml = _load_svg_xml(name)
    if not xml:
        return QIcon()
    ic = QIcon()
    for sz in (16, 18, 20, 24):
        pix = svg_pixmap(name, size=sz, color=tint)
        if not pix.isNull():
            ic.addPixmap(pix)
    while len(_ICON_CACHE) >= _CACHE_LIMIT:
        _ICON_CACHE.popitem(last=False)
    _ICON_CACHE[key] = ic
    return ic


# ---------------------------------------------------------------------------
#  Feuilles de style QSS (derivees des jetons VTT / RecoGC)
# ---------------------------------------------------------------------------

def chip_qss(state: str = "neutral") -> str:
    """Style de pastille interactive ou passive (present, absent, partial, neutral)."""
    tok = tokens()
    mapping = {
        "present": (tok["ok"], tok["okDoux"], 0.48, "600"),
        "ok": (tok["ok"], tok["okDoux"], 0.48, "600"),
        "absent": (tok["error"], tok["errorDoux"], 0.48, "600"),
        "error": (tok["error"], tok["errorDoux"], 0.48, "600"),
        "partial": (tok["warning"], tok["warningDoux"], 0.45, "600"),
        "warning": (tok["warning"], tok["warningDoux"], 0.45, "600"),
        "accent": (tok["accent"], tok["accentDoux"], 0.45, "600"),
        "optional": (tok["secondaire"], tok["surfaceDouce"], 0.30, "500"),
        "neutral": (tok["secondaire"], tok["surfaceDouce"], 0.30, "500"),
    }
    fg, bg, alpha, weight = mapping.get(state, mapping["neutral"])
    return (
        f"QLabel, QPushButton {{ color: {fg.name()}; background: {bg.name()}; "
        f"border: 1px solid {rgba_css(fg, alpha)}; border-radius: 6px; "
        f"padding: 2px 8px; font-weight: {weight}; font-size: 8pt; }}"
        f"QPushButton:hover {{ background: {tok['surfaceSurvol'].name()}; "
        f"border-color: {fg.name()}; }}"
    )


def badge_qss(tone: str = "neutral") -> str:
    """Style de capsule de compteur ou d'identite."""
    tok = tokens()
    if tone in ("ok", "warning", "error", "accent", "info"):
        fg, bg = tok[tone], tok[f"{tone}Doux"]
    else:
        fg, bg = tok["secondaire"], tok["surfaceDouce"]
    return (
        f"QLabel {{ color: {fg.name()}; background: {bg.name()}; "
        f"border: 1px solid {rgba_css(fg, 0.36)}; border-radius: 9px; "
        f"padding: 2px 9px; font-weight: 600; font-size: 8pt; }}"
    )


def start_button_qss() -> str:
    """Bouton d'action primaire d'execution (vert acheve / accent)."""
    tok = tokens()
    ok = tok["ok"]
    fg = max(
        (QColor("#ffffff"), QColor("#000000")),
        key=lambda c: _contrast(c, ok),
    )
    ok_h = _mix(ok, fg, 0.10)
    ok_p = _mix(ok, fg, 0.18)
    return (
        f"QPushButton {{ background: {ok.name()}; color: {fg.name()}; "
        f"border: 1px solid transparent; border-radius: {RAYON_CONTROLE}px; "
        f"padding: 6px 20px; font-weight: 700; font-size: 9.5pt; }}"
        f"QPushButton:hover {{ background: {ok_h.name()}; }}"
        f"QPushButton:pressed {{ background: {ok_p.name()}; }}"
        f"QPushButton:focus {{ border: 2px solid {tok['texte'].name()}; }}"
        f"QPushButton:disabled {{ background: {tok['surfaceDouce'].name()}; "
        f"color: {tok['tertiaire'].name()}; border: 1px solid {tok['ligne'].name()}; }}"
    )


def primary_button_qss() -> str:
    """Bouton primaire accentue."""
    tok = tokens()
    return (
        f"QPushButton {{ background: {tok['accent'].name()}; color: {tok['texteAccent'].name()}; "
        f"border: 1px solid transparent; border-radius: {RAYON_CONTROLE}px; "
        f"padding: 5px 14px; font-weight: 600; }}"
        f"QPushButton:hover {{ background: {tok['accentSurvol'].name()}; }}"
        f"QPushButton:pressed {{ background: {tok['accentPresse'].name()}; }}"
        f"QPushButton:focus {{ border: 2px solid {tok['texte'].name()}; }}"
        f"QPushButton:disabled {{ background: {tok['surfaceDouce'].name()}; "
        f"color: {tok['tertiaire'].name()}; border: 1px solid {tok['ligne'].name()}; }}"
    )


def danger_button_qss() -> str:
    """Bouton d'arret / annulation (teinte d'erreur VTT)."""
    tok = tokens()
    err = tok["error"]
    fg = max(
        (QColor("#ffffff"), QColor("#000000")),
        key=lambda c: _contrast(c, err),
    )
    err_h = _mix(err, fg, 0.10)
    return (
        f"QPushButton {{ background: {err.name()}; color: {fg.name()}; "
        f"border: 1px solid transparent; border-radius: {RAYON_CONTROLE}px; "
        f"padding: 6px 20px; font-weight: 700; font-size: 9.5pt; }}"
        f"QPushButton:hover {{ background: {err_h.name()}; }}"
        f"QPushButton:focus {{ border: 2px solid {tok['texte'].name()}; }}"
    )


def secondary_button_qss() -> str:
    """Bouton secondaire a bordure de controle mesurant >= 3:1."""
    tok = tokens()
    return (
        f"QPushButton {{ background: {tok['surface'].name()}; color: {tok['texte'].name()}; "
        f"border: 1px solid {tok['ligneControle'].name()}; border-radius: {RAYON_CONTROLE}px; "
        f"padding: 4px 10px; font-weight: 600; }}"
        f"QPushButton:hover {{ background: {tok['surfaceSurvol'].name()}; "
        f"border-color: {tok['accent'].name()}; }}"
        f"QPushButton:pressed {{ background: {tok['surfacePressee'].name()}; }}"
        f"QPushButton:focus {{ border: 2px solid {tok['accent'].name()}; }}"
        f"QPushButton:disabled {{ background: {tok['surfaceDouce'].name()}; "
        f"color: {tok['tertiaire'].name()}; border: 1px solid {tok['ligne'].name()}; }}"
    )


def tool_button_qss() -> str:
    """Bouton utilitaire compact d'en-tete ou de barre d'outils."""
    tok = tokens()
    return (
        f"QPushButton {{ background: transparent; color: {tok['texte'].name()}; "
        f"border: 1px solid transparent; border-radius: {RAYON_CONTROLE}px; padding: 4px 8px; "
        f"font-weight: 600; }}"
        f"QPushButton:hover {{ background: {tok['surfaceSurvol'].name()}; "
        f"border-color: {rgba_css(tok['accent'], 0.40)}; }}"
        f"QPushButton:pressed {{ background: {tok['surfacePressee'].name()}; }}"
        f"QPushButton:focus {{ border: 2px solid {tok['accent'].name()}; }}"
        f"QPushButton:disabled {{ color: {tok['tertiaire'].name()}; background: transparent; }}"
    )


def input_qss(active_drop: bool = False, valid: Optional[bool] = None) -> str:
    """Style de QLineEdit avec etat de depot Drag & Drop ou validation SRO."""
    tok = tokens()
    if active_drop:
        return (
            f"QLineEdit {{ background: {tok['accentDoux'].name()}; color: {tok['texte'].name()}; "
            f"border: 2px solid {tok['accent'].name()}; border-radius: {RAYON_CONTROLE}px; "
            f"padding: 4px 8px; }}"
        )
    if valid is True:
        return (
            f"QLineEdit {{ background: {tok['surface'].name()}; color: {tok['texte'].name()}; "
            f"border: 1.5px solid {tok['ok'].name()}; border-radius: {RAYON_CONTROLE}px; "
            f"padding: 4px 8px; }}"
        )
    if valid is False:
        return (
            f"QLineEdit {{ background: {tok['surface'].name()}; color: {tok['texte'].name()}; "
            f"border: 1.5px solid {tok['warning'].name()}; border-radius: {RAYON_CONTROLE}px; "
            f"padding: 4px 8px; }}"
        )
    return (
        f"QLineEdit {{ background: {tok['surface'].name()}; color: {tok['texte'].name()}; "
        f"border: 1px solid {tok['ligneControle'].name()}; border-radius: {RAYON_CONTROLE}px; "
        f"padding: 4px 8px; }}"
        f"QLineEdit:hover {{ border-color: {rgba_css(tok['accent'], 0.55)}; }}"
        f"QLineEdit:focus {{ border: 2px solid {tok['accent'].name()}; }}"
        f"QLineEdit:read-only {{ background: {tok['surfaceDouce'].name()}; }}"
    )


def card_qss(tone: str = "neutral") -> str:
    """Style de conteneur QFrame carte Fluent / VTT."""
    tok = tokens()
    borders = {
        "error": rgba_css(tok["error"], 0.48),
        "warning": rgba_css(tok["warning"], 0.48),
        "ok": rgba_css(tok["ok"], 0.44),
        "accent": rgba_css(tok["accent"], 0.44),
        "neutral": rgba_css(tok["ligne"], 0.68),
    }
    bgs = {
        "error": tok["errorDoux"].name(),
        "warning": tok["warningDoux"].name(),
        "ok": tok["okDoux"].name(),
        "accent": tok["accentDoux"].name(),
        "neutral": tok["surface"].name(),
    }
    return (
        f"QFrame#stairCard {{ background: {bgs.get(tone, bgs['neutral'])}; "
        f"border: 1px solid {borders.get(tone, borders['neutral'])}; "
        f"border-radius: {RAYON_PANNEAU}px; }}"
    )


def progress_qss() -> str:
    """Style de barre de progression."""
    tok = tokens()
    return (
        f"QProgressBar {{ border: 1px solid {rgba_css(tok['ligne'], 0.70)}; "
        f"background: {tok['surfaceDouce'].name()}; border-radius: 5px; "
        f"text-align: center; font-size: 8pt; font-weight: 600; color: {tok['texte'].name()}; }}"
        f"QProgressBar::chunk {{ background: {tok['accent'].name()}; border-radius: 4px; }}"
    )


def dialog_qss() -> str:
    """Feuille de style globale harmonisee pour les dialogues ST-AIR."""
    tok = tokens()
    return (
        f"QDialog, QWidget#stairTopPane, QWidget#stairLogPane {{ "
        f"background: {tok['fond'].name()}; color: {tok['texte'].name()}; }}"
        f"QLabel {{ color: {tok['texte'].name()}; background: transparent; }}"
        f"QCheckBox {{ color: {tok['texte'].name()}; spacing: 5px; }}"
        f"QgsCollapsibleGroupBox {{ font-weight: 700; color: {tok['texte'].name()}; "
        f"border: 1px solid {rgba_css(tok['ligne'], 0.72)}; border-radius: {RAYON_PANNEAU}px; "
        f"margin-top: 6px; padding-top: 4px; background: {tok['surface'].name()}; }}"
        f"QgsCollapsibleGroupBox::title {{ subcontrol-origin: margin; left: 10px; "
        f"padding: 0 5px; color: {tok['texte'].name()}; }}"
        f"QComboBox {{ background: {tok['surface'].name()}; color: {tok['texte'].name()}; "
        f"border: 1px solid {tok['ligneControle'].name()}; border-radius: {RAYON_CONTROLE}px; "
        f"padding: 3px 8px; min-height: 22px; }}"
        f"QComboBox:hover {{ border-color: {rgba_css(tok['accent'], 0.55)}; }}"
        f"QComboBox:focus {{ border: 2px solid {tok['accent'].name()}; }}"
        f"QComboBox:disabled {{ background: {tok['surfaceDouce'].name()}; "
        f"color: {tok['tertiaire'].name()}; }}"
        f"QTextBrowser {{ background: {tok['surface'].name()}; color: {tok['texte'].name()}; "
        f"border: 1px solid {rgba_css(tok['ligne'], 0.80)}; border-radius: {RAYON_PANNEAU}px; "
        f"padding: 6px; }}"
        f"QSplitter::handle {{ background: {rgba_css(tok['ligne'], 0.55)}; "
        f"margin: 2px 24px; border-radius: 1px; }}"
        f"QSplitter::handle:hover {{ background: {tok['accent'].name()}; }}"
    )


# ---------------------------------------------------------------------------
#  Primitives cinetiques et surfaces Fluent / VTT
# ---------------------------------------------------------------------------

def ease_out_cubic(t: float) -> float:
    """Courbe de deceleration OutCubic sur [0, 1]."""
    clamped = max(0.0, min(1.0, t))
    return 1.0 - (1.0 - clamped) ** 3


def lerp_color(c1: QColor, c2: QColor, ratio: float) -> QColor:
    """Interpole lineairement deux QColor."""
    part = max(0.0, min(1.0, ratio))
    inv = 1.0 - part
    return QColor(
        int(c1.red() * inv + c2.red() * part),
        int(c1.green() * inv + c2.green() * part),
        int(c1.blue() * inv + c2.blue() * part),
        int(c1.alpha() * inv + c2.alpha() * part),
    )


def paint_card_surface(painter: QPainter, rect: QRectF, style: dict) -> None:
    """Peint une carte Fluent / VTT avec reflet superieur et repere lateral."""
    radius = float(style.get("radius", RAYON_PANNEAU))
    path = QPainterPath()
    path.addRoundedRect(rect, radius, radius)
    painter.fillPath(path, style["bg"])

    top_alpha = int(style.get("highlight_alpha", 26 if is_dark() else 130))
    if top_alpha > 0 and rect.width() > 24.0:
        painter.setPen(QPen(QColor(255, 255, 255, top_alpha), 1.0))
        inset = max(4.0, radius)
        painter.drawLine(
            QPointF(rect.left() + inset, rect.top() + 1.0),
            QPointF(rect.right() - inset, rect.top() + 1.0),
        )

    left_col: Optional[QColor] = style.get("left_accent")
    if left_col is not None and rect.height() > 14.0:
        painter.setPen(_NO_PEN)
        painter.setBrush(left_col)
        painter.drawRoundedRect(
            QRectF(rect.left() + 3.0, rect.top() + 6.0, 3.0, rect.height() - 12.0),
            1.5,
            1.5,
        )

    bottom_col: Optional[QColor] = style.get("bottom_bar")
    bottom_ratio = float(style.get("bottom_ratio", 0.0))
    if bottom_col is not None and bottom_ratio > 0.01:
        bar_w = max(12.0, (rect.width() - 24.0) * min(1.0, bottom_ratio))
        painter.setPen(_NO_PEN)
        painter.setBrush(bottom_col)
        painter.drawRoundedRect(
            QRectF(rect.center().x() - bar_w / 2.0, rect.bottom() - 2.4, bar_w, 2.4),
            1.2,
            1.2,
        )

    painter.setBrush(_TRANSPARENT)
    painter.setPen(QPen(style["border"], float(style.get("border_width", 1.0))))
    painter.drawPath(path)


class ButtonFeedbackController(QObject):
    """Confirmation visuelle temporisee sur un bouton d'action."""

    def __init__(self, button: QPushButton) -> None:
        super().__init__(button)
        self._btn = button
        self._orig_text = ""
        self._orig_icon = QIcon()
        self._orig_qss = ""
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._restore)

    def flash(self, confirmed_text: str) -> None:
        if not self._timer.isActive():
            self._orig_text = self._btn.text()
            self._orig_icon = QIcon(self._btn.icon())
            self._orig_qss = self._btn.styleSheet()
        tok = tokens()
        ok_col, ok_bg = tok["ok"], tok["okDoux"]
        self._btn.setText(confirmed_text)
        self._btn.setIcon(svg_icon("check.svg", ok_col))
        self._btn.setStyleSheet(
            f"QPushButton {{ background: {ok_bg.name()}; color: {ok_col.name()}; "
            f"border: 1px solid {ok_col.name()}; border-radius: {RAYON_CONTROLE}px; "
            f"padding: 4px 10px; font-weight: 700; }}"
        )
        self._timer.start(1400)

    def _restore(self) -> None:
        self._btn.setText(self._orig_text)
        self._btn.setIcon(self._orig_icon)
        self._btn.setStyleSheet(self._orig_qss)


class SroMedallion(QWidget):
    """Medaillon optique 30x30 affichant l'icone de source teintee selon l'etat."""

    def __init__(self, svg_name: str = "database.svg", parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._svg_name = svg_name
        self._tone = "neutral"
        self.setFixedSize(30, 30)

    def set_state(self, tone: str, svg_name: Optional[str] = None) -> None:
        changed = False
        if self._tone != tone:
            self._tone = tone
            changed = True
        if svg_name and self._svg_name != svg_name:
            self._svg_name = svg_name
            changed = True
        if changed:
            self.update()

    def paintEvent(self, _event) -> None:  # noqa: N802
        tok = tokens()
        mapping = {
            "ok": (tok["ok"], tok["okDoux"]),
            "warning": (tok["warning"], tok["warningDoux"]),
            "error": (tok["error"], tok["errorDoux"]),
            "accent": (tok["accent"], tok["accentDoux"]),
            "neutral": (tok["accent"], tok["surfaceDouce"]),
        }
        fg, bg = mapping.get(self._tone, mapping["neutral"])
        painter = QPainter(self)
        painter.setRenderHint(_ANTIALIAS, True)
        rect = QRectF(1.0, 1.0, float(self.width() - 2), float(self.height() - 2))
        border = QColor(fg)
        border.setAlphaF(0.45)
        paint_card_surface(
            painter,
            rect,
            {"bg": bg, "border": border, "radius": float(RAYON_CONTROLE), "border_width": 1.0},
        )
        pix = svg_pixmap(self._svg_name, size=ICONE_XS, color=fg)
        if not pix.isNull():
            px = int((self.width() - pix.width()) / 2)
            py = int((self.height() - pix.height()) / 2)
            painter.drawPixmap(px, py, pix)
        painter.end()


# ---------------------------------------------------------------------------
#  Parcours Visuel Vivant (4 jalons : Projet -> SRO/Couches -> Controle -> Rapport)
# ---------------------------------------------------------------------------

_STEPS_SPEC: Tuple[Tuple[str, str, str], ...] = (
    ("projet", "1. Dossier projet", "folder-open.svg"),
    ("referentiel", "2. SRO & Couches", "layers.svg"),
    ("controle", "3. Contrôle lot", "activity.svg"),
    ("rapport", "4. Rapport Excel", "file-export.svg"),
)


class _StepConnector(QWidget):
    """Trait de liaison entre deux jalons avec remplissage progressif."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._target_fill, self._current_fill = 0.0, 0.0
        self.setFixedSize(12, HAUTEUR_PARCOURS)
        self._timer = QTimer(self)
        self._timer.setInterval(35)
        self._timer.timeout.connect(self._on_tick)

    def set_filled(self, target: float) -> None:
        clamped = max(0.0, min(1.0, float(target)))
        if abs(clamped - self._target_fill) >= 0.01:
            self._target_fill = clamped
            if not self._timer.isActive():
                self._timer.start()

    def _on_tick(self) -> None:
        diff = self._target_fill - self._current_fill
        self._current_fill = (
            self._target_fill if abs(diff) < 0.02 else self._current_fill + diff * 0.24
        )
        if abs(diff) < 0.02:
            self._timer.stop()
        self.update()

    def paintEvent(self, _event) -> None:  # noqa: N802
        tok = tokens()
        painter = QPainter(self)
        painter.setRenderHint(_ANTIALIAS, True)
        cy, w = float(self.height()) / 2.0, float(self.width())
        painter.setPen(_NO_PEN)
        painter.setBrush(tok["ligne"])
        painter.drawRoundedRect(QRectF(0.0, cy - 1.0, w, 2.0), 1.0, 1.0)
        if self._current_fill > 0.01:
            painter.setBrush(tok["accent"])
            painter.drawRoundedRect(
                QRectF(0.0, cy - 1.2, w * self._current_fill, 2.4), 1.2, 1.2
            )
        painter.end()


class _StepNode(QWidget):
    """Jalon du parcours peint via QPainter avec onde d'ecoute et orbite active."""

    clicked = pyqtSignal(str)

    def __init__(self, key: str, title: str, icon_name: str, parent=None) -> None:
        super().__init__(parent)
        self.key, self._title, self._icon_name = key, title, icon_name
        self._state, self._status_text = "attente", "En attente"
        self._listening = self._interactive = self._hovered = self._pressed = False
        self._hover_t, self._phase = 0.0, 0.0
        self.setFixedHeight(HAUTEUR_PARCOURS)
        self.setMinimumWidth(136)
        self._anim_timer = QTimer(self)
        self._anim_timer.setInterval(45)
        self._anim_timer.timeout.connect(self._on_anim_tick)

    def set_step_state(self, state: str, status_text: str) -> None:
        self._state, self._status_text = state, status_text
        self.setAccessibleName(f"{self._title}, {status_text}")
        self._sync_timer()
        self.update()

    def set_listening(self, active: bool) -> None:
        if self._listening != active:
            self._listening = active
            self._sync_timer()
            self.update()

    def set_interactive_action(self, tooltip: str) -> None:
        self._interactive = bool(tooltip)
        self.setToolTip(tooltip)
        self.setCursor(
            QCursor(CURSOR_POINTING if self._interactive else CURSOR_ARROW)
        )
        self.update()

    def _sync_timer(self) -> None:
        need_anim = self._state == "actif" or self._hovered or self._hover_t > 0.01
        if need_anim and not self._anim_timer.isActive():
            self._anim_timer.start()
        elif not need_anim and self._anim_timer.isActive():
            self._anim_timer.stop()

    def _on_anim_tick(self) -> None:
        target_h = 1.0 if self._hovered else 0.0
        self._hover_t += (target_h - self._hover_t) * 0.26
        if self._state == "actif":
            self._phase = (self._phase + 0.22) % (2.0 * math.pi)
        elif abs(self._hover_t - target_h) < 0.02:
            self._hover_t = target_h
            self._anim_timer.stop()
        self.update()

    def _resolve_colors(self, tok: dict) -> Tuple[QColor, QColor, str]:
        mapping = {
            "termine": (tok["ok"], tok["okDoux"], "check.svg"),
            "actif": (tok["accent"], tok["accentDoux"], self._icon_name),
            "attention": (tok["warning"], tok["warningDoux"], "alert-triangle.svg"),
            "erreur": (tok["error"], tok["errorDoux"], "alert-circle.svg"),
            "attente": (
                tok["accent"] if self._listening else tok["tertiaire"],
                tok["surfaceDouce"],
                self._icon_name,
            ),
        }
        return mapping.get(self._state, mapping["attente"])

    def paintEvent(self, _event) -> None:  # noqa: N802
        tok = tokens()
        fg, base_bg, ic_name = self._resolve_colors(tok)
        bg = (
            tok["surfacePressee"]
            if (self._pressed and self._interactive)
            else lerp_color(base_bg, tok["surfaceSurvol"], self._hover_t * 0.55)
        )
        active_b = (
            self._state in ("actif", "erreur", "attention", "termine")
            or self._listening
        )
        border_col = (
            fg if active_b else lerp_color(tok["ligne"], tok["accent"], self._hover_t)
        )

        painter = QPainter(self)
        painter.setRenderHint(_ANTIALIAS, True)
        painter.setRenderHint(_TEXT_ANTIALIAS, True)
        m = 1.5 if (self._pressed and self._interactive) else 1.0
        rect = QRectF(m, m, self.width() - m * 2.0, self.height() - m * 2.0)
        paint_card_surface(
            painter,
            rect,
            {
                "bg": bg,
                "border": border_col,
                "radius": RAYON_CONTROLE,
                "border_width": (
                    1.4 if (self._state == "actif" or self._hover_t > 0.3) else 1.0
                ),
                "bottom_bar": fg if (self._interactive or self._state == "actif") else None,
                "bottom_ratio": (
                    0.45 * self._hover_t + (0.28 if self._state == "actif" else 0.0)
                ),
            },
        )
        self._paint_medallion(painter, rect, tok, fg, ic_name)
        self._paint_labels(painter, rect, tok, fg)
        painter.end()

    def _paint_medallion(
        self, painter: QPainter, rect: QRectF, tok: dict, fg: QColor, ic_name: str
    ) -> None:
        cx, cy, r = rect.left() + 20.0, rect.center().y(), 12.0
        if self._listening and self._state == "attente":
            painter.setPen(QPen(QColor(fg.red(), fg.green(), fg.blue(), 85), 1.4))
            painter.setBrush(_TRANSPARENT)
            painter.drawEllipse(QPointF(cx, cy), r + 3.0, r + 3.0)
        painter.setPen(QPen(fg, 1.2))
        painter.setBrush(tok["surface"])
        painter.drawEllipse(QPointF(cx, cy), r, r)
        if self._state == "actif":
            painter.setPen(_NO_PEN)
            for idx in range(3):
                ang = self._phase - idx * 0.48
                painter.setBrush(
                    QColor(fg.red(), fg.green(), fg.blue(), int(235 - idx * 70))
                )
                painter.drawEllipse(
                    QPointF(
                        cx + (r + 3.0) * math.cos(ang),
                        cy + (r + 3.0) * math.sin(ang),
                    ),
                    2.2 - idx * 0.4,
                    2.2 - idx * 0.4,
                )
        pix = svg_pixmap(ic_name, size=14, color=fg)
        if not pix.isNull():
            painter.drawPixmap(int(cx - 7), int(cy - 7), pix)

    def _paint_labels(
        self, painter: QPainter, rect: QRectF, tok: dict, fg: QColor
    ) -> None:
        tx, tw = rect.left() + 38.0, max(10.0, rect.width() - 44.0)
        bold = self._state != "attente" or self._listening
        w_bold = (
            QFont.Weight.DemiBold if hasattr(QFont, "Weight") else QFont.DemiBold
        )
        w_med = QFont.Weight.Medium if hasattr(QFont, "Weight") else QFont.Medium
        align = ALIGN_LEFT | ALIGN_VCENTER
        specs = (
            (
                4.5,
                11,
                w_bold if bold else w_med,
                tok["texte"] if bold else tok["secondaire"],
                self._title,
            ),
            (21.5, 10, w_bold, fg, self._status_text),
        )
        for y_off, px, weight, col, txt in specs:
            f = QFont(self.font())
            f.setPixelSize(px)
            f.setWeight(weight)
            painter.setFont(f)
            painter.setPen(col)
            elided = painter.fontMetrics().elidedText(txt, _ELIDE_RIGHT, int(tw))
            painter.drawText(
                QRectF(tx, rect.top() + y_off, tw, 17.0), align, elided
            )

    def enterEvent(self, event) -> None:  # noqa: N802
        self._hovered = True
        self._sync_timer()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802
        self._hovered = self._pressed = False
        self._sync_timer()
        super().leaveEvent(event)

    def mousePressEvent(self, event) -> None:  # noqa: N802
        self._pressed = self._interactive
        self.update()
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        fire = (
            self._pressed
            and self._interactive
            and self.rect().contains(event.pos())
        )
        self._pressed = False
        self.update()
        if fire:
            self.clicked.emit(self.key)
        super().mouseReleaseEvent(event)


class ParcoursVisuelBar(QWidget):
    """Barre horizontale des 4 jalons ST-AIR inspiree de VTT et RecoGC."""

    step_activated = pyqtSignal(str)

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._nodes: Dict[str, _StepNode] = {}
        self._connectors: list = []
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(3)
        for idx, (key, title, ic_name) in enumerate(_STEPS_SPEC):
            node = _StepNode(key, title, ic_name, self)
            node.clicked.connect(self.step_activated.emit)
            self._nodes[key] = node
            lay.addWidget(node, 1)
            if idx + 1 < len(_STEPS_SPEC):
                conn = _StepConnector(self)
                self._connectors.append(conn)
                lay.addWidget(conn)

    def set_step(
        self,
        key: str,
        state: str,
        status_text: str,
        listening: bool = False,
        tooltip: str = "",
    ) -> None:
        node = self._nodes.get(key)
        if node is not None:
            node.set_step_state(state, status_text)
            node.set_listening(listening)
            node.set_interactive_action(tooltip)
            self._refresh_connectors()

    def _refresh_connectors(self) -> None:
        keys = [k for k, _, _ in _STEPS_SPEC]
        for idx, conn in enumerate(self._connectors):
            done = self._nodes[keys[idx]]._state in ("termine", "attention", "erreur")
            nxt_active = self._nodes[keys[idx + 1]]._state == "actif"
            conn.set_filled(1.0 if done else (0.45 if nxt_active else 0.0))

    def apply_theme(self) -> None:
        for child in (*self._nodes.values(), *self._connectors):
            child.update()
