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
Performance logger - persiste les durees d'execution par phase/module en CSV.

Format : timestamp,sro,module_key,phase,duration_ms,feature_count,status
Fichier : <QGIS user profile>/PoleAerien_perf.csv

Usage:
    from .perf_logger import PerfLogger
    PerfLogger.record('comac', 'extraction_qgis', duration_ms=1240, feature_count=850)

    with PerfLogger.timer('police_c6', 'cables_gpkg_load', sro=sro):
        cables = load_cables_from_gpkg(sro)
"""

import csv
import os
import time
from datetime import datetime


def _get_log_path() -> str:
    try:
        from qgis.core import QgsApplication
        profile_dir = QgsApplication.qgisUserDatabaseFilePath()
        base_dir = os.path.dirname(profile_dir)
    except Exception:
        base_dir = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(base_dir, "PoleAerien_perf.csv")


_LOG_PATH = _get_log_path()
_HEADERS = ("timestamp", "sro", "module_key", "phase", "duration_ms", "feature_count", "status")


def _ensure_headers():
    if os.path.exists(_LOG_PATH):
        return
    try:
        with open(_LOG_PATH, "w", newline="", encoding="utf-8") as f:
            csv.writer(f).writerow(_HEADERS)
    except OSError:
        pass


class PerfLogger:
    """Enregistre les durees d'execution dans un CSV persistant."""

    @staticmethod
    def record(
        module_key: str,
        phase: str,
        duration_ms: float,
        sro: str = "",
        feature_count: int = 0,
        status: str = "ok",
    ) -> None:
        """Enregistre une mesure de performance.

        Args:
            module_key: Cle du module (ex: 'comac', 'police_c6')
            phase: Phase mesuree (ex: 'extraction_qgis', 'fddcpi2_query', 'calcul_python')
            duration_ms: Duree en millisecondes
            sro: Code SRO (optionnel)
            feature_count: Nombre d'entites traitees (optionnel)
            status: Statut ('ok', 'error', 'skip')
        """
        _ensure_headers()
        row = (
            datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            sro,
            module_key,
            phase,
            int(duration_ms),
            feature_count,
            status,
        )
        try:
            with open(_LOG_PATH, "a", newline="", encoding="utf-8") as f:
                csv.writer(f).writerow(row)
        except OSError:
            pass

    @staticmethod
    def timer(module_key: str, phase: str, sro: str = "", feature_count: int = 0):
        """Context manager pour mesurer une phase automatiquement.

        Usage:
            with PerfLogger.timer('comac', 'extraction_qgis', sro=sro, feature_count=n):
                ... code a mesurer ...
        """
        return _PerfTimer(module_key, phase, sro, feature_count)

    @staticmethod
    def log_path() -> str:
        """Retourne le chemin du fichier CSV."""
        return _LOG_PATH


class _PerfTimer:
    """Context manager interne pour PerfLogger.timer()."""

    def __init__(self, module_key: str, phase: str, sro: str, feature_count: int):
        self._module_key = module_key
        self._phase = phase
        self._sro = sro
        self._feature_count = feature_count
        self._start = 0.0
        self._status = "ok"

    def __enter__(self):
        self._start = time.perf_counter()
        return self

    def set_status(self, status: str) -> None:
        self._status = status

    def __exit__(self, exc_type, exc_val, exc_tb):
        duration_ms = (time.perf_counter() - self._start) * 1000
        if exc_type is not None:
            self._status = "error"
        PerfLogger.record(
            self._module_key,
            self._phase,
            duration_ms,
            sro=self._sro,
            feature_count=self._feature_count,
            status=self._status,
        )
        return False
