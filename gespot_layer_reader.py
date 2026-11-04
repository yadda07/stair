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
Lecture GESPOT depuis une couche QGIS (data_gespot).

Responsabilité unique : extraire les features d'une QgsVectorLayer et
déléguer à gespot_reader.load_gespot_from_rows() pour le traitement métier.

Contrainte thread-safety : cette fonction DOIT être appelée depuis le
thread principal (itérateur QGIS non thread-safe entre threads).
"""
from __future__ import annotations

import logging
import time
from typing import Optional, Set

from .gespot_reader import GespotLoadResult, load_gespot_from_rows

_logger = logging.getLogger(__name__)


def extract_rows_from_qgs_layer(layer) -> tuple:
    """Extrait les dicts d'attributs bruts depuis une couche QGIS.

    DOIT etre appele depuis le thread principal (iterateur QGIS).
    Ne traite aucune regle metier : retourne les dicts bruts et le
    source_label pour que load_gespot_from_rows() soit appele en
    worker thread avec la whitelist correcte.

    Args:
        layer: QgsVectorLayer contenant les colonnes data_gespot.

    Returns:
        (rows, source_label) ou rows est une list[dict] et
        source_label le nom de la couche.

    Raises:
        ValueError: si la couche est invalide ou sans colonne 'num'.
    """
    if layer is None or not layer.isValid():
        raise ValueError("Couche data_gespot invalide ou non chargee")

    fields = layer.fields()
    field_names = {f.name().lower() for f in fields}
    if 'num' not in field_names:
        raise ValueError(
            f"La couche '{layer.name()}' ne contient pas de colonne 'num' "
            f"(champs disponibles: {[f.name() for f in fields]})"
        )

    rows = []
    for feat in layer.getFeatures():
        attrs = {}
        for field in fields:
            val = feat[field.name()]
            if val is None:
                attrs[field.name().lower()] = ''
            else:
                attrs[field.name().lower()] = (
                    str(val).strip() if isinstance(val, str) else val
                )
        rows.append(attrs)

    source_label = layer.name()
    _logger.debug(
        "extract_rows_from_qgs_layer layer=%s n_features=%d",
        source_label, len(rows),
    )
    return rows, source_label


def load_gespot_from_qgs_layer(
    layer,
    whitelist: Optional[Set[str]] = None,
) -> GespotLoadResult:
    """Charge les enregistrements GESPOT depuis une couche QGIS.

    Lit les features de la couche (itérateur QGIS), extrait les attributs
    nommés et délègue à load_gespot_from_rows() pour la normalisation et
    les règles métier BR-01 à BR-06.

    Args:
        layer:      QgsVectorLayer contenant les colonnes data_gespot
                    (num, voie, centre, type, carac1-5, env1-5, etc.).
        whitelist:  Ensemble de codes stratégie valides (depuis Bases col M).

    Returns:
        GespotLoadResult avec records indexés par NUM, anomalies, compteurs.

    Raises:
        ValueError: si la couche est invalide ou sans colonne 'num'.
    """
    t0 = time.monotonic()

    if layer is None or not layer.isValid():
        raise ValueError("Couche data_gespot invalide ou non chargee")

    fields = layer.fields()
    field_names = {f.name().lower() for f in fields}
    if 'num' not in field_names:
        raise ValueError(
            f"La couche '{layer.name()}' ne contient pas de colonne 'num' "
            f"(champs disponibles: {[f.name() for f in fields]})"
        )

    rows = []
    n_features = 0
    for feat in layer.getFeatures():
        n_features += 1
        attrs = {}
        for field in fields:
            val = feat[field.name()]
            if val is None:
                attrs[field.name().lower()] = ''
            else:
                attrs[field.name().lower()] = str(val).strip() if isinstance(val, str) else val
        rows.append(attrs)

    source_label = layer.name()
    result = load_gespot_from_rows(rows, source_label=source_label,
                                   whitelist=whitelist)

    elapsed_ms = int((time.monotonic() - t0) * 1000)
    n_items = len(result.records)
    _logger.info(
        "load_gespot_from_qgs_layer layer=%s n_features=%d n_items=%d "
        "n_anomalies=%d elapsed_ms=%d",
        source_label, n_features, n_items, len(result.anomalies), elapsed_ms,
    )
    return result
