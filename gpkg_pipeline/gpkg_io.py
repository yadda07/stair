# Youcef ADDA | GEODECI
# FR : Plugins QGIS, solutions SIG et administration PostgreSQL/PostGIS ; audit, automatisation et accompagnement.
# EN : QGIS plugins, GIS solutions and PostgreSQL/PostGIS administration; audits, workflow automation and business support.
# AR : تطوير إضافات QGIS وحلول نظم المعلومات الجغرافية وإدارة PostgreSQL/PostGIS، والتدقيق والأتمتة ومرافقة المؤسسات.
# Site / Website / الموقع : https://geodeci.xyz/
# Contact professionnel / Business enquiries / للتواصل المهني : youcef.geodesien@gmail.com | contact@geodeci.xyz
# Détails / Details / التفاصيل : SIGNATURE.md
# ============================================================================
"""
Lecture / ecriture GeoPackage via sqlite3 + shapely.

Aucune dependance externe (fiona, osgeo, geopandas) : portable en
environnement plugin QGIS, CI et script autonome.

GeoPackage 1.3 spec : https://www.geopackage.org/spec130/
Structure binaire geometrie :
    bytes 0-1   : 'GP' (magic)
    byte  2     : version (0)
    byte  3     : flags (endian + envelope + empty + binary_type)
    bytes 4-7   : srs_id (int32)
    [envelope]  : 0/32/48/48/64 bytes selon flags
    [WKB]       : geometrie standard (shapely.wkb)
"""
from __future__ import annotations

import sqlite3
import struct
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, Iterator, List, Optional, Tuple

from shapely import wkb as shp_wkb
from shapely.geometry.base import BaseGeometry

from .dataclasses import SRID_LAMBERT93

GPKG_MAGIC = b"GP"
_ENVELOPE_SIZES = {0: 0, 1: 32, 2: 48, 3: 48, 4: 64}


# -----------------------------------------------------------------------------
# Parsing du header GPKG binaire
# -----------------------------------------------------------------------------


def _parse_gpkg_header(blob: bytes) -> Tuple[int, int, bytes]:
    """Extrait (srs_id, header_size, wkb_bytes) d'un blob GPKG.

    Raises ValueError si le blob n'est pas un header GPKG valide.
    """
    if len(blob) < 8 or blob[0:2] != GPKG_MAGIC:
        raise ValueError("Blob n'est pas un header GPKG valide (magic absent)")
    version = blob[2]
    flags = blob[3]
    is_little_endian = bool(flags & 0x01)
    envelope_indicator = (flags >> 1) & 0x07
    env_size = _ENVELOPE_SIZES.get(envelope_indicator)
    if env_size is None:
        raise ValueError(f"Envelope indicator invalide: {envelope_indicator}")
    endian_fmt = "<i" if is_little_endian else ">i"
    srs_id = struct.unpack(endian_fmt, blob[4:8])[0]
    header_size = 8 + env_size
    if len(blob) < header_size:
        raise ValueError(f"Blob GPKG tronque : {len(blob)} < {header_size}")
    _ = version  # reserve evolution future
    return srs_id, header_size, blob[header_size:]


def _build_gpkg_header(srs_id: int) -> bytes:
    """Construit un header GPKG minimal (pas d'envelope, LE, non vide, standard)."""
    magic = GPKG_MAGIC
    version = bytes([0])
    flags = bytes([0x01])  # LE, pas d'envelope, non vide, standard
    srs = struct.pack("<i", srs_id)
    return magic + version + flags + srs


def blob_to_geometry(blob: bytes) -> BaseGeometry:
    """Convertit un BLOB GPKG en geometrie shapely."""
    _, _, wkb_bytes = _parse_gpkg_header(blob)
    return shp_wkb.loads(wkb_bytes)


def geometry_to_blob(geom: BaseGeometry, srs_id: int = SRID_LAMBERT93) -> bytes:
    """Serialise une geometrie shapely en BLOB GPKG."""
    return _build_gpkg_header(srs_id) + geom.wkb


