# -*- coding: utf-8 -*-
# Youcef ADDA | GEODECI
# FR : Plugins QGIS, solutions SIG et administration PostgreSQL/PostGIS ; audit, automatisation et accompagnement.
# EN : QGIS plugins, GIS solutions and PostgreSQL/PostGIS administration; audits, workflow automation and business support.
# AR : تطوير إضافات QGIS وحلول نظم المعلومات الجغرافية وإدارة PostgreSQL/PostGIS، والتدقيق والأتمتة ومرافقة المؤسسات.
# Site / Website / الموقع : https://geodeci.xyz/
# Contact professionnel / Business enquiries / للتواصل المهني : youcef.geodesien@gmail.com | contact@geodeci.xyz
# Détails / Details / التفاصيل : SIGNATURE.md
# ============================================================================
"""Tests unitaires pour gespot_c6_comparator._compare_one().

Portee : valider que les controles adresse et centre sont forces OK.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

# Ajoute le dossier plugins/ au path pour l'import absolu
_PLUGINS_DIR = Path(__file__).resolve().parents[2]
if str(_PLUGINS_DIR) not in sys.path:
    sys.path.insert(0, str(_PLUGINS_DIR))

from stair.gespot_c6_comparator import (
    _compare_one,
    C6Record,
)
from stair.gespot_reader import GespotRecord


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_gespot(
    num: str = "0679080",
    voie: str = "RUE X",
    num_voie: str = "10",
    centre: str = "MAG",
    type_calc: str = "BS8",
    strategie_calc: str = "Non",
    milieu_calc: str = "",
    pres_elect_calc: str = "Non",
    inacc_calc: str = "Non",
    recalage_raw: str = "",
    etat_no_yellow: str = "Oui",
    etat_usable: str = "Oui",
    ctrl_visuel: str = "Oui",
    dist_elec: str = "",
    source_file: str = "test.csv",
) -> GespotRecord:
    return GespotRecord(
        num=num,
        voie=voie,
        num_voie=num_voie,
        centre=centre,
        dist_elec=dist_elec,
        source_file=source_file,
        type_calc=type_calc,
        strategie_calc=strategie_calc,
        milieu_calc=milieu_calc,
        pres_elect_calc=pres_elect_calc,
        inacc_calc=inacc_calc,
        recalage_raw=recalage_raw,
        etat_no_yellow=etat_no_yellow,
        etat_usable=etat_usable,
        ctrl_visuel=ctrl_visuel,
    )


def _make_c6(
    num: str = "0679080",
    adresse: str = "10 RUE X",
    centre: str = "MAG",
    type_c6: str = "BS8",
    ctrl_vis: str = "Oui",
    vertic: str = "Oui",
    yellow: str = "Oui",
    usable: str = "Oui",
    env: str = "",
    elec: str = "Non",
    strat: str = "Non",
    inacc: str = "Non",
    source_file: str = "test.xlsx",
) -> C6Record:
    return C6Record(
        num=num,
        adresse=adresse,
        centre=centre,
        type_c6=type_c6,
        ctrl_vis=ctrl_vis,
        vertic=vertic,
        yellow=yellow,
        usable=usable,
        env=env,
        elec=elec,
        strat=strat,
        inacc=inacc,
        source_file=source_file,
    )


# ===========================================================================
# _compare_one : controles adresse et centre forces OK
# ===========================================================================

class TestCompareOneAddressCentreForced:
    def test_adresse_forced_ok_even_with_different_values(self):
        gespot = _make_gespot(voie="RUE A", num_voie="1", centre="MAG")
        c6 = _make_c6(adresse="999 RUE Z", centre="XYZ")
        result = _compare_one(gespot, c6)
        assert result.statut_adresse == 'OK'
        assert result.detail_adresse == ''
        assert result.statut_centre == 'OK'
        assert result.detail_centre == ''

    def test_centre_forced_ok_even_with_different_values(self):
        gespot = _make_gespot(centre="MAG")
        c6 = _make_c6(centre="XYZ")
        result = _compare_one(gespot, c6)
        assert result.statut_centre == 'OK'
        assert result.detail_centre == ''

    def test_adresse_centre_not_counted_in_ecarts(self):
        gespot = _make_gespot(voie="RUE A", num_voie="1", centre="MAG")
        c6 = _make_c6(adresse="999 RUE Z", centre="XYZ")
        result = _compare_one(gespot, c6)
        # adresse et centre forces OK, donc ne doivent pas etre comptes
        assert result.nb_ecarts == 0
        assert result.statut_global == 'OK'

    def test_other_comparisons_still_work(self):
        gespot = _make_gespot(type_calc="BS8", strategie_calc="Non")
        c6 = _make_c6(type_c6="MC2", strat="SP1")
        result = _compare_one(gespot, c6)
        # adresse et centre forces OK
        assert result.statut_adresse == 'OK'
        assert result.statut_centre == 'OK'
        # mais type et strategie doivent etre compares normalement
        assert result.statut_type == 'KO'
        assert result.statut_strat == 'KO'
        assert result.nb_ecarts == 2
