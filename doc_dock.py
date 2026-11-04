# -*- coding: utf-8 -*-
# Youcef ADDA | GEODECI
# FR : Plugins QGIS, solutions SIG et administration PostgreSQL/PostGIS ; audit, automatisation et accompagnement.
# EN : QGIS plugins, GIS solutions and PostgreSQL/PostGIS administration; audits, workflow automation and business support.
# AR : تطوير إضافات QGIS وحلول نظم المعلومات الجغرافية وإدارة PostgreSQL/PostGIS، والتدقيق والأتمتة ومرافقة المؤسسات.
# Site / Website / الموقع : https://geodeci.xyz/
# Contact professionnel / Business enquiries / للتواصل المهني : youcef.geodesien@gmail.com | contact@geodeci.xyz
# Détails / Details / التفاصيل : SIGNATURE.md
# ============================================================================
"""Panneau ancrable QGIS (DockWidget) pour la documentation ST-AIR."""

import os

from qgis.PyQt.QtCore import QEvent, QUrl
from qgis.PyQt.QtGui import QTextDocument
from qgis.PyQt.QtWidgets import (
    QDockWidget,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

try:
    from qgis.gui import QgsDockWidget as _BaseDockWidget
except ImportError:
    _BaseDockWidget = QDockWidget

_HAS_WEBENGINE = False
try:
    from qgis.PyQt.QtWebEngineWidgets import QWebEngineView
    try:
        from qgis.PyQt.QtWebEngineCore import QWebEnginePage
    except ImportError:
        from qgis.PyQt.QtWebEngineWidgets import QWebEnginePage
    _HAS_WEBENGINE = True
except ImportError:
    QWebEngineView = None
    QWebEnginePage = object

from .compat import (
    CURSOR_POINTING,
    DOCK_BOTTOM,
    DOCK_LEFT,
    DOCK_RIGHT,
    DOCK_TOP,
    HORIZONTAL,
)
from . import ui_theme
from .version import get_version

_PORTAL_SECTIONS = (
    ("Accueil", "top"),
    ("Modules", "modules"),
    ("Architecture", "architecture"),
    ("Workflow", "workflow"),
    ("Dossier", "anatomy"),
    ("Rapport", "rapport"),
    ("Demo", "demo"),
    ("FAQ", "faq"),
)


if _HAS_WEBENGINE:
    # Navigation locale
    class _DocWebPage(QWebEnginePage):
        """Intercepte la navigation locale pour synchroniser l'etat du dock."""

        def __init__(self, parent_view, dock):
            super().__init__(parent_view)
            self._dock = dock

        def acceptNavigationRequest(self, url, nav_type, is_main_frame):
            if url.isLocalFile():
                local_path = url.toLocalFile()
                low = local_path.lower()
                if low.endswith(".md") and os.path.isfile(local_path):
                    self._dock.show_markdown_file(local_path)
                    return False
                if os.path.basename(low) == "guide.html":
                    self._dock._mode = "guide"
                    self._dock._update_mode_buttons()
                elif os.path.basename(low) == "index.html":
                    self._dock._mode = "portal"
                    self._dock._update_mode_buttons()
                return True
            if url.scheme() in ("data", "about", "blob"):
                return True
            return False


# Rendu Markdown
def _render_markdown_html(md_path, tok):
    """Convertit un fichier Markdown en page HTML stylisee selon le theme."""
    try:
        with open(md_path, "r", encoding="utf-8") as fh:
            raw_md = fh.read()
    except OSError as exc:
        return (
            f"<html><body><p>doc_dock._render_markdown_html: "
            f"lecture impossible ({md_path}): {exc}</p></body></html>"
        )

    doc = QTextDocument()
    if hasattr(doc, "setMarkdown"):
        doc.setMarkdown(raw_md)
        body_html = doc.toHtml()
    else:
        escaped = (
            raw_md.replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
        )
        body_html = f"<pre>{escaped}</pre>"

    bg_win = tok["fond"].name()
    bg_alt = tok["surfaceDouce"].name()
    txt_main = tok["texte"].name()
    accent = tok["accent"].name()
    border = tok["ligne"].name()

    return f"""<!DOCTYPE html>
<html lang="fr">
<head>
<meta charset="utf-8">
<style>
  body {{
    margin: 0;
    padding: 22px 28px 40px 28px;
    background: {bg_win};
    color: {txt_main};
    font-family: 'Segoe UI', system-ui, -apple-system, sans-serif;
    font-size: 13px;
    line-height: 1.6;
  }}
  h1, h2, h3, h4 {{
    color: {txt_main};
    font-weight: 700;
    margin-top: 1.4em;
    margin-bottom: 0.5em;
  }}
  h1 {{
    font-size: 20px;
    border-bottom: 2px solid {accent};
    padding-bottom: 8px;
  }}
  h2 {{
    font-size: 16px;
    border-bottom: 1px solid {border};
    padding-bottom: 5px;
  }}
  a {{
    color: {accent};
    text-decoration: none;
  }}
  code, pre {{
    font-family: 'Cascadia Mono', 'Consolas', monospace;
    background: {bg_alt};
    color: {txt_main};
    border-radius: 4px;
  }}
  pre {{
    padding: 10px 12px;
    border: 1px solid {border};
    overflow-x: auto;
  }}
  table {{
    border-collapse: collapse;
    width: 100%;
    margin: 12px 0;
  }}
  th, td {{
    border: 1px solid {border};
    padding: 6px 10px;
    text-align: left;
  }}
  th {{
    background: {bg_alt};
    font-weight: 700;
  }}
  hr {{
    border: none;
    border-top: 1px solid {border};
    margin: 18px 0;
  }}
</style>
</head>
<body>
{body_html}
</body>
</html>"""


# Panneau documentation
class StairDocDockWidget(_BaseDockWidget):
    """Panneau ancrable QGIS embarquant la documentation ST-AIR."""

    def __init__(self, iface, plugin_dir, parent=None):
        win_parent = parent or (iface.mainWindow() if iface else None)
        super().__init__(f"ST-AIR - Documentation v{get_version()}", win_parent)
        self._iface = iface
        self._plugin_dir = plugin_dir
        self._registered = False
        self._applying_theme = False
        self._mode = "portal"
        self._guide_html_path = os.path.join(
            self._plugin_dir, "docs", "guide.html"
        )
        self._current_md_path = os.path.join(
            self._plugin_dir, "docs", "GUIDE_SOUS_TRAITANT.md"
        )
        self._index_path = os.path.join(
            self._plugin_dir, "docs", "index.html"
        )

        self.setObjectName("StairDocDockWidget")
        self.setAllowedAreas(
            DOCK_LEFT | DOCK_RIGHT | DOCK_BOTTOM | DOCK_TOP
        )

        self._section_buttons = []
        self._build_ui()
        self._apply_theme()
        self.show_portal()

    # Construction interface
    def _build_ui(self):
        container = QWidget(self)
        root = QVBoxLayout(container)
        root.setContentsMargins(6, 6, 6, 6)
        root.setSpacing(6)

        self._nav_card = QFrame(container)
        self._nav_card.setObjectName("stairCard")
        nav_vbox = QVBoxLayout(self._nav_card)
        nav_vbox.setContentsMargins(10, 8, 10, 8)
        nav_vbox.setSpacing(6)

        top_row = QHBoxLayout()
        top_row.setContentsMargins(0, 0, 0, 0)
        top_row.setSpacing(6)

        self._title_lbl = QLabel("Documentation ST-AIR")
        top_row.addWidget(self._title_lbl)
        top_row.addStretch(1)

        self._btn_portal = QPushButton("Portail interactif")
        self._btn_portal.setCursor(CURSOR_POINTING)
        self._btn_portal.setMinimumHeight(26)
        self._btn_portal.clicked.connect(lambda _=False: self.show_portal())
        top_row.addWidget(self._btn_portal)

        self._btn_guide = QPushButton("Guide sous-traitant")
        self._btn_guide.setCursor(CURSOR_POINTING)
        self._btn_guide.setMinimumHeight(26)
        self._btn_guide.clicked.connect(lambda _=False: self.show_guide())
        top_row.addWidget(self._btn_guide)

        nav_vbox.addLayout(top_row)

        sec_row = QHBoxLayout()
        sec_row.setContentsMargins(0, 0, 0, 0)
        sec_row.setSpacing(4)

        for label, anchor in _PORTAL_SECTIONS:
            btn = QPushButton(label)
            btn.setCursor(CURSOR_POINTING)
            btn.setMinimumHeight(24)
            btn.clicked.connect(
                lambda _=False, a=anchor: self.navigate_section(a)
            )
            sec_row.addWidget(btn)
            self._section_buttons.append(btn)

        sec_row.addStretch(1)
        nav_vbox.addLayout(sec_row)
        root.addWidget(self._nav_card)

        if _HAS_WEBENGINE and QWebEngineView is not None:
            self._web = QWebEngineView(container)
            self._page = _DocWebPage(self._web, self)
            self._web.setPage(self._page)
            self._browser = None
            root.addWidget(self._web, 1)
        else:
            self._web = None
            self._page = None
            self._browser = QTextBrowser(container)
            self._browser.setOpenExternalLinks(False)
            self._browser.setOpenLinks(False)
            self._browser.anchorClicked.connect(self._on_browser_anchor)
            root.addWidget(self._browser, 1)

        self.setWidget(container)

    # Navigation ancres
    def _on_browser_anchor(self, url):
        if url.isLocalFile():
            path = url.toLocalFile()
            low = path.lower()
            if low.endswith(".md") and os.path.isfile(path):
                self.show_markdown_file(path)
                return
            if low.endswith(".html") and os.path.isfile(path):
                if os.path.basename(low) == "guide.html":
                    self.show_guide()
                else:
                    self.show_portal(url.fragment())
                return
        frag = url.fragment()
        if frag and self._browser is not None:
            self._browser.scrollToAnchor(frag)

    # Affichage portail
    def show_portal(self, anchor=""):
        self._mode = "portal"
        self._update_mode_buttons()
        if not os.path.isfile(self._index_path):
            self.show_guide()
            return

        if self._web is not None:
            url = QUrl.fromLocalFile(self._index_path)
            if anchor:
                url.setFragment(anchor)
            self._web.setUrl(url)
        elif self._browser is not None:
            url = QUrl.fromLocalFile(self._index_path)
            self._browser.setSource(url)
            if anchor:
                self._browser.scrollToAnchor(anchor)

    # Affichage guide
    def show_guide(self):
        if os.path.isfile(self._guide_html_path):
            self._mode = "guide"
            self._update_mode_buttons()
            url = QUrl.fromLocalFile(self._guide_html_path)
            if self._web is not None:
                self._web.setUrl(url)
            elif self._browser is not None:
                self._browser.setSource(url)
            return
        if os.path.isfile(self._current_md_path):
            self.show_markdown_file(self._current_md_path)

    # Affichage Markdown
    def show_markdown_file(self, md_path):
        if not os.path.isfile(md_path):
            self.show_guide()
            return
        self._mode = "guide"
        self._current_md_path = md_path
        self._update_mode_buttons()

        tok = ui_theme.tokens()
        html = _render_markdown_html(md_path, tok)
        base_url = QUrl.fromLocalFile(os.path.dirname(md_path) + os.sep)

        if self._web is not None:
            self._web.setHtml(html, base_url)
        elif self._browser is not None:
            self._browser.setHtml(html)

    # Saut section
    def navigate_section(self, anchor):
        if self._mode != "portal" or self._web is None:
            self.show_portal(anchor=anchor)
            return
        current_url = self._web.url()
        if current_url.isLocalFile() and os.path.normcase(
            current_url.toLocalFile()
        ) == os.path.normcase(self._index_path):
            js = (
                f"var el = document.getElementById('{anchor}');"
                f"if (el) {{ el.scrollIntoView({{behavior: 'smooth'}}); }}"
                f"else {{ window.location.hash = '#{anchor}'; }}"
            )
            self._web.page().runJavaScript(js)
        else:
            self.show_portal(anchor=anchor)

    # Ouverture dock
    def ouvrir(self):
        main_win = self._iface.mainWindow() if self._iface else None
        if not self._registered and main_win is not None:
            main_win.addDockWidget(DOCK_RIGHT, self)
            self._registered = True
            try:
                main_win.resizeDocks([self], [760], HORIZONTAL)
            except Exception:
                pass
        self.show()
        self.raise_()
        self.activateWindow()

    # Etat boutons
    def _update_mode_buttons(self):
        if self._mode == "portal":
            self._btn_portal.setStyleSheet(ui_theme.primary_button_qss())
            self._btn_guide.setStyleSheet(ui_theme.secondary_button_qss())
        else:
            self._btn_portal.setStyleSheet(ui_theme.secondary_button_qss())
            self._btn_guide.setStyleSheet(ui_theme.primary_button_qss())

    # Theme adaptatif
    def _apply_theme(self):
        if self._applying_theme:
            return
        self._applying_theme = True
        try:
            tok = ui_theme.tokens()
            self._nav_card.setStyleSheet(ui_theme.card_qss("neutral"))
            self._title_lbl.setStyleSheet(
                f"font-size: 12px; font-weight: 700; color: {tok['texte'].name()}; "
                "background: transparent; border: none;"
            )
            sec_qss = ui_theme.secondary_button_qss()
            for btn in self._section_buttons:
                btn.setStyleSheet(sec_qss)
            self._update_mode_buttons()
        finally:
            self._applying_theme = False

    # Changement palette
    def changeEvent(self, event):
        super().changeEvent(event)
        try:
            pal_change = QEvent.Type.PaletteChange
        except AttributeError:
            pal_change = QEvent.PaletteChange
        if event.type() == pal_change and not self._applying_theme:
            self._apply_theme()