# -----------------------------------------------------------------------------
# Lecture d'une couche
# -----------------------------------------------------------------------------


def _connect_ro(path: str) -> sqlite3.Connection:
    """Connexion sqlite3 en lecture seule."""
    uri = f"file:{Path(path).as_posix()}?mode=ro"
    return sqlite3.connect(uri, uri=True)


def list_layers(gpkg_path: str) -> List[str]:
    """Liste les couches vectorielles d'un GeoPackage."""
    con = _connect_ro(gpkg_path)
    try:
        cur = con.execute(
            "SELECT table_name FROM gpkg_contents WHERE data_type = 'features' "
            "ORDER BY table_name"
        )
        return [r[0] for r in cur.fetchall()]
    finally:
        con.close()


def get_geom_column(con: sqlite3.Connection, table: str) -> str:
    """Nom de la colonne geometrie d'une couche via gpkg_geometry_columns."""
    cur = con.execute(
        "SELECT column_name FROM gpkg_geometry_columns WHERE table_name = ?",
        (table,),
    )
    row = cur.fetchone()
    if not row:
        raise ValueError(f"Pas de colonne geom enregistree pour {table}")
    return row[0]


def iter_features(
    gpkg_path: str,
    table: str,
    where: Optional[str] = None,
    params: Optional[Tuple[Any, ...]] = None,
) -> Iterator[Tuple[Dict[str, Any], BaseGeometry]]:
    """Itere sur les entites d'une couche GPKG.

    Yields (attributs_dict, geom_shapely). L'attribut geom n'est PAS inclus
    dans le dict retourne (evite la duplication).
    """
    con = _connect_ro(gpkg_path)
    try:
        geom_col = get_geom_column(con, table)
        con.row_factory = sqlite3.Row
        query = f'SELECT * FROM "{table}"'
        if where:
            query += f" WHERE {where}"
        cur = con.execute(query, params or ())
        for row in cur:
            attrs = {k: row[k] for k in row.keys() if k != geom_col}
            blob = row[geom_col]
            if blob is None:
                continue
            geom = blob_to_geometry(blob)
            yield attrs, geom
    finally:
        con.close()


def count_features(gpkg_path: str, table: str) -> int:
    """Nombre d'entites dans une couche (SELECT COUNT)."""
    con = _connect_ro(gpkg_path)
    try:
        cur = con.execute(f'SELECT COUNT(*) FROM "{table}"')
        return cur.fetchone()[0]
    finally:
        con.close()


# -----------------------------------------------------------------------------
# Creation d'un nouveau GeoPackage minimal
# -----------------------------------------------------------------------------


_GPKG_APPLICATION_ID = 0x47504B47  # 'GPKG'
_GPKG_USER_VERSION = 10300  # 1.3.0

_BOOTSTRAP_SQL = f"""
PRAGMA application_id = {_GPKG_APPLICATION_ID};
PRAGMA user_version = {_GPKG_USER_VERSION};

CREATE TABLE IF NOT EXISTS gpkg_spatial_ref_sys (
    srs_name TEXT NOT NULL,
    srs_id INTEGER NOT NULL PRIMARY KEY,
    organization TEXT NOT NULL,
    organization_coordsys_id INTEGER NOT NULL,
    definition TEXT NOT NULL,
    description TEXT
);

CREATE TABLE IF NOT EXISTS gpkg_contents (
    table_name TEXT NOT NULL PRIMARY KEY,
    data_type TEXT NOT NULL,
    identifier TEXT UNIQUE,
    description TEXT DEFAULT '',
    last_change DATETIME NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    min_x DOUBLE, min_y DOUBLE, max_x DOUBLE, max_y DOUBLE,
    srs_id INTEGER,
    CONSTRAINT fk_gc_r_srs_id FOREIGN KEY (srs_id) REFERENCES gpkg_spatial_ref_sys(srs_id)
);

CREATE TABLE IF NOT EXISTS gpkg_geometry_columns (
    table_name TEXT NOT NULL,
    column_name TEXT NOT NULL,
    geometry_type_name TEXT NOT NULL,
    srs_id INTEGER NOT NULL,
    z TINYINT NOT NULL,
    m TINYINT NOT NULL,
    CONSTRAINT pk_geom_cols PRIMARY KEY (table_name, column_name),
    CONSTRAINT uk_gc_table_name UNIQUE (table_name),
    CONSTRAINT fk_gc_tn FOREIGN KEY (table_name) REFERENCES gpkg_contents(table_name),
    CONSTRAINT fk_gc_srs FOREIGN KEY (srs_id) REFERENCES gpkg_spatial_ref_sys (srs_id)
);
"""

