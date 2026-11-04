#!/usr/bin/python
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
Dataclasses pour les résultats des modules métier.

QA-EXPERT: Tous les résultats sont immuables après création.
PERF-SPECIALIST: Pas d'état mutable, retours propres.
"""

from dataclasses import dataclass, field
from typing import List, Dict, Tuple, Optional, Any


@dataclass
class ValidationResult:
    """Résultat générique de validation."""
    valide: bool = True
    erreurs: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    
    def add_error(self, msg: str) -> None:
        self.erreurs.append(msg)
        self.valide = False
    
    def add_warning(self, msg: str) -> None:
        self.warnings.append(msg)


@dataclass
class ExcelValidationResult(ValidationResult):
    """Résultat validation fichier Excel."""
    nom_fichier: str = ""
    colonnes_manquantes: List[str] = field(default_factory=list)
    colonnes_en_trop: List[str] = field(default_factory=list)
    structure_ft_ok: bool = False
    structure_bt_ok: bool = False


@dataclass
class PoteauxPolygoneResult:
    """Résultat contrôle poteaux dans polygones (REQ-MAJ-001)."""
    ft_hors_polygone: List[Tuple[str, float, float]] = field(default_factory=list)
    bt_hors_polygone: List[Tuple[str, float, float]] = field(default_factory=list)
    
    @property
    def nb_ft_hors(self) -> int:
        return len(self.ft_hors_polygone)
    
    @property
    def nb_bt_hors(self) -> int:
        return len(self.bt_hors_polygone)
    
    @property
    def tous_dans_polygone(self) -> bool:
        return self.nb_ft_hors == 0 and self.nb_bt_hors == 0


@dataclass
class EtudesValidationResult:
    """Résultat vérification études existent (REQ-MAJ-004)."""
    etudes_absentes_cap_ft: List[str] = field(default_factory=list)
    etudes_absentes_comac: List[str] = field(default_factory=list)
    
    @property
    def toutes_existent(self) -> bool:
        return len(self.etudes_absentes_cap_ft) == 0 and len(self.etudes_absentes_comac) == 0


@dataclass
class ImplantationValidationResult:
    """Résultat vérification type poteau si implantation (REQ-MAJ-006)."""
    erreurs_implantation: List[Dict[str, Any]] = field(default_factory=list)
    
    @property
    def valide(self) -> bool:
        return len(self.erreurs_implantation) == 0


@dataclass
class PoliceC6Result:
    """Résultat analyse Police C6 (refactoring PoliceC6.py).
    
    Remplace les attributs d'état mutable de PoliceC6.__init__
    PERF-SPECIALIST: Pas d'état global, résultats immuables.
    """
    nb_appui_corresp: int = 0
    nb_pbo_corresp: int = 0
    bpo_corresp: List[str] = field(default_factory=list)
    nb_appui_absent: int = 0
    nb_appui_absent_pot: int = 0
    pot_inf_num_present: List[str] = field(default_factory=list)
    inf_num_pot_absent: List[str] = field(default_factory=list)
    ebp_non_appui: List[str] = field(default_factory=list)
    absence: List[str] = field(default_factory=list)
    id_pot_present: List[int] = field(default_factory=list)
    id_pot_absent: List[int] = field(default_factory=list)
    bpe_pot_cap_ft: List[Any] = field(default_factory=list)
    ebp_appui_inconnu: List[str] = field(default_factory=list)
    liste_appui_ebp: List[List[Any]] = field(default_factory=list)
    presence_liste_appui_ebp: bool = False
    pbo_a_supprimer: set = field(default_factory=set)
    ebp_a_supprimer: set = field(default_factory=set)
    liste_cable_appui_trouve: List[List[Any]] = field(default_factory=list)
    liste_appui_capa_appui_absent: List[Any] = field(default_factory=list)


@dataclass
class CableCapaciteResult:
    """Résultat vérification capacité câbles (REQ-PLC6-006)."""
    anomalies: List[Dict[str, Any]] = field(default_factory=list)
    cables_traites: int = 0
    cables_valides: int = 0
    
    @property
    def taux_validite(self) -> float:
        if self.cables_traites == 0:
            return 100.0
        return (self.cables_valides / self.cables_traites) * 100


@dataclass
class BoitierValidationResult:
    """Résultat vérification boîtiers (REQ-PLC6-007)."""
    anomalies: List[Dict[str, Any]] = field(default_factory=list)
    boitiers_traites: int = 0
    boitiers_valides: int = 0


@dataclass
class EtudeC6Result:
    """Résultat analyse d'une étude C6."""
    etude: str = ""
    chemin_c6: str = ""
    statut: str = "PENDING"
    resultat: Optional[PoliceC6Result] = None
    erreur: Optional[str] = None


@dataclass 
class ParcourAutoC6Result:
    """Résultat parcours automatique C6 (REQ-PLC6-003)."""
    etudes_traitees: List[EtudeC6Result] = field(default_factory=list)
    
    @property
    def nb_ok(self) -> int:
        return sum(1 for e in self.etudes_traitees if e.statut == "OK")
    
    @property
    def nb_erreur(self) -> int:
        return sum(1 for e in self.etudes_traitees if e.statut == "ERREUR")
    
    @property
    def nb_total(self) -> int:
        return len(self.etudes_traitees)


