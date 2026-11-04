# -*- coding: utf-8 -*-
# Youcef ADDA | GEODECI
# FR : Plugins QGIS, solutions SIG et administration PostgreSQL/PostGIS ; audit, automatisation et accompagnement.
# EN : QGIS plugins, GIS solutions and PostgreSQL/PostGIS administration; audits, workflow automation and business support.
# AR : تطوير إضافات QGIS وحلول نظم المعلومات الجغرافية وإدارة PostgreSQL/PostGIS، والتدقيق والأتمتة ومرافقة المؤسسات.
# Site / Website / الموقع : https://geodeci.xyz/
# Contact professionnel / Business enquiries / للتواصل المهني : youcef.geodesien@gmail.com | contact@geodeci.xyz
# Détails / Details / التفاصيل : SIGNATURE.md
# ============================================================================
"""Package workflows - Orchestrateurs pour les modules métier."""

from .comac_workflow import ComacWorkflow
from .capft_workflow import CapFtWorkflow
from .c6bd_workflow import C6BdWorkflow
from .c6c3a_workflow import C6C3AWorkflow
from .police_workflow import PoliceWorkflow
from .gespot_workflow import GespotWorkflow

__all__ = [
    'ComacWorkflow',
    'CapFtWorkflow',
    'C6BdWorkflow',
    'C6C3AWorkflow',
    'PoliceWorkflow',
    'GespotWorkflow',
]
