"""Extract ST-AIR test data from PostGIS -> GeoPackage -> ZIP.

Usage :
    Dans la Python Console QGIS : ouvrir ce fichier dans l'editeur, cliquer
    "Run Script" (F5). Pas besoin de exec().

Le script :
    1. Demande le SRO au format `INSEE/TYPE/CAT/NUM` (ex: 63041/B1I/PMZ/00003).
       Accepte aussi les separateurs `-` ou `_`. Valide le pattern avant
       de tourner.
    2. Demande le mot de passe PostgreSQL si non fourni par env.
    3. Pour chaque table cible, applique les filtres metier effectivement
       consommes par le plugin ST-AIR (voir `TABLE_FILTERS` ci-dessous).
    4. Exporte vers un GeoPackage `{SRO_DASH}.gpkg` dans `OUT_DIR`.
    5. Valide `cables > 0` (sinon le GPKG est supprime : le plugin
       rejette les GPKG sans cables, cf. loader.py).
    6. Zippe en `{SRO_DASH}.zip` : ce nommage permet au plugin de
       re-extraire le SRO depuis le nom de fichier si besoin.

Overrides via env vars :
    RL_PG_HOST, RL_PG_PORT, RL_PG_DB, RL_PG_USER, RL_PG_PWD,
    RL_SCHEMA, RL_SRO_FIELD, RL_SRO_CODE, RL_OUT_DIR.

Filtres appliques (justification en tete de TABLE_FILTERS) :
    - infra_pt_pot : sro + inf_type IN ('POT-FT','POT-BT') + affectation<>'3'
    - autres tables : sro uniquement
    - infra_pt_autres : NON exporte (jamais lu par le plugin)
"""
from __future__ import annotations

import os
import re
import zipfile
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

from qgis.core import (
    QgsApplication,
    QgsDataSourceUri,
    QgsProject,
    QgsProviderRegistry,
    QgsTask,
    QgsVectorFileWriter,
    QgsVectorLayer,
)
from qgis.PyQt.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QLineEdit, QListWidget, QListWidgetItem,
    QFileDialog, QDialogButtonBox, QCheckBox,
)
from qgis.PyQt.QtCore import Qt


# ---------------------------------------------------------------------------
# Configuration BDD source
# Priorite : connexion QGIS stockee > env vars
# ---------------------------------------------------------------------------
_ENV_PG_HOST = os.environ.get("RL_PG_HOST", "")
_ENV_PG_PORT = os.environ.get("RL_PG_PORT", "5432")
_ENV_PG_DB = os.environ.get("RL_PG_DB", "")
_ENV_PG_USER = os.environ.get("RL_PG_USER", "")
_ENV_PG_PWD = os.environ.get("RL_PG_PWD", "")

SCHEMA = os.environ.get("RL_SCHEMA", "")
SRO_FIELD = os.environ.get("RL_SRO_FIELD", "sro")
SRO_CODE = os.environ.get("RL_SRO_CODE", "")

OUT_DIR = Path(os.environ.get(
    "RL_OUT_DIR", str(Path.home() / "stair_exports")
))


def _resolve_pg_params() -> dict:
    """Return PG connection params (host, port, dbname, user, password).

    Priority:
        1. QGIS stored connection (incl. password / authcfg)
        2. Environment variables RL_PG_*
    """
    try:
        from .be_export_auth import read_qgis_pg_params
        qgis_params = read_qgis_pg_params()
        if qgis_params:
            return qgis_params
    except Exception:
        pass
    return {
        'host': _ENV_PG_HOST,
        'port': _ENV_PG_PORT,
        'dbname': _ENV_PG_DB,
        'user': _ENV_PG_USER,
        'password': _ENV_PG_PWD,
    }


