"""
Analyse integrale d'un livrable sous-traitant ST-AIR.
Inspecte :
- Shapefiles (fields + dtypes + 3 exemples) via osgeo.ogr
- XLSX (sheets + colonnes + 3 lignes) via openpyxl
- CSV (encoding, delimiter, colonnes, 3 lignes) via heuristique

Usage :
    python analyse_livrable.py <dossier_livrable> <rapport.md>
"""
from __future__ import annotations

import csv
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

try:
    from osgeo import ogr
    ogr.UseExceptions()
except ImportError:
    ogr = None

try:
    from openpyxl import load_workbook
except ImportError:
    load_workbook = None


# ---------------------------------------------------------------------------
# shapefile
# ---------------------------------------------------------------------------
def _shp_fields(path: Path) -> Optional[Dict[str, Any]]:
    if ogr is None:
        return {"error": "osgeo.ogr indisponible"}
    try:
        ds = ogr.Open(str(path))
        if ds is None:
            return {"error": "Open() renvoie None"}
        lyr = ds.GetLayer(0)
        ld = lyr.GetLayerDefn()
        fields: List[Dict[str, Any]] = []
        for i in range(ld.GetFieldCount()):
            fd = ld.GetFieldDefn(i)
            fields.append({
                "name": fd.GetName(),
                "type": fd.GetTypeName(),
                "width": fd.GetWidth(),
                "precision": fd.GetPrecision(),
            })
        rows: List[Dict[str, Any]] = []
        lyr.ResetReading()
        for i, feat in enumerate(lyr):
            if i >= 3:
                break
            rows.append({f["name"]: feat.GetField(f["name"]) for f in fields})
        srs = lyr.GetSpatialRef()
        srs_txt = srs.GetAuthorityCode(None) if srs else None
        geom = ogr.GeometryTypeToName(lyr.GetGeomType())
        return {
            "feature_count": lyr.GetFeatureCount(),
            "geom_type": geom,
            "srs_epsg": srs_txt,
            "fields": fields,
            "sample": rows,
        }
    except Exception as e:
        return {"error": str(e)}


# ---------------------------------------------------------------------------
# xlsx
# ---------------------------------------------------------------------------
def _xlsx_summary(path: Path) -> Dict[str, Any]:
    if load_workbook is None:
        return {"error": "openpyxl indisponible"}
    try:
        wb = load_workbook(filename=str(path), read_only=True, data_only=True)
    except Exception as e:
        return {"error": str(e)}
    out: Dict[str, Any] = {"sheets": []}
    for name in wb.sheetnames:
        ws = wb[name]
        rows_iter = ws.iter_rows(values_only=True)
        try:
            header = next(rows_iter, None) or ()
        except Exception as e:
            out["sheets"].append({"name": name, "error": str(e)})
            continue
        sample: List[Tuple[Any, ...]] = []
        for i, r in enumerate(rows_iter):
            if i >= 3:
                break
            sample.append(r)
        try:
            dim = ws.calculate_dimension()
        except Exception:
            dim = "?"
        out["sheets"].append({
            "name": name,
            "dim": dim,
            "max_row": ws.max_row,
            "max_col": ws.max_column,
            "header": list(header),
            "sample": [list(r) for r in sample],
        })
    wb.close()
    return out


# ---------------------------------------------------------------------------
# csv
# ---------------------------------------------------------------------------
def _sniff_csv(path: Path, max_bytes: int = 65536) -> Dict[str, Any]:
    encodings = ("utf-8-sig", "utf-8", "cp1252", "latin-1")
    data: Optional[str] = None
    chosen: Optional[str] = None
    for enc in encodings:
        try:
            with open(path, "r", encoding=enc, errors="strict") as f:
                data = f.read(max_bytes)
            chosen = enc
            break
        except UnicodeDecodeError:
            continue
    if data is None:
        return {"error": "encoding indetectable"}
    # sniff delimiter
    try:
        dialect = csv.Sniffer().sniff(data[:4096], delimiters=",;\t|")
        delim = dialect.delimiter
    except csv.Error:
        delim = ";" if data.count(";") > data.count(",") else ","
    lines = data.splitlines()
    if not lines:
        return {"encoding": chosen, "delimiter": delim, "rows": 0}
    reader = csv.reader(lines, delimiter=delim)
    rows = list(reader)
    return {
        "encoding": chosen,
        "delimiter": repr(delim),
        "header": rows[0] if rows else [],
        "n_cols": len(rows[0]) if rows else 0,
        "sample": rows[1:4] if len(rows) > 1 else [],
        "approx_lines_in_sample": len(rows),
    }


# ---------------------------------------------------------------------------
# rendu markdown
# ---------------------------------------------------------------------------
def _md_shp(path: Path, base: Path) -> str:
    info = _shp_fields(path)
    rel = path.relative_to(base).as_posix()
    lines = [f"### `{rel}`"]
    if "error" in info:
        lines.append(f"- ERREUR : {info['error']}")
        return "\n".join(lines) + "\n"
    lines.append(f"- features : {info['feature_count']}, geom : {info['geom_type']}, "
                 f"SRS EPSG : {info['srs_epsg']}")
    lines.append(f"- {len(info['fields'])} champs :")
    for f in info["fields"]:
        lines.append(f"  - `{f['name']}` : {f['type']}({f['width']},{f['precision']})")
    if info["sample"]:
        lines.append("- exemple (1er enregistrement) :")
        r0 = info["sample"][0]
        for k, v in r0.items():
            sv = str(v)
            if len(sv) > 80:
                sv = sv[:80] + "..."
            lines.append(f"  - `{k}` = `{sv}`")
    return "\n".join(lines) + "\n"


