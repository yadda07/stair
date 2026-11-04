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
Comparaison fddcpiax SQL vs splitter Python sur N SRO.

Lit les credentials depuis le fichier INI QGIS (jamais affiches).
Pour chaque SRO :
  1. Execute fddcpiax(sro) en SQL -> stats SQL
  2. Exporte les donnees brutes (cables, poteaux, bpe, attaches) -> GPKG temp
  3. Execute le splitter Python sur le GPKG -> stats Python
  4. Compare : nb segments, longueur totale, repartition posemode
Produit un CSV de synthese.
"""
from __future__ import annotations

import csv
import os
import sys
import tempfile
from pathlib import Path

import psycopg2

# Ajouter le plugin au path pour les imports
_PLUGIN_DIR = Path(__file__).resolve().parent.parent
if str(_PLUGIN_DIR.parent) not in sys.path:
    sys.path.insert(0, str(_PLUGIN_DIR.parent))

from PoleAerien_shp.gpkg_pipeline.dataclasses import (
    AttacheInput,
    BpeInput,
    CableInput,
    PoteauInput,
)
from PoleAerien_shp.gpkg_pipeline.splitter import split_cables

SCHEMA = "rip_avg_nge"
MAX_SRO = 20
INI_PATH = Path(
    r"C:\Users\yadda\AppData\Roaming\QGIS\QGIS4"
    r"\profiles\default\QGIS\QGIS4.ini"
)


def _read_pg_params() -> dict:
    """Lit les params PG depuis le fichier INI QGIS (jamais affiches)."""
    params = {}
    with open(INI_PATH, encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.strip()
            if line.startswith("connections\\AUVERGNE\\"):
                key_part = line.split("\\")[-1]
                if "=" in key_part:
                    k, v = key_part.split("=", 1)
                    params[k.strip()] = v.strip()
    return {
        "host": params.get("host", ""),
        "port": params.get("port", "5432"),
        "dbname": params.get("database", ""),
        "user": params.get("username", ""),
        "password": params.get("password", ""),
    }


def _connect(params: dict):
    return psycopg2.connect(
        host=params["host"],
        port=params["port"],
        dbname=params["dbname"],
        user=params["user"],
        password=params["password"],
        connect_timeout=10,
        options="-c search_path=rip_avg_nge,public",
    )


def _fetch_sro_list(conn, limit: int = MAX_SRO) -> list[str]:
    """Recupere N SRO distincts ayant des cables."""
    cur = conn.cursor()
    cur.execute(
        f'SELECT DISTINCT "sro" FROM "{SCHEMA}"."za_sro" '
        f'WHERE "sro" IS NOT NULL ORDER BY "sro" LIMIT %s',
        (limit,),
    )
    rows = cur.fetchall()
    cur.close()
    return [r[0] for r in rows if r[0]]


def _sql_stats(conn, sro: str) -> dict:
    """Execute fddcpiax et retourne les stats aggregees."""
    cur = conn.cursor()
    cur.execute(
        f'SELECT gid_dc2, gid, length, posemode '
        f'FROM "{SCHEMA}".fddcpiax(%s)',
        (sro,),
    )
    rows = cur.fetchall()
    cur.close()
    nb = len(rows)
    total_len = sum(r[2] or 0.0 for r in rows)
    pm_counts = {}
    for r in rows:
        pm = r[3] if r[3] is not None else -1
        pm_counts[pm] = pm_counts.get(pm, 0) + 1
    return {
        "nb_segments": nb,
        "total_length": round(total_len, 2),
        "pm_0": pm_counts.get(0, 0),
        "pm_1": pm_counts.get(1, 0),
        "pm_2": pm_counts.get(2, 0),
        "pm_4": pm_counts.get(4, 0),
    }


def _load_raw_from_db(conn, sro: str) -> tuple:
    """Charge les donnees brutes pour le splitter Python."""
    cur = conn.cursor()

    # Cables bruts (meme source que FDDCPFA mais simplifie : cables directs)
    cur.execute(
        f'SELECT gid, sro, ST_AsBinary(geom), cab_type, cab_capa, '
        f'cb_etiquet, cab_nature, affectation '
        f'FROM "{SCHEMA}".cables '
        f"WHERE sro ILIKE '%%' || %s || '%%' "
        f"AND ST_IsValid(geom) AND ST_Length(geom) > 0.2",
        (sro,),
    )
    cables = []
    for r in cur.fetchall():
        if r[2] is None:
            continue
        cables.append(CableInput(
            gid=r[0], sro=r[1] or "", wkb=bytes(r[2]),
            attrs={
                "cab_type": r[3] or "", "cab_capa": r[4] or 0,
                "cb_etiquet": r[5] or "", "cab_nature": r[6] or "",
                "affectation": r[7] or "",
            },
        ))

    # Poteaux
    cur.execute(
        f'SELECT gid, sro, ST_AsBinary(geom), inf_type '
        f'FROM "{SCHEMA}".infra_pt_pot '
        f"WHERE sro ILIKE '%%' || %s || '%%'",
        (sro,),
    )
    poteaux = []
    for r in cur.fetchall():
        if r[2] is None:
            continue
        poteaux.append(PoteauInput(
            gid=r[0], sro=r[1] or "", wkb=bytes(r[2]),
            inf_type=r[3],
        ))

    # BPE
    cur.execute(
        f'SELECT gid, sro, ST_AsBinary(geom), noe_type '
        f'FROM "{SCHEMA}".bpe '
        f"WHERE sro ILIKE '%%' || %s || '%%'",
        (sro,),
    )
    bpes = []
    for r in cur.fetchall():
        if r[2] is None:
            continue
        bpes.append(BpeInput(
            gid=r[0], sro=r[1] or "", wkb=bytes(r[2]),
            noe_type=r[3],
        ))

    # Attaches
    cur.execute(
        f'SELECT gid, sro, ST_AsBinary(geom) '
        f'FROM "{SCHEMA}".attaches '
        f"WHERE sro ILIKE '%%' || %s || '%%'",
        (sro,),
    )
    attaches = []
    for r in cur.fetchall():
        if r[2] is None:
            continue
        attaches.append(AttacheInput(
            gid=r[0], sro=r[1] or "", wkb=bytes(r[2]),
        ))

    cur.close()
    return cables, poteaux, bpes, attaches


def _python_stats(cables, poteaux, bpes, attaches, sro: str) -> dict:
    """Execute le splitter Python et retourne les stats."""
    if not cables:
        return {
            "nb_segments": 0, "total_length": 0.0,
            "pm_1": 0, "pm_4": 0, "retracted": 0,
        }
    segs, stats = split_cables(
        cables=cables, poteaux=poteaux, bpes=bpes, attaches=attaches,
        tolerance=0.70, min_segment_length=0.20, sro=sro,
    )
    pm_counts = {}
    for s in segs:
        pm_counts[s.posemode] = pm_counts.get(s.posemode, 0) + 1
    total_len = sum(s.length for s in segs)
    return {
        "nb_segments": len(segs),
        "total_length": round(total_len, 2),
        "pm_1": pm_counts.get(1, 0),
        "pm_4": pm_counts.get(4, 0),
        "retracted": stats.nb_cables_retracted,
    }


def main():
    params = _read_pg_params()
    if not params["host"]:
        print("ERREUR: impossible de lire les credentials QGIS")
        return 1

    print(f"Connexion {params['host']}:{params['port']}/{params['dbname']} "
          f"user={params['user']}")
    conn = _connect(params)
    print("Connecte.")

    sros = _fetch_sro_list(conn, MAX_SRO)
    print(f"{len(sros)} SRO trouves : {', '.join(sros[:5])}...")

    results = []
    for i, sro in enumerate(sros):
        print(f"\n[{i+1}/{len(sros)}] SRO={sro}")

        # SQL
        try:
            sql = _sql_stats(conn, sro)
            print(f"  SQL : {sql['nb_segments']} segs, "
                  f"{sql['total_length']:.0f}m, "
                  f"pm1={sql['pm_1']} pm4={sql['pm_4']}")
        except Exception as e:
            print(f"  SQL ERREUR: {e}")
            sql = {"nb_segments": -1, "total_length": 0,
                   "pm_0": 0, "pm_1": 0, "pm_2": 0, "pm_4": 0}

        # Python
        try:
            cables, poteaux, bpes, attaches = _load_raw_from_db(conn, sro)
            print(f"  Donnees: {len(cables)} cables, {len(poteaux)} pot, "
                  f"{len(bpes)} bpe, {len(attaches)} att")
            py = _python_stats(cables, poteaux, bpes, attaches, sro)
            print(f"  Python: {py['nb_segments']} segs, "
                  f"{py['total_length']:.0f}m, "
                  f"pm1={py['pm_1']} pm4={py['pm_4']} "
                  f"retracted={py['retracted']}")
        except Exception as e:
            print(f"  Python ERREUR: {e}")
            py = {"nb_segments": -1, "total_length": 0,
                  "pm_1": 0, "pm_4": 0, "retracted": 0}

        # Ecarts
        seg_diff = py["nb_segments"] - sql["nb_segments"]
        len_diff = py["total_length"] - sql["total_length"]
        pct_diff = (
            abs(seg_diff) / max(sql["nb_segments"], 1) * 100
        )
        print(f"  ECART: segs={seg_diff:+d} ({pct_diff:.1f}%), "
              f"longueur={len_diff:+.0f}m")

        results.append({
            "sro": sro,
            "sql_segs": sql["nb_segments"],
            "sql_len": sql["total_length"],
            "sql_pm0": sql["pm_0"],
            "sql_pm1": sql["pm_1"],
            "sql_pm2": sql["pm_2"],
            "sql_pm4": sql["pm_4"],
            "py_segs": py["nb_segments"],
            "py_len": py["total_length"],
            "py_pm1": py["pm_1"],
            "py_pm4": py["pm_4"],
            "py_retracted": py["retracted"],
            "diff_segs": seg_diff,
            "diff_len": round(len_diff, 2),
            "diff_pct": round(pct_diff, 1),
        })

    conn.close()

    # Export CSV
    out_dir = _PLUGIN_DIR / "tests"
    csv_path = out_dir / "compare_sql_vs_python_results.csv"
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=results[0].keys())
        w.writeheader()
        w.writerows(results)
    print(f"\nResultats: {csv_path}")

    # Resume
    print("\n===== RESUME =====")
    total_sql = sum(r["sql_segs"] for r in results if r["sql_segs"] > 0)
    total_py = sum(r["py_segs"] for r in results if r["py_segs"] > 0)
    print(f"Total SQL: {total_sql} segments")
    print(f"Total Python: {total_py} segments")
    if total_sql > 0:
        print(f"Ecart global: {(total_py - total_sql) / total_sql * 100:+.1f}%")
    big_diffs = [r for r in results if r["diff_pct"] > 20]
    if big_diffs:
        print(f"SRO avec ecart > 20% : {[r['sro'] for r in big_diffs]}")
    else:
        print("Aucun SRO avec ecart > 20%")

    return 0


if __name__ == "__main__":
    sys.exit(main())
