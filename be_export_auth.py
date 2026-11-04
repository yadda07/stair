# -*- coding: utf-8 -*-
# Youcef ADDA | GEODECI
# FR : Plugins QGIS, solutions SIG et administration PostgreSQL/PostGIS ; audit, automatisation et accompagnement.
# EN : QGIS plugins, GIS solutions and PostgreSQL/PostGIS administration; audits, workflow automation and business support.
# AR : تطوير إضافات QGIS وحلول نظم المعلومات الجغرافية وإدارة PostgreSQL/PostGIS، والتدقيق والأتمتة ومرافقة المؤسسات.
# Site / Website / الموقع : https://geodeci.xyz/
# Contact professionnel / Business enquiries / للتواصل المهني : youcef.geodesien@gmail.com | contact@geodeci.xyz
# Détails / Details / التفاصيل : SIGNATURE.md
# ============================================================================
"""Internal helper around the QGIS stored PostgreSQL connection.

Provides a short-lived background probe and helpers used by the
internal connection setup dialog.
"""
from __future__ import annotations

import hashlib
import logging
import os
import threading
import time
from typing import Dict, Optional

from qgis.PyQt.QtCore import QObject, pyqtSignal, QTimer

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
#  Configuration
# ---------------------------------------------------------------------------
QGIS_CONN_NAME = os.environ.get("STAIR_QGIS_CONN_NAME", "AUVERGNE")

_ALLOWED_HOSTS = frozenset(
    h.strip() for h in os.environ.get("STAIR_ALLOWED_HOSTS", "").split(",") if h.strip()
)

_DB_SCHEMA = os.environ.get("STAIR_AUTH_SCHEMA", "public")
_DB_TABLE = os.environ.get("STAIR_AUTH_TABLE", "stair_export_auth")

_CONNECT_TIMEOUT_S = 3

_STATUS_CONN_FAILED = 1
_STATUS_NOT_AUTHORIZED = 2
_STATUS_AUTHORIZED = 3


def read_qgis_pg_params(conn_name: str = "") -> Optional[Dict[str, str]]:
    """Return PG params dict (host, port, dbname, user, password) or None."""
    if not conn_name:
        conn_name = os.environ.get("STAIR_QGIS_CONN_NAME") or QGIS_CONN_NAME
    if not conn_name:
        logger.warning("qgis_conn_name_not_configured")
        return None
    try:
        from qgis.core import QgsSettings
        prefix = f"PostgreSQL/connections/{conn_name}/"
        s = QgsSettings()
        host = s.value(prefix + "host", "")
        if not host:
            return None
        params = {
            'host': host,
            'port': s.value(prefix + "port", "5432"),
            'dbname': s.value(prefix + "database", ""),
            'user': s.value(prefix + "username", ""),
            'password': s.value(prefix + "password", ""),
        }
        authcfg = s.value(prefix + "authcfg", "")
        if not authcfg:
            authcfg = _AUTHCFG_NAME
        if authcfg and not params['password']:
            try:
                from qgis.core import QgsApplication, QgsAuthMethodConfig
                config = QgsAuthMethodConfig()
                QgsApplication.authManager().loadAuthenticationConfig(
                    authcfg, config, True,
                )
                resolved_user = config.config("username")
                resolved_pwd = config.config("password")
                if resolved_user:
                    params['user'] = resolved_user
                if resolved_pwd:
                    params['password'] = resolved_pwd
            except Exception:
                pass
        if params['host'] and params['user']:
            return params
        return None
    except Exception:
        return None


def _user_hash(user: str) -> str:
    return hashlib.sha256(user.lower().strip().encode()).hexdigest()


