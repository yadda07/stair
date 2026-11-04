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
Version management for ST-AIR plugin.

Reads the plugin version from metadata.txt (single source of truth).
Pure Python, no QGIS dependencies, importable outside QGIS.
"""

import logging
import os
from configparser import ConfigParser
from typing import Optional

logger = logging.getLogger(__name__)


def _read_version_from_metadata(plugin_dir: str) -> str:
    """Read version from metadata.txt file.

    Args:
        plugin_dir: Path to the plugin directory containing metadata.txt

    Returns:
        Version string from metadata.txt, or "unknown" if file missing/invalid
    """
    path = os.path.join(plugin_dir, "metadata.txt")
    if not os.path.exists(path):
        logger.warning(
            "metadata.txt not found at %s, returning unknown version",
            path
        )
        return "unknown"
    
    cp = ConfigParser()
    try:
        cp.read(path, encoding="utf-8")
        version = cp.get("general", "version", fallback="unknown")
        if version == "unknown":
            logger.warning(
                "version field not found in metadata.txt at %s",
                path
            )
        return version
    except Exception as exc:
        logger.warning(
            "Failed to read version from metadata.txt at %s: %s",
            path,
            exc
        )
        return "unknown"


def get_version(plugin_dir: Optional[str] = None) -> str:
    """Get the plugin version.

    Args:
        plugin_dir: Path to plugin directory. If None, uses the directory
                    of this file (the plugin root).

    Returns:
        Version string from metadata.txt, or "unknown" if unavailable
    """
    if plugin_dir is None:
        plugin_dir = os.path.dirname(os.path.abspath(__file__))
    return _read_version_from_metadata(plugin_dir)


# Constant loaded at import time
VERSION: str = get_version()
