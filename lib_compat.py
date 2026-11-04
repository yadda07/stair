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
Couche de compatibilite pour les librairies Python tierces (pandas, pyarrow, …).

Certaines versions de QGIS (3.34.x) embarquent un pyarrow ancien (< 7.0)
qui ne possede pas ``pyarrow.compute.match_substring_regex``.
Pandas >= 2.0 utilise ArrowStringArray par defaut et appelle cette fonction
meme avec ``regex=False``, provoquant un AttributeError fatal.

Ce module :
  1. Detecte les versions de pandas / pyarrow au chargement.
  2. Fournit ``patch_pandas_arrow_backend()`` pour forcer le backend string
     « python » si pyarrow est incomplet — a appeler au demarrage du plugin.
  3. Fournit ``safe_str_contains(series, substring)`` comme wrapper garanti
     sans dependance pyarrow.
  4. Fournit un lecteur .xlsx stdlib (zip + xml.etree) pour les QgsTask :
     evite openpyxl/lxml et les « access violation » Windows QGIS 3.40+.

Importer ce module le plus tot possible (avant tout import pandas applicatif).
"""

import logging
import re as _re
import xml.etree.ElementTree as _ET
import zipfile as _zipfile
from typing import Optional

_log = logging.getLogger(__name__)

# ── detection pyarrow ────────────────────────────────────────────────────

_PYARROW_OK: bool = False
_PYARROW_VERSION: str = "absent"

try:
    import pyarrow as _pa
    _PYARROW_VERSION = getattr(_pa, "__version__", "inconnu")

    import pyarrow.compute as _pc
    _PYARROW_OK = hasattr(_pc, "match_substring_regex")
except ImportError:
    pass

# ── detection pandas ─────────────────────────────────────────────────────

_PANDAS_VERSION: str = "absent"

try:
    import pandas as _pd
    _PANDAS_VERSION = getattr(_pd, "__version__", "inconnu")
except ImportError:
    pass


def env_summary() -> str:
    """Retourne un resume une ligne de l'environnement detecte."""
    arrow_status = "OK" if _PYARROW_OK else "match_substring_regex ABSENT"
    return (
        f"pandas={_PANDAS_VERSION} "
        f"pyarrow={_PYARROW_VERSION} ({arrow_status})"
    )


# ── patch global pandas ─────────────────────────────────────────────────

_PATCHED: bool = False


def patch_pandas_arrow_backend() -> bool:
    """Force pandas a utiliser le backend string « python » si pyarrow est
    incomplet.

    Retourne True si le patch a ete applique, False sinon.
    Idempotent : appels multiples sans effet supplementaire.
    """
    global _PATCHED
    if _PATCHED:
        return False

    _log.debug(
        "lib_compat.patch_pandas_arrow_backend: %s", env_summary()
    )

    if _PYARROW_OK:
        _log.info(
            "lib_compat: pyarrow %s OK, pas de patch necessaire",
            _PYARROW_VERSION,
        )
        _PATCHED = True
        return False

    try:
        import pandas as pd

        if hasattr(pd.options.mode, "string_storage"):
            pd.options.mode.string_storage = "python"
            _log.warning(
                "lib_compat: pyarrow %s incomplet, "
                "string_storage force a 'python'",
                _PYARROW_VERSION,
            )
            _PATCHED = True
            return True

        _log.info(
            "lib_compat: pandas %s sans option string_storage, "
            "pas de patch necessaire",
            _PANDAS_VERSION,
        )
    except Exception:
        _log.exception("lib_compat: erreur lors du patch pandas")

    _PATCHED = True
    return False


# ── lecteur XLSX stdlib (thread-safe, sans openpyxl) ─────────────────────

_XLSX_NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
_XLSX_REL_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
_RE_CELL_COL = _re.compile(r"^([A-Za-z]+)")

_OPENPYXL_SAFE: bool = False


def _xlsx_tag(local: str) -> str:
    return f"{{{_XLSX_NS}}}{local}"


def _xlsx_col_index(letters: str) -> int:
    idx = 0
    for ch in letters.upper():
        idx = idx * 26 + (ord(ch) - ord("A") + 1)
    return idx - 1


def _xlsx_cell_col(cell_ref: str) -> int:
    m = _RE_CELL_COL.match(cell_ref or "")
    return _xlsx_col_index(m.group(1)) if m else 0


def _xlsx_shared_strings(zf: _zipfile.ZipFile) -> list:
    try:
        root = _ET.fromstring(zf.read("xl/sharedStrings.xml"))
    except KeyError:
        return []
    out = []
    for si in root.findall(f".//{_xlsx_tag('si')}"):
        parts = []
        for t in si.findall(f".//{_xlsx_tag('t')}"):
            if t.text:
                parts.append(t.text)
        out.append("".join(parts))
    return out


