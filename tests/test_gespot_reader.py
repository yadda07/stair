# -*- coding: utf-8 -*-
# Youcef ADDA | GEODECI
# FR : Plugins QGIS, solutions SIG et administration PostgreSQL/PostGIS ; audit, automatisation et accompagnement.
# EN : QGIS plugins, GIS solutions and PostgreSQL/PostGIS administration; audits, workflow automation and business support.
# AR : تطوير إضافات QGIS وحلول نظم المعلومات الجغرافية وإدارة PostgreSQL/PostGIS، والتدقيق والأتمتة ومرافقة المؤسسات.
# Site / Website / الموقع : https://geodeci.xyz/
# Contact professionnel / Business enquiries / للتواصل المهني : youcef.geodesien@gmail.com | contact@geodeci.xyz
# Détails / Details / التفاصيل : SIGNATURE.md
# ============================================================================
"""Tests unitaires pour gespot_reader.load_gespot_from_rows().

Portee : valider la lecture par noms de colonnes (BDD/GPKG) et la
resolution des doublons, sans dependance QGIS.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

# Ajoute le dossier plugins/ au path pour l'import absolu
_PLUGINS_DIR = Path(__file__).resolve().parents[2]
if str(_PLUGINS_DIR) not in sys.path:
    sys.path.insert(0, str(_PLUGINS_DIR))

from stair.gespot_reader import (
    GespotRecord,
    load_gespot_from_rows,
    _build_record_from_dict,
    _normalize_oui_non,
    _normalize_to_empty,
    _br02_calc_strategie,
    _br04bis_calc_pres_elect,
    _br05_calc_inacc,
    _br06_calc_recalage,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_row(
    num: str = "0679080",
    voie: str = "SANAT",
    num_voie: str = "",
    centre: str = "MAG",
    type_raw: str = "BS8",
    strategique: str = "N",
    pres_elect: str = "",
    support_pc: str = "",
    inacc: str = "",
    recalage: str = "",
    distance_electrique: str = "",
    source_file: str = "D0_19x.csv",
    caracs=None,
    envs=None,
    etat_pts=None,
) -> dict:
    """Construit un dict d'attributs similaire a une ligne BDD data_gespot."""
    caracs = caracs or ["", "", "", "", ""]
    envs = envs or ["", "", "", "", ""]
    etat_pts = etat_pts or [("", ""), ("", ""), ("", "")]
    d = {
        "num": num,
        "voie": voie,
        "num_voie": num_voie,
        "centre": centre,
        "type": type_raw,
        "strategique": strategique,
        "pres_elect": pres_elect,
        "support_pc": support_pc,
        "inacc": inacc,
        "recalage": recalage,
        "distance_electrique": distance_electrique,
        "source_file": source_file,
    }
    for i, c in enumerate(caracs, 1):
        d[f"carac{i}"] = c
    for i, e in enumerate(envs, 1):
        d[f"env{i}"] = e
    for i, (etat, declass) in enumerate(etat_pts, 1):
        d[f"etat_pt{i}"] = etat
        d[f"declass_pt{i}"] = declass
    return d


# ===========================================================================
# _build_record_from_dict : cas nominal
# ===========================================================================

class TestBuildRecordFromDict:
    def test_nominal_fields(self):
        attrs = _make_row(num="0679080", voie="RUE X", centre="MAG")
        rec = _build_record_from_dict(attrs, "data_gespot", set())
        assert rec.num == "0679080"
        assert rec.voie == "RUE X"
        assert rec.centre == "MAG"
        assert rec.source_file == "D0_19x.csv"

    def test_source_file_fallback(self):
        attrs = _make_row(source_file="")
        rec = _build_record_from_dict(attrs, "data_gespot", set())
        assert rec.source_file == "data_gespot"

    def test_dist_elec_mapped_from_distance_electrique(self):
        attrs = _make_row(distance_electrique="OUI")
        rec = _build_record_from_dict(attrs, "data_gespot", set())
        assert rec.dist_elec == "OUI"

    def test_type_calc_br01(self):
        attrs = _make_row(type_raw="MC2", caracs=["ANC", "", "", "", ""])
        rec = _build_record_from_dict(attrs, "data_gespot", set())
        assert rec.type_calc == "MC2 ANC MIN"

    def test_strategie_calc_br02(self):
        attrs = _make_row(strategique="O", support_pc="SP1")
        rec = _build_record_from_dict(attrs, "data_gespot", set())
        assert rec.strategie_calc == "SP1"

    def test_strategie_calc_non_when_not_O(self):
        attrs = _make_row(strategique="N")
        rec = _build_record_from_dict(attrs, "data_gespot", set())
        assert rec.strategie_calc == "Non"

    def test_pres_elect_default_non(self):
        attrs = _make_row(pres_elect="")
        rec = _build_record_from_dict(attrs, "data_gespot", set())
        assert rec.pres_elect_calc == "Non"

    def test_inacc_from_env_codes(self):
        attrs = _make_row(envs=["IN8", "", "", "", ""])
        rec = _build_record_from_dict(attrs, "data_gespot", set())
        assert rec.inacc_calc == "Oui"

    def test_recalage_br06(self):
        attrs = _make_row(recalage="O")
        rec = _build_record_from_dict(attrs, "data_gespot", set())
        assert rec.recalage_raw == "O"

    def test_etat_declass_br04(self):
        attrs = _make_row(etat_pts=[("00", ""), ("", ""), ("", "")])
        rec = _build_record_from_dict(attrs, "data_gespot", set())
        assert rec.etat_no_yellow == "Non"
        assert rec.etat_usable == "Non"
        assert rec.ctrl_visuel == "Oui"

    def test_none_values_treated_as_empty(self):
        attrs = _make_row()
        attrs["voie"] = None
        attrs["centre"] = None
        rec = _build_record_from_dict(attrs, "data_gespot", set())
        assert rec.voie == ""
        assert rec.centre == ""


