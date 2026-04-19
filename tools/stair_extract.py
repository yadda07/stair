"""
ST-AIR : outil d'extraction read-only pour ingenierie du fork sous-traitant.

Lit la base PostgreSQL NGE en transaction READ ONLY STRICTE, produit :
  - <out>/discovery.json                : schemas, tables, fonctions, triggers listes
  - <out>/functions/<schema>__<name>.sql : pg_get_functiondef de chaque fonction
  - <out>/triggers/<schema>__<name>.sql  : pg_get_triggerdef de chaque trigger
  - <out>/reference_data/*.csv          : 7 tables comac (cables, hypothese, ...)
  - <out>/projets/<SRO>/<SRO>.gpkg      : 3 SRO varies (petit/moyen/gros) en GPKG multi-couches
  - <out>/analysis_report.md            : synthese automatique

La connexion utilise PGPASSWORD via env pour ogr2ogr (pas de password en CLI).
Aucune ecriture cote BDD : la session est readonly=True + SET TRANSACTION READ ONLY.

Usage console Python QGIS :
    import sys
    sys.path.insert(0, r'C:\\Users\\yadda\\AppData\\Roaming\\QGIS\\QGIS4\\profiles\\default\\python\\plugins\\PoleAerien_shp\\tools')
    import stair_extract
    stair_extract.run(out_dir=r'C:\\tmp\\stair_samples')

Usage CLI (hors QGIS) :
    set PG_HOST=...
    set PG_PORT=5432
    set PG_DATABASE=...
    set PG_USER=...
    set PG_PASSWORD=...
    python stair_extract.py C:\\tmp\\stair_samples
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

try:
    import psycopg2
    from psycopg2 import sql as pgsql
except ImportError:
    print("ERREUR : psycopg2 absent. Installer : pip install psycopg2-binary", file=sys.stderr)
    raise

SCHEMAS_TARGET = ("rip_avg_nge", "comac")
REFERENCE_TABLES = (
    "cables",
    "cable_capacites_possibles",
    "hypothese",
    "communes",
    "supports",
    "armements",
    "sro_nge_axione",
)
SRO_LAYERS = (
    ("infra_pt_pot", "SELECT * FROM rip_avg_nge.infra_pt_pot WHERE sro ILIKE %(pat)s"),
    ("infra_pt_chb", "SELECT * FROM rip_avg_nge.infra_pt_chb WHERE sro ILIKE %(pat)s"),
    ("infra_pt_autres", "SELECT * FROM rip_avg_nge.infra_pt_autres WHERE sro ILIKE %(pat)s"),
    ("etude_cap_ft", "SELECT * FROM rip_avg_nge.etude_cap_ft WHERE sro ILIKE %(pat)s"),
    ("etude_comac", "SELECT * FROM rip_avg_nge.etude_comac WHERE sro ILIKE %(pat)s"),
    ("bpe", "SELECT * FROM rip_avg_nge.bpe WHERE sro ILIKE %(pat)s"),
    ("attaches", "SELECT * FROM rip_avg_nge.attaches WHERE sro ILIKE %(pat)s"),
    ("cables_dc", "SELECT * FROM rip_avg_nge.fddcpiax(%(sro)s, 0.70)"),
)


# ---------------------------------------------------------------------------
# credentials
# ---------------------------------------------------------------------------
def _params_from_qgis() -> Optional[Dict[str, Any]]:
    try:
        from qgis.core import QgsSettings
    except ImportError:
        return None
    s = QgsSettings()
    s.beginGroup("PostgreSQL/connections")
    names = s.childGroups()
    s.endGroup()
    for name in names:
        pref = f"PostgreSQL/connections/{name}"
        db = s.value(f"{pref}/database", "")
        if "auvergne" in name.lower() or "auvergne" in str(db).lower():
            return _read_qgis_params(pref, name)
    return None


def _read_qgis_params(pref: str, name: str) -> Dict[str, Any]:
    from qgis.core import QgsSettings, QgsApplication
    s = QgsSettings()
    params = {
        "host": s.value(f"{pref}/host", ""),
        "port": int(s.value(f"{pref}/port", 5432) or 5432),
        "database": s.value(f"{pref}/database", ""),
        "user": s.value(f"{pref}/username", ""),
        "password": s.value(f"{pref}/password", ""),
        "connection_name": name,
    }
    if not params["password"]:
        authcfg = s.value(f"{pref}/authcfg", "")
        if authcfg:
            cfg = QgsApplication.authManager().configAuthMethodConfig(authcfg)
            if cfg:
                params["user"] = cfg.config("username", params["user"]) or params["user"]
                params["password"] = cfg.config("password", "") or ""
    return params


def _params_from_env() -> Optional[Dict[str, Any]]:
    keys = ("PG_HOST", "PG_PORT", "PG_DATABASE", "PG_USER", "PG_PASSWORD")
    if not all(os.environ.get(k) for k in keys):
        return None
    return {
        "host": os.environ["PG_HOST"],
        "port": int(os.environ["PG_PORT"]),
        "database": os.environ["PG_DATABASE"],
        "user": os.environ["PG_USER"],
        "password": os.environ["PG_PASSWORD"],
        "connection_name": "env",
    }


def _resolve_params() -> Dict[str, Any]:
    params = _params_from_qgis() or _params_from_env()
    if not params:
        raise RuntimeError(
            "Aucune connexion PostgreSQL trouvee. "
            "Configure une connexion 'Auvergne' dans QGIS, "
            "ou exporte PG_HOST/PG_PORT/PG_DATABASE/PG_USER/PG_PASSWORD."
        )
    if not params.get("password"):
        env_pwd = os.environ.get("PG_PASSWORD")
        if env_pwd:
            params["password"] = env_pwd
    if not params.get("user"):
        env_usr = os.environ.get("PG_USER")
        if env_usr:
            params["user"] = env_usr
    missing = [k for k in ("host", "database", "user", "password") if not params.get(k)]
    if missing:
        raise RuntimeError(
            f"Parametres incomplets, manquant : {missing}. "
            f"Config QGIS trouvee mais champs vides, ou vars d'env absentes."
        )
    return params


# ---------------------------------------------------------------------------
# connexion read-only
# ---------------------------------------------------------------------------
def _connect_readonly(params: Dict[str, Any]):
    conn = psycopg2.connect(
        host=params["host"],
        port=params["port"],
        database=params["database"],
        user=params["user"],
        password=params["password"],
        application_name="stair_extract_readonly",
    )
    # autocommit = True pour qu'une erreur sur une requete n'aborte pas les suivantes.
    # default_transaction_read_only = on : toute transaction implicite est read-only
    # cote serveur -> INSERT/UPDATE/DELETE/DDL rejetes par PostgreSQL.
    conn.autocommit = True
    with conn.cursor() as cur:
        cur.execute("SET SESSION default_transaction_read_only = on")
    return conn


# ---------------------------------------------------------------------------
# discovery
# ---------------------------------------------------------------------------
def _list_tables(conn) -> List[Dict[str, Any]]:
    query = """
        SELECT table_schema, table_name, table_type
        FROM information_schema.tables
        WHERE table_schema = ANY(%s)
        ORDER BY table_schema, table_name
    """
    with conn.cursor() as cur:
        cur.execute(query, (list(SCHEMAS_TARGET),))
        rows = cur.fetchall()
    return [{"schema": r[0], "name": r[1], "type": r[2]} for r in rows]


def _list_columns(conn, schema: str, table: str) -> List[Dict[str, Any]]:
    query = """
        SELECT column_name, data_type, is_nullable, character_maximum_length
        FROM information_schema.columns
        WHERE table_schema = %s AND table_name = %s
        ORDER BY ordinal_position
    """
    with conn.cursor() as cur:
        cur.execute(query, (schema, table))
        rows = cur.fetchall()
    return [
        {"name": r[0], "type": r[1], "nullable": r[2], "max_len": r[3]}
        for r in rows
    ]


def _list_functions(conn) -> List[Dict[str, Any]]:
    query = """
        SELECT p.oid, n.nspname, p.proname,
               pg_get_function_identity_arguments(p.oid) AS args,
               pg_get_function_result(p.oid) AS result
        FROM pg_proc p
        JOIN pg_namespace n ON n.oid = p.pronamespace
        WHERE n.nspname = ANY(%s)
        ORDER BY n.nspname, p.proname
    """
    with conn.cursor() as cur:
        cur.execute(query, (list(SCHEMAS_TARGET),))
        rows = cur.fetchall()
    return [
        {"oid": int(r[0]), "schema": r[1], "name": r[2], "args": r[3], "result": r[4]}
        for r in rows
    ]


def _list_triggers(conn) -> List[Dict[str, Any]]:
    query = """
        SELECT t.oid, t.tgrelid::regclass::text AS table_name, t.tgname
        FROM pg_trigger t
        WHERE NOT t.tgisinternal
          AND t.tgrelid::regclass::text ~ ANY(%s)
        ORDER BY 2, 3
    """
    patterns = [f"^{s}\\." for s in SCHEMAS_TARGET]
    with conn.cursor() as cur:
        cur.execute(query, (patterns,))
        rows = cur.fetchall()
    return [{"oid": int(r[0]), "table": r[1], "name": r[2]} for r in rows]


def _row_counts(conn, tables: List[Dict[str, Any]]) -> Dict[str, int]:
    counts: Dict[str, int] = {}
    for t in tables:
        if t["type"] != "BASE TABLE":
            continue
        fq = f'"{t["schema"]}"."{t["name"]}"'
        try:
            with conn.cursor() as cur:
                cur.execute(f"SELECT COUNT(*) FROM {fq}")
                counts[f"{t['schema']}.{t['name']}"] = int(cur.fetchone()[0])
        except Exception as e:
            print(f"  WARN count {fq} : {e}")
            counts[f"{t['schema']}.{t['name']}"] = -1
    return counts


def _build_discovery(conn) -> Dict[str, Any]:
    tables = _list_tables(conn)
    out: Dict[str, Any] = {
        "schemas": list(SCHEMAS_TARGET),
        "tables": tables,
        "columns": {},
        "functions": _list_functions(conn),
        "triggers": _list_triggers(conn),
        "row_counts": _row_counts(conn, tables),
    }
    for t in tables:
        key = f"{t['schema']}.{t['name']}"
        out["columns"][key] = _list_columns(conn, t["schema"], t["name"])
    return out


# ---------------------------------------------------------------------------
# dump SQL (functions + triggers)
# ---------------------------------------------------------------------------
def _dump_function(conn, func: Dict[str, Any], out_dir: Path) -> Optional[Path]:
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT pg_get_functiondef(%s)", (func["oid"],))
            row = cur.fetchone()
        if not row:
            return None
    except Exception as e:
        print(f"  WARN functiondef {func['schema']}.{func['name']} : {e}")
        return None
    safe = f"{func['schema']}__{func['name']}.sql".replace("/", "_").replace("\\", "_")
    path = out_dir / safe
    path.write_text(row[0], encoding="utf-8")
    return path


def _dump_all_functions(conn, functions: List[Dict[str, Any]], out_dir: Path) -> int:
    out_dir.mkdir(parents=True, exist_ok=True)
    n = 0
    for f in functions:
        if _dump_function(conn, f, out_dir) is not None:
            n += 1
    return n


def _dump_trigger(conn, trig: Dict[str, Any], out_dir: Path) -> Optional[Path]:
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT pg_get_triggerdef(%s)", (trig["oid"],))
            row = cur.fetchone()
        if not row:
            return None
    except Exception as e:
        print(f"  WARN triggerdef {trig['table']}.{trig['name']} : {e}")
        return None
    safe_tbl = trig["table"].replace(".", "__").replace('"', "")
    path = out_dir / f"{safe_tbl}__{trig['name']}.sql"
    path.write_text(row[0], encoding="utf-8")
    return path


def _dump_all_triggers(conn, triggers: List[Dict[str, Any]], out_dir: Path) -> int:
    out_dir.mkdir(parents=True, exist_ok=True)
    n = 0
    for t in triggers:
        if _dump_trigger(conn, t, out_dir) is not None:
            n += 1
    return n


# ---------------------------------------------------------------------------
# reference_data CSV
# ---------------------------------------------------------------------------
def _export_reference_table(conn, table: str, out_dir: Path) -> Optional[Path]:
    out = out_dir / f"{table}.csv"
    sql_copy = pgsql.SQL(
        "COPY (SELECT * FROM comac.{tbl}) TO STDOUT "
        "WITH (FORMAT CSV, HEADER, DELIMITER ';', ENCODING 'UTF8')"
    ).format(tbl=pgsql.Identifier(table))
    try:
        with conn.cursor() as cur, open(out, "wb") as f:
            cur.copy_expert(sql_copy, f)
        return out
    except Exception as e:
        print(f"  WARN export comac.{table} : {str(e).strip()}")
        try:
            conn.rollback()
        except Exception:
            pass
        if out.exists():
            out.unlink()
        return None


def _export_reference_data(conn, out_dir: Path) -> Dict[str, Any]:
    out_dir.mkdir(parents=True, exist_ok=True)
    exported: Dict[str, Any] = {}
    for tbl in REFERENCE_TABLES:
        path = _export_reference_table(conn, tbl, out_dir)
        exported[tbl] = {
            "path": str(path) if path else None,
            "sha256": _sha256(path) if path else None,
        }
    _write_version_file(out_dir, exported)
    return exported


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _write_version_file(out_dir: Path, exported: Dict[str, Any]) -> None:
    lines = [f"# reference_data exporte le {dt.datetime.now().isoformat(timespec='seconds')}"]
    for tbl, info in exported.items():
        if info["sha256"]:
            lines.append(f"{tbl}.csv sha256={info['sha256']}")
        else:
            lines.append(f"{tbl}.csv MANQUANT")
    (out_dir / "VERSION.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")


# ---------------------------------------------------------------------------
# selection SRO
# ---------------------------------------------------------------------------
def _pick_sros(conn, n: int = 3) -> List[Tuple[str, int]]:
    query = """
        SELECT sro, COUNT(*) AS n
        FROM rip_avg_nge.infra_pt_pot
        WHERE sro IS NOT NULL AND sro <> ''
        GROUP BY sro
        HAVING COUNT(*) BETWEEN 20 AND 5000
        ORDER BY n
    """
    try:
        conn.rollback()
    except Exception:
        pass
    with conn.cursor() as cur:
        cur.execute(query)
        rows = cur.fetchall()
    if not rows:
        return []
    if len(rows) <= n:
        return [(r[0], int(r[1])) for r in rows]
    idx = [int(i * (len(rows) - 1) / (n - 1)) for i in range(n)]
    return [(rows[i][0], int(rows[i][1])) for i in idx]


# ---------------------------------------------------------------------------
# export SRO via ogr2ogr
# ---------------------------------------------------------------------------
def _find_ogr2ogr() -> str:
    exe = shutil.which("ogr2ogr")
    if exe:
        return exe
    candidates = [
        r"C:\Program Files\QGIS 3.44.0\bin\ogr2ogr.exe",
        r"C:\Program Files\QGIS 3.40.0\bin\ogr2ogr.exe",
        r"C:\Program Files\QGIS 3.34.0\bin\ogr2ogr.exe",
        r"C:\OSGeo4W\bin\ogr2ogr.exe",
        r"C:\OSGeo4W64\bin\ogr2ogr.exe",
    ]
    for c in candidates:
        if os.path.exists(c):
            return c
    raise RuntimeError(
        "ogr2ogr introuvable. Ajouter OSGeo4W\\bin au PATH ou installer QGIS."
    )


def _ogr_pg_conn_string(params: Dict[str, Any]) -> str:
    return (
        f"PG:host={params['host']} port={params['port']} "
        f"dbname={params['database']} user={params['user']} "
        f"active_schema=rip_avg_nge"
    )


def _ogr_env(params: Dict[str, Any]) -> Dict[str, str]:
    env = os.environ.copy()
    env["PGPASSWORD"] = params["password"]
    env["PG_USE_COPY"] = "YES"
    env["PGOPTIONS"] = "-c default_transaction_read_only=on"
    return env


def _export_sro_layer(
    sro: str, layer: str, query: str, gpkg: Path,
    append: bool, params: Dict[str, Any], ogr: str
) -> bool:
    pg = _ogr_pg_conn_string(params)
    if "%(sro)s" in query:
        sql_q = query.replace("%(sro)s", f"'{sro}'")
    else:
        sql_q = query.replace("%(pat)s", f"'%{sro}%'")
    cmd = [ogr]
    if append:
        cmd.append("-append")
    cmd += [
        "-f", "GPKG", str(gpkg), pg,
        "-sql", sql_q,
        "-nln", layer,
        "-lco", "SPATIAL_INDEX=YES",
    ]
    try:
        res = subprocess.run(
            cmd, env=_ogr_env(params), capture_output=True, text=True, check=False
        )
    except Exception as e:
        print(f"  WARN ogr2ogr {layer} : {e}")
        return False
    if res.returncode != 0:
        print(f"  WARN ogr2ogr {layer} rc={res.returncode} : {res.stderr.strip()[:300]}")
        return False
    return True


def _export_sro(sro: str, out_dir: Path, params: Dict[str, Any]) -> Dict[str, Any]:
    ogr = _find_ogr2ogr()
    safe = sro.replace("/", "-").replace("\\", "-").replace(":", "-")
    proj = out_dir / safe
    proj.mkdir(parents=True, exist_ok=True)
    gpkg = proj / f"{safe}.gpkg"
    if gpkg.exists():
        gpkg.unlink()
    results: Dict[str, bool] = {}
    for i, (layer, query) in enumerate(SRO_LAYERS):
        ok = _export_sro_layer(sro, layer, query, gpkg, append=(i > 0), params=params, ogr=ogr)
        results[layer] = ok
    return {"sro": sro, "gpkg": str(gpkg), "layers": results}


# ---------------------------------------------------------------------------
# rapport
# ---------------------------------------------------------------------------
def _fmt_table_md(rows: List[List[str]]) -> str:
    if not rows:
        return "(vide)"
    w = [max(len(str(r[i])) for r in rows) for i in range(len(rows[0]))]
    lines = ["| " + " | ".join(str(r[i]).ljust(w[i]) for i in range(len(r))) + " |" for r in rows]
    sep = "|-" + "-|-".join("-" * wi for wi in w) + "-|"
    return "\n".join([lines[0], sep] + lines[1:])


def _report(out_dir: Path, discovery: Dict[str, Any], sros: List[Dict[str, Any]],
            params: Dict[str, Any]) -> Path:
    path = out_dir / "analysis_report.md"
    lines: List[str] = []
    lines.append("# ST-AIR : rapport d'extraction read-only")
    lines.append("")
    lines.append(f"Date : {dt.datetime.now().isoformat(timespec='seconds')}")
    lines.append(f"Cible : `{params['host']}:{params['port']}/{params['database']}` user=`{params['user']}`")
    lines.append(f"Connexion : `{params.get('connection_name', '?')}`")
    lines.append("Session : `readonly=True` + `SET TRANSACTION READ ONLY`")
    lines.append("")
    lines.append("## Tables (par schema)")
    lines.append("")
    rows: List[List[str]] = [["schema", "table", "type", "rows"]]
    for t in discovery["tables"]:
        k = f"{t['schema']}.{t['name']}"
        rows.append([t["schema"], t["name"], t["type"], str(discovery["row_counts"].get(k, "-"))])
    lines.append(_fmt_table_md(rows))
    lines.append("")
    lines.append(f"## Fonctions ({len(discovery['functions'])})")
    lines.append("")
    for f in discovery["functions"]:
        lines.append(f"- `{f['schema']}.{f['name']}({f['args']})` -> `{f['result']}`")
    lines.append("")
    lines.append(f"## Triggers ({len(discovery['triggers'])})")
    lines.append("")
    for t in discovery["triggers"]:
        lines.append(f"- `{t['table']}` : `{t['name']}`")
    lines.append("")
    lines.append("## Echantillons SRO")
    lines.append("")
    for s in sros:
        ok = sum(1 for v in s["layers"].values() if v)
        tot = len(s["layers"])
        lines.append(f"- `{s['sro']}` -> `{s['gpkg']}` ({ok}/{tot} couches OK)")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# orchestration
# ---------------------------------------------------------------------------
def run(out_dir: str, nb_sros: int = 3, explicit_sros: Optional[List[str]] = None) -> Path:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    (out / "functions").mkdir(exist_ok=True)
    (out / "triggers").mkdir(exist_ok=True)
    (out / "reference_data").mkdir(exist_ok=True)
    (out / "projets").mkdir(exist_ok=True)

    params = _resolve_params()
    print(f"[stair_extract] cible : {params['host']}:{params['port']}/{params['database']} "
          f"user={params['user']} (connection_name={params.get('connection_name')})")

    with _connect_readonly(params) as conn:
        print("[stair_extract] discovery...")
        disco = _build_discovery(conn)
        (out / "discovery.json").write_text(
            json.dumps(disco, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        print(f"  tables={len(disco['tables'])} functions={len(disco['functions'])} "
              f"triggers={len(disco['triggers'])}")

        print("[stair_extract] dump fonctions SQL...")
        n_fn = _dump_all_functions(conn, disco["functions"], out / "functions")
        print(f"  {n_fn} fonctions ecrites")

        print("[stair_extract] dump triggers SQL...")
        n_tr = _dump_all_triggers(conn, disco["triggers"], out / "triggers")
        print(f"  {n_tr} triggers ecrits")

        print("[stair_extract] export reference_data (comac.*)...")
        _export_reference_data(conn, out / "reference_data")

        if explicit_sros:
            chosen = [(s, -1) for s in explicit_sros]
        else:
            print("[stair_extract] selection SRO varies...")
            chosen = _pick_sros(conn, nb_sros)
        print(f"  SRO retenus : {chosen}")

    results: List[Dict[str, Any]] = []
    for sro, n in chosen:
        print(f"[stair_extract] export SRO {sro} (n={n}) via ogr2ogr...")
        results.append(_export_sro(sro, out / "projets", params))

    report_path = _report(out, disco, results, params)
    print(f"[stair_extract] rapport : {report_path}")
    return report_path


def _main_cli() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    out = sys.argv[1]
    nb = int(sys.argv[2]) if len(sys.argv) > 2 else 3
    run(out, nb_sros=nb)
    return 0


if __name__ == "__main__":
    sys.exit(_main_cli())