# ---------------------------------------------------------------------------
# Filtres metier : chaque WHERE ne livre que ce que les modules du plugin
# ST-AIR consomment effectivement. Sources verifiees dans le code plugin :
#   - infra_pt_pot : qgis_utils.extraire_poteaux_etude L325-328,
#                    CapFt.py -> inf_type = 'POT-FT'
#                    Comac.py -> inf_type = 'POT-BT'
#                    C6_vs_Bd.py, C6_vs_C3A_vs_Bd.py -> inf_type = 'POT-FT'
#                    => export FT + BT pour couvrir tous les modules
#   - affectation 3 : regle metier sous-traitant (poteaux hors perimetre)
#   - cables : pas de filtre cote export (posemode est calcule par le
#              splitter, cab_capa < 6 utile pour comptage cuivre)
#   - bpe / attaches / etude_cap_ft / etude_comac : aucun filtre metier,
#              SRO uniquement
#   - infra_pt_autres : NON exporte, jamais lu par aucun module
# ---------------------------------------------------------------------------

_SQL_IDENT_CHARS = set(
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-"
)


def _sanitize_ident(value: str) -> str:
    """Garde uniquement les caracteres safe pour un identifiant SQL."""
    return "".join(c for c in value if c in _SQL_IDENT_CHARS)


def _sro_clause(sro_code: str, field: str = SRO_FIELD) -> str:
    """Construit le predicat `"<field>" = '<sro>'` avec echappement."""
    field_safe = _sanitize_ident(field)
    sro_escaped = sro_code.replace("'", "''")
    return f"\"{field_safe}\" = '{sro_escaped}'"


# Map {table_cible : builder_WHERE(sro_code)}. Ordre = ordre d'export.
# Nommage au SINGULIER pour les tables etude_* : le plugin ST-AIR
# (dialog_v2.bind_gpkg_layers, gpkg_pipeline.load_caches_from_gpkg) attend
# exactement `etude_cap_ft` et `etude_comac` pour l'auto-binding des combos.
TABLE_FILTERS: Dict[str, Callable[[str], str]] = {
    "cables": lambda sro: _sro_clause(sro),
    "infra_pt_pot": lambda sro: (
        f"{_sro_clause(sro)} "
        f"AND \"inf_type\" IN ('POT-FT', 'POT-BT') "
        f"AND (\"affectation\" IS NULL OR \"affectation\" <> '3')"
    ),
    "etude_cap_ft": lambda sro: _sro_clause(sro),
    "etude_comac": lambda sro: _sro_clause(sro),
    "bpe": lambda sro: _sro_clause(sro),
    "attaches": lambda sro: _sro_clause(sro),
}

TABLES: List[str] = list(TABLE_FILTERS.keys())


# Pattern SRO attendu par le plugin (project_detector.py:52-57).
# Accepte / - _ comme separateurs en entree, normalise en / pour la BDD
# et en - pour le nom de fichier.
_SRO_PATTERN = re.compile(
    r'^(\d{5})[-_/]([A-Z0-9]{3})[-_/]([A-Z]{3})[-_/](\d{5})$',
    re.IGNORECASE,
)


def _validate_sro(raw: str) -> Optional[Tuple[str, str]]:
    """Valide un SRO et retourne (forme_slash, forme_dash) ou None.

    Exemples :
        '63041/B1I/PMZ/00003' -> ('63041/B1I/PMZ/00003', '63041-B1I-PMZ-00003')
        '63041-B1I-PMZ-00003' -> idem
        '63041_b1i_pmz_00003' -> idem (case-insensitive)
        'xxx'                 -> None
    """
    raw = (raw or "").strip()
    match = _SRO_PATTERN.match(raw)
    if not match:
        return None
    insee, type_code, cat, num = match.groups()
    slash = f"{insee}/{type_code.upper()}/{cat.upper()}/{num}"
    dash = f"{insee}-{type_code.upper()}-{cat.upper()}-{num}"
    return slash, dash


# ---------------------------------------------------------------------------
# Helpers QGIS / PostgreSQL
# ---------------------------------------------------------------------------
def _log(msg: str) -> None:
    print(f"[stair_export] {msg}")