# ===========================================================================
# Helpers de normalisation NULL/Non
# ===========================================================================

class TestNormalizeOuiNon:
    def test_none_returns_non(self):
        assert _normalize_oui_non(None) == 'Non'

    def test_empty_string_returns_non(self):
        assert _normalize_oui_non('') == 'Non'

    def test_non_variants_return_non(self):
        for val in ['Non', 'NON', 'n', 'N']:
            assert _normalize_oui_non(val) == 'Non'

    def test_null_variants_return_non(self):
        for val in ['NULL', 'null', 'Null']:
            assert _normalize_oui_non(val) == 'Non'

    def test_oui_variants_preserved(self):
        for val in ['O', 'Oui', 'OUI', 'o']:
            assert _normalize_oui_non(val) == val

    def test_other_values_preserved(self):
        assert _normalize_oui_non('SP1') == 'SP1'
        assert _normalize_oui_non('XYZ') == 'XYZ'


class TestNormalizeToEmpty:
    def test_none_returns_empty(self):
        assert _normalize_to_empty(None) == ''

    def test_empty_string_returns_empty(self):
        assert _normalize_to_empty('') == ''

    def test_non_variants_return_empty(self):
        for val in ['Non', 'NON', 'n', 'N']:
            assert _normalize_to_empty(val) == ''

    def test_null_variants_return_empty(self):
        for val in ['NULL', 'null', 'Null']:
            assert _normalize_to_empty(val) == ''

    def test_other_values_preserved(self):
        assert _normalize_to_empty('SP1') == 'SP1'
        assert _normalize_to_empty('XYZ') == 'XYZ'


# ===========================================================================
# Regles metier modifiees (NULL/Non + fallback strategie)
# ===========================================================================

class TestBr02CalcStrategieWithNull:
    def test_support_pc_non_triggers_fallback(self):
        # support_pc='Non' doit declencher le fallback sur carac
        result = _br02_calc_strategie('O', 'Non', ['SP1', '', '', '', ''], {'SP1'})
        assert result == 'SP1'

    def test_support_pc_null_triggers_fallback(self):
        # support_pc='NULL' doit declencher le fallback sur carac
        result = _br02_calc_strategie('O', 'NULL', ['SP1', '', '', '', ''], {'SP1'})
        assert result == 'SP1'

    def test_support_pc_empty_triggers_fallback(self):
        # support_pc='' doit declencher le fallback sur carac
        result = _br02_calc_strategie('O', '', ['SP1', '', '', '', ''], {'SP1'})
        assert result == 'SP1'

    def test_support_pc_n_triggers_fallback(self):
        # support_pc='N' doit declencher le fallback sur carac
        result = _br02_calc_strategie('O', 'N', ['SP1', '', '', '', ''], {'SP1'})
        assert result == 'SP1'

    def test_support_pc_value_no_fallback(self):
        # support_pc='SP1' ne doit pas declencher le fallback
        result = _br02_calc_strategie('O', 'SP1', ['XYZ', '', '', '', ''], {'SP1', 'XYZ'})
        assert result == 'SP1'

    def test_support_pc_value_no_regression(self):
        # support_pc='SP1' avec strategie='O' -> retourne 'SP1'
        result = _br02_calc_strategie('O', 'SP1', ['XYZ', '', '', '', ''], {'SP1', 'XYZ'})
        assert result == 'SP1'

    def test_support_pc_with_spaces_triggers_fallback(self):
        # support_pc=' Non ' doit declencher le fallback sur carac
        result = _br02_calc_strategie('O', ' Non ', ['SP1', '', '', '', ''], {'SP1'})
        assert result == 'SP1'

    def test_fallback_ignores_anc_even_if_whitelisted(self):
        # ANC est ignore dans le fallback sur caracs meme s'il est dans la whitelist
        result = _br02_calc_strategie('O', '', ['ANC', 'SP1', '', '', ''], {'ANC', 'SP1'})
        assert result == 'SP1'

    def test_strategique_non_returns_non(self):
        # strategique='N' -> retourne 'Non' quel que soit support_pc
        result = _br02_calc_strategie('N', 'SP1', ['SP1', '', '', '', ''], {'SP1'})
        assert result == 'Non'


