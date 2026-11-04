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
Batch Runner - DAG-aware multi-module execution controller.

Sprint E2: Groups execute sequentially; background modules (gespot_c6)
run in parallel with the sequential group chain.
Sprint E3: Group 1 (capft/c6bd/c6c3a) and Group 2 (comac+police_c6)
will be made truly intra-group parallel.

Public API identical to the original sequential runner (same signals).
"""

import logging
from qgis.PyQt.QtCore import QObject, pyqtSignal, QTimer

from .version import get_version

logger = logging.getLogger(__name__)


# Module registry: key -> display name (module 'maj' supprime : BDD retiree)
MODULE_REGISTRY = {
    'capft':     '1. VERIF CAP_FT',
    'comac':     '2. VERIF COMAC',
    'c6bd':      '3. C6 vs Donnees Projet',
    'police_c6': '4. POLICE C6',
    'c6c3a':     '5. C6-C3A-C7-BD',
    'gespot_c6': '6. GESPOT vs C6',
}

# DAG: explicit dependency and group declarations.
# - group: ascending order = execution order; modules in same group run after
#          the previous group completes (Sprint E3 makes intra-group parallel)
# - depends_on: list of prerequisite module keys; evaluated against selected
#               modules only (not selected = auto-satisfied)
# - background: True = launched immediately, runs in parallel with ALL groups
MODULE_DAG = {
    # Un module par groupe : pas de QgsTask Excel/openpyxl en parallele (Windows QGIS 3.40+).
    'capft':     {'depends_on': [],      'group': 1, 'background': False},
    'c6bd':      {'depends_on': [],      'group': 2, 'background': False},
    'c6c3a':     {'depends_on': [],      'group': 3, 'background': False},
    'comac':     {'depends_on': [],      'group': 4, 'background': False},
    'police_c6': {'depends_on': [],      'group': 5, 'background': False},
    'gespot_c6': {'depends_on': [],      'group': 6, 'background': False},
}
# Note: comac and police_c6 in same group (2) because fddcpi2 is pre-fetched
# by BatchDataExtractor._pre_extract_cables() before runner.start().
# Note: gespot_c6 was background=True (Sprint E2) but caused sporadic
# "access violation" crashes on QGIS 3.44 when openpyxl's C XML parser
# (lxml/libxml2) ran concurrently with QGIS C++ layer iteration.
# group=3 ensures it runs alone after all other modules finish.


class BatchRunner(QObject):
    """DAG-aware batch execution controller.

    Signals (identical to original sequential runner):
        module_started(str, int, int): module_key, position, total
        module_finished(str, bool, str): module_key, success, message
        batch_progress(int): overall percent 0-100
        modules_finished(dict): {module_key: {'success': bool, 'message': str}}
        batch_finished(dict): same as modules_finished
        batch_cancelled(): emitted on cancel
        log_message(str, str): message, level
    """

    module_started = pyqtSignal(str, int, int)
    module_finished = pyqtSignal(str, bool, str)
    batch_progress = pyqtSignal(int)
    modules_finished = pyqtSignal(dict)
    batch_finished = pyqtSignal(dict)
    batch_cancelled = pyqtSignal()
    log_message = pyqtSignal(str, str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._launchers = {}
        self._results = {}
        self._running = False
        self._cancelled = False

        self._plan = []
        self._group_index = 0
        self._active_group_keys = set()
        self._background_active = set()
        self._sequential_done = False
        self._total_modules = 0
        self._launch_position = 0

    @property
    def is_running(self) -> bool:
        return self._running

    def finalize_batch(self):
        self.batch_finished.emit(dict(self._results))

    def set_launcher(self, module_key: str, launcher_fn):
        """Register a launcher for a module.

        Args:
            module_key: Key from MODULE_REGISTRY
            launcher_fn: Callable accepting a callback: fn(on_done(success, msg))
        """
        self._launchers[module_key] = launcher_fn

    def start(self, module_keys: list):
        """Start batch execution respecting DAG dependencies.

        Args:
            module_keys: List of module keys to execute (order irrelevant, DAG governs).
        """
        if self._running:
            return

        valid_keys = [k for k in module_keys if k in self._launchers]
        if not valid_keys:
            self.log_message.emit("Aucun module valide selectionne.", 'warning')
            return

        self._results = {}
        self._running = True
        self._cancelled = False
        self._sequential_done = False
        self._group_index = 0
        self._active_group_keys = set()
        self._background_active = set()
        self._launch_position = 0

        background, sequential = self._split_background(valid_keys)
        try:
            self._plan = self._compute_plan(sequential, set(valid_keys))
        except RuntimeError as exc:
            self._running = False
            self.log_message.emit(f"Plan batch invalide : {exc}", 'error')
            return
        self._total_modules = len(valid_keys)

        names = ', '.join(MODULE_REGISTRY.get(k, k) for k in valid_keys)
        self.log_message.emit(
            f"Lancement de {self._total_modules} module(s) : {names}", 'info'
        )
        logger.info(
            "batch_start module_count=%d modules=%s plugin_version=%s",
            self._total_modules,
            ','.join(valid_keys),
            get_version()
        )
        self.batch_progress.emit(0)

        for key in background:
            QTimer.singleShot(100, lambda k=key: self._launch_single(k))

        QTimer.singleShot(100, self._launch_next_group)

    def cancel(self):
        """Cancel batch execution. Stops all active and pending modules."""
        if not self._running:
            return
        self._cancelled = True
        self._running = False
        self._active_group_keys.clear()
        self._background_active.clear()
        self._plan = []
        self.log_message.emit("Execution annulee par l'utilisateur.", 'warning')
        self.batch_cancelled.emit()

    # ------------------------------------------------------------------
    #  Internal: plan computation
    # ------------------------------------------------------------------

    def _split_background(self, keys):
        bg = [k for k in keys if MODULE_DAG.get(k, {}).get('background')]
        seq = [k for k in keys if not MODULE_DAG.get(k, {}).get('background')]
        return bg, seq

    def _compute_plan(self, sequential_keys, all_selected):
        """Group sequential_keys by DAG group number.

        Returns list of lists: [[group0_keys], [group1_keys], ...].
        Empty groups are skipped. Dependencies on non-selected modules
        are auto-satisfied.
        """
        selected = set(all_selected)
        remaining = set(sequential_keys)
        done = set()
        plan = []

        while remaining:
            ready = []
            for key in remaining:
                node = MODULE_DAG.get(key, {'depends_on': [], 'group': 99})
                deps = [d for d in node.get('depends_on', []) if d in selected]
                if all(d in done for d in deps):
                    ready.append(key)
            if not ready:
                raise RuntimeError(f"DAG invalide ou cyclique: {sorted(remaining)}")

            min_group = min(MODULE_DAG.get(k, {'group': 99})['group'] for k in ready)
            current = sorted(k for k in ready if MODULE_DAG.get(k, {'group': 99})['group'] == min_group)
            plan.append(current)
            done.update(current)
            remaining.difference_update(current)

        return plan

    # ------------------------------------------------------------------
    #  Internal: execution
    # ------------------------------------------------------------------

    def _launch_next_group(self):
        if self._cancelled:
            return
        if self._group_index >= len(self._plan):
            self._sequential_done = True
            if not self._background_active:
                self._finish_batch()
            return
        group_keys = self._plan[self._group_index]
        self._active_group_keys = set(group_keys)
        for key in group_keys:
            self._launch_single(key)

    def _launch_single(self, key):
        if self._cancelled:
            return
        if MODULE_DAG.get(key, {}).get('background'):
            self._background_active.add(key)
        name = MODULE_REGISTRY.get(key, key)
        self._launch_position += 1
        self.log_message.emit(
            f"--- Module {self._launch_position}/{self._total_modules} : {name} ---",
            'info'
        )
        self.module_started.emit(key, self._launch_position - 1, self._total_modules)
        launcher = self._launchers.get(key)
        if launcher is None:
            self._on_module_done(key, False, f"Lanceur non configure pour '{key}'")
            return
        callback = self._make_callback(key)
        try:
            launcher(callback)
        except Exception as exc:
            self._on_module_done(key, False, f"Erreur lancement : {exc}")

    def _make_callback(self, key):
        def on_done(success: bool, message: str = ''):
            self._on_module_done(key, success, message)
        return on_done

    def _on_module_done(self, key: str, success: bool, message: str = ''):
        if not self._running:
            return
        name = MODULE_REGISTRY.get(key, key)
        self._results[key] = {'success': success, 'message': message}
        level = 'success' if success else 'error'
        status = 'OK' if success else 'ERREUR'
        self.log_message.emit(f"{name} : {status} {message}", level)
        self.module_finished.emit(key, success, message)
        self._update_progress()

        if key in self._background_active:
            self._background_active.discard(key)
            if self._sequential_done and not self._background_active:
                self._finish_batch()
            return

        self._active_group_keys.discard(key)
        if not self._active_group_keys:
            self._group_index += 1
            QTimer.singleShot(200, self._launch_next_group)

    def _update_progress(self):
        total = self._total_modules
        done = len(self._results)
        pct = int((done / total) * 100) if total > 0 else 100
        self.batch_progress.emit(pct)

    def _finish_batch(self):
        self._running = False
        successes = sum(1 for r in self._results.values() if r['success'])
        total = len(self._results)
        errors = total - successes
        if errors == 0:
            self.log_message.emit(
                f"Tous les modules termines avec succes ({total}/{total}).", 'success'
            )
        else:
            self.log_message.emit(
                f"Termine : {successes}/{total} OK, {errors} erreur(s).", 'warning'
            )
        self.batch_progress.emit(100)
        self.modules_finished.emit(dict(self._results))
