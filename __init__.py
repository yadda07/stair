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
/***************************************************************************
 Pôle Aérien
                                 A QGIS plugin
 Contrôle qualité et mise à jour des poteaux aériens ENEDIS (FT/BT)
                            -------------------
        begin                : 2022-03-01
        copyright            : (C) 2022-2026 by NGE ES
        email                : contact@nge-es.fr
        git sha              : $Format:%H$
 ***************************************************************************/

/***************************************************************************
 *                                                                         *
 *   This program is free software; you can redistribute it and/or modify  *
 *   it under the terms of the GNU General Public License as published by  *
 *   the Free Software Foundation; either version 2 of the License, or     *
 *   (at your option) any later version.                                   *
 *                                                                         *
 ***************************************************************************/
 This script initializes the plugin, making it known to QGIS.
"""

# -----------------------------------------------------------------------------
# FIX Windows fatal exception (access violation) dans openpyxl.reader.excel
# (reader/excel.py:135 -> fromstring(src) dans read_manifest).
#
# lxml/libxml2 n'est pas thread-safe sur Windows quand il est appele depuis un
# thread worker QgsTask pendant que GDAL/QGIS utilisent deja libxml2 sur le
# thread principal. Stack observee : xmlDictReference +
# RtlpEnterCriticalSectionContended sous PyQgsTask::start.
#
# openpyxl bascule sur xml.etree.ElementTree (pur Python, thread-safe) si la
# variable d'environnement OPENPYXL_LXML n'est pas "True" (cf.
# openpyxl/xml/__init__.py). Elle est lue a l'import du module, il faut donc
# la definir AVANT tout `import openpyxl` dans le process QGIS.
# -----------------------------------------------------------------------------
import warnings

warnings.filterwarnings("ignore", category=DeprecationWarning, module="openpyxl")

from .lib_compat import ensure_openpyxl_safe_backend as _ensure_ox
_ensure_ox()

# -----------------------------------------------------------------------------
# FIX pyarrow.compute.match_substring_regex manquant sur QGIS 3.34.x
#
# Certaines versions embarquent un pyarrow < 7.0 sans cette fonction.
# Pandas >= 2.0 l'appelle via ArrowStringArray meme pour str.contains(regex=False).
# On force le backend string « python » pour eviter le crash.
# Doit etre execute avant tout import pandas applicatif.
# -----------------------------------------------------------------------------
from .lib_compat import patch_pandas_arrow_backend as _patch_pa
_patch_pa()


class Initialisation:
    """Classe de métadonnées du plugin"""

    def __init__(self):
        pass

    def name(self):
        return "Pôle Aérien"

    def version(self):
        from .version import get_version
        return get_version()

    def description(self):
        return "Contrôle qualité et mise à jour des poteaux aériens ENEDIS (FT/BT) pour projets FTTH"

    def qgisMinimumVersion(self):
        return "3.28"

    def qgisMaximumVersion(self):
        return "4.99"

    def experimental(self):
        return False

    def author(self):
        return "NGE ES"

    def authorName(self):
        return self.author()

    def email(self):
        return "yadda@nge-es.fr"

    def icon(self):
        return "images/icon.svg"


# noinspection PyPep8Naming
def classFactory(iface):  # pylint: disable=invalid-name
    """Load PoleAerien class from file PoleAerien.

    :param iface: A QGIS interface instance.
    :type iface: QgsInterface
    """
    from .PoleAerien import PoleAerien
    return PoleAerien(iface)