_L93_DEFINITION = (
    'PROJCS["RGF93 v1 / Lambert-93",'
    'GEOGCS["RGF93 v1",DATUM["Reseau_Geodesique_Francais_1993_v1",'
    'SPHEROID["GRS 1980",6378137,298.257222101]],'
    'PRIMEM["Greenwich",0],UNIT["degree",0.0174532925199433]],'
    'PROJECTION["Lambert_Conformal_Conic_2SP"],'
    'PARAMETER["latitude_of_origin",46.5],'
    'PARAMETER["central_meridian",3],'
    'PARAMETER["standard_parallel_1",49],'
    'PARAMETER["standard_parallel_2",44],'
    'PARAMETER["false_easting",700000],'
    'PARAMETER["false_northing",6600000],'
    'UNIT["metre",1],AXIS["Easting",EAST],AXIS["Northing",NORTH],AUTHORITY["EPSG","2154"]]'
)


def init_gpkg(path: str) -> None:
    """Cree un GeoPackage vide avec les tables systeme GPKG 1.3 minimales.

    Si le fichier existe deja, les CREATE IF NOT EXISTS sont no-op.
    """
    con = sqlite3.connect(path)
    try:
        con.executescript(_BOOTSTRAP_SQL)
        # Entrees SRS minimales requises par la spec
        con.execute(
            "INSERT OR IGNORE INTO gpkg_spatial_ref_sys VALUES (?,?,?,?,?,?)",
            ("Undefined cartesian", -1, "NONE", -1, "undefined", None),
        )
        con.execute(
            "INSERT OR IGNORE INTO gpkg_spatial_ref_sys VALUES (?,?,?,?,?,?)",
            ("Undefined geographic", 0, "NONE", 0, "undefined", None),
        )
        con.execute(
            "INSERT OR IGNORE INTO gpkg_spatial_ref_sys VALUES (?,?,?,?,?,?)",
            ("WGS 84", 4326, "EPSG", 4326, "geographic", None),
        )
        con.execute(
            "INSERT OR IGNORE INTO gpkg_spatial_ref_sys VALUES (?,?,?,?,?,?)",
            ("RGF93 / Lambert-93", 2154, "EPSG", 2154, _L93_DEFINITION, None),
        )
        con.commit()
    finally:
        con.close()


# -----------------------------------------------------------------------------
# Ecriture d'une couche
# -----------------------------------------------------------------------------


_SQL_TYPE_MAP = {
    int: "INTEGER",
    float: "REAL",
    str: "TEXT",
    bool: "INTEGER",
    bytes: "BLOB",
}


def _sql_type(value: Any) -> str:
    """Mappe un type Python vers un type SQLite pour DDL."""
    for py_type, sql_type in _SQL_TYPE_MAP.items():
        if isinstance(value, py_type):
            return sql_type
    return "TEXT"