def _xlsx_sheet_path(zf: _zipfile.ZipFile, sheet_name: str) -> Optional[str]:
    wb = _ET.fromstring(zf.read("xl/workbook.xml"))
    rid = None
    for sh in wb.findall(f".//{_xlsx_tag('sheet')}"):
        if sh.get("name") == sheet_name:
            rid = sh.get(f"{{{_XLSX_REL_NS}}}id")
            break
    if not rid:
        return None
    rels = _ET.fromstring(zf.read("xl/_rels/workbook.xml.rels"))
    for rel in rels:
        if rel.get("Id") == rid:
            target = rel.get("Target", "")
            if target.startswith("/"):
                return target.lstrip("/")
            if target.startswith("xl/"):
                return target
            return "xl/" + target
    return None


def _xlsx_cell_value(c, shared: list):
    if c.get("t") == "inlineStr":
        is_el = c.find(_xlsx_tag("is"))
        if is_el is not None:
            parts = []
            for t in is_el.findall(f".//{_xlsx_tag('t')}"):
                if t.text:
                    parts.append(t.text)
            if parts:
                return "".join(parts)
        return None
    v_el = c.find(_xlsx_tag("v"))
    if v_el is None or v_el.text is None:
        return None
    if c.get("t") == "s":
        idx = int(v_el.text)
        return shared[idx] if 0 <= idx < len(shared) else v_el.text
    if c.get("t") == "b":
        return v_el.text == "1"
    raw = v_el.text
    try:
        return int(raw) if raw.isdigit() else float(raw)
    except (ValueError, AttributeError):
        return raw


def list_xlsx_sheet_names_stdlib(filepath: str) -> list:
    """Liste les onglets d'un .xlsx sans openpyxl (thread-safe)."""
    with _zipfile.ZipFile(filepath, "r") as zf:
        wb = _ET.fromstring(zf.read("xl/workbook.xml"))
        return [
            sh.get("name")
            for sh in wb.findall(f".//{_xlsx_tag('sheet')}")
            if sh.get("name")
        ]


def _xlsx_xf_bold_flags(zf: _zipfile.ZipFile) -> list:
    """Pour chaque index de style cellule (attribut ``s``), indique si le font est gras."""
    try:
        root = _ET.fromstring(zf.read("xl/styles.xml"))
    except KeyError:
        return []

    fonts_parent = root.find(_xlsx_tag("fonts"))
    font_bold: list = []
    if fonts_parent is not None:
        for font in fonts_parent.findall(_xlsx_tag("font")):
            b_el = font.find(_xlsx_tag("b"))
            if b_el is None:
                font_bold.append(False)
            else:
                val = b_el.get("val")
                if val is None:
                    font_bold.append(True)
                else:
                    font_bold.append(val.lower() not in ("0", "false"))

    xfs_parent = root.find(_xlsx_tag("cellXfs"))
    xf_bold: list = []
    if xfs_parent is not None:
        for xf in xfs_parent.findall(_xlsx_tag("xf")):
            fid = int(xf.get("fontId", "0"))
            xf_bold.append(font_bold[fid] if fid < len(font_bold) else False)
    return xf_bold


def _xlsx_cell_bold(c, xf_bold: list) -> bool:
    s_attr = c.get("s")
    if s_attr is None or not str(s_attr).isdigit():
        return False
    idx = int(s_attr)
    return idx < len(xf_bold) and bool(xf_bold[idx])


def read_xlsx_sheet_with_bold_stdlib(filepath: str, sheet_name: str) -> list:
    """Lit une feuille : chaque cellule est ``(valeur, is_bold)``.

    Thread-safe, sans openpyxl. Pour Police C6 (gras colonne câble).
    """
    rows_by_num = {}
    max_row = 0
    with _zipfile.ZipFile(filepath, "r") as zf:
        sheet_path = _xlsx_sheet_path(zf, sheet_name)
        if not sheet_path:
            return []
        shared = _xlsx_shared_strings(zf)
        xf_bold = _xlsx_xf_bold_flags(zf)
        root = _ET.fromstring(zf.read(sheet_path))
        for row_el in root.findall(f".//{_xlsx_tag('row')}"):
            r_attr = row_el.get("r")
            if r_attr:
                row_num = int(r_attr)
            else:
                row_num = max_row + 1 if max_row else 1
            max_row = max(max_row, row_num)
            cells = {}
            max_col = -1
            for c in row_el.findall(_xlsx_tag("c")):
                ref = c.get("r") or ""
                col = _xlsx_cell_col(ref)
                max_col = max(max_col, col)
                cells[col] = (
                    _xlsx_cell_value(c, shared),
                    _xlsx_cell_bold(c, xf_bold),
                )
            if max_col < 0:
                rows_by_num[row_num] = []
            else:
                rows_by_num[row_num] = [
                    cells.get(i, (None, False)) for i in range(max_col + 1)
                ]
    return [rows_by_num.get(i, []) for i in range(1, max_row + 1)]