class TestBr04bisCalcPresElectWithNull:
    def test_null_returns_non(self):
        assert _br04bis_calc_pres_elect('NULL') == 'Non'

    def test_non_returns_non(self):
        assert _br04bis_calc_pres_elect('Non') == 'Non'

    def test_empty_returns_non(self):
        assert _br04bis_calc_pres_elect('') == 'Non'

    def test_oui_preserved(self):
        assert _br04bis_calc_pres_elect('O') == 'O'


class TestBr05CalcInaccWithNull:
    def test_inacc_null_no_env_returns_non(self):
        assert _br05_calc_inacc('NULL', False) == 'Non'

    def test_inacc_null_lowercase_no_env_returns_non(self):
        assert _br05_calc_inacc('null', False) == 'Non'

    def test_inacc_non_no_env_returns_non(self):
        assert _br05_calc_inacc('Non', False) == 'Non'

    def test_inacc_null_with_env_returns_oui(self):
        # env IN8 prime sur inacc_raw
        assert _br05_calc_inacc('NULL', True) == 'Oui'

    def test_inacc_null_lowercase_with_env_returns_oui(self):
        # env IN8 prime sur inacc_raw
        assert _br05_calc_inacc('null', True) == 'Oui'

    def test_inacc_non_with_env_returns_oui(self):
        # env IN8 prime sur inacc_raw
        assert _br05_calc_inacc('Non', True) == 'Oui'

    def test_inacc_o_no_env_returns_oui(self):
        assert _br05_calc_inacc('O', False) == 'Oui'

    def test_inacc_oui_no_env_returns_oui(self):
        assert _br05_calc_inacc('Oui', False) == 'Oui'


class TestBr06CalcRecalageWithNull:
    def test_recalage_null_returns_oui(self):
        # NULL -> traité comme vide -> verticalité attendue = Oui
        assert _br06_calc_recalage('NULL') == 'Oui'

    def test_recalage_null_lowercase_returns_oui(self):
        # null -> traité comme vide -> verticalité attendue = Oui
        assert _br06_calc_recalage('null') == 'Oui'

    def test_recalage_non_returns_oui(self):
        # Non -> traité comme vide -> verticalité attendue = Oui
        assert _br06_calc_recalage('Non') == 'Oui'

    def test_recalage_empty_returns_oui(self):
        assert _br06_calc_recalage('') == 'Oui'

    def test_recalage_o_returns_non(self):
        # RECALAGE=O -> verticalité attendue = Non
        assert _br06_calc_recalage('O') == 'Non'

    def test_recalage_oui_returns_non(self):
        assert _br06_calc_recalage('Oui') == 'Non'


# ===========================================================================
# load_gespot_from_rows : cas nominaux et edge cases
# ===========================================================================

class TestLoadGespotFromRows:
    def test_empty_rows(self):
        result = load_gespot_from_rows([], "data_gespot")
        assert len(result.records) == 0
        assert len(result.anomalies) == 0

    def test_single_row(self):
        rows = [_make_row(num="0679080")]
        result = load_gespot_from_rows(rows, "data_gespot")
        assert len(result.records) == 1
        # normalize_appui_num strips leading zeros
        assert "679080" in result.records
        assert result.records["679080"].num == "0679080"

    def test_skips_empty_num(self):
        rows = [_make_row(num=""), _make_row(num="0679080")]
        result = load_gespot_from_rows(rows, "data_gespot")
        assert len(result.records) == 1

    def test_skips_none_num(self):
        rows = [_make_row(num=None), _make_row(num="0679080")]
        result = load_gespot_from_rows(rows, "data_gespot")
        assert len(result.records) == 1

    def test_duplicate_identical(self):
        row = _make_row(num="0679080")
        rows = [row, dict(row)]
        result = load_gespot_from_rows(rows, "data_gespot")
        assert len(result.records) == 1
        assert len(result.anomalies) == 1
        assert result.anomalies[0]["type"] == "DOUBLON_IDENTIQUE"

    def test_duplicate_conflictual(self):
        row1 = _make_row(num="0679080", voie="RUE A")
        row2 = _make_row(num="0679080", voie="RUE B")
        result = load_gespot_from_rows([row1, row2], "data_gespot")
        assert len(result.records) == 0
        assert len(result.anomalies) == 1
        assert result.anomalies[0]["type"] == "DOUBLON_CONFLICTUEL"

    def test_file_counts_populated(self):
        rows = [_make_row(num="0679080"), _make_row(num="0679081")]
        result = load_gespot_from_rows(rows, "data_gespot")
        assert "data_gespot" in result.file_counts
        assert result.file_counts["data_gespot"] == 2

    def test_whitelist_passed_through(self):
        attrs = _make_row(strategique="O", support_pc="", caracs=["XYZ", "", "", "", ""])
        rows = [attrs]
        result = load_gespot_from_rows(rows, "data_gespot", whitelist={"XYZ"})
        rec = list(result.records.values())[0]
        assert rec.strategie_calc == "XYZ"