def _pg_connection(params: dict):
    """Ouvre une connexion PG via QGIS providerMetadata."""
    uri = QgsDataSourceUri()
    uri.setConnection(
        params['host'], params['port'], params['dbname'],
        params['user'], params.get('password', ''),
    )
    md = QgsProviderRegistry.instance().providerMetadata("postgres")
    if md is None:
        raise RuntimeError("PostgreSQL provider metadata not available in QGIS")
    return md.createConnection(uri.uri(False), {})


def _discover_geometry(conn, schema: str, table: str) -> Optional[Tuple[str, int]]:
    """Return (geom_column, srid) from geometry_columns or None if absent."""
    sch = _sanitize_ident(schema)
    tbl = _sanitize_ident(table)
    sql = (
        "SELECT f_geometry_column, srid FROM geometry_columns "
        f"WHERE f_table_schema = '{sch}' AND f_table_name = '{tbl}' LIMIT 1"
    )
    try:
        rows = conn.executeSql(sql)
    except Exception as exc:
        _log(f"  geometry_columns query failed: {exc}")
        return None
    if not rows:
        return None
    return str(rows[0][0]), int(rows[0][1])


def _fetch_sro_list(params: dict) -> List[str]:
    """Fetch available SRO codes from za_sro table. Returns [] on failure."""
    try:
        conn = _pg_connection(params)
        schema = _sanitize_ident(SCHEMA)
        rows = conn.executeSql(
            f"SELECT DISTINCT \"sro\" FROM \"{schema}\".\"za_sro\" "
            f"WHERE \"sro\" IS NOT NULL ORDER BY \"sro\""
        )
        return [str(r[0]) for r in rows if r and r[0]]
    except Exception:
        return []


def _prompt_sro(pg_params: dict) -> Tuple[str, str]:
    """Return (sro_slash, sro_dash).

    Raises:
        ValueError : SRO invalide ou non fourni.
    """
    raw = SRO_CODE
    if not raw:
        sro_list = _fetch_sro_list(pg_params)
        if sro_list:
            _log(f"{len(sro_list)} SRO disponibles (autocomplete actif).")
        from .dialog_v2 import SroInputDialog
        dlg = SroInputDialog(
            current_sro='', project_name='Export GPKG',
            sro_list=sro_list,
        )
        if not dlg.exec():
            raise ValueError("SRO non fourni, export annule.")
        raw = dlg.get_sro()
        if not raw:
            raise ValueError("SRO non fourni, export annule.")

    validated = _validate_sro(raw)
    if validated is None:
        raise ValueError(
            f"SRO invalide : '{raw}'. Format attendu : "
            f"5 chiffres / 3 alphanum / 3 lettres / 5 chiffres "
            f"(ex: 63041/B1I/PMZ/00003)."
        )
    return validated


def _build_uri(
    schema: str, table: str, geom_col: str, srid: int,
    where_sql: str, params: dict,
) -> QgsDataSourceUri:
    uri = QgsDataSourceUri()
    uri.setConnection(
        params['host'], params['port'], params['dbname'],
        params['user'], params.get('password', ''),
    )
    uri.setUseEstimatedMetadata(True)
    uri.setDataSource(schema, table, geom_col, where_sql, "")
    if srid:
        uri.setSrid(str(srid))
    return uri


def _load_layer(
    schema: str, table: str, geom_col: str, srid: int,
    where_sql: str, params: dict,
) -> QgsVectorLayer:
    uri = _build_uri(schema, table, geom_col, srid, where_sql, params)
    name = f"{schema}.{table}"
    layer = QgsVectorLayer(uri.uri(False), name, "postgres")
    if not layer.isValid():
        raise RuntimeError(f"Layer invalid: {name}")
    return layer