def read_xlsx_sheet_stdlib(filepath: str, sheet_name: str) -> list:
    """Lit une feuille .xlsx en lignes de valeurs sans openpyxl/lxml.

    A utiliser dans les QgsTask sous Windows (QGIS 3.40+) pour eviter le
    crash « access violation » dans openpyxl.reader.excel.read_manifest.

    Returns:
        Liste indexee comme openpyxl (index 0 = ligne 1 du classeur).
    """
    rows_by_num = {}
    max_row = 0
    with _zipfile.ZipFile(filepath, "r") as zf:
        sheet_path = _xlsx_sheet_path(zf, sheet_name)
        if not sheet_path:
            return []
        shared = _xlsx_shared_strings(zf)
        root = _ET.fromstring(zf.read(sheet_path))
        for row_el in root.findall(f".//{_xlsx_tag('row')}"):
            r_attr = row_el.get("r")
            if r_attr:
                row_num = int(r_attr)
            else:
                row_num = max_row + 1 if max_row else 1
            max_row = max(max_row, row_num)
            cells = {}
            max_col = -1
            for c in row_el.findall(_xlsx_tag("c")):
                ref = c.get("r") or ""
                col = _xlsx_cell_col(ref)
                max_col = max(max_col, col)
                cells[col] = _xlsx_cell_value(c, shared)
            if max_col < 0:
                rows_by_num[row_num] = []
            else:
                rows_by_num[row_num] = [cells.get(i) for i in range(max_col + 1)]
    return [rows_by_num.get(i, []) for i in range(1, max_row + 1)]


def xlsx_cell_1based(rows: list, row: int, col: int):
    """Valeur cellule comme ``openpyxl`` (lignes/colonnes 1-based)."""
    if row < 1 or row > len(rows):
        return None
    line = rows[row - 1]
    idx = col - 1
    if idx < 0 or idx >= len(line):
        return None
    return line[idx]


def read_xlsx_first_sheet_stdlib(filepath: str) -> tuple:
    """Retourne (nom_premiere_feuille, lignes) ou (None, [])."""
    names = list_xlsx_sheet_names_stdlib(filepath)
    if not names:
        return None, []
    name = names[0]
    return name, read_xlsx_sheet_stdlib(filepath, name)


# ── openpyxl / lxml (exports Excel sur thread principal) ──────────────────


def ensure_openpyxl_safe_backend() -> bool:
    """Force openpyxl a utiliser xml.etree si pas encore importe avec lxml."""
    global _OPENPYXL_SAFE
    import os

    os.environ.setdefault("OPENPYXL_LXML", "False")

    try:
        import openpyxl as _ox

        lxml_on = bool(getattr(_ox, "LXML", False))
        if lxml_on:
            _log.warning(
                "lib_compat: openpyxl utilise encore lxml (LXML=True). "
                "Redemarrer QGIS apres mise a jour du plugin."
            )
            _OPENPYXL_SAFE = False
            return False
    except ImportError:
        _OPENPYXL_SAFE = True
        return True

    _OPENPYXL_SAFE = True
    return True


def read_excel_sheet(
    filepath: str,
    sheet_name: str,
    header: int = 0,
    names=None,
):
    """Lit une feuille .xlsx en DataFrame sans ``pd.ExcelFile`` ni openpyxl."""
    import pandas as pd

    rows = read_xlsx_sheet_stdlib(filepath, sheet_name)
    if not rows:
        return pd.DataFrame()
    hdr = list(names) if names is not None else None
    data = []
    for row_idx, row in enumerate(rows):
        if row_idx == header:
            if hdr is None:
                hdr = list(row)
        elif row_idx > header:
            data.append(row)
    if hdr is None:
        return pd.DataFrame()
    return pd.DataFrame(data, columns=hdr)


def safe_str_contains(series, substring: str) -> int:
    """Compte les valeurs de *series* contenant *substring* (sans pyarrow)."""
    return int(series.astype(str).apply(lambda v: substring in v).sum())
