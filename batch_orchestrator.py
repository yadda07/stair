# -*- coding: utf-8 -*-
"""
Batch Orchestrator - Bridges DialogV2 with existing workflows.

Translates detected project paths + QGIS layer selections into
workflow.start_analysis() calls, and routes workflow signals
back to the BatchRunner callback system.
"""

import os
import time

from qgis.PyQt.QtCore import QObject
from qgis.core import QgsMessageLog, QgsTask, QgsApplication, QgsProject
from .compat import MSG_INFO, MSG_WARNING, MSG_CRITICAL, FIELD_TYPE_STRING

from .project_detector import DetectionResult
from .db_layer_loader import DbLayerLoader
from .qgis_utils import (
    detect_etude_field as _detect_etude_field,
    extraire_poteaux_etude,
    reset_crs_cache,
    show_feature_count,
)
from .core_utils import normalize_appui_num
from .report_export_task import UnifiedReportExportTask
from .batch_extractor import BatchDataExtractor

class _LoadProjectLayersTask(QgsTask):
    """Background task: PG connection + layer creation for project mode.

    Network I/O (connection, metadata fetch) runs in worker thread.
    Project registration (addMapLayer) deferred to main thread callback.

    For Axione (extent_wkt set): skip infra_pt_pot (GraceTHD replaces it),
    load etude layers by spatial intersection with GraceTHD extent.
    For NGE (extent_wkt empty): load all layers filtered by SRO.
    """

    def __init__(self, sro, extent_wkt=''):
        label = "Chargement couches BDD (spatial)" if extent_wkt else f"Chargement couches BDD (SRO={sro})"
        super().__init__(label)
        self.sro = sro
        self.extent_wkt = extent_wkt
        self.lyr_pot = None
        self.lyr_cap = None
        self.lyr_com = None
        self.loader = None
        self.error_msg = None
        self.counts = {}

    def run(self):
        self.loader = DbLayerLoader()
        if not self.loader.connect():
            self.error_msg = "Mode Projet: connexion PostgreSQL impossible"
            return False

        if self.isCanceled():
            return False

        # Axione: skip infra_pt_pot (GraceTHD poteaux loaded on main thread)
        if not self.extent_wkt:
            self.lyr_pot = self.loader.load_infra_pt_pot(
                self.sro, add_to_project=False
            )
            if not self.lyr_pot or not self.lyr_pot.isValid():
                self.error_msg = "Mode Projet: couche infra_pt_pot non chargee"
                return False
            self.counts['pot'] = self.lyr_pot.featureCount()

        if self.isCanceled():
            return False

        # etude_cap_ft: spatial for Axione, SRO for NGE
        if self.extent_wkt:
            self.lyr_cap = self.loader.load_etude_cap_ft_by_extent(self.extent_wkt)
        else:
            self.lyr_cap = self.loader.load_etude_cap_ft(
                self.sro, add_to_project=False
            )
        if self.lyr_cap and self.lyr_cap.isValid():
            self.counts['cap'] = self.lyr_cap.featureCount()
        else:
            self.lyr_cap = None

        if self.isCanceled():
            return False

        # etude_comac: spatial for Axione, SRO for NGE
        if self.extent_wkt:
            self.lyr_com = self.loader.load_etude_comac_by_extent(self.extent_wkt)
        else:
            self.lyr_com = self.loader.load_etude_comac(
                self.sro, add_to_project=False
            )
        if self.lyr_com and self.lyr_com.isValid():
            self.counts['com'] = self.lyr_com.featureCount()
        else:
            self.lyr_com = None

        return True

    def finished(self, result):
        pass