def _build_options(layer_name: str, append: bool) -> QgsVectorFileWriter.SaveVectorOptions:
    opts = QgsVectorFileWriter.SaveVectorOptions()
    opts.driverName = "GPKG"
    opts.layerName = layer_name
    opts.fileEncoding = "UTF-8"
    if append:
        opts.actionOnExistingFile = QgsVectorFileWriter.CreateOrOverwriteLayer
    return opts


def _export_to_gpkg(layer: QgsVectorLayer, gpkg_path: Path, append: bool, ctx) -> None:
    # Nom de couche ecrit dans le GPKG = derniere partie apres '.' (schema.table -> table)
    opts = _build_options(layer.name().split(".")[-1], append)

    writer = getattr(QgsVectorFileWriter, "writeAsVectorFormatV3", None)
    if writer is not None:
        result = writer(layer, str(gpkg_path), ctx, opts)
    else:
        result = QgsVectorFileWriter.writeAsVectorFormatV2(
            layer, str(gpkg_path), ctx, opts
        )

    if result[0] != QgsVectorFileWriter.NoError:
        raise RuntimeError(
            f"Export failed for {layer.name()}: code={result[0]} msg={result[1:]}"
        )


def _zip_file(src: Path, dst: Path) -> None:
    with zipfile.ZipFile(dst, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.write(src, arcname=src.name)


# ---------------------------------------------------------------------------
# Async task (does not block the QGIS UI)
# ---------------------------------------------------------------------------
class ExtractTask(QgsTask):
    """Background extract: discover + load + export + zip, without blocking QGIS."""

    def __init__(
        self,
        sro_slash: str,
        pg_params: dict,
        gpkg_path: Path,
        zip_path: Path,
        ctx,
    ) -> None:
        super().__init__("ST-AIR: extract test data", QgsTask.CanCancel)
        self._sro = sro_slash
        self._pg_params = pg_params
        self._gpkg_path = gpkg_path
        self._zip_path = zip_path
        self._ctx = ctx
        self.exported: List[Tuple[str, int]] = []  # (table, feature_count)
        self.failed: List[Tuple[str, str]] = []
        self._fatal: Optional[str] = None

    def run(self) -> bool:
        try:
            conn = _pg_connection(self._pg_params)
        except Exception as exc:
            self._fatal = f"connection failed: {exc}"
            return False

        total = max(len(TABLES), 1)
        export_idx = 0
        for i, table in enumerate(TABLES):
            if self.isCanceled():
                return False
            self.setProgress(100.0 * i / total)
            full = f"{SCHEMA}.{table}"
            try:
                where_sql = TABLE_FILTERS[table](self._sro)
                _log(f"[{i + 1}/{total}] {full}")
                _log(f"    WHERE {where_sql}")
                geom_info = _discover_geometry(conn, SCHEMA, table)
                if geom_info is None:
                    raise RuntimeError(
                        "not registered in geometry_columns (non-spatial or missing)"
                    )
                geom_col, srid = geom_info
                layer = _load_layer(
                    SCHEMA, table, geom_col, srid, where_sql, self._pg_params,
                )
                nb = layer.featureCount()
                _log(
                    f"    geom='{geom_col}' srid={srid} feat={nb} crs={layer.crs().authid()}"
                )
                _export_to_gpkg(layer, self._gpkg_path, export_idx > 0, self._ctx)
                self.exported.append((table, nb))
                export_idx += 1
            except Exception as exc:
                _log(f"    FAILED: {exc}")
                self.failed.append((full, str(exc)))

        self.setProgress(100.0)
        return True

    def _count_for(self, table: str) -> int:
        for t, nb in self.exported:
            if t == table:
                return nb
        return 0

    def finished(self, result: bool) -> None:  # main thread
        if self._fatal:
            _log(f"FATAL: {self._fatal}")
            return
        if self.isCanceled():
            _log("Task cancelled by user.")
            return

        nb_cables = self._count_for("cables")
        nb_pot = self._count_for("infra_pt_pot")

        if not self.exported or not self._gpkg_path.exists():
            _log(f"  {self._sro}: aucun GPKG produit.")
            return

        if nb_cables == 0:
            _log(
                f"  {self._sro}: 0 cables, GPKG supprime "
                f"(le plugin rejette un GPKG sans cables)."
            )
            _safe_unlink(self._gpkg_path)
            return

        if nb_pot == 0:
            _log(f"  {self._sro}: WARNING 0 poteaux (FT+BT).")

        try:
            _zip_file(self._gpkg_path, self._zip_path)
        except Exception as exc:
            _log(f"  {self._sro}: zip failed: {exc}")
            return

        _safe_unlink(self._gpkg_path)

        tables_ok = len(self.exported)
        tables_ko = len(self.failed)
        _log(f"  {self._sro}: OK ({tables_ok} tables, {nb_cables} cables) -> {self._zip_path.name}")
        if tables_ko:
            for name, err in self.failed:
                _log(f"    FAILED: {name}: {err}")


def _safe_unlink(path: Path) -> None:
    try:
        if path.exists():
            path.unlink()
    except OSError:
        pass


def _clean_previous(gpkg_path: Path, zip_path: Path) -> None:
    """Remove previous GPKG/ZIP and unload related QGIS layers (main thread)."""
    if gpkg_path.exists():
        gpkg_str = str(gpkg_path).replace("\\", "/")
        project = QgsProject.instance()
        to_remove = [
            lid for lid, lyr in project.mapLayers().items()
            if gpkg_str in lyr.source().replace("\\", "/")
        ]
        if to_remove:
            project.removeMapLayers(to_remove)
        gpkg_path.unlink()
    if zip_path.exists():
        zip_path.unlink()


# ---------------------------------------------------------------------------
# Export batch dialog: multi-SRO selection + output folder
# ---------------------------------------------------------------------------
class ExportBatchDialog(QDialog):
    """Select multiple SROs + output folder for batch export."""

    def __init__(self, sro_list, default_dir='', parent=None):
        super().__init__(parent)
        self.setWindowTitle('Export GPKG - Selection SRO')
        self.setMinimumSize(420, 480)
        self._sro_list = sorted(sro_list)
        self._result_sros = []
        self._result_dir = default_dir or str(OUT_DIR)

        root = QVBoxLayout(self)
        root.setSpacing(8)

        root.addWidget(QLabel(
            f"<b>{len(self._sro_list)} SRO disponibles</b>"
        ))

        self._filter = QLineEdit()
        self._filter.setPlaceholderText('Filtrer...')
        self._filter.setClearButtonEnabled(True)
        self._filter.textChanged.connect(self._apply_filter)
        root.addWidget(self._filter)

        btn_row = QHBoxLayout()
        self._chk_all = QCheckBox('Tout selectionner')
        self._chk_all.toggled.connect(self._toggle_all)
        btn_row.addWidget(self._chk_all)
        btn_row.addStretch()
        self._sel_label = QLabel('0 selectionne(s)')
        btn_row.addWidget(self._sel_label)
        root.addLayout(btn_row)

        self._list = QListWidget()
        for sro in self._sro_list:
            item = QListWidgetItem(sro)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Unchecked)
            self._list.addItem(item)
        self._list.itemChanged.connect(self._update_count)
        root.addWidget(self._list)

        dir_row = QHBoxLayout()
        dir_row.addWidget(QLabel('Dossier :'))
        self._dir_edit = QLineEdit(self._result_dir)
        self._dir_edit.setReadOnly(True)
        dir_row.addWidget(self._dir_edit)
        self._dir_btn = QPushButton('...')
        self._dir_btn.setFixedWidth(32)
        self._dir_btn.clicked.connect(self._browse_dir)
        dir_row.addWidget(self._dir_btn)
        root.addLayout(dir_row)

        self._btn_box = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel
        )
        self._btn_box.accepted.connect(self._accept)
        self._btn_box.rejected.connect(self.reject)
        ok_btn = self._btn_box.button(QDialogButtonBox.StandardButton.Ok)
        ok_btn.setText('Exporter')
        ok_btn.setEnabled(False)
        root.addWidget(self._btn_box)

    def _apply_filter(self, text):
        text = text.strip().upper()
        for i in range(self._list.count()):
            item = self._list.item(i)
            item.setHidden(text not in item.text().upper())

    def _toggle_all(self, checked):
        state = Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked
        for i in range(self._list.count()):
            item = self._list.item(i)
            if not item.isHidden():
                item.setCheckState(state)

    def _update_count(self):
        n = self._checked_count()
        self._sel_label.setText(f'{n} selectionne(s)')
        ok_btn = self._btn_box.button(QDialogButtonBox.StandardButton.Ok)
        ok_btn.setEnabled(n > 0)

    def _checked_count(self):
        return sum(
            1 for i in range(self._list.count())
            if self._list.item(i).checkState() == Qt.CheckState.Checked
        )

    def _browse_dir(self):
        d = QFileDialog.getExistingDirectory(
            self, 'Dossier d\'export', self._result_dir,
        )
        if d:
            self._result_dir = d
            self._dir_edit.setText(d)

    def _accept(self):
        self._result_sros = []
        for i in range(self._list.count()):
            item = self._list.item(i)
            if item.checkState() == Qt.CheckState.Checked:
                self._result_sros.append(item.text())
        self._result_dir = self._dir_edit.text()
        self.accept()

    def selected_sros(self):
        return list(self._result_sros)

    def export_dir(self):
        return self._result_dir


