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
Batch Data Extractor - centralises les extractions QGIS.

Single extraction pass on the main thread before any task is launched.
All modules share the same pre-extracted data via ExtractedData.

Depuis la suppression de la BDD PostgreSQL : les cables / BPE / attaches
doivent desormais etre charges depuis le GeoPackage livre (cf gpkg_pipeline).
Les champs correspondants de ExtractedData sont conserves comme contrat
pour les consommateurs, mais ne sont plus peuples par cet extracteur.
"""

from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional
from .compat import MSG_INFO, MSG_WARNING, MSG_CRITICAL


@dataclass
class ExtractedData:
    """Immutable snapshot of all batch data extracted on the main thread."""

    # CAP_FT extraction
    doublons_capft: List = field(default_factory=list)
    hors_etude_capft: List = field(default_factory=list)
    dico_qgis_capft: Dict = field(default_factory=dict)
    dico_prives_capft: Dict = field(default_factory=dict)
    all_inf_nums_capft: Any = field(default_factory=set)
    coords_qgis_capft: Dict = field(default_factory=dict)

    # COMAC extraction
    doublons_comac: List = field(default_factory=list)
    hors_etude_comac: List = field(default_factory=list)
    dico_qgis_comac: Dict = field(default_factory=dict)
    dico_prives_comac: Dict = field(default_factory=dict)
    all_inf_nums_comac: Any = field(default_factory=set)
    coords_qgis_comac: Dict = field(default_factory=dict)

    # Appuis WKB: two variants (COMAC needs /commune, Police C6 does not)
    appuis_wkb_commune: List = field(default_factory=list)
    appuis_wkb_plain: List = field(default_factory=list)

    # C6 vs BD extraction (DataFrames)
    df_c6bd_in: Any = None
    df_c6bd_out: Any = None

    # Cables / BPE / attaches : a peupler depuis un GeoPackage externe
    # (cf gpkg_pipeline). Conserves en contrat pour les consommateurs.
    cables: Optional[List] = None       # list[CableSegment]
    cables_source: str = ''             # 'gpkg' | ''
    bpe_list: List = field(default_factory=list)
    attaches_raw: List = field(default_factory=list)

    # Metadata
    sro: str = ''


class BatchDataExtractor:
    """Centralised QGIS + PostgreSQL extraction for batch runs.

    Called once on the main thread before runner.start().
    Delegates to existing module functions (no logic duplication).
    """

    def extract_all(
        self,
        module_keys,
        lyr_pot,
        lyr_cap,
        lyr_com,
        sro: str,
        capft_col: str = '',
        comac_col: str = '',
        c6bd_col: str = '',
    ) -> ExtractedData:
        """Extract all QGIS-side data required by module_keys.

        Note : les cables/BPE/attaches ne sont plus extraits ici (BDD supprimee).
        Ils doivent etre peuples en amont depuis un GeoPackage.
        """
        keys = set(module_keys)
        data = ExtractedData(sro=sro)

        needs_capft = 'capft' in keys
        needs_comac = 'comac' in keys
        needs_c6bd = bool({'c6bd', 'c6c3a', 'police_c6'} & keys)
        needs_appuis = bool({'comac', 'police_c6'} & keys)

        if needs_capft and lyr_pot and lyr_cap:
            self._extract_capft(data, lyr_pot.name(), lyr_cap.name(), capft_col)

        if needs_comac and lyr_pot and lyr_com:
            self._extract_comac(data, lyr_pot.name(), lyr_com.name(), comac_col)

        if needs_c6bd and lyr_pot and lyr_cap:
            self._extract_c6bd(data, lyr_pot.name(), lyr_cap.name(), c6bd_col)

        if needs_appuis and lyr_pot and sro:
            self._extract_appuis(data, lyr_pot)

        return data

    # ------------------------------------------------------------------
    #  Phase A: QGIS extractions (main thread)
    # ------------------------------------------------------------------

    def _extract_capft(self, data, pot_name, cap_name, col):
        from .CapFt import CapFt
        try:
            cap = CapFt()
            (data.doublons_capft, data.hors_etude_capft,
             data.dico_qgis_capft, data.dico_prives_capft,
             data.all_inf_nums_capft, data.coords_qgis_capft) = (
                cap.extraire_donnees_capft(pot_name, cap_name, col)
            )
        except Exception as e:
            from qgis.core import QgsMessageLog, Qgis
            QgsMessageLog.logMessage(
                f"BatchExtractor._extract_capft: {e}", "PoleAerien", MSG_WARNING
            )

    def _extract_comac(self, data, pot_name, com_name, col):
        from .Comac import Comac
        try:
            com = Comac()
            (data.doublons_comac, data.hors_etude_comac,
             data.dico_qgis_comac, data.dico_prives_comac,
             data.all_inf_nums_comac, data.coords_qgis_comac) = (
                com.extraire_donnees_comac(pot_name, com_name, col)
            )
        except Exception as e:
            from qgis.core import QgsMessageLog, Qgis
            QgsMessageLog.logMessage(
                f"BatchExtractor._extract_comac: {e}", "PoleAerien", MSG_WARNING
            )

    def _extract_c6bd(self, data, pot_name, cap_name, col):
        from .C6_vs_Bd import C6_vs_Bd
        try:
            c6bd = C6_vs_Bd()
            data.df_c6bd_in, data.df_c6bd_out = c6bd.extraire_poteaux_in_out(
                pot_name, cap_name, col
            )
        except Exception as e:
            from qgis.core import QgsMessageLog, Qgis
            QgsMessageLog.logMessage(
                f"BatchExtractor._extract_c6bd: {e}", "PoleAerien", MSG_WARNING
            )

    def _extract_appuis(self, data, lyr_pot):
        from .cable_analyzer import extraire_appuis_wkb_both
        try:
            data.appuis_wkb_commune, data.appuis_wkb_plain = (
                extraire_appuis_wkb_both(lyr_pot)
            )
        except Exception as e:
            from qgis.core import QgsMessageLog, Qgis
            QgsMessageLog.logMessage(
                f"BatchExtractor._extract_appuis: {e}", "PoleAerien", MSG_WARNING
            )