def _md_xlsx(path: Path, base: Path) -> str:
    info = _xlsx_summary(path)
    rel = path.relative_to(base).as_posix()
    lines = [f"### `{rel}`"]
    if "error" in info:
        lines.append(f"- ERREUR : {info['error']}")
        return "\n".join(lines) + "\n"
    for sh in info["sheets"]:
        if "error" in sh:
            lines.append(f"- sheet `{sh['name']}` : ERREUR {sh['error']}")
            continue
        lines.append(f"- sheet `{sh['name']}` : {sh['max_row']}x{sh['max_col']} "
                     f"(dim {sh['dim']})")
        hdr = sh["header"]
        if hdr:
            hdr_fmt = ", ".join(f"`{h}`" for h in hdr[:20])
            if len(hdr) > 20:
                hdr_fmt += f" ... (+{len(hdr) - 20})"
            lines.append(f"  - colonnes : {hdr_fmt}")
        for i, row in enumerate(sh["sample"]):
            preview = ", ".join(str(v) if v is not None else "" for v in row[:10])
            if len(preview) > 160:
                preview = preview[:160] + "..."
            lines.append(f"  - row{i + 1} : {preview}")
    return "\n".join(lines) + "\n"


def _md_csv(path: Path, base: Path) -> str:
    info = _sniff_csv(path)
    rel = path.relative_to(base).as_posix()
    size = path.stat().st_size
    lines = [f"### `{rel}` ({size} bytes)"]
    if "error" in info:
        lines.append(f"- ERREUR : {info['error']}")
        return "\n".join(lines) + "\n"
    lines.append(f"- encoding : `{info['encoding']}`, delimiter : {info['delimiter']}, "
                 f"n_cols : {info['n_cols']}")
    if info.get("header"):
        hdr_fmt = ", ".join(f"`{h}`" for h in info["header"][:30])
        if len(info["header"]) > 30:
            hdr_fmt += f" ... (+{len(info['header']) - 30})"
        lines.append(f"- colonnes : {hdr_fmt}")
    for i, row in enumerate(info.get("sample", [])):
        preview = " | ".join(row[:8])
        if len(preview) > 160:
            preview = preview[:160] + "..."
        lines.append(f"- row{i + 1} : {preview}")
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# exploration
# ---------------------------------------------------------------------------
def _walk_files(base: Path) -> Dict[str, List[Path]]:
    buckets: Dict[str, List[Path]] = {"shp": [], "xlsx": [], "csv": [], "txt": [], "other": []}
    for p in base.rglob("*"):
        if not p.is_file():
            continue
        ext = p.suffix.lower()
        if ext == ".shp":
            buckets["shp"].append(p)
        elif ext == ".xlsx":
            buckets["xlsx"].append(p)
        elif ext == ".csv":
            buckets["csv"].append(p)
        elif ext == ".txt":
            buckets["txt"].append(p)
        else:
            buckets["other"].append(p)
    return buckets


def analyse(base_path: str, out_md: str) -> None:
    base = Path(base_path)
    if not base.exists():
        raise FileNotFoundError(base)
    out = Path(out_md)
    out.parent.mkdir(parents=True, exist_ok=True)
    buckets = _walk_files(base)

    lines: List[str] = []
    lines.append(f"# Analyse du livrable : `{base.name}`")
    lines.append("")
    lines.append(f"Racine : `{base}`")
    lines.append("")
    lines.append("## Inventaire")
    lines.append("")
    lines.append(f"- {len(buckets['shp'])} shapefiles")
    lines.append(f"- {len(buckets['xlsx'])} xlsx")
    lines.append(f"- {len(buckets['csv'])} csv")
    lines.append(f"- {len(buckets['txt'])} txt")
    lines.append(f"- {len(buckets['other'])} autres (pdf, jpg, pcm, dbf, prj, cpg, shx, qgs, zip, qmd)")
    lines.append("")

    lines.append("## Shapefiles")
    lines.append("")
    for p in sorted(buckets["shp"]):
        lines.append(_md_shp(p, base))

    lines.append("## CSV")
    lines.append("")
    for p in sorted(buckets["csv"]):
        lines.append(_md_csv(p, base))

    lines.append("## XLSX")
    lines.append("")
    for p in sorted(buckets["xlsx"]):
        lines.append(_md_xlsx(p, base))

    lines.append("## Autres (liste)")
    lines.append("")
    for p in sorted(buckets["other"]):
        lines.append(f"- `{p.relative_to(base).as_posix()}` ({p.stat().st_size} bytes)")

    out.write_text("\n".join(lines), encoding="utf-8")
    print(f"[analyse_livrable] rapport : {out}")


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print(__doc__)
        sys.exit(2)
    analyse(sys.argv[1], sys.argv[2])