# ---------------------------------------------------------------------------
# Entry point: prompts on main thread, then dispatches async task
# ---------------------------------------------------------------------------
def run() -> None:
    pg_params = _resolve_pg_params()
    if not pg_params.get('host') or not pg_params.get('user'):
        _log("ABORT: connexion PostgreSQL non configuree dans QGIS.")
        return

    sro_list = _fetch_sro_list(pg_params)
    if not sro_list:
        _log("ABORT: aucun SRO trouve dans za_sro.")
        return
    _log(f"{len(sro_list)} SRO disponibles.")

    dlg = ExportBatchDialog(sro_list, str(OUT_DIR))
    if not dlg.exec():
        _log("Export annule.")
        return

    selected = dlg.selected_sros()
    out_dir = Path(dlg.export_dir())
    if not selected:
        _log("Aucun SRO selectionne.")
        return

    out_dir.mkdir(parents=True, exist_ok=True)

    validated = []
    for raw in selected:
        v = _validate_sro(raw)
        if v is None:
            _log(f"SRO invalide ignore: {raw}")
            continue
        validated.append(v)

    if not validated:
        _log("Aucun SRO valide.")
        return

    conn_str = f"{pg_params['user']}@{pg_params['host']}:{pg_params['port']}/{pg_params['dbname']}"
    _log(f"Connexion : {conn_str}")
    _log(f"Dossier   : {out_dir}")
    _log(f"Export    : {len(validated)} SRO en parallele")

    ctx = QgsProject.instance().transformContext()
    tm = QgsApplication.taskManager()
    tasks = []
    for sro_slash, sro_dash in validated:
        gpkg_path = out_dir / f"{sro_dash}.gpkg"
        zip_path = out_dir / f"{sro_dash}.zip"
        _clean_previous(gpkg_path, zip_path)
        task = ExtractTask(sro_slash, pg_params, gpkg_path, zip_path, ctx)
        tasks.append(task)
        tm.addTask(task)
        _log(f"  -> {sro_slash}")

    globals()["_stair_extract_tasks"] = tasks
    globals()["_stair_extract_task"] = tasks
    _log(f"{len(tasks)} tache(s) soumise(s) au Task Manager.")


# Auto-run when "Run Script" is clicked in the QGIS Python Console.
if __name__ == "__main__" or __name__ == "__console__":
    run()