def write_layer(
    gpkg_path: str,
    layer_name: str,
    rows: Iterable[Dict[str, Any]],
    geom_column: str = "geom",
    geometry_type: str = "LINESTRING",
    srs_id: int = SRID_LAMBERT93,
    replace: bool = True,
) -> int:
    """Ecrit une nouvelle couche dans un GeoPackage (cree si necessaire).

    Args:
        gpkg_path : chemin du GeoPackage (cree si inexistant)
        layer_name : nom de la couche / table
        rows : iterable de dicts incluant `geom_column` avec une valeur
               shapely.geometry (ou None). Les autres cles -> colonnes.
        geom_column : nom de la colonne geometrie
        geometry_type : type WKB ('POINT', 'LINESTRING', 'POLYGON'...)
        srs_id : SRID (defaut 2154 Lambert 93)
        replace : si True, DROP TABLE existante avant ecriture

    Returns:
        Nombre de lignes inserees.
    """
    init_gpkg(gpkg_path)
    rows_list = list(rows)
    if not rows_list:
        return 0

    # Determine le schema a partir de la premiere ligne.
    # `fid` est exclu : toujours genere par AUTOINCREMENT pour eviter
    # les collisions entre couches source / destination.
    first = rows_list[0]
    other_cols = [k for k in first.keys() if k != geom_column and k != "fid"]

    con = sqlite3.connect(gpkg_path)
    try:
        if replace:
            con.execute(f'DROP TABLE IF EXISTS "{layer_name}"')
            con.execute(
                "DELETE FROM gpkg_contents WHERE table_name = ?", (layer_name,)
            )
            con.execute(
                "DELETE FROM gpkg_geometry_columns WHERE table_name = ?",
                (layer_name,),
            )

        cols_ddl = ['"fid" INTEGER PRIMARY KEY AUTOINCREMENT']
        cols_ddl.append(f'"{geom_column}" BLOB')
        for c in other_cols:
            sql_t = _sql_type(first.get(c))
            cols_ddl.append(f'"{c}" {sql_t}')
        con.execute(f'CREATE TABLE "{layer_name}" ({", ".join(cols_ddl)})')

        con.execute(
            "INSERT INTO gpkg_contents (table_name, data_type, identifier, srs_id) "
            "VALUES (?, 'features', ?, ?)",
            (layer_name, layer_name, srs_id),
        )
        con.execute(
            "INSERT INTO gpkg_geometry_columns "
            "(table_name, column_name, geometry_type_name, srs_id, z, m) "
            "VALUES (?, ?, ?, ?, 0, 0)",
            (layer_name, geom_column, geometry_type, srs_id),
        )

        placeholders = ",".join(["?"] * (1 + len(other_cols)))
        insert_sql = (
            f'INSERT INTO "{layer_name}" '
            f'("{geom_column}", {", ".join(f"""\"{c}\"""" for c in other_cols)}) '
            f"VALUES ({placeholders})"
        )
        if not other_cols:
            insert_sql = (
                f'INSERT INTO "{layer_name}" ("{geom_column}") VALUES (?)'
            )

        count = 0
        for row in rows_list:
            geom = row.get(geom_column)
            blob: Optional[bytes] = None
            if isinstance(geom, BaseGeometry) and not geom.is_empty:
                blob = geometry_to_blob(geom, srs_id)
            elif isinstance(geom, (bytes, bytearray)):
                blob = bytes(geom)
            values = [blob] + [row.get(c) for c in other_cols]
            con.execute(insert_sql, values)
            count += 1

        con.commit()
        return count
    finally:
        con.close()


# -----------------------------------------------------------------------------
# Helpers conversion dataclass <-> dict
# -----------------------------------------------------------------------------


def dataclass_to_row(obj: Any) -> Dict[str, Any]:
    """Aplatit un dataclass en dict pour ecriture GPKG."""
    if not is_dataclass(obj):
        raise TypeError("dataclass_to_row attend un dataclass")
    d = asdict(obj)
    # Les sous-dicts `attrs` sont fusionnes a la racine
    attrs = d.pop("attrs", None)
    if attrs:
        for k, v in attrs.items():
            if k not in d:
                d[k] = v
    return d