def _probe_authorization_status(params: Dict[str, str]) -> int:
    """Open a short PG connection and return one of ``_STATUS_*``."""
    try:
        import psycopg2
    except ImportError:
        logger.debug("psycopg2_unavailable abort")
        return _STATUS_CONN_FAILED

    host = (params.get('host') or '').strip()
    if _ALLOWED_HOSTS and host not in _ALLOWED_HOSTS:
        logger.warning(
            "host_not_whitelisted host=%r expected=%s",
            host, sorted(_ALLOWED_HOSTS),
        )
        return _STATUS_CONN_FAILED

    if not _DB_SCHEMA or not _DB_TABLE:
        logger.warning("auth_table_not_configured")
        return _STATUS_NOT_AUTHORIZED

    t_connect = time.monotonic()
    conn = None
    try:
        conn = psycopg2.connect(
            host=host,
            port=params['port'],
            dbname=params['dbname'],
            user=params['user'],
            password=params.get('password', ''),
            connect_timeout=_CONNECT_TIMEOUT_S,
        )
    except Exception as exc:
        logger.debug(
            "pg_connect_fail host=%s dbname=%r err_type=%s elapsed_ms=%d",
            host, params.get('dbname'),
            type(exc).__name__,
            int((time.monotonic() - t_connect) * 1000),
        )
        return _STATUS_CONN_FAILED

    elapsed_connect_ms = int((time.monotonic() - t_connect) * 1000)
    logger.debug("pg_connect_ok host=%s elapsed_ms=%d", host, elapsed_connect_ms)

    cur = None
    try:
        cur = conn.cursor()
        cur.execute("SELECT current_user")
        row = cur.fetchone()
        current_user = (row[0] if row else "").lower().strip()
        if not current_user:
            logger.debug("current_user_empty")
            return _STATUS_CONN_FAILED
        uhash8 = _user_hash(current_user)[:8]

        t_sql = time.monotonic()
        try:
            cur.execute(
                "SELECT 1 FROM \"{schema}\".\"{table}\" "
                "WHERE username = %s AND enabled = TRUE LIMIT 1".format(
                    schema=_DB_SCHEMA, table=_DB_TABLE,
                ),
                (current_user,),
            )
            hit = cur.fetchone() is not None
            elapsed_sql_ms = int((time.monotonic() - t_sql) * 1000)
            logger.info(
                "db_check uhash8=%s r=%s elapsed_ms=%d",
                uhash8, int(hit), elapsed_sql_ms,
            )
            return _STATUS_AUTHORIZED if hit else _STATUS_NOT_AUTHORIZED
        except Exception as exc:
            elapsed_sql_ms = int((time.monotonic() - t_sql) * 1000)
            logger.error(
                "db_query_failed err_type=%s msg=%s elapsed_ms=%d",
                type(exc).__name__, exc, elapsed_sql_ms,
            )
            try:
                conn.rollback()
            except Exception:
                pass
            return _STATUS_NOT_AUTHORIZED
    except Exception as exc:
        logger.warning(
            "probe_unexpected_error err_type=%s msg=%s",
            type(exc).__name__, exc,
        )
        return _STATUS_CONN_FAILED
    finally:
        if cur is not None:
            try:
                cur.close()
            except Exception:
                pass
        try:
            conn.close()
        except Exception:
            pass


def test_pg_connection(
    host: str, port, dbname: str, username: str, password: str,
) -> tuple:
    """Try a sync connection. Returns ``(ok, message)``."""
    t0 = time.monotonic()
    try:
        import psycopg2
    except ImportError:
        logger.error("test_pg_connection_psycopg2_missing")
        return False, "psycopg2 indisponible dans cette installation QGIS"

    h = (host or '').strip()
    if _ALLOWED_HOSTS and h not in _ALLOWED_HOSTS:
        return False, (
            "Host %r non autorise." % h
        )

    try:
        conn = psycopg2.connect(
            host=h, port=port, dbname=dbname, user=username,
            password=password, connect_timeout=_CONNECT_TIMEOUT_S,
        )
        cur = conn.cursor()
        cur.execute("SELECT current_user")
        row = cur.fetchone()
        cur.close()
        conn.close()
        elapsed_ms = int((time.monotonic() - t0) * 1000)
        logger.info(
            "test_pg_connection ok user_hash8=%s elapsed_ms=%d",
            _user_hash(row[0])[:8] if row else "none", elapsed_ms,
        )
        return True, "Connexion OK (current_user=%s)" % (row[0] if row else "?")
    except Exception as exc:
        elapsed_ms = int((time.monotonic() - t0) * 1000)
        logger.info(
            "test_pg_connection fail err_type=%s elapsed_ms=%d",
            type(exc).__name__, elapsed_ms,
        )
        return False, "%s: %s" % (type(exc).__name__, exc)


_AUTHCFG_NAME = os.environ.get("STAIR_AUTHCFG_NAME", "stair_export_auvergne")


def save_qgis_pg_credentials(
    host: str, port, dbname: str, username: str, password: str,
    conn_name: str = "",
) -> bool:
    """Persist credentials in QgsAuthManager and link them to ``conn_name``."""
    if not conn_name:
        conn_name = os.environ.get("STAIR_QGIS_CONN_NAME") or QGIS_CONN_NAME
    if not conn_name:
        logger.error("save_creds_conn_name_empty")
        return False

    authcfg_name = os.environ.get("STAIR_AUTHCFG_NAME") or _AUTHCFG_NAME
    if not authcfg_name:
        logger.error("authcfg_name_not_configured")
        return False

    try:
        from qgis.core import (
            QgsApplication, QgsAuthMethodConfig, QgsSettings,
        )
    except ImportError:
        logger.error("save_creds_qgis_unavailable")
        return False

    auth_manager = QgsApplication.authManager()
    if auth_manager is None:
        logger.error("save_creds_authmgr_none")
        return False

    existing_id = None
    try:
        configs = auth_manager.availableAuthMethodConfigs()
        for cfg_id, cfg_meta in configs.items():
            if cfg_meta.name() == authcfg_name:
                existing_id = cfg_id
                break
    except Exception as exc:
        logger.warning(
            "save_creds_list_authcfg_err err_type=%s msg=%s",
            type(exc).__name__, exc,
        )

    cfg = QgsAuthMethodConfig()
    cfg.setName(authcfg_name)
    cfg.setMethod("Basic")
    cfg.setConfig("username", username)
    cfg.setConfig("password", password)

    if existing_id:
        cfg.setId(existing_id)
        ok = auth_manager.updateAuthenticationConfig(cfg)
        op = "update"
    else:
        ok = auth_manager.storeAuthenticationConfig(cfg)
        op = "store"

    if not ok:
        logger.error("save_creds_authcfg_%s_failed", op)
        return False

    authcfg_id = cfg.id()
    if not authcfg_id:
        logger.error("save_creds_authcfg_id_empty op=%s", op)
        return False

    settings = QgsSettings()
    prefix = "PostgreSQL/connections/%s/" % conn_name
    try:
        settings.setValue(prefix + "host", host)
        settings.setValue(prefix + "port", str(port))
        settings.setValue(prefix + "database", dbname)
        settings.setValue(prefix + "authcfg", authcfg_id)
        settings.setValue(prefix + "service", "")
        settings.setValue(prefix + "savePassword", "false")
        settings.setValue(prefix + "saveUsername", "false")
        settings.remove(prefix + "username")
        settings.remove(prefix + "password")
    except Exception as exc:
        logger.error(
            "save_creds_settings_write_err err_type=%s msg=%s",
            type(exc).__name__, exc,
        )
        return False

    logger.info(
        "save_creds_ok conn=%s authcfg_id=%s op=%s",
        conn_name, authcfg_id, op,
    )
    return True