class _PreExtractPgTask(QgsTask):
    def __init__(self, sro, be_type, gracethd_dir):
        super().__init__(f"Prechargement batch ({sro})")
        self.sro = sro
        self.be_type = be_type
        self.gracethd_dir = gracethd_dir
        self.data = None
        self.error_msg = None

    def run(self):
        try:
            extractor = BatchDataExtractor()
            self.data = extractor.extract_all(
                ['comac'], None, None, None,
                self.sro, self.be_type, self.gracethd_dir
            )
            return True
        except Exception as e:
            self.error_msg = str(e)
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
            maj_workflow, capft_workflow, comac_workflow,
            c6bd_workflow, police_workflow, c6c3a_workflow,
            iface
        )
    """

    def __init__(self, dialog, runner,
                 maj_wf, capft_wf, comac_wf,
                 c6bd_wf, police_wf, c6c3a_wf,
                 iface, gespot_wf=None, parent=None):
        super().__init__(parent)
        self._dlg = dialog
        self._runner = runner
        self._iface = iface

        # Workflows
        self._maj_wf = maj_wf
        self._capft_wf = capft_wf
        self._comac_wf = comac_wf
        self._c6bd_wf = c6bd_wf
        self._police_wf = police_wf
        self._c6c3a_wf = c6c3a_wf
        self._gespot_wf = gespot_wf

        # Per-module done callbacks: {module_key: callback}
        # Dict instead of single attr so multiple modules can run in parallel
        self._done_callbacks = {}
        self._module_start_times = {}
        # Per-module progress tracking: {module_key: 0-100}
        # Enables correct progress bar with parallel modules
        self._module_progress = {}
        self._batch_results = {}

        # Shared fddcpi2 cache: avoids duplicate PostgreSQL calls
        # when both COMAC and Police C6 run in the same batch.
        # Key: sro (str), Value: list[CableSegment]
        self._fddcpi_cache = {}

        # Shared SRO + appuis WKB cache: avoids duplicate QGIS extraction
        # when both COMAC and Police C6 need the same data from infra_pt_pot.
        # Format: {'sro': str, 'appuis_wkb': list[dict]}
        self._sro_appuis_cache = {}

        # Project mode: DB-loaded layers (temporary or persistent)
        self._db_loader = None
        self._pm_lyr_pot = None
        self._pm_lyr_cap = None
        self._pm_lyr_com = None

        # Async layer loading state
        self._layer_load_task = None
        self._pending_module_keys = None
        self._report_task = None
        self._prefetch_task = None
        self._pending_prefetch_keys = None
        # P-02: pre-extracted batch data (populated before runner.start)
        self._extracted_data = None

        # Register launchers
        self._runner.set_launcher('maj', self._launch_maj)
        self._runner.set_launcher('capft', self._launch_capft)
        self._runner.set_launcher('comac', self._launch_comac)
        self._runner.set_launcher('c6bd', self._launch_c6bd)
        self._runner.set_launcher('police_c6', self._launch_police_c6)
        self._runner.set_launcher('c6c3a', self._launch_c6c3a)
        self._runner.set_launcher('gespot_c6', self._launch_gespot_c6)

        # Connect runner signals -> dialog
        self._runner.module_started.connect(self._on_module_started)
        self._runner.module_finished.connect(self._on_module_finished)
        self._runner.modules_finished.connect(self._on_batch_finished)
        self._runner.batch_cancelled.connect(self._on_batch_cancelled)
        self._runner.log_message.connect(self._dlg.log_message)

        # Connect workflow progress_changed with per-module key tagging
        # Lambda with default arg captures key at definition time (closure-safe)
        _wf_map = {
            'maj': self._maj_wf,
            'capft': self._capft_wf,
            'comac': self._comac_wf,
            'c6bd': self._c6bd_wf,
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

    # ------------------------------------------------------------------
    #  Start / Cancel
    # ------------------------------------------------------------------
    # QP-06: Noms des couches temporaires creees par le plugin
    _TEMP_LAYER_PREFIXES = (
        'fddcpi2_', 'gracethd_cables_', 'Poteaux BT absents', 'Poteaux FT absents',
        'anomalies_c6_', 'cables_anomalie',
    )


    _REPORT_PROGRESS_START = 85
    _REPORT_PROGRESS_END = 99

    def _on_start(self, module_keys):
        self._module_progress = {}
        self._batch_results = {}
        self._extracted_data = None
        self._fddcpi_cache = {}
        self._sro_appuis_cache = {}
        self._be_type = 'nge'
        self._gracethd_dir = ''
        self._gracethd_lyr_preloaded = None
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
        self._detect_be_type(sro, det)

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
            from .db_connection import extract_sro_from_layer
            lyr_pot = self._lyr_pot()
            if lyr_pot:
                sro_for_fetch = extract_sro_from_layer(lyr_pot) or ''
        if self._start_async_pre_extract(module_keys, sro_for_fetch):
            return

        self._runner.start(module_keys)

    def _detect_be_type(self, sro, det):
        """Detect bureau d'etudes (NGE/Axione).

        Source de verite : toujours la table comac.sro_nge_axione en BDD.
        GraceTHD local = indice pour gracethd_dir, pas pour be_type.
        """
        try:
            if not sro:
                return

            from .db_connection import get_shared_connection
            db = get_shared_connection()
            if not db.connect():
                return

            be = db.query_sro_be(sro)
            if not be:
                return

            self._be_type = be

            if be == 'nge':
                self._dlg.log_message(
                    f"SRO {sro} - Bureau d'etudes: NGE "
                    "(source cables: PostgreSQL / fddcpiax)",
                    'info'
                )
                return

            # --- Axione: verifier presence GraceTHD ---
            self._dlg.log_message(
                "═══════════════════════════════════════════",
                'info'
            )

            self._dlg.log_message(
                f"SRO {sro} - Bureau d'etudes: AXIONE",
                'info'
            )

            if det.has_gracethd:
                self._gracethd_dir = det.gracethd_dir
                self._dlg.log_message(
                    "  Methode: GraceTHD (fichiers locaux SHP + CSV)",
                    'info'
                )
                self._log_gracethd_inventory(det.gracethd_dir)
            else:
                self._dlg.log_message(
                    "ATTENTION: Repertoire GraceTHD introuvable dans le projet.",
                    'warning'
                )
                self._dlg.log_message(
                    "  Placez un dossier contenant t_noeud.shp + t_cableline.shp "
                    "(+ t_cable.csv, t_ptech.csv, t_ebp.csv) dans le projet.",
                    'warning'
                )

            self._dlg.log_message(
                "═══════════════════════════════════════════",
                'info'
            )

        except Exception as e:
            QgsMessageLog.logMessage(
                f"Erreur detection BE: {e}", "PoleAerien", MSG_WARNING
            )

    def _log_gracethd_inventory(self, gracethd_dir):
        """Log a detailed inventory of the GraceTHD directory contents."""
        from .gracethd_reader import GraceTHDReader

        reader = GraceTHDReader(gracethd_dir)
        inv = reader.inventory()

        self._dlg.log_message(
            f"  Repertoire: {inv['dir_name']}/",
            'info'
        )
        self._dlg.log_message(
            f"  Contenu: {inv['total_files']} fichiers "
            f"({inv['shp_count']} SHP, {inv['csv_count']} CSV, "
            f"{inv['other_count']} auxiliaires) - "
            f"{inv['total_size_mb']} Mo",
            'info'
        )

        # Tables cles avec comptage
        self._dlg.log_message("  Tables cles:", 'info')
        table_labels = {
            't_noeud.shp': 'Noeuds (geometries)',
            't_cableline.shp': 'Traces cables (geometries)',
            't_cheminement.shp': 'Cheminements (geometries)',
            't_cable.csv': 'Cables (attributs)',
            't_ptech.csv': 'Points techniques',
            't_ebp.csv': 'Boitiers (BPE/PBO)',
        }
        for fname, label in table_labels.items():
            val = inv['key_tables'].get(fname)
            if val == 'present':
                status = 'OK'
                level = 'success'
            elif val == 'absent':
                status = 'ABSENT'
                level = 'warning'
            elif isinstance(val, int) and val >= 0:
                status = f'{val} enregistrements'
                level = 'success'
            else:
                status = 'ABSENT'
                level = 'warning'
            self._dlg.log_message(
                f"    {label:.<40s} {status}",
                level
            )

        # Fichiers requis manquants
        if inv['missing_required']:
            self._dlg.log_message(
                f"  REQUIS MANQUANTS: {', '.join(inv['missing_required'])}",
                'error'
            )

        # Fichiers optionnels manquants (juste info)
        if inv['missing_optional']:
            self._dlg.log_message(
                f"  Optionnels absents: {', '.join(inv['missing_optional'])}",
                'warning'
            )

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

            needs_pot = set(module_keys) & {'maj', 'capft', 'comac', 'c6bd', 'police_c6', 'c6c3a'}
            needs_cap = set(module_keys) & {'maj', 'capft', 'c6bd', 'c6c3a'}
            needs_com = set(module_keys) & {'maj', 'comac'}

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
        # Axione+GraceTHD: SRO non requis (intersection spatiale avec emprise GraceTHD)
        if is_pm and not self._dlg.sro and not (self._be_type == 'axione' and self._gracethd_dir):
            issues.append(
                "Mode Projet : impossible de determiner le SRO depuis le nom du dossier. "
                "Format attendu : XXXXX-YYY-ZZZ-NNNNN (ex: 63041-B1I-PMZ-00003)."
            )

        # --- Per-module resource checks ---
        if 'maj' in module_keys and not det.has_ftbt:
            issues.append(
                "Module MAJ: fichier FT-BT KO non detecte. "
                "Placez un fichier Excel contenant 'FT-BT KO' dans le nom a la racine du projet."
            )

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

        # Axione SROs: GraceTHD required for cable verification modules
        cable_modules = set(module_keys) & {'comac', 'police_c6'}
        if self._be_type == 'axione' and cable_modules and not self._gracethd_dir:
            issues.append(
                f"SRO Axione: repertoire GraceTHD requis pour {', '.join(sorted(cable_modules))}. "
                "Placez un dossier contenant t_noeud.shp + t_cableline.shp "
                "(+ t_cable.csv, t_ptech.csv, t_ebp.csv) dans le projet."
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
            if not det.has_gespot:
                issues.append(
                    "Module GESPOT vs C6: dossier GSPOT/GESPOT non detecte. "
                    "Creer un dossier nomme GSPOT ou GESPOT a la racine du projet."
                )
            if not det.has_c6:
                issues.append(
                    "Module GESPOT vs C6: dossier C6 (= CAP FT) non detecte."
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
            be_type=self._be_type,
            gracethd_dir=self._gracethd_dir,
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
        for wf in (self._maj_wf, self._capft_wf, self._comac_wf,
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
        self._dlg.reset_after_batch()
        self._runner.finalize_batch()

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

    def _on_batch_finished(self, results):
        self._log_batch_recap(results)
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
                'maj': 'MAJ BD', 'capft': 'CAP_FT', 'comac': 'COMAC',
                'c6bd': 'C6 vs BD', 'police_c6': 'POLICE C6', 'c6c3a': 'C6-C3A-C7-BD',
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

        if key == 'maj':
            liste_ft = result.get('liste_ft', [])
            liste_bt = result.get('liste_bt', [])
            ft_count = liste_ft[2] if len(liste_ft) > 2 else 0
            bt_count = liste_bt[2] if len(liste_bt) > 2 else 0
            if ft_count or bt_count:
                parts.append(f"{ft_count} FT, {bt_count} BT a mettre a jour")

        elif key == 'capft':
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
                    ref_lbl = "GraceTHD" if result.get('be_type') == 'axione' else "BDD"
                    msg = f"{nb_ecart} ecarts cables, {nb_absent} absents {ref_lbl}"
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
             mods & {'capft', 'comac', 'c6bd', 'police_c6', 'maj', 'gespot_c6'}),
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
        """Launch background task to load PostgreSQL layers for project mode.

        Layer creation (network I/O) runs in a worker thread.
        On completion, layers are registered in QgsProject on the main thread,
        then the batch starts.

        For Axione with GraceTHD: load poteaux from local files first (main
        thread), compute extent, then load etude layers by spatial intersection
        instead of SRO filter.  infra_pt_pot is skipped (GraceTHD replaces it).
        """
        sro = self._dlg.sro
        extent_wkt = ''

        # Axione: load GraceTHD poteaux first, compute extent for spatial query
        if self._be_type == 'axione' and self._gracethd_dir:
            from .gracethd_reader import GraceTHDReader
            reader = GraceTHDReader(self._gracethd_dir)
            gracethd_lyr = reader.load_poteaux_as_layer(
                layer_name=f"gracethd_poteaux [{sro or 'spatial'}]"
            )
            if gracethd_lyr and gracethd_lyr.isValid() and gracethd_lyr.featureCount() > 0:
                self._gracethd_lyr_preloaded = gracethd_lyr
                ext = gracethd_lyr.extent()
                extent_wkt = ext.asWktPolygon()
                self._dlg.log_message(
                    f"Mode Projet Axione: {gracethd_lyr.featureCount()} poteaux GraceTHD, "
                    f"recherche etudes par intersection spatiale...", 'info'
                )
            else:
                self._dlg.log_message(
                    "GraceTHD: echec chargement poteaux, fallback SRO", 'warning'
                )
                self._gracethd_lyr_preloaded = None

        if not sro and not extent_wkt:
            self._dlg.log_message("Mode Projet: SRO non disponible", 'error')
            self._pending_module_keys = None
            self._dlg.reset_after_batch()
            return

        if not extent_wkt:
            self._dlg.log_message(
                f"Mode Projet: chargement depuis BDD (SRO={sro})", 'info'
            )

        self._layer_load_task = _LoadProjectLayersTask(sro, extent_wkt)
        self._layer_load_task.taskCompleted.connect(self._on_layers_loaded)
        self._layer_load_task.taskTerminated.connect(self._on_layers_load_failed)
        QgsApplication.taskManager().addTask(self._layer_load_task)

    def _on_layers_loaded(self):
        """Main thread callback: register layers in QgsProject, start batch."""
        task = self._layer_load_task
        self._layer_load_task = None

        if not task:
            return

        self._db_loader = task.loader

        # Always add layers to QGIS project so that get_layer_safe() and
        # other name-based lookups work inside workflows.
        # Cleanup after batch removes them if user didn't ask to keep.

        # infra_pt_pot: GraceTHD poteaux for Axione, BDD for NGE
        gracethd_preloaded = getattr(self, '_gracethd_lyr_preloaded', None)
        if gracethd_preloaded and gracethd_preloaded.isValid():
            self._pm_lyr_pot = gracethd_preloaded
            self._gracethd_lyr_preloaded = None
            QgsProject.instance().addMapLayer(self._pm_lyr_pot)
            show_feature_count(self._pm_lyr_pot)
            self._dlg.log_message(
                f"  poteaux GraceTHD: {self._pm_lyr_pot.featureCount()} poteaux "
                f"(source: t_ptech + t_noeud.nd_codeext)", 'success'
            )
            type_counts = {}
            for feat in self._pm_lyr_pot.getFeatures():
                t = feat['inf_type']
                type_counts[t] = type_counts.get(t, 0) + 1
            type_parts = [f"{t}: {n}" for t, n in sorted(type_counts.items(), key=lambda x: -x[1])]
            self._dlg.log_message(
                f"  classification FT/BT: {', '.join(type_parts)}", 'info'
            )
            # Extract SRO from etude zones found by spatial intersection
            self._log_spatial_sro(task)
        elif self._be_type == 'axione' and self._gracethd_dir:
            self._dlg.log_message(
                "  poteaux GraceTHD: echec chargement, fallback BDD", 'warning'
            )
            self._pm_lyr_pot = task.lyr_pot
            QgsProject.instance().addMapLayer(self._pm_lyr_pot)
            show_feature_count(self._pm_lyr_pot)
            self._db_loader._loaded_layers.append(self._pm_lyr_pot.id())
            self._dlg.log_message(
                f"  infra_pt_pot (BDD): {task.counts.get('pot', '?')} poteaux", 'success'
            )
        else:
            self._pm_lyr_pot = task.lyr_pot
            QgsProject.instance().addMapLayer(self._pm_lyr_pot)
            show_feature_count(self._pm_lyr_pot)
            self._db_loader._loaded_layers.append(self._pm_lyr_pot.id())
            self._dlg.log_message(
                f"  infra_pt_pot: {task.counts.get('pot', '?')} poteaux", 'success'
            )

        # etude_cap_ft (optional)
        if task.lyr_cap:
            self._pm_lyr_cap = task.lyr_cap
            QgsProject.instance().addMapLayer(self._pm_lyr_cap)
            show_feature_count(self._pm_lyr_cap)
            self._db_loader._loaded_layers.append(self._pm_lyr_cap.id())
            self._dlg.log_message(
                f"  etude_cap_ft: {task.counts.get('cap', '?')} zones", 'success'
            )
        else:
            self._dlg.log_message(
                "  etude_cap_ft: non disponible (modules CAP FT/MAJ limites)", 'warning'
            )
            self._pm_lyr_cap = None

        # etude_comac (optional)
        if task.lyr_com:
            self._pm_lyr_com = task.lyr_com
            QgsProject.instance().addMapLayer(self._pm_lyr_com)
            show_feature_count(self._pm_lyr_com)
            self._db_loader._loaded_layers.append(self._pm_lyr_com.id())
            self._dlg.log_message(
                f"  etude_comac: {task.counts.get('com', '?')} zones", 'success'
            )
        else:
            self._dlg.log_message(
                "  etude_comac: non disponible (module COMAC/MAJ limites)", 'warning'
            )
            self._pm_lyr_com = None

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
                from .db_connection import extract_sro_from_layer
                sro_pm = extract_sro_from_layer(self._pm_lyr_pot) or ''
            if self._start_async_pre_extract(keys, sro_pm):
                return

            self._runner.start(keys)

    def _start_async_pre_extract(self, module_keys, sro):
        cable_modules = set(module_keys) & {'comac', 'police_c6'}
        if not cable_modules or not sro:
            return False

        self._pending_prefetch_keys = list(module_keys)
        self._prefetch_task = _PreExtractPgTask(sro, self._be_type, self._gracethd_dir)
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
        if keys:
            self._runner.start(keys)

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
            self._runner.start(keys)

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
        """Remove temporary DB layers if user did not ask to keep them."""
        if not self._db_loader:
            return

        if not self._dlg.load_layers_in_qgis:
            self._db_loader.cleanup_layers()
            QgsMessageLog.logMessage(
                "Mode Projet: couches temporaires supprimees", "PoleAerien", MSG_INFO
            )

        self._pm_lyr_pot = None
        self._pm_lyr_cap = None
        self._pm_lyr_com = None
        self._db_loader = None

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
    def _launch_maj(self, on_done):
        """Launch MAJ FT/BT module."""
        self._done_callbacks['maj'] = on_done
        det = self._detection()

        lyr_pot = self._lyr_pot()
        lyr_cap = self._lyr_capft()
        lyr_com = self._lyr_comac()

        if not lyr_pot or not lyr_cap or not lyr_com:
            on_done(False, "Couches QGIS non sélectionnées")
            return

        if not det.has_ftbt:
            on_done(False, "Fichier FT-BT KO non détecté")
            return

        # Disconnect previous, connect for this batch
        self._safe_connect_once(
            self._maj_wf.analysis_finished, self._on_maj_done
        )
        self._safe_connect_once(
            self._maj_wf.error_occurred, self._on_maj_error
        )
        self._safe_connect_once(
            self._maj_wf.message_received, self._relay_message
        )

        col_cap = self._auto_field(lyr_cap)
        col_com = self._auto_field(lyr_com)
        self._maj_wf.start_analysis(
            lyr_pot, lyr_cap, lyr_com, det.ftbt_excel,
            col_cap=col_cap, col_com=col_com
        )

    def _on_maj_done(self, result):
        import pandas as pd

        cb = self._done_callbacks.pop('maj', None)
        if not cb:
            return

        liste_ft = result.get('liste_ft', [0, None, 0, None])
        liste_bt = result.get('liste_bt', [0, None, 0, None])
        ft_count = liste_ft[2] if len(liste_ft) > 2 else 0
        bt_count = liste_bt[2] if len(liste_bt) > 2 else 0
        self._batch_results['maj'] = result

        # Extract DataFrames of matched rows (index=gid)
        data_ft = liste_ft[3] if len(liste_ft) > 3 and isinstance(liste_ft[3], pd.DataFrame) else pd.DataFrame()
        data_bt = liste_bt[3] if len(liste_bt) > 3 and isinstance(liste_bt[3], pd.DataFrame) else pd.DataFrame()

        if data_ft.empty and data_bt.empty:
            cb(True, f"Analyse: {ft_count} FT, {bt_count} BT (aucune MAJ)")
            return

        # Get layer for SQL update
        lyr = self._lyr_pot()
        if not lyr:
            cb(True, f"Analyse: {ft_count} FT, {bt_count} BT (couche introuvable)")
            return

        layer_name = lyr.name()
        self._dlg.log_message("Mise a jour BD en cours...", "info")

        # Save state for async callbacks
        self._maj_pending_cb = cb
        self._maj_ft_count = ft_count
        self._maj_bt_count = bt_count

        def _on_sql_progress(msg, pct):
            self._dlg.log_message(msg, "info")

        def _on_sql_finished(ft_updated, bt_updated):
            self._dlg.log_message(
                f"MAJ BD terminee: {ft_updated} FT, {bt_updated} BT mis a jour", "success"
            )
            done_cb = self._maj_pending_cb
            if done_cb:
                done_cb(True,
                        f"Analyse: {self._maj_ft_count} FT, {self._maj_bt_count} BT"
                        f" | MAJ: {ft_updated} FT, {bt_updated} BT")

        def _on_sql_error(err):
            self._dlg.log_message(f"Erreur MAJ SQL: {err}", "error")
            done_cb = self._maj_pending_cb
            if done_cb:
                done_cb(False, f"Erreur MAJ: {err}")

        self._maj_wf.start_updates_sql_background(
            layer_name, data_ft, data_bt,
            _on_sql_progress, _on_sql_finished, _on_sql_error
        )

    def _on_maj_error(self, err):
        cb = self._done_callbacks.pop('maj', None)
        if cb:
            cb(False, str(err))

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
            be_type=self._be_type,
            gracethd_dir=self._gracethd_dir,
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
            self._sro_appuis_cache = {'sro': sro, 'appuis_wkb': appuis_wkb}
        self._batch_results['comac'] = result
        cb = self._done_callbacks.pop('comac', None)
        if cb:
            cb(True, 'OK')

    def _on_comac_error(self, err):
        cb = self._done_callbacks.pop('comac', None)
        if cb:
            cb(False, str(err))

    # ------------------------------------------------------------------
    def _launch_c6bd(self, on_done):
        """Launch C6 vs BD module."""
        self._done_callbacks['c6bd'] = on_done
        det = self._detection()

        lyr_pot = self._lyr_pot()
        lyr_cap = self._lyr_capft()
        export_dir = self._export_dir()

        if not lyr_pot or not lyr_cap:
            on_done(False, "Couches QGIS non sélectionnées")
            return
        if not det.has_c6:
            on_done(False, "Dossier C6 non détecté")
            return

        c6_path = det.c6_dir

        self._safe_connect_once(
            self._c6bd_wf.analysis_finished, self._on_c6bd_analysis
        )
        self._safe_connect_once(
            self._c6bd_wf.error_occurred, self._on_c6bd_error
        )
        self._safe_connect_once(
            self._c6bd_wf.message_received, self._relay_message
        )

        # col_cap=None → auto-detect in workflow
        self._c6bd_wf.start_analysis(
            lyr_pot, lyr_cap, None,
            c6_path, export_dir
        )

    def _on_c6bd_analysis(self, result):
        self._batch_results['c6bd'] = result
        cb = self._done_callbacks.pop('c6bd', None)
        if cb:
            cb(True, 'OK')

    def _on_c6bd_error(self, err):
        cb = self._done_callbacks.pop('c6bd', None)
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

        # BE type routing (Axione -> GraceTHD)
        params['be_type'] = self._be_type
        params['gracethd_dir'] = self._gracethd_dir
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
            self._sro_appuis_cache = {'sro': sro, 'appuis_wkb': appuis_wkb}
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

        Si les couches QGIS (infra_pt_pot + etude_cap_ft) sont disponibles,
        construit un etude_map pour annoter chaque poteau avec son nom d'etude
        ou 'HORS ETUDE'. Sinon, le module tourne en pur fichier-fichier.
        """
        self._done_callbacks['gespot_c6'] = on_done
        det = self._detection()

        if not det.has_gespot:
            on_done(False, "Dossier GESPOT non detecte")
            return
        if not det.has_c6:
            on_done(False, "Dossier C6 non detecte")
            return
        if not self._gespot_wf:
            on_done(False, "GespotWorkflow non initialise")
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
            gespot_dir=det.gespot_dir,
            c6_dir=det.c6_dir,
            export_dir=det.export_dir,
            etude_map=etude_map,
        )

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
