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
Batch Orchestrator - Bridges DialogV2 with existing workflows.

Translates detected project paths + QGIS layer selections into
workflow.start_analysis() calls, and routes workflow signals
back to the BatchRunner callback system.
"""

import os
import time
from collections import deque

from qgis.PyQt.QtCore import QObject
from qgis.core import QgsMessageLog, QgsTask, QgsApplication, QgsProject
from .compat import MSG_INFO, MSG_WARNING, MSG_CRITICAL, FIELD_TYPE_STRING

from .project_detector import DetectionResult
from .qgis_utils import (
    detect_etude_field as _detect_etude_field,
    extraire_poteaux_etude,
    reset_crs_cache,
    show_feature_count,
)
from .core_utils import normalize_appui_num
from .report_export_task import UnifiedReportExportTask
from .batch_extractor import BatchDataExtractor
from .usage_diag import UsageDiag


# -----------------------------------------------------------------------------
# Taches de chargement PostgreSQL (mode BDD pour equipes internes).
# Chargees depuis la connexion QGIS "AUVERGNE" via QgsProviderRegistry.
# Utilisent les memes filtres que export_zip_gpkg.py (TABLE_FILTERS).
# -----------------------------------------------------------------------------


class _LoadProjectLayersTask(QgsTask):
    """Charge les couches etude depuis PostgreSQL (infra_pt_pot, etude_cap_ft,
    etude_comac, data_gespot) avec filtre SRO.

    S'execute dans un thread worker QgsTask. Les couches resultantes sont
    ajoutees au QgsProject dans le callback finished() (main thread).
    """

    def __init__(self, sro, pg_params, extent_wkt=''):
        super().__init__(f"Chargement couches BDD SRO={sro}")
        self.sro = sro
        self._pg_params = pg_params
        self.extent_wkt = extent_wkt
        self.lyr_pot = None
        self.lyr_cap = None
        self.lyr_com = None
        self.lyr_gespot = None
        self.error_msg = ''
        self.counts = {}

    def run(self):
        import time as _time
        from qgis.core import QgsDataSourceUri, QgsProviderRegistry, QgsVectorLayer
        from .export_zip_gpkg import (
            TABLE_FILTERS, TABLE_SCHEMAS, SCHEMA, GESPOT_SCHEMA,
            _discover_geometry, _sanitize_ident,
        )
        t0 = _time.monotonic()
        try:
            conn_uri = QgsDataSourceUri()
            conn_uri.setConnection(
                self._pg_params['host'], self._pg_params['port'],
                self._pg_params['dbname'],
                self._pg_params['user'], self._pg_params.get('password', ''),
            )
            md = QgsProviderRegistry.instance().providerMetadata("postgres")
            if md is None:
                self.error_msg = "PostgreSQL provider metadata not available"
                return False
            conn = md.createConnection(conn_uri.uri(False), {})
        except Exception as exc:
            self.error_msg = f"Connexion BDD echouee : {exc}"
            return False

        layers_to_load = [
            ('infra_pt_pot', 'lyr_pot'),
            ('etude_cap_ft', 'lyr_cap'),
            ('etude_comac', 'lyr_com'),
            ('data_gespot', 'lyr_gespot'),
        ]

        for table, attr in layers_to_load:
            if self.isCanceled():
                return False
            schema = TABLE_SCHEMAS.get(table, SCHEMA)
            try:
                where_sql = TABLE_FILTERS[table](self.sro)
                geom_info = _discover_geometry(conn, schema, table)
                if geom_info is None:
                    QgsMessageLog.logMessage(
                        f"[BDD] {schema}.{table}: pas de geometrie",
                        "PoleAerien", MSG_WARNING,
                    )
                    continue
                geom_col, srid = geom_info
                uri = QgsDataSourceUri()
                uri.setConnection(
                    self._pg_params['host'], self._pg_params['port'],
                    self._pg_params['dbname'],
                    self._pg_params['user'], self._pg_params.get('password', ''),
                )
                uri.setUseEstimatedMetadata(True)
                uri.setDataSource(schema, table, geom_col, where_sql, "")
                if srid:
                    uri.setSrid(str(srid))
                layer = QgsVectorLayer(uri.uri(False), table, "postgres")
                if not layer.isValid():
                    QgsMessageLog.logMessage(
                        f"[BDD] {schema}.{table}: couche invalide",
                        "PoleAerien", MSG_WARNING,
                    )
                    continue
                setattr(self, attr, layer)
                nb = layer.featureCount()
                self.counts[table] = nb
                QgsMessageLog.logMessage(
                    f"[BDD] {schema}.{table}: {nb} features chargees "
                    f"elapsed_ms={int((_time.monotonic() - t0) * 1000)}",
                    "PoleAerien", MSG_INFO,
                )
            except Exception as exc:
                QgsMessageLog.logMessage(
                    f"[BDD] {schema}.{table}: echec chargement : {exc}",
                    "PoleAerien", MSG_WARNING,
                )

        if self.lyr_pot is None:
            self.error_msg = "infra_pt_pot non charge (obligatoire)"
            return False
        return True

    def finished(self, result):
        pass


class _PreExtractPgTask(QgsTask):
    """Pre-fetch cables, bpe, attaches depuis PostgreSQL avec filtre SRO.

    Construit un ExtractedData equivalent a load_gpkg_delivery pour que
    COMAC et Police C6 consomment les caches sans BDD supplementaire.
    """

    def __init__(self, sro, pg_params):
        super().__init__(f"Prechargement batch BDD SRO={sro}")
        self.sro = sro
        self._pg_params = pg_params
        self.data = None
        self._fddcpiax_layer = None
        self.error_msg = ''

    def run(self):
        import time as _time
        from qgis.core import QgsDataSourceUri, QgsProviderRegistry, QgsVectorLayer
        from .export_zip_gpkg import TABLE_FILTERS, TABLE_SCHEMAS, SCHEMA
        from .export_zip_gpkg import _discover_geometry
        from .batch_extractor import ExtractedData
        from .cable_segment import CableSegment
        t0 = _time.monotonic()

        try:
            conn_uri = QgsDataSourceUri()
            conn_uri.setConnection(
                self._pg_params['host'], self._pg_params['port'],
                self._pg_params['dbname'],
                self._pg_params['user'], self._pg_params.get('password', ''),
            )
            md = QgsProviderRegistry.instance().providerMetadata("postgres")
            if md is None:
                self.error_msg = "PostgreSQL provider metadata not available"
                return False
            conn = md.createConnection(conn_uri.uri(False), {})
        except Exception as exc:
            self.error_msg = f"Connexion BDD echouee : {exc}"
            return False

        data = ExtractedData(sro=self.sro)
        data.cables_source = 'pg_fddcpiax'

        # Load cables decoupes via fddcpiax(sro)
        # executeSql utilise une transaction read-write (contrairement au
        # curseur read-only de QgsVectorLayer) : fddcpiax fait du DDL
        # (DROP TABLE, CREATE TEMP TABLE).
        try:
            from .export_zip_gpkg import _sanitize_ident
            sch = _sanitize_ident(SCHEMA)
            sro_escaped = self.sro.replace("'", "''")
            sql = (
                f'SELECT gid_dc2, gid_dc, gid, sro, nro, length, '
                f'cab_type, cab_capa, cab_modulo, cb_etiquet, posemode, '
                f'ST_AsText(geom) AS geom_wkt '
                f'FROM "{sch}".fddcpiax(\'{sro_escaped}\')'
            )
            rows = conn.executeSql(sql)
            cables = []
            for row in rows:
                seg = CableSegment(
                    gid_dc2=int(row[0] or 0),
                    gid_dc=int(row[1] or 0),
                    gid=int(row[2] or 0),
                    sro=str(row[3] or ''),
                    nro=str(row[4] or ''),
                    length=float(row[5] or 0),
                    cab_type=str(row[6] or ''),
                    cab_capa=int(row[7] or 0),
                    cab_modulo=int(row[8] or 0),
                    cb_etiquet=str(row[9] or ''),
                    posemode=int(row[10] or 0),
                    geom_wkt=str(row[11] or ''),
                )
                cables.append(seg)
            data.cables = cables
            QgsMessageLog.logMessage(
                f"[BDD] fddcpiax: {len(cables)} segments decoupes "
                f"elapsed_ms={int((_time.monotonic() - t0) * 1000)}",
                "PoleAerien", MSG_INFO,
            )
        except Exception as exc:
            QgsMessageLog.logMessage(
                f"[BDD] fddcpiax echoue : {exc}",
                "PoleAerien", MSG_WARNING,
            )

        # Load bpe
        try:
            schema = TABLE_SCHEMAS.get('bpe', SCHEMA)
            where_sql = TABLE_FILTERS['bpe'](self.sro)
            geom_info = _discover_geometry(conn, schema, 'bpe')
            if geom_info:
                geom_col, srid = geom_info
                uri = QgsDataSourceUri()
                uri.setConnection(
                    self._pg_params['host'], self._pg_params['port'],
                    self._pg_params['dbname'],
                    self._pg_params['user'], self._pg_params.get('password', ''),
                )
                uri.setUseEstimatedMetadata(True)
                uri.setDataSource(schema, 'bpe', geom_col, where_sql, "")
                if srid:
                    uri.setSrid(str(srid))
                layer = QgsVectorLayer(uri.uri(False), 'bpe', "postgres")
                if layer.isValid():
                    from qgis.core import QgsFeatureRequest
                    fields = layer.fields()
                    bpe_cols = ('gid', 'fid', 'noe_type', 'noe_codext', 'sro')
                    bpe_idx = [fields.indexFromName(c) for c in bpe_cols if fields.indexFromName(c) >= 0]
                    req_bpe = QgsFeatureRequest()
                    if bpe_idx:
                        req_bpe.setSubsetOfAttributes(bpe_idx)
                    bpe_list = []
                    for feat in layer.getFeatures(req_bpe):
                        attrs = {c: feat[c] for c in bpe_cols if fields.indexFromName(c) >= 0}
                        gid = attrs.get('gid') or attrs.get('fid')
                        if gid is None:
                            continue
                        geom_wkt = feat.geometry().asWkt() if feat.hasGeometry() else ''
                        bpe_list.append({
                            'gid': int(gid),
                            'geom_wkt': geom_wkt,
                            'noe_type': str(attrs.get('noe_type', '')),
                            'noe_codext': str(attrs.get('noe_codext', '')),
                            'sro': str(attrs.get('sro', '')),
                        })
                    data.bpe_list = bpe_list
                    QgsMessageLog.logMessage(
                        f"[BDD] bpe: {len(bpe_list)} features",
                        "PoleAerien", MSG_INFO,
                    )
        except Exception as exc:
            QgsMessageLog.logMessage(
                f"[BDD] bpe pre-fetch echoue : {exc}",
                "PoleAerien", MSG_WARNING,
            )

        # Load attaches
        try:
            schema = TABLE_SCHEMAS.get('attaches', SCHEMA)
            where_sql = TABLE_FILTERS['attaches'](self.sro)
            geom_info = _discover_geometry(conn, schema, 'attaches')
            if geom_info:
                geom_col, srid = geom_info
                uri = QgsDataSourceUri()
                uri.setConnection(
                    self._pg_params['host'], self._pg_params['port'],
                    self._pg_params['dbname'],
                    self._pg_params['user'], self._pg_params.get('password', ''),
                )
                uri.setUseEstimatedMetadata(True)
                uri.setDataSource(schema, 'attaches', geom_col, where_sql, "")
                if srid:
                    uri.setSrid(str(srid))
                layer = QgsVectorLayer(uri.uri(False), 'attaches', "postgres")
                if layer.isValid():
                    from qgis.core import QgsFeatureRequest
                    fields = layer.fields()
                    att_cols = ('gid', 'fid', 'noe_usage', 'sro')
                    att_idx = [fields.indexFromName(c) for c in att_cols if fields.indexFromName(c) >= 0]
                    req_att = QgsFeatureRequest()
                    if att_idx:
                        req_att.setSubsetOfAttributes(att_idx)
                    attaches = []
                    for feat in layer.getFeatures(req_att):
                        attrs = {c: feat[c] for c in att_cols if fields.indexFromName(c) >= 0}
                        gid = attrs.get('gid') or attrs.get('fid')
                        if gid is None:
                            continue
                        geom_wkt = feat.geometry().asWkt() if feat.hasGeometry() else ''
                        attaches.append({
                            'gid': int(gid),
                            'geom_wkt': geom_wkt,
                            'noe_usage': str(attrs.get('noe_usage', '')),
                            'sro': str(attrs.get('sro', '')),
                        })
                    data.attaches_raw = attaches
                    QgsMessageLog.logMessage(
                        f"[BDD] attaches: {len(attaches)} features",
                        "PoleAerien", MSG_INFO,
                    )
        except Exception as exc:
            QgsMessageLog.logMessage(
                f"[BDD] attaches pre-fetch echoue : {exc}",
                "PoleAerien", MSG_WARNING,
            )

        if data.cables is None:
            self.error_msg = "cables non pre-charge (obligatoire)"
            return False
        self.data = data
        return True

    def finished(self, result):
        pass


class _LoadGpkgDeliveryTask(QgsTask):
    """Tache asynchrone d'extraction et preparation d'un livrable GeoPackage/ZIP."""

    def __init__(self, path, sro_hint='', keep_extracted=True, gpkg_member=None):
        super().__init__(
            f"Chargement GeoPackage ({os.path.basename(path)})",
            QgsTask.CanCancel,
        )
        self.path = path
        self.sro_hint = sro_hint
        self.keep_extracted = keep_extracted
        self.gpkg_member = gpkg_member
        self.caches = None
        self.error_exc = None

    def run(self):
        from .gpkg_pipeline import load_caches_from_gpkg, load_caches_from_zip
        try:
            self.setProgress(10)
            lower = self.path.lower()
            if lower.endswith('.zip'):
                self.caches = load_caches_from_zip(
                    self.path,
                    sro_hint=self.sro_hint,
                    keep_extracted=self.keep_extracted,
                    gpkg_member=self.gpkg_member,
                )
            elif lower.endswith('.gpkg'):
                self.caches = load_caches_from_gpkg(
                    self.path,
                    sro_hint=self.sro_hint,
                )
            else:
                raise ValueError(
                    f"Extension inattendue : {self.path}. Attendu : .zip ou .gpkg"
                )
            if self.isCanceled():
                return False
            self.setProgress(100)
            return True
        except Exception as exc:
            self.error_exc = exc
            return False

    def finished(self, result):
        pass


_VALIDATION_LABELS = {
    'hors_etude': 'poteaux hors perimetre etude',
    'doublons': 'etudes en doublon',
    'fichiers_doublons': 'fichiers en doublon',
    'erreur_lecture': 'erreurs de lecture',
}


def _format_validation_error(result):
    """Format a validation error result into a readable message."""
    err_type = result.get('error_type', '')
    data = result.get('data', [])
    label = _VALIDATION_LABELS.get(err_type, err_type)
    if isinstance(data, list):
        count = len(data)
        return f"{count} {label}"
    return f"{label}: {data}"


class BatchOrchestrator(QObject):
    """Connects DialogV2 → BatchRunner → Workflows.

    Usage (in PoleAerien.__init__):
        self.batch_orch = BatchOrchestrator(
            dialog_v2, batch_runner,
            capft_workflow, comac_workflow,
            police_workflow, c6c3a_workflow,
            iface, gespot_wf=...
        )
    """

    def __init__(self, dialog, runner,
                 capft_wf, comac_wf,
                 police_wf, c6c3a_wf,
                 iface, gespot_wf=None, c6bd_wf=None, parent=None):
        super().__init__(parent)
        self._dlg = dialog
        self._runner = runner
        self._iface = iface

        # Workflows
        self._capft_wf = capft_wf
        self._comac_wf = comac_wf
        self._police_wf = police_wf
        self._c6c3a_wf = c6c3a_wf
        self._gespot_wf = gespot_wf
        self._c6bd_wf = c6bd_wf

        # Per-module done callbacks: {module_key: callback}
        # Dict instead of single attr so multiple modules can run in parallel
        self._done_callbacks = {}
        self._module_start_times = {}
        # Per-module progress tracking: {module_key: 0-100}
        # Enables correct progress bar with parallel modules
        self._module_progress = {}
        self._batch_results = {}

        # Diagnostic sous-traitant (envoi automatique)
        self._diag = UsageDiag(plugin_dir=os.path.dirname(__file__))
        self._diag_log_buffer = deque(maxlen=200)
        self._diag_start_ts = 0.0

        # Shared fddcpi2 cache: avoids duplicate PostgreSQL calls
        # when both COMAC and Police C6 run in the same batch.
        # Key: sro (str), Value: list[CableSegment]
        self._fddcpi_cache = {}

        # Shared SRO + appuis WKB cache: avoids duplicate QGIS extraction
        # when both COMAC and Police C6 need the same data from infra_pt_pot.
        # Format: {'sro': str, 'appuis_wkb': list[dict]}
        self._sro_appuis_cache = {}

        # Project mode: DB-loaded layers (temporary)
        self._pm_lyr_pot = None
        self._pm_lyr_cap = None
        self._pm_lyr_com = None
        self._pm_lyr_gespot = None

        # Async layer loading state
        self._layer_load_task = None
        self._pending_module_keys = None
        self._report_task = None
        self._prefetch_task = None
        self._pending_prefetch_keys = None
        # P-02: pre-extracted batch data (populated before runner.start)
        self._extracted_data = None

        # GeoPackage delivery caches (peuples par load_gpkg_delivery).
        # Quand present, alimente _fddcpi_cache + _extracted_data avant chaque
        # batch afin que Comac et Police C6 trouvent les cables, BPE et
        # attaches sans recourir a la BDD supprimee.
        self._gpkg_caches = None  # Optional[gpkg_pipeline.GpkgCaches]
        self._gpkg_load_task = None

        # Register launchers (module 'maj' retire : MAJ BD hors perimetre sous-traitant)
        self._runner.set_launcher('capft', self._launch_capft)
        self._runner.set_launcher('c6bd', self._launch_c6bd)
        self._runner.set_launcher('comac', self._launch_comac)
        self._runner.set_launcher('police_c6', self._launch_police_c6)
        self._runner.set_launcher('c6c3a', self._launch_c6c3a)
        self._runner.set_launcher('gespot_c6', self._launch_gespot_c6)

        # Connect runner signals -> dialog
        self._runner.module_started.connect(self._on_module_started)
        self._runner.module_finished.connect(self._on_module_finished)
        self._runner.modules_finished.connect(self._on_batch_finished)
        self._runner.batch_cancelled.connect(self._on_batch_cancelled)
        self._runner.log_message.connect(self._dlg.log_message)
        self._runner.log_message.connect(self._on_runner_log)

        # Connect workflow progress_changed with per-module key tagging
        # Lambda with default arg captures key at definition time (closure-safe)
        _wf_map = {
            'capft': self._capft_wf,
            'c6bd': self._c6bd_wf,
            'comac': self._comac_wf,
            'police_c6': self._police_wf,
            'c6c3a': self._c6c3a_wf,
            'gespot_c6': self._gespot_wf,
        }
        for _key, _wf in _wf_map.items():
            if _wf and hasattr(_wf, 'progress_changed'):
                _wf.progress_changed.connect(
                    lambda pct, k=_key: self._on_module_progress(k, pct)
                )

        # Connect dialog signals
        self._dlg.start_requested.connect(self._on_start)
        self._dlg.cancel_requested.connect(self._on_cancel)
        if hasattr(self._dlg, 'gpkg_delivery_requested'):
            self._dlg.gpkg_delivery_requested.connect(self._on_gpkg_delivery)

    def _on_gpkg_delivery(self, path):
        """Slot du signal dialog `gpkg_delivery_requested` : charge un ZIP/GPKG en tache de fond."""
        if self._gpkg_load_task is not None:
            try:
                self._gpkg_load_task.cancel()
            except Exception:
                pass
            self._gpkg_load_task = None

        self._dlg.log_message(
            f"[GPKG] Preparation asynchrone de {os.path.basename(path)}...",
            'info',
        )
        if hasattr(self._dlg, 'loadGpkgBtn'):
            self._dlg.loadGpkgBtn.setEnabled(False)

        task = _LoadGpkgDeliveryTask(path, keep_extracted=True)
        self._gpkg_load_task = task
        task.taskCompleted.connect(self._on_gpkg_load_done)
        task.taskTerminated.connect(self._on_gpkg_load_failed)
        QgsApplication.taskManager().addTask(task)

    def _on_gpkg_load_done(self):
        task = self._gpkg_load_task
        self._gpkg_load_task = None
        if hasattr(self._dlg, 'loadGpkgBtn'):
            self._dlg.loadGpkgBtn.setEnabled(True)
        if not task or not task.caches:
            return
        try:
            summary = self._apply_gpkg_caches(
                task.caches,
                load_layers_in_qgis=True,
            )
            self._dlg.log_message(
                f"[GPKG] Pret : SRO={summary['sro'] or '?'} - "
                f"{summary['n_cables']} cables, {summary['n_bpe']} BPE, "
                f"{summary['n_attaches']} attaches.",
                'success',
            )
            if hasattr(self._dlg, 'bind_gpkg_layers'):
                self._dlg.bind_gpkg_layers(sro_label=summary.get('sro') or '')
            self._dlg._gpkg_caches = self._gpkg_caches
            self._dlg._gpkg_summary = summary
        except Exception as exc:
            self._dlg.log_message(f"[GPKG] Erreur chargement couches : {exc}", 'error')
            QgsMessageLog.logMessage(
                f"BatchOrchestrator._on_gpkg_load_done: {exc}",
                "PoleAerien", MSG_CRITICAL,
            )

    def _on_gpkg_load_failed(self):
        task = self._gpkg_load_task
        self._gpkg_load_task = None
        if hasattr(self._dlg, 'loadGpkgBtn'):
            self._dlg.loadGpkgBtn.setEnabled(True)
        if not task or task.isCanceled():
            return
        exc = task.error_exc
        if isinstance(exc, FileNotFoundError):
            self._dlg.log_message(f"[GPKG] Fichier introuvable : {exc}", 'error')
        elif isinstance(exc, ValueError):
            self._dlg.log_message(f"[GPKG] Livrable invalide : {exc}", 'error')
        else:
            self._dlg.log_message(f"[GPKG] Erreur inattendue : {exc}", 'error')
            QgsMessageLog.logMessage(
                f"BatchOrchestrator._on_gpkg_load_failed: {exc}",
                "PoleAerien", MSG_CRITICAL,
            )

    # ------------------------------------------------------------------
    #  Start / Cancel
    # ------------------------------------------------------------------
    # QP-06: Noms des couches temporaires creees par le plugin
    _TEMP_LAYER_PREFIXES = (
        'cables_recales_', 'cables_decoupes_', 'gracethd_cables_',
        'Poteaux BT absents', 'Poteaux FT absents',
        'anomalies_c6_', 'cables_anomalie',
    )


    _REPORT_PROGRESS_START = 85
    _REPORT_PROGRESS_END = 99

    # ------------------------------------------------------------------
    #  GeoPackage delivery : alimentation des caches batch
    # ------------------------------------------------------------------
    def load_gpkg_delivery(
        self,
        path,
        *,
        load_layers_in_qgis=False,
        keep_extracted=True,
        sro_hint='',
        gpkg_member=None,
    ):
        """Charge un livrable GeoPackage (ZIP ou .gpkg) et prepare les caches.

        Appelee par l'UI (dialog_v2) quand l'utilisateur pointe un livrable
        en remplacement de l'ancienne connexion PostgreSQL. Peuple
        `_fddcpi_cache` + `_extracted_data` pour que Comac et Police C6
        consomment les cables / BPE / attaches sans BDD.

        Args:
            path                : chemin absolu vers un .zip ou un .gpkg.
            load_layers_in_qgis : si True, ajoute les couches du GPKG dans
                                  le projet QGIS courant (mode QGIS).
            keep_extracted      : si path est un .zip, conserve le GPKG
                                  extrait apres retour (utile si
                                  load_layers_in_qgis=True).
            sro_hint            : force le code SRO (sinon infere).
            gpkg_member         : nom du .gpkg a extraire si le ZIP en
                                  contient plusieurs.

        Returns:
            dict : resume pour l'UI (sro, n_cables, n_bpe, n_attaches,
                   segments_layer, split_on_the_fly, source_gpkg).

        Raises:
            FileNotFoundError : fichier introuvable.
            ValueError        : extension inconnue, GPKG sans cables,
                                ou plusieurs GPKG dans le ZIP sans
                                gpkg_member.
        """
        from .gpkg_pipeline import (
            load_caches_from_gpkg,
            load_caches_from_zip,
        )

        lower = path.lower()
        if lower.endswith('.zip'):
            caches = load_caches_from_zip(
                path,
                sro_hint=sro_hint,
                keep_extracted=keep_extracted,
                gpkg_member=gpkg_member,
            )
        elif lower.endswith('.gpkg'):
            caches = load_caches_from_gpkg(path, sro_hint=sro_hint)
        else:
            raise ValueError(
                f"Extension inattendue : {path}. Attendu : .zip ou .gpkg"
            )

        return self._apply_gpkg_caches(
            caches,
            load_layers_in_qgis=load_layers_in_qgis,
        )

    def _apply_gpkg_caches(self, caches, *, load_layers_in_qgis=False):
        """Enregistre les caches GPKG sur le thread principal et charge les couches QGIS."""
        from .gpkg_pipeline import load_gpkg_layers_into_qgis
        from .batch_extractor import ExtractedData

        self._gpkg_caches = caches
        self._fddcpi_cache = {'sro': caches.sro, 'cables': caches.cables}

        if self._extracted_data is None:
            self._extracted_data = ExtractedData(sro=caches.sro)
        else:
            self._extracted_data.sro = caches.sro or self._extracted_data.sro
        self._extracted_data.cables = caches.cables
        self._extracted_data.cables_source = 'gpkg'
        self._extracted_data.bpe_list = caches.bpe_list
        self._extracted_data.attaches_raw = caches.attaches

        if load_layers_in_qgis and caches.source_gpkg \
                and os.path.isfile(caches.source_gpkg):
            try:
                load_gpkg_layers_into_qgis(
                    caches.source_gpkg,
                    group_name=f"SRO {caches.sro}" if caches.sro else None,
                )
            except Exception as exc:
                QgsMessageLog.logMessage(
                    f"Chargement QGIS des couches GPKG echoue : {exc}",
                    "PoleAerien", MSG_WARNING,
                )

        self._dlg.log_message(
            f"[GPKG] Livrable charge : SRO={caches.sro or '?'} - "
            f"{len(caches.cables)} cables, {len(caches.bpe_list)} BPE, "
            f"{len(caches.attaches)} attaches "
            f"(couche : {caches.segments_layer}"
            f"{', split a la volee' if caches.split_on_the_fly else ''})",
            'info',
        )
        return {
            'sro': caches.sro,
            'n_cables': len(caches.cables),
            'n_bpe': len(caches.bpe_list),
            'n_attaches': len(caches.attaches),
            'segments_layer': caches.segments_layer,
            'split_on_the_fly': caches.split_on_the_fly,
            'source_gpkg': caches.source_gpkg,
        }

    def has_gpkg_caches(self):
        """True si un livrable GeoPackage a ete charge dans ce batch."""
        return self._gpkg_caches is not None

    def gpkg_caches_summary(self):
        """Resume du livrable GeoPackage charge, ou None si aucun."""
        c = self._gpkg_caches
        if c is None:
            return None
        return {
            'sro': c.sro,
            'n_cables': len(c.cables),
            'n_bpe': len(c.bpe_list),
            'n_attaches': len(c.attaches),
            'segments_layer': c.segments_layer,
            'split_on_the_fly': c.split_on_the_fly,
            'source_gpkg': c.source_gpkg,
        }

    def clear_gpkg_delivery(self):
        """Decharge le livrable GeoPackage et vide les caches associes."""
        self._gpkg_caches = None
        self._fddcpi_cache = {}
        if self._extracted_data is not None:
            self._extracted_data.cables = None
            self._extracted_data.cables_source = ''
            self._extracted_data.bpe_list = []
            self._extracted_data.attaches_raw = []

    def _restore_gpkg_caches_if_any(self):
        """Reinjecte les caches GPKG dans _fddcpi_cache + _extracted_data.

        Appele par `_on_start` apres le reset des caches pour preserver un
        livrable GeoPackage deja charge par l'utilisateur.
        """
        c = self._gpkg_caches
        if c is None:
            return
        from .batch_extractor import ExtractedData
        self._fddcpi_cache = {'sro': c.sro, 'cables': c.cables}
        if self._extracted_data is None:
            self._extracted_data = ExtractedData(sro=c.sro)
        self._extracted_data.cables = c.cables
        self._extracted_data.cables_source = 'gpkg'
        self._extracted_data.bpe_list = c.bpe_list
        self._extracted_data.attaches_raw = c.attaches

    def _on_start(self, module_keys):
        self._module_progress = {}
        self._batch_results = {}
        self._diag_log_buffer.clear()
        self._diag_start_ts = time.time()
        self._extracted_data = None
        self._fddcpi_cache = {}
        self._sro_appuis_cache = {}
        # Reinjecte un livrable GeoPackage deja charge avant ce batch.
        self._restore_gpkg_caches_if_any()
        self._report_task = None
        self._prefetch_task = None
        self._pending_prefetch_keys = None
        reset_crs_cache()
        self._dlg.textBrowser.clear()

        # QP-06: Nettoyage couches temporaires du batch precedent
        self._cleanup_temp_layers()

        # Detect BE type (NGE vs Axione)
        det = self._detection()
        sro = self._dlg.sro if self._dlg.is_project_mode else ''
        if not sro and det.sro:
            sro = det.sro

        # --- Preflight checks (all-at-once before any module runs) ---
        issues = self._preflight_checks(module_keys, det)
        if issues:
            self._dlg.log_message(
                f"Verification pre-lancement : {len(issues)} probleme(s) detecte(s)",
                'error'
            )
            for issue in issues:
                self._dlg.log_message(f"  {issue}", 'warning')
            self._dlg.log_message(
                "Corrigez ces problemes puis relancez l'analyse.", 'error'
            )
            return

        # --- Data quality checks (warnings + blockers) ---
        blockers = self._preflight_data_quality(module_keys)
        if blockers:
            self._dlg.log_message(
                f"Qualite donnees : {len(blockers)} probleme(s) bloquant(s)",
                'error'
            )
            for b in blockers:
                self._dlg.log_message(f"  {b}", 'error')
            self._dlg.log_message(
                "Corrigez ces problemes puis relancez l'analyse.", 'error'
            )
            return

        self._dlg.set_running(True)

        # Modules qui n'ont besoin d'aucune couche QGIS ni BDD
        # Note: gespot_c6 utilise infra_pt_pot + etude_cap_ft pour la colonne ETUDE
        _FILE_ONLY_MODULES = set()
        needs_layers = set(module_keys) - _FILE_ONLY_MODULES

        # Project mode: load layers from DB asynchronously, then start batch
        if self._dlg.is_project_mode and needs_layers:
            self._pending_module_keys = module_keys
            self._start_async_layer_loading()
            return

        # Mode Couches QGIS: log les couches selectionnees avec leur nombre d'entites
        if needs_layers:
            self._log_selected_layers(module_keys)

        # P-02: pre-fetch PG data (fddcpi2, BPE, attaches) before tasks start
        # Enables COMAC + Police C6 to run in parallel (P-03)
        sro_for_fetch = self._dlg.sro if self._dlg.is_project_mode else ''
        if not sro_for_fetch:
            det_pre = self._detection()
            sro_for_fetch = det_pre.sro if det_pre else ''
        if not sro_for_fetch:
            from .qgis_utils import extract_sro_from_layer
            lyr_pot = self._lyr_pot()
            if lyr_pot:
                sro_for_fetch = extract_sro_from_layer(lyr_pot) or ''
        if self._start_async_pre_extract(module_keys, sro_for_fetch):
            return

        self._start_runner(module_keys, sro_for_fetch)

    def _preflight_checks(self, module_keys, det=None):
        """Validate ALL prerequisites upfront. Returns list of actionable messages (empty = OK)."""
        issues = []
        if det is None:
            det = self._detection()
        is_pm = self._dlg.is_project_mode

        # --- Export directory ---
        export_dir = self._export_dir()
        if export_dir and not os.path.isdir(export_dir):
            issues.append(
                f"Dossier d'export introuvable : {export_dir}"
            )
        elif export_dir:
            try:
                test_path = os.path.join(export_dir, '.poleaerien_test')
                with open(test_path, 'w') as _f:
                    pass
                os.remove(test_path)
            except OSError:
                issues.append(
                    f"Dossier d'export non inscriptible : {export_dir}"
                )

        # --- Layer checks (QGIS mode only) ---
        if not is_pm:
            lyr_pot = self._lyr_pot()
            lyr_cap = self._lyr_capft()
            lyr_com = self._lyr_comac()

            needs_pot = set(module_keys) & {'capft', 'comac', 'c6bd', 'police_c6', 'c6c3a'}
            needs_cap = set(module_keys) & {'capft', 'c6bd', 'c6c3a'}
            needs_com = set(module_keys) & {'comac'}

            if needs_pot and (not lyr_pot or not lyr_pot.isValid()):
                issues.append(
                    "Couche poteaux (infra_pt_pot) manquante ou invalide. "
                    "Chargez-la dans le projet QGIS "
                    "et selectionnez-la dans la liste deroulante."
                )
            elif needs_pot and lyr_pot and lyr_pot.featureCount() == 0:
                issues.append(
                    f"Couche {lyr_pot.name()} vide (aucun appui). "
                    "Verifiez le filtre ou le SRO applique."
                )

            if needs_cap and (not lyr_cap or not lyr_cap.isValid()):
                issues.append(
                    "Couche etude_cap_ft manquante ou invalide. "
                    "Chargez la couche de zonage CAP FT dans le projet QGIS."
                )

            if needs_com and (not lyr_com or not lyr_com.isValid()):
                issues.append(
                    "Couche etude_comac manquante ou invalide. "
                    "Chargez la couche de zonage COMAC dans le projet QGIS."
                )

        # --- Project mode: SRO ---
        if is_pm and not self._dlg.sro:
            issues.append(
                "Mode Projet : impossible de determiner le SRO depuis le nom du dossier. "
                "Format attendu : XXXXX-YYY-ZZZ-NNNNN (ex: 63041-B1I-PMZ-00003)."
            )

        # --- Per-module resource checks ---
        if 'capft' in module_keys and not det.has_capft:
            issues.append(
                "Module CAP_FT: dossier CAP FT non detecte. "
                "Noms acceptes: CAP FT, CAPFT, ETUDE CAP FT, KPFT."
            )

        if 'comac' in module_keys and not det.has_comac:
            issues.append(
                "Module COMAC: dossier COMAC non detecte. "
                "Noms acceptes: COMAC, Export COMAC, ETUDE COMAC."
            )

        c6_browse_modules = set(module_keys) & {'c6bd', 'police_c6'}
        if c6_browse_modules and not det.has_c6:
            issues.append(
                f"Module(s) {', '.join(sorted(c6_browse_modules))}: dossier C6 (= CAP FT) non detecte. "
                "Les annexes C6 sont les fichiers d'etude dans le dossier CAP FT."
            )

        if 'c6c3a' in module_keys:
            if not det.has_c6 and not det.has_c6_annexe:
                issues.append(
                    "Module C6-C3A-C7-BD: fichier C6 non detecte. "
                    "Attendu: dossier C6 ou Annexe C6 avec un .xlsx, ou CAP FT."
                )
            if not det.has_c7 and not det.has_c3a:
                issues.append(
                    "Module C6-C3A-C7-BD: ni fichier C7 ni C3A detecte. "
                    "Au moins un des deux est necessaire."
                )

        if 'gespot_c6' in module_keys:
            if not det.has_c6:
                issues.append(
                    "Module GESPOT vs C6: dossier C6 (= CAP FT) non detecte. "
                    "GESPOT provient de la couche data_gespot (GPKG ou BDD)."
                )

        return issues

    def _preflight_data_quality(self, module_keys):
        """Analyse qualite des donnees AVANT lancement.
        
        Delegue a preflight_checks.run_data_quality_checks() (module partage
        avec le bouton Diagnostic).
        
        Returns:
            list[str]: Problemes bloquants (vide = OK).
        """
        from .preflight_checks import run_data_quality_checks, format_check_log

        is_pm = self._dlg.is_project_mode
        lyr_pot = self._pm_lyr_pot if is_pm else self._lyr_pot()
        lyr_cap = self._pm_lyr_cap if is_pm else self._lyr_capft()
        lyr_com = self._pm_lyr_com if is_pm else self._lyr_comac()

        if is_pm and not lyr_pot:
            return []

        det = self._detection()
        checks = run_data_quality_checks(
            lyr_pot, lyr_cap, lyr_com,
            module_keys,
            auto_field_fn=self._auto_field,
            comac_dir=det.comac_dir if det.has_comac else '',
            capft_dir=det.capft_dir if det.has_capft else '',
        )

        self._dlg.log_message("--- Qualite donnees ---", 'info')
        blockers = []
        for check in checks:
            msg, level = format_check_log(check)
            self._dlg.log_message(msg, level)
            if check['level'] == 'BLOCKER':
                blockers.append(check['message'])

        if not blockers:
            self._dlg.log_message("Qualite donnees : OK", 'success')

        return blockers

    def _cleanup_temp_layers(self):
        """QP-06: Remove temporary layers created by previous batch runs."""
        from qgis.core import QgsProject, QgsMessageLog
        project = QgsProject.instance()
        to_remove = []
        for layer_id, layer in project.mapLayers().items():
            name = layer.name()
            if any(name.startswith(prefix) for prefix in self._TEMP_LAYER_PREFIXES):
                to_remove.append(layer_id)
        if to_remove:
            for lid in to_remove:
                project.removeMapLayer(lid)
            QgsMessageLog.logMessage(
                f"Mode Projet: {len(to_remove)} couches temporaires supprimees",
                "PoleAerien", MSG_INFO
            )
            self._dlg.log_message(
                f"{len(to_remove)} couche(s) temporaire(s) du batch precedent supprimee(s)",
                'info'
            )

    def _on_cancel(self):
        # Cancel layer loading task if in progress
        if self._layer_load_task:
            self._layer_load_task.cancel()
        if self._prefetch_task:
            self._prefetch_task.cancel()
        if self._report_task:
            self._report_task.cancel()
        self._runner.cancel()
        # Cancel active workflow
        for wf in (self._capft_wf, self._comac_wf,
                    self._c6bd_wf, self._police_wf, self._c6c3a_wf,
                    self._gespot_wf):
            if wf and hasattr(wf, 'cancel'):
                try:
                    wf.cancel()
                except Exception:
                    pass

        if self._prefetch_task and not self._runner.is_running:
            self._pending_prefetch_keys = None
            self._cleanup_project_mode_layers()
            self._dlg.reset_after_batch()
            self._dlg.log_message("Batch annulé.", 'warning')

    # ------------------------------------------------------------------
    #  Runner signal handlers
    # ------------------------------------------------------------------
    def _on_module_progress(self, key: str, pct: int) -> None:
        """Update per-module progress slot and recompute global bar."""
        self._module_progress[key] = max(0, min(100, pct))
        self._dlg.set_progress(self._compute_global_progress())

    def _compute_global_progress(self) -> int:
        """Average active module progress, capped at REPORT_PROGRESS_START."""
        slots = self._module_progress
        if not slots:
            return 0
        avg = sum(slots.values()) // len(slots)
        return min(avg, self._REPORT_PROGRESS_START - 1)

    def _set_report_progress(self, report_pct):
        bounded = max(0, min(100, int(report_pct)))
        span = self._REPORT_PROGRESS_END - self._REPORT_PROGRESS_START
        mapped = self._REPORT_PROGRESS_START + (bounded / 100.0) * span
        self._dlg.set_progress(int(mapped))

    def _finalize_batch_ui(self):
        self._create_absent_poteaux_layers()
        self._cleanup_project_mode_layers()
        self._release_batch_memory()
        self._dlg.reset_after_batch()
        self._runner.finalize_batch()

    def _release_batch_memory(self):
        """Libere les structures lourdes intermediaires en fin de batch."""
        import gc
        self._batch_results.clear()
        self._sro_appuis_cache.clear()
        if self._gpkg_caches is None:
            self._extracted_data = None
            self._fddcpi_cache.clear()
        gc.collect()

    def shutdown(self):
        """Arrete les taches actives et libere toutes les ressources au dechargement du plugin."""
        for task in (
            self._gpkg_load_task,
            self._layer_load_task,
            self._prefetch_task,
            self._report_task,
        ):
            if task is not None:
                try:
                    task.cancel()
                except Exception:
                    pass
        self._gpkg_load_task = None
        self._layer_load_task = None
        self._prefetch_task = None
        self._report_task = None
        try:
            self._runner.cancel()
        except Exception:
            pass
        self._cleanup_project_mode_layers()
        self.clear_gpkg_delivery()
        self._release_batch_memory()

    def _start_report_task(self):
        if not self._batch_results:
            self._finalize_batch_ui()
            return
        self._dlg.set_progress_step("Generation du rapport unifie...")
        self._dlg.log_message("Generation du rapport unifie...", 'info')
        det = self._detection()
        self._report_task = UnifiedReportExportTask({
            'batch_results': self._batch_results,
            'export_dir': self._export_dir(),
            'include_comac_drawings': self._dlg.include_comac_drawings,
            'include_data_dictionary': self._dlg.include_data_dictionary,
            'sro': det.sro if det else '',
        })
        self._report_task.signals.progress.connect(self._on_report_progress)
        self._report_task.signals.message.connect(self._relay_message)
        self._report_task.signals.finished.connect(self._on_report_done)
        self._report_task.signals.error.connect(self._on_report_error)
        QgsApplication.taskManager().addTask(self._report_task)

    def _on_report_done(self, result):
        self._report_task = None
        filepath = result.get('filepath')
        self._dlg.log_message("Rapport unifie genere avec succes.", 'success')
        if filepath:
            self._dlg.log_result_link(filepath)
        self._finalize_batch_ui()

    def _on_report_error(self, err):
        task = self._report_task
        self._report_task = None
        if task and task.isCanceled():
            self._cleanup_project_mode_layers()
            self._dlg.reset_after_batch()
            self._dlg.log_message("Batch annulé.", 'warning')
            return
        self._dlg.log_message(f"Erreur rapport: {err}", 'error')
        self._do_diag(report_error=str(err))
        self._finalize_batch_ui()

    def _on_module_started(self, key, _idx, _total):
        self._module_start_times[key] = time.time()
        self._module_progress[key] = 0
        self._dlg.set_progress(max(self._compute_global_progress(), 2))
        from .batch_runner import MODULE_REGISTRY
        name = MODULE_REGISTRY.get(key, key)
        self._dlg.set_progress_step(f"{name} ({_idx + 1}/{_total})")

    def _on_module_finished(self, key, success, msg):
        self._module_progress[key] = 100
        self._dlg.set_progress(self._compute_global_progress())

    def _on_runner_log(self, msg, level):
        if level in ('error', 'warning', 'critical'):
            self._diag_log_buffer.append({'level': level, 'message': msg})

    def _do_diag(self, runner_results=None, cancelled=False, report_error=None):
        self._dlg.log_message("[Diag] Envoi diagnostic d'execution...", 'info')
        try:
            sro = self._dlg.sro if self._dlg.is_project_mode else ''
            if not sro:
                det = self._detection()
                sro = det.sro if det else ''

            durations = {}
            for key, t0 in self._module_start_times.items():
                durations[key] = round(time.time() - t0, 2)

            modules = list(self._module_start_times.keys())
            if runner_results is None:
                runner_results = {}

            record = self._diag.build_record(
                modules=modules,
                batch_results=self._batch_results,
                runner_results=runner_results,
                durations=durations,
                error_buffer=list(self._diag_log_buffer),
                sro=sro,
            )
            if cancelled:
                record['cancelled'] = True
            if report_error:
                record['report_error'] = report_error

            self._diag.submit(record)
            self._dlg.log_message("[Diag] Diagnostic transmis (voir Message Log QGIS).", 'success')
        except Exception as exc:
            self._dlg.log_message(f"[Diag] Erreur collecte : {exc}", 'warning')
            QgsMessageLog.logMessage(
                f"usage_diag: erreur collecte : {exc}",
                "PoleAerien",
                MSG_WARNING,
            )

    def _on_batch_finished(self, results):
        self._log_batch_recap(results)
        self._do_diag(runner_results=results)
        self._start_report_task()

    def _log_batch_recap(self, runner_results):
        """Log a structured recap with metrics from each completed module."""
        if not self._batch_results:
            return

        self._dlg.log_message("--- RECAPITULATIF ---", 'info')

        elapsed = {}
        for key, t0 in self._module_start_times.items():
            elapsed[key] = time.time() - t0

        sro = self._dlg.sro if self._dlg.is_project_mode else ''
        if not sro:
            det = self._detection()
            sro = det.sro if det else ''

        for key, result in self._batch_results.items():
            name = {
                'capft': 'CAP_FT', 'comac': 'COMAC',
                'c6bd': 'C6 vs Donnees Projet', 'police_c6': 'POLICE C6', 'c6c3a': 'C6-C3A-C7-BD',
                'gespot_c6': 'GESPOT vs C6',
            }.get(key, key)

            dur = elapsed.get(key)
            dur_str = f" ({dur:.1f}s)" if dur else ""

            if dur:
                try:
                    from .perf_logger import PerfLogger
                    run_res = runner_results.get(key, {})
                    status = 'ok' if run_res.get('success') else 'error'
                    PerfLogger.record(key, 'module_total',
                                      dur * 1000, sro=sro or '', status=status)
                except Exception:
                    pass

            metrics = self._extract_metrics(key, result)
            if metrics:
                self._dlg.log_message(f"  {name}{dur_str}: {metrics}", 'info')
            else:
                run_res = runner_results.get(key, {})
                status = 'OK' if run_res.get('success') else 'ERREUR'
                self._dlg.log_message(f"  {name}{dur_str}: {status}", 'info')

    def _extract_metrics(self, key, result):
        """Extract human-readable metrics from a module result dict."""
        parts = []

        if key == 'capft':
            resultats = result.get('resultats')
            dico_qgis = result.get('dico_qgis', {})
            dico_excel = result.get('dico_excel', {})
            nb_etudes = len(dico_qgis)
            nb_ok = len(resultats[2]) if resultats and len(resultats) > 2 else 0
            nb_fiches = sum(len(v) for v in dico_excel.values()) if dico_excel else 0
            parts.append(f"{nb_etudes} etudes, {nb_ok} correspondances, {nb_fiches} fiches Excel")

        elif key == 'comac':
            resultats = result.get('resultats')
            verif = result.get('verif_cables', [])
            spatial = result.get('dico_spatial_match', {})
            dico_qgis = result.get('dico_qgis', {})
            nb_etudes = len(dico_qgis)
            nb_name = len(resultats[2]) if resultats and len(resultats) > 2 else 0
            nb_spatial = len(spatial)
            parts.append(f"{nb_etudes} etudes, {nb_name + nb_spatial} correspondances ({nb_name} nom, {nb_spatial} spatial)")
            if verif:
                nb_ecart = sum(1 for v in verif if v.get('statut') == 'ECART')
                nb_absent = sum(1 for v in verif if v.get('statut', '').startswith('ABSENT') and not v.get('statut', '').endswith('_NON_RESOLU') and not v.get('statut', '').endswith('_HORS_PERIMETRE'))
                nb_non_resolu = sum(1 for v in verif if v.get('statut', '').endswith('_NON_RESOLU'))
                nb_hors_p = sum(1 for v in verif if v.get('statut', '').endswith('_HORS_PERIMETRE'))
                if nb_ecart or nb_absent or nb_non_resolu or nb_hors_p:
                    msg = f"{nb_ecart} ecarts cables, {nb_absent} absents GPKG"
                    if nb_non_resolu:
                        msg += f", {nb_non_resolu} non resolus commune/code"
                    if nb_hors_p:
                        msg += f", {nb_hors_p} hors perimetre"
                    parts.append(msg)
                else:
                    parts.append("cables OK")

        elif key == 'police_c6':
            stats = result.get('stats', [])
            if stats:
                total_ecart = sum(s.get('nb_ecart', 0) for s in stats)
                total_absent = sum(s.get('nb_absent', 0) for s in stats)
                total_boitier = sum(s.get('nb_boitier_err', 0) for s in stats)
                parts.append(f"{len(stats)} etudes")
                anomalies = total_ecart + total_absent + total_boitier
                if anomalies:
                    parts.append(f"{anomalies} anomalie(s)")
                else:
                    parts.append("aucune anomalie")

        elif key == 'c6bd':
            if result.get('success'):
                parts.append("OK")

        elif key == 'c6c3a':
            if result.get('success'):
                parts.append("OK")

        elif key == 'gespot_c6':
            nb_c = result.get('nb_compares', 0)
            nb_ok = result.get('nb_ok', 0)
            nb_e = result.get('nb_ecart', 0)
            nb_a = result.get('nb_anomalies', 0)
            parts.append(f"{nb_c} compares, {nb_ok} OK, {nb_e} ecarts")
            if nb_a:
                parts.append(f"{nb_a} anomalie(s) source")

        return ", ".join(parts) if parts else ""

    def _on_report_progress(self, progress):
        self._set_report_progress(progress)

    def _create_absent_poteaux_layers(self):
        """Create temporary point layers for poteaux absent from QGIS but with coords in Excel."""
        try:
            import warnings
            from qgis.core import (
                QgsVectorLayer, QgsFeature, QgsGeometry,
                QgsPointXY, QgsField, QgsProject,
            )
            STYLE_DIR = os.path.join(os.path.dirname(__file__), 'styles')

            sources = {
                'comac': {
                    'points': [],
                    'layer_name': 'Poteaux BT absents QGIS (COMAC)',
                    'style': os.path.join(STYLE_DIR, 'pot_bt_abs_qgis.qml'),
                },
                'capft': {
                    'points': [],
                    'layer_name': 'Poteaux FT absents QGIS (CAP_FT)',
                    'style': os.path.join(STYLE_DIR, 'pot_ft_abs_qgis.qml'),
                },
            }

            for pt in self._batch_results.get('comac', {}).get('coords_absents', []):
                sources['comac']['points'].append(pt)

            for pt in self._batch_results.get('capft', {}).get('coords_absents', []):
                sources['capft']['points'].append(pt)

            for src_key, src_data in sources.items():
                pts = src_data['points']
                if not pts:
                    continue

                lyr = QgsVectorLayer(
                    "Point?crs=EPSG:2154", src_data['layer_name'], "memory"
                )
                pr = lyr.dataProvider()
                with warnings.catch_warnings():
                    warnings.filterwarnings("ignore", category=DeprecationWarning)
                    pr.addAttributes([
                        QgsField("nom", FIELD_TYPE_STRING),
                        QgsField("source", FIELD_TYPE_STRING),
                        QgsField("fichier", FIELD_TYPE_STRING),
                    ])
                lyr.updateFields()

                features = []
                for pt in pts:
                    feat = QgsFeature(lyr.fields())
                    feat.setGeometry(QgsGeometry.fromPointXY(QgsPointXY(pt['x'], pt['y'])))
                    feat.setAttribute("nom", pt['nom'])
                    feat.setAttribute("source", src_key.upper())
                    feat.setAttribute("fichier", pt['fichier'])
                    features.append(feat)

                pr.addFeatures(features)
                lyr.updateExtents()
                QgsProject.instance().addMapLayer(lyr)
                show_feature_count(lyr)

                qml_path = src_data['style']
                if os.path.exists(qml_path):
                    lyr.loadNamedStyle(qml_path)
                    lyr.triggerRepaint()

                self._dlg.log_message(
                    f"Couche '{src_data['layer_name']}' ajoutee ({len(features)} poteaux).",
                    'info'
                )

        except Exception as e:
            QgsMessageLog.logMessage(
                f"Erreur creation couche absents: {e}", "PoleAerien", MSG_WARNING
            )

    def _on_batch_cancelled(self):
        self._cleanup_project_mode_layers()
        self._dlg.reset_after_batch()
        self._dlg.log_message("Batch annulé.", 'warning')
        self._do_diag(cancelled=True)
        self._release_batch_memory()

    # ------------------------------------------------------------------
    #  Helper: get common params from dialog
    # ------------------------------------------------------------------
    def _detection(self) -> DetectionResult:
        return self._dlg.detection

    def _export_dir(self) -> str:
        return self._dlg.get_export_dir()

    def _lyr_pot(self):
        if self._pm_lyr_pot:
            return self._pm_lyr_pot
        return self._dlg.comboInfraPtPot.currentLayer()

    def _lyr_capft(self):
        if self._pm_lyr_cap:
            return self._pm_lyr_cap
        return self._dlg.comboEtudeCapFt.currentLayer()

    def _lyr_comac(self):
        if self._pm_lyr_com:
            return self._pm_lyr_com
        return self._dlg.comboEtudeComac.currentLayer()

    def _log_selected_layers(self, module_keys):
        """Log selected QGIS layers with feature counts at batch start."""
        mods = set(module_keys)
        layers = [
            ("infra_pt_pot", self._lyr_pot(),
             mods & {'capft', 'comac', 'c6bd', 'police_c6', 'gespot_c6'}),
            ("etude_cap_ft", self._lyr_capft(),
             mods & {'capft', 'c6bd', 'police_c6', 'gespot_c6'}),
            ("etude_comac", self._lyr_comac(),
             mods & {'comac'}),
        ]
        for label, lyr, needed in layers:
            if not needed:
                continue
            if lyr and lyr.isValid():
                self._dlg.log_message(
                    f"  {lyr.name()}: {lyr.featureCount()} entites",
                    'info'
                )
            else:
                self._dlg.log_message(
                    f"  {label}: non selectionnee", 'warning'
                )

    # ------------------------------------------------------------------
    #  Project Mode: async DB layer loading
    # ------------------------------------------------------------------
    def _start_async_layer_loading(self):
        """Lance le chargement asynchrone des couches depuis PostgreSQL."""
        sro = self._dlg.sro or ''
        if not sro:
            det = self._detection()
            sro = det.sro if det else ''
        if not sro:
            self._dlg.log_message(
                "[BDD] SRO non determine : impossible de charger les couches.",
                'error',
            )
            self._pending_module_keys = None
            self._dlg.reset_after_batch()
            return

        try:
            from .export_zip_gpkg import _resolve_pg_params
            pg_params = _resolve_pg_params()
            if not pg_params or not pg_params.get('host'):
                self._dlg.log_message(
                    "[BDD] Connexion PostgreSQL non configuree.",
                    'error',
                )
                self._pending_module_keys = None
                self._dlg.reset_after_batch()
                return
        except Exception as exc:
            self._dlg.log_message(f"[BDD] Erreur parametres PG : {exc}", 'error')
            self._pending_module_keys = None
            self._dlg.reset_after_batch()
            return

        self._dlg.log_message(
            f"[BDD] Chargement des couches pour SRO={sro}...", 'info',
        )
        self._layer_load_task = _LoadProjectLayersTask(sro, pg_params)
        self._layer_load_task.taskCompleted.connect(self._on_layers_loaded)
        self._layer_load_task.taskTerminated.connect(self._on_layers_failed)
        QgsApplication.taskManager().addTask(self._layer_load_task)

    def _on_layers_failed(self):
        """Callback : echec du chargement BDD."""
        task = self._layer_load_task
        self._layer_load_task = None
        msg = "Chargement BDD echoue"
        if task and task.error_msg:
            msg = task.error_msg
        self._dlg.log_message(f"[BDD] {msg}", 'error')
        self._pending_module_keys = None
        self._dlg.reset_after_batch()

    def _on_layers_loaded(self):
        """Main thread callback: register layers in QgsProject, start batch."""
        task = self._layer_load_task
        self._layer_load_task = None

        if not task:
            return

        # Add layers to QGIS project so that get_layer_safe() and
        # other name-based lookups work inside workflows.
        self._pm_lyr_pot = task.lyr_pot
        if self._pm_lyr_pot:
            QgsProject.instance().addMapLayer(self._pm_lyr_pot)
            show_feature_count(self._pm_lyr_pot)
            self._dlg.log_message(
                f"  infra_pt_pot: {task.counts.get('infra_pt_pot', '?')} poteaux", 'success'
            )

        # etude_cap_ft (optional)
        if task.lyr_cap:
            self._pm_lyr_cap = task.lyr_cap
            QgsProject.instance().addMapLayer(self._pm_lyr_cap)
            show_feature_count(self._pm_lyr_cap)
            self._dlg.log_message(
                f"  etude_cap_ft: {task.counts.get('etude_cap_ft', '?')} zones", 'success'
            )
        else:
            self._dlg.log_message(
                "  etude_cap_ft: non disponible (modules CAP FT limites)", 'warning'
            )
            self._pm_lyr_cap = None

        # etude_comac (optional)
        if task.lyr_com:
            self._pm_lyr_com = task.lyr_com
            QgsProject.instance().addMapLayer(self._pm_lyr_com)
            show_feature_count(self._pm_lyr_com)
            self._dlg.log_message(
                f"  etude_comac: {task.counts.get('etude_comac', '?')} zones", 'success'
            )
        else:
            self._dlg.log_message(
                "  etude_comac: non disponible (module COMAC limite)", 'warning'
            )
            self._pm_lyr_com = None

        # data_gespot (optional, for GESPOT vs C6 module)
        if task.lyr_gespot:
            self._pm_lyr_gespot = task.lyr_gespot
            QgsProject.instance().addMapLayer(self._pm_lyr_gespot)
            show_feature_count(self._pm_lyr_gespot)
            self._dlg.log_message(
                f"  data_gespot: {task.counts.get('data_gespot', '?')} appuis", 'success'
            )
        else:
            self._pm_lyr_gespot = None

        # Data quality checks on loaded layers (Mode Projet)
        keys = self._pending_module_keys
        self._pending_module_keys = None
        if keys:
            blockers = self._preflight_data_quality(keys)
            if blockers:
                self._dlg.log_message(
                    f"Qualite donnees : {len(blockers)} probleme(s) bloquant(s)", 'error'
                )
                for b in blockers:
                    self._dlg.log_message(f"  {b}", 'error')
                self._dlg.log_message(
                    "Corrigez ces problemes puis relancez l'analyse.", 'error'
                )
                self._cleanup_project_mode_layers()
                self._dlg.reset_after_batch()
                return

            # P-02: pre-fetch PG data before tasks start (project mode)
            sro_pm = self._dlg.sro or ''
            if not sro_pm and self._pm_lyr_pot:
                from .qgis_utils import extract_sro_from_layer
                sro_pm = extract_sro_from_layer(self._pm_lyr_pot) or ''
            if self._start_async_pre_extract(keys, sro_pm):
                return

            self._start_runner(keys, sro_pm)

    def _start_runner(self, module_keys, sro=''):
        """Pre-extract shared WKB appuis in one pass when COMAC and Police C6 both run."""
        if {'comac', 'police_c6'}.issubset(set(module_keys)) and not self._sro_appuis_cache:
            lyr_pot = self._lyr_pot()
            if lyr_pot and lyr_pot.isValid():
                from .cable_analyzer import extraire_appuis_wkb_both
                from .qgis_utils import extract_sro_from_layer
                sro_val = sro or (self._dlg.sro if self._dlg.is_project_mode else '')
                if not sro_val:
                    det = self._detection()
                    sro_val = (det.sro if det else '') or extract_sro_from_layer(lyr_pot) or ''
                wkb_com, wkb_plain = extraire_appuis_wkb_both(lyr_pot)
                self._sro_appuis_cache = {
                    'sro': sro_val,
                    'appuis_wkb': wkb_com,
                    'appuis_wkb_commune': wkb_com,
                    'appuis_wkb_plain': wkb_plain,
                }
        self._runner.start(module_keys)

    def _start_async_pre_extract(self, module_keys, sro):
        cable_modules = set(module_keys) & {'comac', 'police_c6'}
        if not cable_modules or not sro:
            return False

        try:
            from .export_zip_gpkg import _resolve_pg_params
            pg_params = _resolve_pg_params()
            if not pg_params or not pg_params.get('host'):
                return False
        except Exception:
            return False

        self._pending_prefetch_keys = list(module_keys)
        self._prefetch_task = _PreExtractPgTask(sro, pg_params)
        self._prefetch_task.taskCompleted.connect(self._on_pre_extract_done)
        self._prefetch_task.taskTerminated.connect(self._on_pre_extract_failed)
        QgsApplication.taskManager().addTask(self._prefetch_task)
        return True

    def _on_pre_extract_done(self):
        task = self._prefetch_task
        self._prefetch_task = None
        keys = self._pending_prefetch_keys
        self._pending_prefetch_keys = None
        if task and task.data:
            self._extracted_data = task.data
            if task.data.cables is not None:
                self._fddcpi_cache = {'sro': task.data.sro, 'cables': task.data.cables}
                self._dlg.log_message(
                    f"[P-02] cables pre-fetches: {len(task.data.cables)} segments (source: {task.data.cables_source})",
                    'info'
                )
                self._load_fddcpiax_layer(task)
        if keys:
            self._start_runner(keys, task.data.sro if task and task.data else '')

    def _load_fddcpiax_layer(self, task):
        """Charge la couche cables_decoupes (fddcpiax) dans le projet QGIS."""
        cables = task.data.cables
        if not cables:
            return
        try:
            from qgis.core import QgsVectorLayer, QgsFeature, QgsGeometry, QgsField, QgsProject
            from .compat import FIELD_TYPE_LONGLONG, FIELD_TYPE_INT, FIELD_TYPE_STRING, FIELD_TYPE_DOUBLE
            import warnings

            sro_safe = (task.data.sro or 'unknown').replace('/', '_')
            layer_name = f"cables_decoupes_{sro_safe}"

            layer = QgsVectorLayer("LineString?crs=EPSG:2154", layer_name, "memory")
            provider = layer.dataProvider()
            with warnings.catch_warnings():
                warnings.filterwarnings("ignore", category=DeprecationWarning)
                provider.addAttributes([
                    QgsField("gid_dc2", FIELD_TYPE_LONGLONG),
                    QgsField("gid_dc", FIELD_TYPE_LONGLONG),
                    QgsField("gid", FIELD_TYPE_INT),
                    QgsField("cab_capa", FIELD_TYPE_INT),
                    QgsField("cab_type", FIELD_TYPE_STRING),
                    QgsField("cb_etiquet", FIELD_TYPE_STRING),
                    QgsField("posemode", FIELD_TYPE_INT),
                    QgsField("length", FIELD_TYPE_DOUBLE),
                ])
            layer.updateFields()

            features = []
            for c in cables:
                if not c.geom_wkt:
                    continue
                geom = QgsGeometry.fromWkt(c.geom_wkt)
                if geom.isNull() or geom.isEmpty():
                    continue
                feat = QgsFeature(layer.fields())
                feat.setGeometry(geom)
                feat.setAttribute("gid_dc2", c.gid_dc2)
                feat.setAttribute("gid_dc", c.gid_dc)
                feat.setAttribute("gid", c.gid)
                feat.setAttribute("cab_capa", c.cab_capa)
                feat.setAttribute("cab_type", c.cab_type)
                feat.setAttribute("cb_etiquet", c.cb_etiquet)
                feat.setAttribute("posemode", c.posemode)
                feat.setAttribute("length", c.length)
                features.append(feat)

            provider.addFeatures(features)
            layer.updateExtents()
            QgsProject.instance().addMapLayer(layer)

            style_path = os.path.join(
                os.path.dirname(__file__), 'styles', 'cables_dc.qml'
            )
            if os.path.exists(style_path):
                layer.loadNamedStyle(style_path)
                layer.triggerRepaint()

            self._dlg.log_message(
                f"[P-02] couche '{layer_name}' chargee: {len(features)} cables decoupes",
                'info'
            )
        except Exception as exc:
            QgsMessageLog.logMessage(
                f"[P-02] chargement couche cables_decoupes echoue: {exc}",
                "PoleAerien", MSG_WARNING,
            )

    def _on_pre_extract_failed(self):
        task = self._prefetch_task
        self._prefetch_task = None
        keys = self._pending_prefetch_keys
        self._pending_prefetch_keys = None
        if task and task.isCanceled():
            return
        if task and task.error_msg:
            self._dlg.log_message(f"[P-02] pre-extraction ignoree: {task.error_msg}", 'warning')
        if keys:
            self._start_runner(keys, task.data.sro if task and task.data else '')

    def _log_spatial_sro(self, task):
        """Extract SRO from etude zones found by spatial intersection.

        Compares the SRO found in etude zones with the detected SRO
        (from folder name or GraceTHD). Logs a warning if they differ.
        """
        detected_sro = self._dlg.sro or ''
        found_sros = set()

        for lyr in (task.lyr_cap, task.lyr_com):
            if not lyr or not lyr.isValid():
                continue
            field_names = [f.name().lower() for f in lyr.fields()]
            if 'sro' not in field_names:
                continue
            for feat in lyr.getFeatures():
                val = feat['sro']
                if val and str(val).strip():
                    found_sros.add(str(val).strip())

        if not found_sros:
            self._dlg.log_message(
                "  SRO etudes: aucune zone etude trouvee par intersection spatiale", 'warning'
            )
            return

        sro_list = sorted(found_sros)
        self._dlg.log_message(
            f"  SRO etudes (intersection spatiale): {', '.join(sro_list)}", 'info'
        )

        if detected_sro and detected_sro not in found_sros:
            self._dlg.log_message(
                f"  ATTENTION: SRO detecte ({detected_sro}) different du SRO "
                f"des zones etude ({', '.join(sro_list)}). "
                f"Les zones etude trouvees par intersection spatiale seront utilisees.", 'warning'
            )

    def _on_layers_load_failed(self):
        """Main thread callback: layer loading failed or was cancelled."""
        task = self._layer_load_task
        self._layer_load_task = None
        self._pending_module_keys = None

        msg = (task.error_msg if task and task.error_msg
               else "Mode Projet: erreur chargement couches")
        self._dlg.log_message(msg, 'error')
        self._dlg.reset_after_batch()

    def _cleanup_project_mode_layers(self):
        """Remove temporary DB layers from QgsProject after batch."""
        project = QgsProject.instance()
        removed = 0
        for lyr in (self._pm_lyr_pot, self._pm_lyr_cap,
                    self._pm_lyr_com, self._pm_lyr_gespot):
            if lyr is not None:
                try:
                    project.removeMapLayer(lyr.id())
                    removed += 1
                except Exception:
                    pass
        if removed:
            QgsMessageLog.logMessage(
                f"Mode Projet: {removed} couche(s) temporaire(s) supprimee(s)",
                "PoleAerien", MSG_INFO,
            )
        self._pm_lyr_pot = None
        self._pm_lyr_cap = None
        self._pm_lyr_com = None
        self._pm_lyr_gespot = None

    def _auto_field(self, layer, _patterns=None):
        """Auto-detect study field name from layer. Delegue a qgis_utils."""
        result = _detect_etude_field(layer, context="BatchOrchestrator")
        if result:
            return result
        # Fallback: first text field
        if layer and layer.isValid():
            for field in layer.fields():
                if field.typeName().lower() in ('string', 'text', 'varchar'):
                    return field.name()
        return ''

    # ------------------------------------------------------------------
    #  Module launchers
    # ------------------------------------------------------------------
    def _launch_capft(self, on_done):
        """Launch CAP_FT module."""
        self._done_callbacks['capft'] = on_done
        det = self._detection()

        lyr_pot = self._lyr_pot()
        lyr_cap = self._lyr_capft()
        col_cap = self._auto_field(lyr_cap)
        export_dir = self._export_dir()

        if not lyr_pot or not lyr_cap:
            on_done(False, "Couches QGIS non sélectionnées")
            return
        if not det.has_capft:
            on_done(False, "Dossier CAP FT non détecté")
            return

        self._safe_connect_once(
            self._capft_wf.analysis_finished, self._on_capft_analysis
        )
        self._safe_connect_once(
            self._capft_wf.error_occurred, self._on_capft_error
        )
        self._safe_connect_once(
            self._capft_wf.message_received, self._relay_message
        )

        self._capft_wf.start_analysis(
            lyr_pot, lyr_cap, col_cap,
            det.capft_dir, export_dir
        )

    def _on_capft_analysis(self, result):
        err_type = result.get('error_type', '')
        if err_type:
            cb = self._done_callbacks.pop('capft', None)
            if cb:
                cb(False, _format_validation_error(result))
            return
        self._batch_results['capft'] = result
        cb = self._done_callbacks.pop('capft', None)
        if cb:
            cb(True, 'OK')

    def _on_capft_error(self, err):
        cb = self._done_callbacks.pop('capft', None)
        if cb:
            cb(False, str(err))

    # ------------------------------------------------------------------
    def _launch_c6bd(self, on_done):
        """Launch C6 vs Donnees Projet module."""
        self._done_callbacks['c6bd'] = on_done
        det = self._detection()

        lyr_pot = self._lyr_pot()
        lyr_cap = self._lyr_capft()
        col_cap = self._auto_field(lyr_cap)
        export_dir = self._export_dir()

        if not lyr_pot or not lyr_cap:
            on_done(False, "Couches QGIS non selectionnees")
            return
        if not det.has_c6:
            on_done(False, "Dossier C6 (= CAP FT) non detecte")
            return

        self._safe_connect_once(
            self._c6bd_wf.analysis_finished, self._on_c6bd_analysis
        )
        self._safe_connect_once(
            self._c6bd_wf.error_occurred, self._on_c6bd_error
        )
        self._safe_connect_once(
            self._c6bd_wf.message_received, self._relay_message
        )

        self._c6bd_wf.start_analysis(
            lyr_pot, lyr_cap, col_cap,
            det.c6_dir, export_dir
        )

    def _on_c6bd_analysis(self, result):
        err_type = result.get('error_type', '')
        if err_type:
            cb = self._done_callbacks.pop('c6bd', None)
            if cb:
                cb(False, _format_validation_error(result))
            return
        self._batch_results['c6bd'] = result
        cb = self._done_callbacks.pop('c6bd', None)
        if cb:
            cb(True, 'OK')

    def _on_c6bd_error(self, err):
        cb = self._done_callbacks.pop('c6bd', None)
        if cb:
            cb(False, str(err))

    # ------------------------------------------------------------------
    def _launch_comac(self, on_done):
        """Launch COMAC module."""
        self._done_callbacks['comac'] = on_done
        det = self._detection()

        lyr_pot = self._lyr_pot()
        lyr_comac = self._lyr_comac()
        col_comac = self._auto_field(lyr_comac)
        export_dir = self._export_dir()

        if not lyr_pot or not lyr_comac:
            on_done(False, "Couches QGIS non sélectionnées")
            return
        if not det.has_comac:
            on_done(False, "Dossier COMAC non détecté")
            return

        self._safe_connect_once(
            self._comac_wf.analysis_finished, self._on_comac_analysis
        )
        self._safe_connect_once(
            self._comac_wf.error_occurred, self._on_comac_error
        )
        self._safe_connect_once(
            self._comac_wf.message_received, self._relay_message
        )

        # Pass cached data if available (e.g. Police C6 ran first)
        fddcpi = self._fddcpi_cache.get('cables') if self._fddcpi_cache else None
        sro_appuis = self._sro_appuis_cache if self._sro_appuis_cache else None
        sro = self._dlg.sro if self._dlg.is_project_mode else None
        if not sro and det.sro:
            sro = det.sro
        spatial_tol = getattr(self._dlg, 'spatial_tolerance', 7.5)
        self._comac_wf.start_analysis(
            lyr_pot, lyr_comac, col_comac,
            det.comac_dir, export_dir,
            fddcpi_cache=fddcpi,
            sro_appuis_cache=sro_appuis,
            sro=sro,
            spatial_tolerance=spatial_tol
        )

    def _on_comac_analysis(self, result):
        err_type = result.get('error_type', '')
        if err_type:
            cb = self._done_callbacks.pop('comac', None)
            if cb:
                cb(False, _format_validation_error(result))
            return
        # Cache fddcpi2 data for other modules in this batch
        cables_all = result.get('fddcpi_cables_all')
        sro = result.get('fddcpi_sro')
        if cables_all is not None and sro:
            self._fddcpi_cache = {'sro': sro, 'cables': cables_all}
        # Cache SRO + appuis WKB for Police C6
        appuis_wkb = result.get('appuis_wkb')
        if sro and appuis_wkb is not None and not self._sro_appuis_cache:
            self._sro_appuis_cache = {
                'sro': sro,
                'appuis_wkb': appuis_wkb,
                'appuis_wkb_commune': appuis_wkb,
            }
        self._batch_results['comac'] = result
        cb = self._done_callbacks.pop('comac', None)
        if cb:
            cb(True, 'OK')

    def _on_comac_error(self, err):
        cb = self._done_callbacks.pop('comac', None)
        if cb:
            cb(False, str(err))

    # ------------------------------------------------------------------
    def _launch_police_c6(self, on_done):
        """Launch Police C6 module."""
        self._done_callbacks['police_c6'] = on_done
        det = self._detection()

        if not det.has_c6:
            on_done(False, "Dossier C6 non détecté")
            return

        # In project mode, skip check_layers_exist (layer comes from DB loader)
        if not self._dlg.is_project_mode:
            liste_absent = self._police_wf.check_layers_exist()
            if liste_absent:
                on_done(False, f"Couches manquantes: {', '.join(liste_absent)}")
                return

        self._safe_connect_once(
            self._police_wf.analysis_finished, self._on_police_done
        )
        self._safe_connect_once(
            self._police_wf.error_occurred, self._on_police_error
        )
        self._safe_connect_once(
            self._police_wf.message_received, self._relay_message
        )

        params = {}

        # Project mode: inject layer reference and SRO directly
        if self._pm_lyr_pot:
            params['layer_appuis'] = self._pm_lyr_pot
            params['sro'] = self._dlg.sro
        elif det.sro:
            params['sro'] = det.sro

        # Pass study layer info so auto-browse can get study names
        lyr_cap = self._lyr_capft()
        if lyr_cap:
            params['table_etude'] = lyr_cap.name()
            col = self._auto_field(lyr_cap)
            if col:
                params['colonne_etude'] = col

        # Inject cached data if available (e.g. COMAC ran first)
        if self._fddcpi_cache:
            params['fddcpi_cables_cache'] = self._fddcpi_cache.get('cables')
        if self._sro_appuis_cache:
            params['sro_appuis_cache'] = self._sro_appuis_cache

        # En batch, pas d'export individuel: tout va dans le rapport unifie
        params['skip_individual_export'] = True

        # P-02: inject pre-fetched BPE + attaches so task skips PG queries
        if self._extracted_data:
            if self._extracted_data.bpe_list:
                params['bpe_list_cache'] = self._extracted_data.bpe_list
            if self._extracted_data.attaches_raw:
                params['attaches_cache'] = self._extracted_data.attaches_raw

        c6_path = det.c6_dir
        self._police_wf.reset_logic()

        if os.path.isdir(c6_path):
            params['repertoire_c6'] = c6_path
            self._police_wf.run_analysis_auto_browse(params)
        else:
            params['single_c6_file'] = c6_path
            params['single_etude_name'] = os.path.splitext(os.path.basename(c6_path))[0]
            self._police_wf.run_analysis_auto_browse(params)

    def _on_police_done(self, result):
        # Cache fddcpi2 data for other modules in this batch
        cables_all = result.get('fddcpi_cables_all')
        sro = result.get('fddcpi_sro')
        if cables_all is not None and sro:
            self._fddcpi_cache = {'sro': sro, 'cables': cables_all}
        # Cache SRO + appuis WKB for COMAC
        appuis_wkb = result.get('appuis_wkb')
        if sro and appuis_wkb is not None and not self._sro_appuis_cache:
            self._sro_appuis_cache = {
                'sro': sro,
                'appuis_wkb': appuis_wkb,
                'appuis_wkb_plain': appuis_wkb,
            }
        self._batch_results['police_c6'] = result
        cb = self._done_callbacks.pop('police_c6', None)
        if cb:
            cb(True, 'OK')

    def _on_police_error(self, err):
        cb = self._done_callbacks.pop('police_c6', None)
        if cb:
            cb(False, str(err))

    # ------------------------------------------------------------------
    def _launch_c6c3a(self, on_done):
        """Launch C6-C3A-C7-BD module."""
        self._done_callbacks['c6c3a'] = on_done
        det = self._detection()

        if not det.has_c6 and not det.has_c6_annexe:
            on_done(False, "Dossier C6 non détecté")
            return

        # Prefer c6_annexe_file (standalone C6 folder/file) over c6_dir search
        c6_file = ''
        if det.has_c6_annexe:
            c6_file = det.c6_annexe_file
        elif os.path.isdir(det.c6_dir):
            for f in sorted(os.listdir(det.c6_dir)):
                if f.lower().endswith('.xlsx'):
                    c6_file = os.path.join(det.c6_dir, f)
                    break
        else:
            c6_file = det.c6_dir

        if not c6_file or not os.path.isfile(c6_file):
            on_done(False, "Fichier C6 introuvable")
            return

        params = {
            'fichier_c6': c6_file,
            'fichier_c7': det.c7_file or '',
            'chemin_export': self._export_dir(),
            'mode_c3a': 'QGIS',
        }

        # Layer names from QGIS project
        lyr_pot = self._lyr_pot()
        lyr_cap = self._lyr_capft()
        if lyr_pot:
            params['table_infra'] = lyr_pot.name()
        if lyr_cap:
            params['table_decoupage'] = lyr_cap.name()
            params['champs_dcp'] = self._auto_field(lyr_cap)
            params['valeur_dcp'] = '%'

        if det.has_c3a:
            params['fichier_c3a'] = det.c3a_file
            params['mode_c3a'] = 'EXCEL'

        self._safe_connect_once(
            self._c6c3a_wf.analysis_finished, self._on_c6c3a_analysis
        )
        self._safe_connect_once(
            self._c6c3a_wf.error_occurred, self._on_c6c3a_error
        )
        self._safe_connect_once(
            self._c6c3a_wf.message_received, self._relay_message
        )

        self._c6c3a_wf.start_analysis(params)

    def _on_c6c3a_analysis(self, result):
        if result.get('success'):
            self._batch_results['c6c3a'] = result
            cb = self._done_callbacks.pop('c6c3a', None)
            if cb:
                cb(True, 'OK')
        else:
            cb = self._done_callbacks.pop('c6c3a', None)
            if cb:
                cb(False, 'Erreur analyse')

    def _on_c6c3a_error(self, err):
        cb = self._done_callbacks.pop('c6c3a', None)
        if cb:
            cb(False, str(err))

    def _launch_gespot_c6(self, on_done):
        """Lance le module GESPOT vs C6.

        Priorite source GESPOT :
            1. Couche QGIS 'data_gespot' dans le projet (GPKG ou BDD)
            2. Dossier CSV GESPOT (fallback historique)

        Si les couches QGIS (infra_pt_pot + etude_cap_ft) sont disponibles,
        construit un etude_map pour annoter chaque poteau avec son nom d'etude
        ou 'HORS ETUDE'. Sinon, le module tourne en pur fichier-fichier.
        """
        self._done_callbacks['gespot_c6'] = on_done
        det = self._detection()

        if not det.has_c6:
            on_done(False, "Dossier C6 non detecte")
            return
        if not self._gespot_wf:
            on_done(False, "GespotWorkflow non initialise")
            return

        gespot_rows = None
        gespot_source_label = ''
        gespot_dir = det.gespot_dir if det.has_gespot else ''

        # Source prioritaire : couche data_gespot dans le projet QGIS
        # Extraction des dicts bruts sur le main thread (iterateur QGIS).
        # Les regles metier (BR-01 a BR-06) seront appliquees dans le worker
        # thread avec la whitelist C6 correcte.
        gespot_layer = self._find_gespot_layer()
        if gespot_layer is not None:
            try:
                from .gespot_layer_reader import extract_rows_from_qgs_layer
                gespot_rows, gespot_source_label = extract_rows_from_qgs_layer(
                    gespot_layer)
                self._dlg.log_message(
                    f"[GESPOT] Couche data_gespot extraite : "
                    f"{len(gespot_rows)} features brutes",
                    'info',
                )
            except Exception as exc:
                QgsMessageLog.logMessage(
                    f"[GESPOT] Lecture couche data_gespot echouee : {exc}",
                    "PoleAerien", MSG_WARNING,
                )
                gespot_rows = None

        if gespot_rows is None and not gespot_dir:
            on_done(False,
                    "Source GESPOT non disponible : "
                    "aucune couche 'data_gespot' ni dossier GESPOT")
            return

        etude_map = self._build_gespot_etude_map()

        self._safe_connect_once(
            self._gespot_wf.analysis_finished, self._on_gespot_c6_done
        )
        self._safe_connect_once(
            self._gespot_wf.error_occurred, self._on_gespot_c6_error
        )
        self._safe_connect_once(
            self._gespot_wf.message_received, self._relay_message
        )

        self._gespot_wf.start_analysis(
            gespot_dir=gespot_dir,
            c6_dir=det.c6_dir,
            export_dir=det.export_dir,
            etude_map=etude_map,
            gespot_rows=gespot_rows,
            gespot_source_label=gespot_source_label,
        )

    def _find_gespot_layer(self):
        """Cherche une couche 'data_gespot' dans le projet QGIS.

        Returns:
            QgsVectorLayer ou None.
        """
        try:
            project = QgsProject.instance()
            layers = project.mapLayersByName('data_gespot')
            if layers:
                lyr = layers[0]
                if lyr.isValid():
                    return lyr
        except Exception:
            pass
        return None

    def _build_gespot_etude_map(self):
        """Construit {normalized_inf_num: etude_name ou 'HORS ETUDE'}.

        Utilise infra_pt_pot + etude_cap_ft si disponibles.
        Retourne {} si les couches ne sont pas chargees.
        """
        lyr_pot = self._lyr_pot()
        lyr_cap = self._lyr_capft()
        if not lyr_pot or not lyr_cap:
            return {}
        col_cap = self._auto_field(lyr_cap)
        if not col_cap:
            return {}
        try:
            _, hors_etude, dico_etude, _, _, _ = extraire_poteaux_etude(
                lyr_pot.name(), lyr_cap.name(), col_cap,
                'POT-%', 'GESPOT',
            )
            etude_map = {}
            for nom_etude, inf_nums in dico_etude.items():
                for raw_inf in inf_nums:
                    key = normalize_appui_num(raw_inf)
                    if key:
                        etude_map[key] = nom_etude
            for raw_inf in hors_etude:
                key = normalize_appui_num(raw_inf)
                if key and key not in etude_map:
                    etude_map[key] = 'HORS ETUDE'
            QgsMessageLog.logMessage(
                f"[GESPOT] etude_map: {len(etude_map)} poteaux "
                f"({sum(1 for v in etude_map.values() if v != 'HORS ETUDE')} en etude, "
                f"{sum(1 for v in etude_map.values() if v == 'HORS ETUDE')} hors etude)",
                'PoleAerien', MSG_INFO,
            )
            return etude_map
        except Exception as exc:
            QgsMessageLog.logMessage(
                f"[GESPOT] Impossible de construire etude_map: {exc}",
                'PoleAerien', MSG_WARNING,
            )
            return {}

    def _on_gespot_c6_done(self, result):
        self._batch_results['gespot_c6'] = result
        output_path = result.get('output_path', '')
        if output_path:
            self._dlg.log_result_link(output_path)
        cb = self._done_callbacks.pop('gespot_c6', None)
        if cb:
            cb(True, 'OK')

    def _on_gespot_c6_error(self, err):
        cb = self._done_callbacks.pop('gespot_c6', None)
        if cb:
            cb(False, str(err))

    # ------------------------------------------------------------------
    #  Helpers
    # ------------------------------------------------------------------
    def _relay_message(self, msg, color):
        """Relay workflow messages to dialog log."""
        level_map = {
            'red': 'error',
            'orange': 'warning',
            'green': 'success',
            'grey': 'info',
            'gray': 'info',
            'blue': 'info',
        }
        level = level_map.get(color, 'info')
        self._dlg.log_message(msg, level)

    def _safe_connect_once(self, signal, slot):
        """Connect signal to slot, disconnecting previous connection first."""
        try:
            signal.disconnect(slot)
        except (TypeError, RuntimeError):
            pass
        signal.connect(slot)