class AuthProbe(QObject):
    """Background connection probe with retry. Emits ``authorized``
    once when done. Emits ``prompt_credentials`` after all retries
    fail to connect.
    """
    authorized = pyqtSignal(bool)
    prompt_credentials = pyqtSignal()
    _attempt_done = pyqtSignal(int)

    _RETRY_DELAYS_MS = (1500, 3500, 7000)

    def __init__(self, parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        self._thread: Optional[threading.Thread] = None
        self._result: bool = False
        self._should_prompt: bool = False
        self._params: Optional[Dict[str, str]] = None
        self._attempt: int = 0
        self._final_emitted: bool = False
        self._attempt_done.connect(self._on_attempt_done)
        self._t_start: float = 0.0

    @property
    def should_prompt(self) -> bool:
        return self._should_prompt

    def start(self) -> None:
        self._params = read_qgis_pg_params()
        if self._params is None:
            logger.debug("probe_skip_no_conn")
            QTimer.singleShot(0, self._emit_final)
            return
        self._attempt = 0
        self._final_emitted = False
        self._t_start = time.monotonic()
        logger.debug(
            "probe_start max_attempts=%d",
            len(self._RETRY_DELAYS_MS) + 1,
        )
        self._spawn_attempt()

    def _spawn_attempt(self) -> None:
        self._attempt += 1
        self._thread = threading.Thread(
            target=self._run,
            daemon=True,
            name="probe_t%d" % self._attempt,
        )
        self._thread.start()

    def _run(self) -> None:
        t0 = time.monotonic()
        status = _probe_authorization_status(self._params)
        elapsed_ms = int((time.monotonic() - t0) * 1000)
        attempt = self._attempt
        logger.debug(
            "probe_try n=%d s=%d elapsed_ms=%d",
            attempt, status, elapsed_ms,
        )
        self._attempt_done.emit(status)

    def _on_attempt_done(self, status: int) -> None:
        if self._final_emitted:
            return

        if status == _STATUS_AUTHORIZED:
            self._result = True
            self._should_prompt = False
            logger.info(
                "probe_ok attempt=%d total_elapsed_ms=%d",
                self._attempt,
                int((time.monotonic() - self._t_start) * 1000),
            )
            self._emit_final()
            return

        if status == _STATUS_NOT_AUTHORIZED:
            self._result = False
            self._should_prompt = False
            logger.info(
                "probe_no attempt=%d total_elapsed_ms=%d",
                self._attempt,
                int((time.monotonic() - self._t_start) * 1000),
            )
            self._emit_final()
            return

        if self._attempt <= len(self._RETRY_DELAYS_MS):
            delay_ms = self._RETRY_DELAYS_MS[self._attempt - 1]
            logger.debug(
                "probe_retry next_in_ms=%d left=%d",
                delay_ms, len(self._RETRY_DELAYS_MS) - self._attempt + 1,
            )
            QTimer.singleShot(delay_ms, self._spawn_attempt)
            return

        self._result = False
        self._should_prompt = True
        logger.info(
            "probe_giveup attempts=%d total_elapsed_ms=%d",
            self._attempt,
            int((time.monotonic() - self._t_start) * 1000),
        )
        self._emit_final()

    def _emit_final(self) -> None:
        if self._final_emitted:
            return
        self._final_emitted = True
        try:
            self.authorized.emit(self._result)
            if self._should_prompt:
                self.prompt_credentials.emit()
        except RuntimeError:
            return

    def cancel(self) -> None:
        """Stop emitting any further signal. Safe to call multiple times."""
        if not self._final_emitted:
            logger.debug("probe_cancel attempt=%d", self._attempt)
        self._final_emitted = True
