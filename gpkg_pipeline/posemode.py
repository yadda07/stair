# Youcef ADDA | GEODECI
# FR : Plugins QGIS, solutions SIG et administration PostgreSQL/PostGIS ; audit, automatisation et accompagnement.
# EN : QGIS plugins, GIS solutions and PostgreSQL/PostGIS administration; audits, workflow automation and business support.
# AR : تطوير إضافات QGIS وحلول نظم المعلومات الجغرافية وإدارة PostgreSQL/PostGIS، والتدقيق والأتمتة ومرافقة المؤسسات.
# Site / Website / الموقع : https://geodeci.xyz/
# Contact professionnel / Business enquiries / للتواصل المهني : youcef.geodesien@gmail.com | contact@geodeci.xyz
# Détails / Details / التفاصيل : SIGNATURE.md
# ============================================================================
"""
Classification posemode pour livrable sous-traitant aerien.

Version simplifiee de la table de decision de `rip_avg_nge.fddcpiax` :
- Les chambres (`chb_`) et autres (`atr_`) sont HORS livrable aerien
- Seuls les prefixes `pot_` (poteau) et `bpe_` (boite) sont reconnus
- Tout autre cas -> orphelin (posemode=4)

Codes posemode (compatibles avec la BDD NGE pour faciliter les controles) :
    1 = aerien
    4 = orphelin / indetermine
    0, 2, 3 = reserves (souterrain, facade, etc.) - non produits ici
"""
from __future__ import annotations

from typing import Optional


POSEMODE_AERIEN: int = 1
POSEMODE_ORPHELIN: int = 4


def classify_posemode(
    start_ref: Optional[str],
    end_ref: Optional[str],
) -> int:
    """Classe un segment selon ses 2 extremites matchees a l'infrastructure.

    Args:
        start_ref : reference ST_StartPoint (`pot_123`, `bpe_45` ou None)
        end_ref : reference ST_EndPoint (idem)

    Returns:
        POSEMODE_AERIEN si les 2 extremites sont pot_/bpe_,
        POSEMODE_ORPHELIN sinon.
    """
    if not start_ref or not end_ref:
        return POSEMODE_ORPHELIN

    start_ok = _is_aerien_prefix(start_ref)
    end_ok = _is_aerien_prefix(end_ref)
    if start_ok and end_ok:
        return POSEMODE_AERIEN
    return POSEMODE_ORPHELIN


def _is_aerien_prefix(ref: str) -> bool:
    """Vrai si la reference est un point de l'aerien (poteau ou BPE)."""
    return ref.startswith("pot_") or ref.startswith("bpe_")
