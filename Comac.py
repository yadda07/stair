#!/usr/bin/python

# -*- coding: utf-8 -*-



import os
import re

import openpyxl

from .qgis_utils import extraire_poteaux_etude

from .core_utils import normalize_appui_num, normaliser_boitier
from .compat import MSG_INFO, MSG_WARNING, MSG_CRITICAL

from .security_rules import (

    get_capacite_fo_from_code,

    get_capacites_possibles,

    verifier_portee,

    verifier_distance_sol,

    est_terrain_prive,

    EXCEL_COL_NUM_APPUI,

    EXCEL_COL_HAUTEUR_HORS_SOL,

    EXCEL_COL_CONDUCTEUR,

    EXCEL_COL_LONGUEUR_FACTURER,

    EXCEL_COL_FO_TYPE_LIGNE,

    EXCEL_COL_BOITIER

)

from .pcm_parser import (

    parse_pcm_file,

    parse_repertoire_pcm,

    verifier_securite_etude,

    get_anomalies_securite,

    get_supports_portee_molle

)





class Comac:

    """ Second pour contenir quelques fonctions qui seront utilisées dans le fichier principale CheckOptyce.py """



    def __init__(self):

        """Le constructeur de ma classe

        Il prend pour attribut de classe les *** """



    @staticmethod

    def parse_references_cables_comac(valeur_col_ao: str) -> list:

        """

        Parse la colonne AO (idx 40) de l'Export COMAC.

        Les références câbles sont concaténées, chaque référence se termine par 'P-'.

        

        Exemples:

            "L1092-12-P-" → ["L1092-12-P"]

            "L1092-12-P-L1092-14-P-" → ["L1092-12-P", "L1092-14-P"]

            "L1092-12-P-L1092-12-P-L1092-11-P-" → ["L1092-12-P", "L1092-12-P", "L1092-11-P"]

        

        Args:

            valeur_col_ao: Valeur brute de la colonne AO

        

        Returns:

            Liste de références câbles nettoyées (ex: ["L1092-12-P", "L1092-14-P"])

        """

        if not valeur_col_ao or not isinstance(valeur_col_ao, str):

            return []

        

        valeur = str(valeur_col_ao).strip()

        if not valeur:

            return []

        

        # Chaque reference commence par L, suivie de chiffres-chiffres, optionnellement -P

        # Ex: L1092-13-P, L1092-15 (sans -P)

        # Gere les concatenations avec ou sans tiret: L1092-13-P-L1092-14-P ou L1092-13-PL1092-14-P

        return re.findall(r'L\d+-\d+(?:-P)?', valeur, re.IGNORECASE)



    @staticmethod

    def _normalize_insee_code(value):

        if value is None:

            return ''

        s = str(value).strip().replace(' ', '')

        if not s:

            return ''

        if s.endswith('.0'):

            s = s[:-2]

        if not s.isdigit() or len(s) > 5:

            return ''

        return s.zfill(5)



    @staticmethod

    def _build_support_key(nompot, code_insee):

        if nompot is None:

            return ''

        nom = str(nompot).strip()

        if not nom:

            return ''

        if '/' in nom or not code_insee:

            return nom

        return f"{nom}/{code_insee}"



    def extraire_donnees_comac(self, table_poteau, table_etude_comac, colonne_comac,
                               be_type='nge'):

        """Extraction complete COMAC en une seule passe.

        
        Args:
            be_type: 'nge' (POT-BT uniquement) ou 'axione' (POT-FT + POT-BT)

        Returns:

            tuple: (doublons, hors_etude, dico_qgis, dico_poteaux_prives)

        """
        # Axione: COMAC inclut FT + BT ; NGE: COMAC = BT uniquement
        pot_filter = 'POT-%' if be_type == 'axione' else 'POT-BT'

        return extraire_poteaux_etude(

            table_poteau, table_etude_comac, colonne_comac,

            pot_filter, 'COMAC', keep_commune=True

        )



    def LectureFichiersExcelsComac(self, repertoire, zone_climatique='ZVN'):

        """

        Fonction pour parcourir les fichiers Excel (.xlsx) COMAC

        et extraire les numéros de poteaux + données sécurité.

        

        Détecte automatiquement les fichiers Excel COMAC par:

        - Pattern nom: EXPORTCOMAC, Export_Comac, ou nom d'étude (NGE-*, PA-*)

        - Structure: colonne A avec numéros poteaux (format Exxxxxx ou BT-xxx)

        

        Args:

            repertoire: Chemin du répertoire contenant les fichiers Excel

            zone_climatique: 'ZVN' (vent normal) ou 'ZVF' (vent fort)

        

        Returns:

            tuple: (doublons, erreurs, dict_poteaux, dict_verif_secu, dict_cables_par_appui, dict_boitier_par_appui)

        """

        from qgis.core import QgsMessageLog, Qgis

        

        # Validation zone climatique

        if zone_climatique not in ('ZVN', 'ZVF'):

            zone_climatique = 'ZVN'

        dicoPoteauBt_SousTraitant = {}

        dicoVerifSecu = {}  # Résultats vérifications sécurité

        dicoCablesParAppui = {}  # {appui_norm → [refs_cables]} pour vérif CabCOMAC

        dicoEtudeParAppui = {}  # {appui_norm → etude_name} pour colonne NOM ETUDE

        dicoBoitierParAppui = {}  # {appui_norm → True} pour appuis avec boîtier=oui

        dicoCoordsPoteaux = {}  # {nompot → (x, y)} coordonnees Lambert 93 depuis Excel

        fichiersComacExistants = {}

        fichiersComacEnDoublons = []

        impossibiliteDelireFichier = {}

        

        # Patterns de noms de fichiers COMAC acceptés

        PATTERNS_COMAC = ['EXPORTCOMAC', 'EXPORT_COMAC', 'COMAC', 'NGE-', 'PA-']

        # Fichiers à exclure

        PATTERNS_EXCLUS = ['ANALYSE_', 'RAPPORT', 'SYNTHESE', 'RESUME', 'C6', 'C7', 'FICHEAPPUI',
                           '_DISTANCES', '_CONTROLES', '_LONGUEURS']

        

        fichiers_trouves = 0

        fichiers_valides = 0



        for subdir, _, files in os.walk(repertoire):

            for name in files:

                if name.endswith('.xlsx') and "~$" not in name:

                    name_upper = name.upper()

                    

                    # Exclure fichiers non-COMAC (par nom fichier OU nom dossier parent)

                    parent_folder = os.path.basename(subdir).upper()

                    if any(excl in name_upper for excl in PATTERNS_EXCLUS):

                        continue

                    if any(excl in parent_folder for excl in PATTERNS_EXCLUS):

                        continue

                    

                    # Vérifier si le nom correspond à un pattern COMAC

                    is_comac_pattern = any(pat in name_upper for pat in PATTERNS_COMAC)

                    

                    # Fallback: accepter tous les Excel dans dossiers d'études (NGE-*, PA-*)

                    is_in_etude_folder = any(pat in parent_folder for pat in ['NGE-', 'PA-', 'B1L-', 'B1I-'])

                    

                    if is_comac_pattern or is_in_etude_folder:

                        filepath = os.path.join(subdir, name)

                        try:

                            document = openpyxl.load_workbook(filepath, data_only=True)

                        except Exception as e:

                            impossibiliteDelireFichier[filepath] = str(e)

                            continue



                        try:

                            feuille_1 = document.worksheets[0]

                            code_insee_fichier = self._normalize_insee_code(

                                feuille_1.cell(row=1, column=9).value

                            )

                        except Exception as e:

                            impossibiliteDelireFichier[filepath] = f"Feuille illisible : {e}"

                            document.close()

                            continue



                        listePoteauBt = []

                        listeVerifSecu = []  # Vérifications par ligne

                        cables_appui_fichier = {}  # {appui → [refs]} pour ce fichier



                        # Lecture à partir de la ligne 4

                        # Col A (idx 0): N° poteau, Col G (idx 6): distance cable/BT

                        # Col AO (idx 40): Type ligne FO, Col AU (idx 46): Longueur à facturer

                        for row in feuille_1.iter_rows(min_row=4, min_col=1, max_col=50, values_only=True):

                            # Validation NULL stricte (pattern Maj_Ft_Bt.py)

                            if not row or len(row) == 0:

                                continue

                            

                            numPotBt = row[0]  # Col A

                            if not numPotBt or numPotBt == '' or str(numPotBt).strip() == '':

                                continue

                            
                            nompot_raw = str(numPotBt).strip()

                            # Filtrer les portees/distances (pas des numeros de poteaux)
                            # Ex: "Supports FT_X à E000Y/03158", "E000X_03158 à E000Y_03158"
                            if ' à ' in nompot_raw or ' a ' in nompot_raw.lower():
                                continue
                            if nompot_raw.lower().startswith('support'):
                                continue

                            nompot = nompot_raw.replace("BT ", "BT-")

                            nompot_cle = self._build_support_key(nompot, code_insee_fichier)

                            if not nompot_cle:

                                continue


                            listePoteauBt.append(nompot_cle)

                            # Extraction coordonnees XY (col K=10, L=11) Lambert 93
                            coord_x_raw = row[10] if len(row) > 10 and row[10] else None
                            coord_y_raw = row[11] if len(row) > 11 and row[11] else None
                            if coord_x_raw and coord_y_raw:
                                try:
                                    cx = float(str(coord_x_raw).replace(',', '.').replace(' ', ''))
                                    cy = float(str(coord_y_raw).replace(',', '.').replace(' ', ''))
                                    if cx > 100000 and cy > 6000000:
                                        dicoCoordsPoteaux[nompot_cle] = (cx, cy)
                                except (ValueError, TypeError):
                                    pass

                            
                            # Extraction données sécurité avec validation NULL explicite

                            hauteur_hors_sol_raw = row[EXCEL_COL_HAUTEUR_HORS_SOL] if len(row) > EXCEL_COL_HAUTEUR_HORS_SOL and row[EXCEL_COL_HAUTEUR_HORS_SOL] else None

                            conducteur_raw = row[EXCEL_COL_CONDUCTEUR] if len(row) > EXCEL_COL_CONDUCTEUR and row[EXCEL_COL_CONDUCTEUR] else None

                            type_ligne_fo = row[EXCEL_COL_FO_TYPE_LIGNE] if len(row) > EXCEL_COL_FO_TYPE_LIGNE and row[EXCEL_COL_FO_TYPE_LIGNE] else None

                            longueur_raw = row[EXCEL_COL_LONGUEUR_FACTURER] if len(row) > EXCEL_COL_LONGUEUR_FACTURER and row[EXCEL_COL_LONGUEUR_FACTURER] else None

                            

                            # Parse longueur (portée)

                            portee = 0.0

                            if longueur_raw:

                                try:

                                    portee = float(str(longueur_raw).replace(',', '.').replace('m', '').strip())

                                except (ValueError, AttributeError):

                                    portee = 0.0

                            

                            # Parse hauteur hors sol (distance câble/sol)

                            hauteur_sol = 0.0

                            if hauteur_hors_sol_raw:

                                try:

                                    hauteur_sol = float(str(hauteur_hors_sol_raw).replace(',', '.').replace('m', '').strip())

                                except (ValueError, AttributeError):

                                    hauteur_sol = 0.0

                            

                            # Parse boîtier fibre optique (col AR): oui/non ou 0/1 (format PCM)

                            boitier_raw = row[EXCEL_COL_BOITIER] if len(row) > EXCEL_COL_BOITIER else None

                            boitier_val = normaliser_boitier(boitier_raw)

                            if boitier_val in ('oui', 'non'):

                                nompot_norm_b = normalize_appui_num(nompot_cle, keep_commune=True)

                                if nompot_norm_b:

                                    dicoBoitierParAppui[nompot_norm_b] = boitier_val

                            

                            # Parse références câbles concaténées depuis col AO

                            refs_cables = self.parse_references_cables_comac(

                                str(type_ligne_fo) if type_ligne_fo else ''

                            )

                            if refs_cables:

                                nompot_norm = normalize_appui_num(nompot_cle, keep_commune=True)

                                if nompot_norm:

                                    cables_appui_fichier[nompot_norm] = refs_cables

                            

                            # Capacité FO depuis code câble (première ref pour compat existante)

                            capacite_fo = get_capacite_fo_from_code(type_ligne_fo) if type_ligne_fo else 0

                            

                            # Vérification portée

                            verif_portee = None

                            if portee > 0 and capacite_fo > 0:

                                verif_portee = verifier_portee(portee, capacite_fo, zone_climatique)

                            

                            # Vérification distance câble/sol (>= 4m)

                            verif_hauteur_sol = None

                            if hauteur_sol > 0:

                                verif_hauteur_sol = verifier_distance_sol(hauteur_sol)

                            

                            # Stockage résultat

                            listeVerifSecu.append({

                                'poteau': nompot_cle,

                                'portee': portee,

                                'capacite_fo': capacite_fo,

                                'type_ligne_fo': type_ligne_fo,

                                'hauteur_sol': hauteur_sol,

                                'conducteur': conducteur_raw,

                                'verif_portee': verif_portee,

                                'verif_hauteur_sol': verif_hauteur_sol

                            })



                        fichiers_trouves += 1

                        

                        if listePoteauBt:

                            # Utiliser chemin relatif comme clé pour éviter conflits de noms

                            rel_path = os.path.relpath(filepath, repertoire)

                            etude_name = os.path.basename(subdir)  # Nom du dossier parent = nom étude

                            key = etude_name if etude_name not in dicoPoteauBt_SousTraitant else rel_path

                            

                            dicoPoteauBt_SousTraitant[key] = listePoteauBt

                            dicoVerifSecu[key] = listeVerifSecu

                            # Agréger câbles par appui (tous fichiers confondus)

                            for appui, refs in cables_appui_fichier.items():

                                if appui not in dicoCablesParAppui:

                                    dicoCablesParAppui[appui] = refs

                                else:

                                    dicoCablesParAppui[appui].extend(refs)

                                if appui not in dicoEtudeParAppui:

                                    dicoEtudeParAppui[appui] = etude_name

                            fichiers_valides += 1



                            if name in fichiersComacExistants:

                                fichiersComacEnDoublons.append((name, fichiersComacExistants[name], rel_path))



                            fichiersComacExistants[name] = rel_path

        

        total_poteaux = sum(len(v) for v in dicoPoteauBt_SousTraitant.values())
        QgsMessageLog.logMessage(
            f"[COMAC] Lecture terminée: {fichiers_valides}/{fichiers_trouves} fichiers valides, "
            f"{total_poteaux} poteaux total, "
            f"{len(dicoCablesParAppui)} appuis avec cables, {len(dicoBoitierParAppui)} appuis avec boitier, "
            f"{len(dicoCoordsPoteaux)} poteaux avec coordonnees XY",
            "PoleAerien", MSG_INFO
        )

        return fichiersComacEnDoublons, impossibiliteDelireFichier, dicoPoteauBt_SousTraitant, dicoVerifSecu, dicoCablesParAppui, dicoBoitierParAppui, dicoCoordsPoteaux, dicoEtudeParAppui



    def LectureFichiersPCM(self, repertoire, zone_climatique='ZVN'):

        """

        Lecture des fichiers .pcm COMAC (XML) pour extraction complète.

        Remplace/complète la lecture Excel avec données plus fiables.

        

        Args:

            repertoire: Chemin du répertoire contenant les .pcm

            zone_climatique: 'ZVN' ou 'ZVF'

        

        Returns:

            tuple: (etudes_dict, erreurs_dict, anomalies_list, supports_pm_list)

        """

        etudes, erreurs = parse_repertoire_pcm(repertoire, zone_climatique)

        

        if erreurs:

            QgsMessageLog.logMessage(

                f"[COMAC_PCM] {len(erreurs)} erreur(s) lecture PCM",

                "PoleAerien", MSG_WARNING

            )

        

        anomalies = get_anomalies_securite(etudes)

        supports_pm = get_supports_portee_molle(etudes)

        

        # Extraction poteaux par étude (compatible avec workflow existant)

        dico_poteaux = {}

        for nom_etude, etude in etudes.items():

            poteaux = list(etude.supports.keys())

            if poteaux:

                dico_poteaux[nom_etude] = poteaux

        

        QgsMessageLog.logMessage(

            f"[COMAC_PCM] {len(etudes)} etudes, {len(anomalies)} anomalies, "

            f"{len(supports_pm)} portees molles, {len(dico_poteaux)} avec poteaux",

            "PoleAerien", MSG_INFO

        )

        return etudes, erreurs, anomalies, supports_pm, dico_poteaux



    def traitementResultatFinaux(self, dicoEtudeComacPoteauQgis, dicoPoteauBt_SousTraitant,
                                 all_inf_nums=None, coords_qgis=None, coords_excel=None,
                                 spatial_tolerance=7.5):

        """Traite les résultats finaux des deux dictionnaires.

        
        Args:
            all_inf_nums: Set de tous les inf_num normalises de infra_pt_pot.
                Permet de distinguer ABSENT (poteau inexistant) vs HORS_PERIMETRE
                (poteau existe mais hors zones etude_comac).
            coords_qgis: {inf_num: (x, y)} coordonnees QGIS (Lambert 93).
                Utilise pour le fallback spatial quand le matching par nom echoue.
            coords_excel: {pot_name: (x, y)} coordonnees Excel COMAC (Lambert 93).

        Returns:

            tuple: (introuvables_excel, introuvables_qgis, existants, hors_perimetre, spatial_matches)

        """
        from qgis.core import QgsMessageLog, Qgis

        total_qgis = sum(len(v) for v in dicoEtudeComacPoteauQgis.values())
        total_excel = sum(len(v) for v in dicoPoteauBt_SousTraitant.values())
        if all_inf_nums is None:
            all_inf_nums = set()

        # Index sans commune pour le check ABSENT vs HORS_PERIMETRE
        # Les cles Excel NGE n'ont pas /commune (ex: "1016436") mais
        # all_inf_nums a /commune (ex: "1016436/63041") quand keep_commune=True
        all_inf_sans_commune = {k.split('/')[0] for k in all_inf_nums if '/' in k}
        all_inf_sans_commune.update(k for k in all_inf_nums if '/' not in k)

        # Fallback: quand aucune zone etude n'existe (0 polygones etude_comac),
        # tous les poteaux sont "hors etude" et dico_qgis est vide.
        # Dans ce cas, utiliser TOUS les poteaux QGIS pour le matching.
        if not dicoEtudeComacPoteauQgis and coords_qgis:
            dicoEtudeComacPoteauQgis = {'_TOUS_POTEAUX_': list(coords_qgis.keys())}
            QgsMessageLog.logMessage(
                f"[COMAC] Aucune zone etude_comac: {len(coords_qgis)} poteaux QGIS "
                "utilises sans filtre geographique pour le matching",
                "PoleAerien", MSG_INFO
            )

        dicoPotBt_Excel_Introuvable = {}
        dicoPotBt_HorsPerimetre = {}

        dicoPotBt_AResoudre = {}

        dicoPotBt_NonResolu = {}

        dicoPotBtExistants = {}

        comptabilite = 0

        nb_match_exact = 0

        nb_match_sans_commune = 0

        ambiguous_samples = {}



        # Index rapide QGIS: clé normalisée -> liste (etude, inf_num_complet)

        index_qgis = {}

        index_qgis_sans_commune = {}

        for etude_comac, listePoteauxQgis in dicoEtudeComacPoteauQgis.items():

            for inf_num_bt in listePoteauxQgis:

                cle = normalize_appui_num(inf_num_bt, strip_e_prefix=True, keep_commune=True)

                index_qgis.setdefault(cle, []).append((etude_comac, inf_num_bt))

                cle_sans_commune = cle.split('/')[0] if '/' in cle else cle

                index_qgis_sans_commune.setdefault(cle_sans_commune, []).append((cle, etude_comac, inf_num_bt))

        study_keys_initial = set(index_qgis_sans_commune.keys())

        from collections import Counter
        sans_commune = Counter(k.split('/')[0] for k in index_qgis)
        collisions = {num: cnt for num, cnt in sans_commune.items() if cnt > 1}
        if collisions:
            QgsMessageLog.logMessage(
                f"[COMAC] COMMUNES DISTINCTES: {len(collisions)} numeros identiques dans communes differentes "
                f"(auraient collisionne sans keep_commune): {dict(list(collisions.items())[:10])}",
                "PoleAerien", MSG_WARNING
            )

        def _consume_match(cle_match, etude_comac, inf_num_bt, poteau_excel, fichier_excel):

            nonlocal comptabilite

            comptabilite += 1

            dicoPotBtExistants[comptabilite] = [inf_num_bt, etude_comac, poteau_excel, fichier_excel]

            if etude_comac in dicoEtudeComacPoteauQgis:

                new_list = [x for x in dicoEtudeComacPoteauQgis[etude_comac] if x != inf_num_bt]

                if new_list:

                    dicoEtudeComacPoteauQgis[etude_comac] = new_list

                else:

                    del dicoEtudeComacPoteauQgis[etude_comac]

            if cle_match in index_qgis:

                index_qgis[cle_match] = [

                    item for item in index_qgis[cle_match]

                    if not (item[0] == etude_comac and item[1] == inf_num_bt)

                ]

                if not index_qgis[cle_match]:

                    del index_qgis[cle_match]

            cle_sans_commune = cle_match.split('/')[0] if '/' in cle_match else cle_match

            if cle_sans_commune in index_qgis_sans_commune:

                index_qgis_sans_commune[cle_sans_commune] = [

                    item for item in index_qgis_sans_commune[cle_sans_commune]

                    if not (item[0] == cle_match and item[1] == etude_comac and item[2] == inf_num_bt)

                ]

                if not index_qgis_sans_commune[cle_sans_commune]:

                    del index_qgis_sans_commune[cle_sans_commune]



        # Traitement Excel

        for excel, listePoteau in dicoPoteauBt_SousTraitant.items():

            PotBtintrouvableSt = []
            PotBtHorsPerimetreSt = []
            PotBtAResoudreSt = []
            PotBtNonResoluSt = []

            

            for poteauSt in listePoteau:

                cle_excel = normalize_appui_num(poteauSt, strip_e_prefix=True, keep_commune=True)

                

                if cle_excel in index_qgis and index_qgis[cle_excel]:

                    etude_comac, inf_num_bt = index_qgis[cle_excel][0]
                    _consume_match(cle_excel, etude_comac, inf_num_bt, poteauSt, excel)
                    nb_match_exact += 1

                else:
                    cle_sans_commune = cle_excel.split('/')[0] if '/' in cle_excel else cle_excel
                    candidats_sans_commune = index_qgis_sans_commune.get(cle_sans_commune, []) if cle_sans_commune else []

                    if len(candidats_sans_commune) == 1:
                        cle_match, etude_comac, inf_num_bt = candidats_sans_commune[0]
                        _consume_match(cle_match, etude_comac, inf_num_bt, poteauSt, excel)
                        nb_match_sans_commune += 1
                    elif len(candidats_sans_commune) > 1:
                        PotBtAResoudreSt.append(poteauSt)
                        if len(ambiguous_samples) < 10 and poteauSt not in ambiguous_samples:
                            ambiguous_samples[poteauSt] = [cand[2] for cand in candidats_sans_commune[:4]]
                    elif cle_sans_commune in study_keys_initial:
                        PotBtNonResoluSt.append(poteauSt)
                    elif cle_excel and (cle_excel in all_inf_nums or cle_sans_commune in all_inf_sans_commune):
                        PotBtHorsPerimetreSt.append(poteauSt)
                    else:
                        PotBtintrouvableSt.append(poteauSt)



            if PotBtintrouvableSt:

                dicoPotBt_Excel_Introuvable[excel] = PotBtintrouvableSt

            if PotBtHorsPerimetreSt:

                dicoPotBt_HorsPerimetre[excel] = PotBtHorsPerimetreSt
            if PotBtAResoudreSt:

                dicoPotBt_AResoudre[excel] = PotBtAResoudreSt
            if PotBtNonResoluSt:

                dicoPotBt_NonResolu[excel] = PotBtNonResoluSt

        if ambiguous_samples:
            QgsMessageLog.logMessage(
                f"[COMAC] Candidats ambigus (sample): {ambiguous_samples}",
                "PoleAerien", MSG_WARNING
            )


        # --- Spatial fallback: match unmatched poles by proximity (1.5m) ---
        dicoPotBt_SpatialMatch = {}

        if coords_qgis and coords_excel and dicoPotBt_AResoudre:
            from .core_utils import match_poles_spatial

            unmatched_qgis_coords = {}
            for etude, pots in dicoEtudeComacPoteauQgis.items():
                for inf_num in pots:
                    coord = coords_qgis.get(inf_num)
                    if coord:
                        unmatched_qgis_coords[inf_num] = coord

            unmatched_excel_coords = {}
            for fichier, pots in dicoPotBt_AResoudre.items():
                for pot_name in pots:
                    coord = coords_excel.get(pot_name)
                    if coord:
                        unmatched_excel_coords[pot_name] = coord


            spatial_matches = []
            if unmatched_qgis_coords and unmatched_excel_coords:
                spatial_matches = match_poles_spatial(
                    unmatched_qgis_coords, unmatched_excel_coords,
                    tolerance=spatial_tolerance
                )

            if spatial_matches:
                excel_matched = {}
                qgis_matched = {}
                for qgis_name, excel_name, dist in spatial_matches:
                    excel_matched[excel_name] = (qgis_name, dist)
                    qgis_matched[qgis_name] = (excel_name, dist)

                for fichier in list(dicoPotBt_AResoudre.keys()):
                    remaining = []
                    for pot_name in dicoPotBt_AResoudre[fichier]:
                        if pot_name in excel_matched:
                            qgis_name, dist = excel_matched[pot_name]
                            comptabilite += 1
                            dicoPotBt_SpatialMatch[comptabilite] = {
                                'inf_num_qgis': qgis_name,
                                'inf_num_excel': pot_name,
                                'fichier': fichier,
                                'distance_m': dist,
                            }
                        else:
                            remaining.append(pot_name)
                    if remaining:
                        dicoPotBt_AResoudre[fichier] = remaining
                    else:
                        del dicoPotBt_AResoudre[fichier]

                for etude in list(dicoEtudeComacPoteauQgis.keys()):
                    remaining = [p for p in dicoEtudeComacPoteauQgis[etude]
                                 if p not in qgis_matched]
                    if remaining:
                        dicoEtudeComacPoteauQgis[etude] = remaining
                    else:
                        del dicoEtudeComacPoteauQgis[etude]


        for fichier, appuis in dicoPotBt_AResoudre.items():
            if appuis:
                dicoPotBt_NonResolu[fichier] = appuis

        # Resume comparaison
        nb_match = len(dicoPotBtExistants)
        nb_spatial = len(dicoPotBt_SpatialMatch)
        nb_absent = sum(len(v) for v in dicoPotBt_Excel_Introuvable.values())
        nb_hors_p = sum(len(v) for v in dicoPotBt_HorsPerimetre.values())
        nb_non_resolu = sum(len(v) for v in dicoPotBt_NonResolu.values())
        nb_restant_qgis = sum(len(v) for v in dicoEtudeComacPoteauQgis.values())
        QgsMessageLog.logMessage(
            f"[COMAC] Comparaison: {nb_match} correspondances par nom, "
            f"{nb_spatial} correspondances spatiales (<{spatial_tolerance}m), "
            f"{nb_non_resolu} non resolus (commune/code ambigu), "
            f"{nb_hors_p} hors perimetre etude, "
            f"{nb_absent} absents couche SRO, "
            f"{nb_restant_qgis} restants QGIS non trouves dans Excel",
            "PoleAerien", MSG_INFO
        )
        if dicoPotBt_NonResolu:
            sample_nr = []
            for fichier, pots in list(dicoPotBt_NonResolu.items())[:2]:
                sample_nr.append(f"{fichier}: {pots[:5]}")
            QgsMessageLog.logMessage(
                f"[COMAC] Non resolus commune/code: {sample_nr}",
                "PoleAerien", MSG_WARNING
            )
        if dicoPotBt_Excel_Introuvable:
            sample_abs = []
            for fichier, pots in list(dicoPotBt_Excel_Introuvable.items())[:2]:
                sample_abs.append(f"{fichier}: {pots[:5]}")
            QgsMessageLog.logMessage(
                f"[COMAC] Absents couche SRO (inexistants dans infra_pt_pot filtre SRO): {sample_abs}",
                "PoleAerien", MSG_WARNING
            )

        return dicoPotBt_Excel_Introuvable, dicoEtudeComacPoteauQgis, dicoPotBtExistants, dicoPotBt_HorsPerimetre, dicoPotBt_SpatialMatch, dicoPotBt_NonResolu



    def comparer_comac_cables(

        self,

        dico_cables_comac: dict,

        cables_par_appui: dict,

        ref_label: str = 'BDD'

    ) -> list:

        """Compare cables COMAC vs BDD/GraceTHD. Delegue a cable_analyzer.comparer_source_cables()."""

        from .cable_analyzer import comparer_source_cables

        return comparer_source_cables(

            dico_cables_comac, cables_par_appui,

            source_label="COMAC",

            get_capacites_fn=get_capacites_possibles,

            ref_label=ref_label,

            dedup_refs=True

        )



    def ecrireResultatsAnalyseExcels(self, resultatsFinaux, nom, dico_verif_secu=None, verif_cables=None, verif_boitiers=None):

        """Fonction qui permet d'écrire dans le fichier Excel à partir d'un fichier modèle.

        

        Args:

            resultatsFinaux: tuple (introuvables_excel, introuvables_qgis, existants)

            nom: Chemin du fichier Excel à créer

            dico_verif_secu: Dictionnaire des vérifications sécurité par fichier

            verif_cables: Liste de dicts comparaison câbles COMAC vs BDD (feuille VERIF_CABLES)

            verif_boitiers: Dict[num_appui, {boitier_source, bpe_trouve, bpe_noe_type, statut}]

        """



        dicoPotBt_Excel_Introuvable = resultatsFinaux[0]

        dicoEtudeComacPotQgisIntrouvable = resultatsFinaux[1]

        dicoPotBtExistants = resultatsFinaux[2]

        dicoPotBt_HorsPerimetre = resultatsFinaux[3] if len(resultatsFinaux) > 3 else {}
        dicoPotBt_SpatialMatch = resultatsFinaux[4] if len(resultatsFinaux) > 4 else {}

        dicoPotBt_NonResolu = resultatsFinaux[5] if len(resultatsFinaux) > 5 else {}



        fichierXlsx = openpyxl.workbook.Workbook()



        ############################## FEUILLE 1 : VALIDATION_COMPLET ################################################



        my_ligne = 1  # Ligne à partir de laquelle on commencera à écrire dans le fichier Annexe

        # Parcourir fichier C3A, pour comparer ses contenus aux fichiers C6

        feuille = fichierXlsx.create_sheet("ANALYSE COMAC", 0)



        alignement = openpyxl.styles.Alignment(horizontal='center', vertical='bottom', text_rotation=0, wrap_text=False,

                                               shrink_to_fit=True, indent=0)



        # Pour mettre du GRAS à partir de la colonne 18 et 23 pour les câbles.

        feuille.cell(row=1, column=1, value="INF_NUM QGIS").font = openpyxl.styles.Font(bold=True)

        feuille.cell(row=1, column=2, value="ETUDE QGIS").font = openpyxl.styles.Font(bold=True)

        feuille.cell(row=1, column=3, value="INF_NUM EXCEL").font = openpyxl.styles.Font(bold=True)

        feuille.cell(row=1, column=4, value="NOM FICHIER EXCEL").font = openpyxl.styles.Font(bold=True)

        feuille.cell(row=1, column=5, value="REMARQUES").font = openpyxl.styles.Font(bold=True)



        feuille.cell(row=1, column=1).alignment = alignement

        feuille.cell(row=1, column=2).alignment = alignement

        feuille.cell(row=1, column=3).alignment = alignement

        feuille.cell(row=1, column=4).alignment = alignement

        feuille.cell(row=1, column=5).alignment = alignement



        feuille.column_dimensions["A"].width = 25

        feuille.column_dimensions["B"].width = 30

        feuille.column_dimensions["C"].width = 25

        feuille.column_dimensions["D"].width = 50

        feuille.column_dimensions["E"].width = 40



        red_color = openpyxl.styles.colors.Color(rgb='fc4e2a')

        orange_color = openpyxl.styles.colors.Color(rgb='fd8d3c')



        ####################### SOUS-TRAITS INTROUVABLE #######################################")

        for fichierExcels, listesDesAppuisSous_traitant in dicoPotBt_Excel_Introuvable.items():

            for inf_num_excel in listesDesAppuisSous_traitant:

                # Si tiragle câble complètement validé

                my_ligne += 1



                feuille.cell(row=my_ligne, column=3, value=inf_num_excel).fill = openpyxl.styles.PatternFill(fill_type='solid', fgColor=red_color)

                feuille.cell(row=my_ligne, column=4, value=fichierExcels).fill = openpyxl.styles.PatternFill(fill_type='solid', fgColor=red_color)

                feuille.cell(row=my_ligne, column=5, value="ABSENT - inexistant dans couche SRO").fill = openpyxl.styles.PatternFill(fill_type='solid', fgColor=red_color)


        ####################### HORS PERIMETRE ETUDE ####################################

        blue_color = openpyxl.styles.colors.Color(rgb='6baed6')

        amber_color = openpyxl.styles.colors.Color(rgb='ffd966')

        for fichierExcels, listesDesAppuis in dicoPotBt_HorsPerimetre.items():

            for inf_num_excel in listesDesAppuis:

                my_ligne += 1

                feuille.cell(row=my_ligne, column=3, value=inf_num_excel).fill = openpyxl.styles.PatternFill(fill_type='solid', fgColor=blue_color)

                feuille.cell(row=my_ligne, column=4, value=fichierExcels).fill = openpyxl.styles.PatternFill(fill_type='solid', fgColor=blue_color)

                feuille.cell(row=my_ligne, column=5, value="HORS PERIMETRE - existe dans la zone SRO mais hors zone etude COMAC").fill = openpyxl.styles.PatternFill(fill_type='solid', fgColor=blue_color)


        for fichierExcels, listesDesAppuis in dicoPotBt_NonResolu.items():

            for inf_num_excel in listesDesAppuis:

                my_ligne += 1

                feuille.cell(row=my_ligne, column=3, value=inf_num_excel).fill = openpyxl.styles.PatternFill(fill_type='solid', fgColor=amber_color)

                feuille.cell(row=my_ligne, column=4, value=fichierExcels).fill = openpyxl.styles.PatternFill(fill_type='solid', fgColor=amber_color)

                feuille.cell(row=my_ligne, column=5, value="NON RESOLU - code present mais ambigu entre plusieurs communes ou non leve par le spatial").fill = openpyxl.styles.PatternFill(fill_type='solid', fgColor=amber_color)


        ####################### QGIS INTROUVABLE #######################################

        for etudeQgis, listesDesAppuisQGis in dicoEtudeComacPotQgisIntrouvable.items():

            for inf_num in listesDesAppuisQGis:

                # Si tiragle câble complètement validé

                my_ligne += 1



                feuille.cell(row=my_ligne, column=1, value=inf_num).fill = openpyxl.styles.PatternFill(fill_type='solid', fgColor=orange_color)

                feuille.cell(row=my_ligne, column=2, value=etudeQgis).fill = openpyxl.styles.PatternFill(fill_type='solid', fgColor=orange_color)

                feuille.cell(row=my_ligne, column=5, value="infra inexistant dans les Excels").fill = openpyxl.styles.PatternFill(fill_type='solid', fgColor=orange_color)


        ####################### CORRESPONDANCES SPATIALES (<1.5m) ######################

        if dicoPotBt_SpatialMatch:
            green_spatial = openpyxl.styles.colors.Color(rgb='a8d5ba')
            for _, match_info in dicoPotBt_SpatialMatch.items():
                my_ligne += 1
                dist = match_info['distance_m']
                feuille.cell(row=my_ligne, column=1, value=match_info['inf_num_qgis']).fill = openpyxl.styles.PatternFill(fill_type='solid', fgColor=green_spatial)
                feuille.cell(row=my_ligne, column=3, value=match_info['inf_num_excel']).fill = openpyxl.styles.PatternFill(fill_type='solid', fgColor=green_spatial)
                feuille.cell(row=my_ligne, column=4, value=match_info['fichier']).fill = openpyxl.styles.PatternFill(fill_type='solid', fgColor=green_spatial)
                feuille.cell(row=my_ligne, column=5, value=f"correspondance spatiale ({dist}m)").fill = openpyxl.styles.PatternFill(fill_type='solid', fgColor=green_spatial)

        my_colonne = 1

        my_ligne += 1



        ####################### POTEAUX TROUVEES #######################################")

        for _, listesDesAppuisBT in dicoPotBtExistants.items():

            for valeur in listesDesAppuisBT:

                # Si tirage câble complètement validé

                feuille.cell(row=my_ligne, column=my_colonne, value=valeur)

                my_colonne += 1



            feuille.cell(row=my_ligne, column=5, value="correspondance trouvée")



            # On passe à la ligne suivante

            # Si la première liste est terminé, on passe à la ligne suivante en récommençant par au toujours au niveau de la colonne A

            my_ligne += 1

            my_colonne = 1



        ############################## FEUILLE 2 : VERIF_SECURITE ################################################

        if dico_verif_secu:

            feuille_secu = fichierXlsx.create_sheet("VERIF_SECURITE", 1)

            

            # En-têtes

            headers_secu = ["FICHIER", "POTEAU", "PORTEE (m)", "CAPACITE FO", "TYPE LIGNE", 

                           "PORTEE MAX", "DEPASSEMENT", "HAUTEUR SOL (m)", "VERIF_PORTEE", "VERIF_HAUTEUR"]

            for col_idx, header in enumerate(headers_secu, 1):

                feuille_secu.cell(row=1, column=col_idx, value=header).font = openpyxl.styles.Font(bold=True)

                feuille_secu.cell(row=1, column=col_idx).alignment = alignement

            

            # Largeurs colonnes

            feuille_secu.column_dimensions["A"].width = 40

            feuille_secu.column_dimensions["B"].width = 20

            feuille_secu.column_dimensions["C"].width = 12

            feuille_secu.column_dimensions["D"].width = 12

            feuille_secu.column_dimensions["E"].width = 15

            feuille_secu.column_dimensions["F"].width = 12

            feuille_secu.column_dimensions["G"].width = 12

            feuille_secu.column_dimensions["H"].width = 15

            feuille_secu.column_dimensions["I"].width = 30

            feuille_secu.column_dimensions["J"].width = 30

            

            green_color = openpyxl.styles.colors.Color(rgb='90EE90')

            ligne_secu = 2

            

            for fichier, liste_verif in dico_verif_secu.items():

                for verif in liste_verif:

                    portee = verif.get('portee', 0)

                    capacite = verif.get('capacite_fo', 0)

                    hauteur_sol = verif.get('hauteur_sol', 0)

                    

                    # Ignorer lignes sans données

                    if portee == 0 and capacite == 0 and hauteur_sol == 0:

                        continue

                    

                    # Vérification portée

                    verif_portee = verif.get('verif_portee')

                    portee_max = verif_portee.get('portee_max', 0) if verif_portee else 0

                    depassement = verif_portee.get('depassement', 0) if verif_portee else 0

                    portee_valide = verif_portee.get('valide', True) if verif_portee else True

                    

                    # Vérification hauteur sol (>= 4m)

                    verif_haut = verif.get('verif_hauteur_sol')

                    hauteur_valide = verif_haut.get('valide', True) if verif_haut else True

                    

                    # Statuts vérification

                    statut_portee = "OK" if portee_valide else f"PORTEE MOLLE (+{depassement:.1f}m)"

                    statut_hauteur = ""

                    if hauteur_sol > 0:

                        statut_hauteur = "OK" if hauteur_valide else f"HAUTEUR < 4m ({hauteur_sol:.1f}m)"

                    

                    feuille_secu.cell(row=ligne_secu, column=1, value=fichier)

                    feuille_secu.cell(row=ligne_secu, column=2, value=verif.get('poteau', ''))

                    feuille_secu.cell(row=ligne_secu, column=3, value=portee)

                    feuille_secu.cell(row=ligne_secu, column=4, value=capacite)

                    feuille_secu.cell(row=ligne_secu, column=5, value=verif.get('type_ligne_fo', ''))

                    feuille_secu.cell(row=ligne_secu, column=6, value=portee_max)

                    feuille_secu.cell(row=ligne_secu, column=7, value=depassement)

                    feuille_secu.cell(row=ligne_secu, column=8, value=hauteur_sol if hauteur_sol > 0 else '')

                    feuille_secu.cell(row=ligne_secu, column=9, value=statut_portee if portee > 0 else '')

                    feuille_secu.cell(row=ligne_secu, column=10, value=statut_hauteur)

                    

                    # Coloration cellule portée

                    if portee > 0:

                        color_portee = green_color if portee_valide else red_color

                        feuille_secu.cell(row=ligne_secu, column=9).fill = openpyxl.styles.PatternFill(

                            fill_type='solid', fgColor=color_portee)

                    

                    # Coloration cellule hauteur

                    if hauteur_sol > 0:

                        color_hauteur = green_color if hauteur_valide else red_color

                        feuille_secu.cell(row=ligne_secu, column=10).fill = openpyxl.styles.PatternFill(

                            fill_type='solid', fgColor=color_hauteur)

                    

                    ligne_secu += 1



        ############################## FEUILLE 3 : VERIF_CABLES ################################################

        if verif_cables:

            sheet_idx = len(fichierXlsx.sheetnames)

            feuille_cables = fichierXlsx.create_sheet("VERIF_CABLES", sheet_idx)

            

            green_color = openpyxl.styles.colors.Color(rgb='90EE90')

            

            has_boitier = any(e.get('boitier_comac') for e in verif_cables)

            headers_cables = [

                "APPUI", "NOM ETUDE", "NB CABLES COMAC", "REFS COMAC", "CAPAS COMAC",

                "NB CABLES BDD", "CAPAS BDD", "STATUT", "MESSAGE"

            ]

            if has_boitier:

                headers_cables.extend(["BOITIER COMAC", "BPE TYPE", "BOITIER STATUT"])

            for col_idx, header in enumerate(headers_cables, 1):

                feuille_cables.cell(row=1, column=col_idx, value=header).font = openpyxl.styles.Font(bold=True)

                feuille_cables.cell(row=1, column=col_idx).alignment = alignement

            

            feuille_cables.column_dimensions["A"].width = 20

            feuille_cables.column_dimensions["B"].width = 30

            feuille_cables.column_dimensions["C"].width = 18

            feuille_cables.column_dimensions["D"].width = 40

            feuille_cables.column_dimensions["E"].width = 25

            feuille_cables.column_dimensions["F"].width = 18

            feuille_cables.column_dimensions["G"].width = 25

            feuille_cables.column_dimensions["H"].width = 15

            feuille_cables.column_dimensions["I"].width = 60

            if has_boitier:

                feuille_cables.column_dimensions["J"].width = 18

                feuille_cables.column_dimensions["K"].width = 18

                feuille_cables.column_dimensions["L"].width = 18

            

            ligne_cab = 2

            for entry in verif_cables:

                statut = entry.get('statut', '')

                

                feuille_cables.cell(row=ligne_cab, column=1, value=entry.get('num_appui', ''))

                feuille_cables.cell(row=ligne_cab, column=2, value=entry.get('nom_etude', ''))

                feuille_cables.cell(row=ligne_cab, column=3, value=entry.get('nb_cables_comac', 0))

                feuille_cables.cell(row=ligne_cab, column=4, value=entry.get('cables_comac', ''))

                feuille_cables.cell(row=ligne_cab, column=5, value='+'.join(entry.get('capas_comac', [])))

                feuille_cables.cell(row=ligne_cab, column=6, value=entry.get('nb_cables_bdd', 0))

                feuille_cables.cell(row=ligne_cab, column=7, value='+'.join(str(c) for c in entry.get('capas_bdd', [])))

                feuille_cables.cell(row=ligne_cab, column=8, value=statut)

                feuille_cables.cell(row=ligne_cab, column=9, value=entry.get('message', ''))

                

                # Coloration selon statut câbles

                if statut == 'OK':

                    color = green_color

                elif statut == 'ECART':

                    color = red_color

                elif statut == 'ECART_PEP':

                    color = orange_color

                elif statut.startswith('ABSENT'):

                    color = orange_color

                else:

                    color = None

                

                if color:

                    feuille_cables.cell(row=ligne_cab, column=8).fill = openpyxl.styles.PatternFill(

                        fill_type='solid', fgColor=color)

                

                # Colonnes boîtier

                if has_boitier:

                    boitier_val = entry.get('boitier_comac', '')

                    bpe_type = entry.get('bpe_noe_type', '')

                    boitier_statut = entry.get('boitier_statut', '')

                    feuille_cables.cell(row=ligne_cab, column=10, value=boitier_val)

                    feuille_cables.cell(row=ligne_cab, column=11, value=bpe_type)

                    feuille_cables.cell(row=ligne_cab, column=12, value=boitier_statut)

                    

                    if boitier_statut == 'ERREUR':

                        boitier_err_color = openpyxl.styles.colors.Color(rgb='FF6B6B')

                        for col_b in range(10, 13):

                            feuille_cables.cell(row=ligne_cab, column=col_b).fill = openpyxl.styles.PatternFill(

                                fill_type='solid', fgColor=boitier_err_color)

                    elif boitier_statut == 'OK':

                        for col_b in range(10, 13):

                            feuille_cables.cell(row=ligne_cab, column=col_b).fill = openpyxl.styles.PatternFill(

                                fill_type='solid', fgColor=green_color)

                

                ligne_cab += 1



        if 'Sheet' in fichierXlsx.sheetnames:

            del fichierXlsx['Sheet']



        try:

            fichierXlsx.save(filename=nom)

        except (PermissionError, OSError) as e:

            from qgis.core import QgsMessageLog, Qgis

            QgsMessageLog.logMessage(

                f"[COMAC] Impossible d'ecrire le rapport: {nom} - {e}",

                "PoleAerien", MSG_CRITICAL

            )

            raise



    def ecrireResultatsPCM(self, etudes, anomalies, supports_pm, nom_fichier):

        """

        Écrit les résultats d'analyse des fichiers .pcm dans un Excel.

        

        Args:

            etudes: Dict des études PCM parsées

            anomalies: Liste des anomalies de sécurité

            supports_pm: Liste des supports en portée molle

            nom_fichier: Chemin du fichier Excel à créer

        """

        if not etudes:

            etudes = {}

        if anomalies is None:

            anomalies = []

        if supports_pm is None:

            supports_pm = []

        fichierXlsx = openpyxl.workbook.Workbook()

        

        alignement = openpyxl.styles.Alignment(horizontal='center', vertical='bottom', 

                                                shrink_to_fit=True)

        red_color = openpyxl.styles.colors.Color(rgb='fc4e2a')

        orange_color = openpyxl.styles.colors.Color(rgb='fd8d3c')

        green_color = openpyxl.styles.colors.Color(rgb='90EE90')

        

        # === FEUILLE 1: RESUME ETUDES ===

        feuille_resume = fichierXlsx.create_sheet("RESUME_ETUDES", 0)

        headers_resume = ["ETUDE", "COMMUNE", "NB_SUPPORTS", "NB_LIGNES_FO", 

                         "NB_ANOMALIES", "HYPOTHESES"]

        for col, h in enumerate(headers_resume, 1):

            feuille_resume.cell(row=1, column=col, value=h).font = openpyxl.styles.Font(bold=True)

        

        ligne = 2

        for nom_etude, etude in etudes.items():

            nb_anomalies = len([a for a in anomalies if a.get('etude') == nom_etude])

            nb_lignes_fo = len([l for l in etude.lignes_tcf if l.capacite_fo > 0])

            

            feuille_resume.cell(row=ligne, column=1, value=nom_etude)

            feuille_resume.cell(row=ligne, column=2, value=etude.commune)

            feuille_resume.cell(row=ligne, column=3, value=len(etude.supports))

            feuille_resume.cell(row=ligne, column=4, value=nb_lignes_fo)

            feuille_resume.cell(row=ligne, column=5, value=nb_anomalies)

            feuille_resume.cell(row=ligne, column=6, value=', '.join(etude.hypotheses or []))

            

            if nb_anomalies > 0:

                feuille_resume.cell(row=ligne, column=5).fill = openpyxl.styles.PatternFill(

                    fill_type='solid', fgColor=red_color)

            ligne += 1

        

        # Largeurs colonnes

        feuille_resume.column_dimensions["A"].width = 30

        feuille_resume.column_dimensions["B"].width = 20

        feuille_resume.column_dimensions["F"].width = 15

        

        # === FEUILLE 2: ANOMALIES SECURITE ===

        feuille_anom = fichierXlsx.create_sheet("ANOMALIES_SECURITE", 1)

        headers_anom = ["ETUDE", "SUPPORT DEPART", "SUPPORT ARRIVEE", "CABLE", 

                       "CAPACITE FO", "PORTEE (m)", "PORTEE MAX (m)", "DEPASSEMENT (m)",

                       "HAUTEUR TRAVERSE (m)", "PORTEE KO", "HAUTEUR KO", "A POSER"]

        for col, h in enumerate(headers_anom, 1):

            feuille_anom.cell(row=1, column=col, value=h).font = openpyxl.styles.Font(bold=True)

        

        ligne = 2

        for anom in anomalies:

            feuille_anom.cell(row=ligne, column=1, value=anom.get('etude', ''))

            feuille_anom.cell(row=ligne, column=2, value=anom.get('support_depart', ''))

            feuille_anom.cell(row=ligne, column=3, value=anom.get('support_arrivee', ''))

            feuille_anom.cell(row=ligne, column=4, value=anom.get('cable', ''))

            feuille_anom.cell(row=ligne, column=5, value=anom.get('capacite_fo', 0))

            feuille_anom.cell(row=ligne, column=6, value=anom.get('portee', 0))

            feuille_anom.cell(row=ligne, column=7, value=anom.get('portee_max', 0))

            feuille_anom.cell(row=ligne, column=8, value=anom.get('depassement', 0))

            feuille_anom.cell(row=ligne, column=9, value=anom.get('hauteur_traverse', 0))

            

            portee_ko = anom.get('portee_ko', False)

            hauteur_ko = anom.get('hauteur_ko', False)

            feuille_anom.cell(row=ligne, column=10, value="OUI" if portee_ko else "")

            feuille_anom.cell(row=ligne, column=11, value="OUI" if hauteur_ko else "")

            feuille_anom.cell(row=ligne, column=12, value="OUI" if anom.get('a_poser') else "")

            

            if portee_ko:

                feuille_anom.cell(row=ligne, column=10).fill = openpyxl.styles.PatternFill(

                    fill_type='solid', fgColor=red_color)

            if hauteur_ko:

                feuille_anom.cell(row=ligne, column=11).fill = openpyxl.styles.PatternFill(

                    fill_type='solid', fgColor=orange_color)

            ligne += 1

        

        # Largeurs colonnes

        for col in ["A", "B", "C", "D"]:

            feuille_anom.column_dimensions[col].width = 20

        

        # === FEUILLE 3: SUPPORTS PORTEE MOLLE ===

        feuille_pm = fichierXlsx.create_sheet("PORTEES_MOLLES", 2)

        headers_pm = ["ETUDE", "SUPPORT", "NATURE", "HAUTEUR (m)", "CLASSE", "ETAT"]

        for col, h in enumerate(headers_pm, 1):

            feuille_pm.cell(row=1, column=col, value=h).font = openpyxl.styles.Font(bold=True)

        

        ligne = 2

        for pm in supports_pm:

            feuille_pm.cell(row=ligne, column=1, value=pm.get('etude', ''))

            feuille_pm.cell(row=ligne, column=2, value=pm.get('support', ''))

            feuille_pm.cell(row=ligne, column=3, value=pm.get('nature', ''))

            feuille_pm.cell(row=ligne, column=4, value=pm.get('hauteur', 0))

            feuille_pm.cell(row=ligne, column=5, value=pm.get('classe', ''))

            feuille_pm.cell(row=ligne, column=6, value=pm.get('etat', ''))

            

            feuille_pm.cell(row=ligne, column=2).fill = openpyxl.styles.PatternFill(

                fill_type='solid', fgColor=orange_color)

            ligne += 1

        

        feuille_pm.column_dimensions["A"].width = 30

        feuille_pm.column_dimensions["B"].width = 15

        

        # === FEUILLE 4: DETAIL LIGNES FO ===

        feuille_fo = fichierXlsx.create_sheet("LIGNES_FO", 3)

        headers_fo = ["ETUDE", "CABLE", "CAPACITE FO", "A POSER", "NB PORTEES", 

                     "PORTEE MAX", "SUPPORTS"]

        for col, h in enumerate(headers_fo, 1):

            feuille_fo.cell(row=1, column=col, value=h).font = openpyxl.styles.Font(bold=True)

        

        ligne = 2

        for nom_etude, etude in etudes.items():

            for l in etude.lignes_tcf:

                if not l.capacite_fo:

                    continue

                feuille_fo.cell(row=ligne, column=1, value=nom_etude)

                feuille_fo.cell(row=ligne, column=2, value=l.cable)

                feuille_fo.cell(row=ligne, column=3, value=l.capacite_fo)

                feuille_fo.cell(row=ligne, column=4, value="OUI" if l.a_poser else "")

                feuille_fo.cell(row=ligne, column=5, value=len(l.portees))

                feuille_fo.cell(row=ligne, column=6, value=max(l.portees) if l.portees else 0)

                feuille_fo.cell(row=ligne, column=7, value=' → '.join(l.supports))

                ligne += 1

        

        feuille_fo.column_dimensions["A"].width = 30

        feuille_fo.column_dimensions["G"].width = 60

        

        if 'Sheet' in fichierXlsx.sheetnames:

            del fichierXlsx['Sheet']

        

        try:

            fichierXlsx.save(filename=nom_fichier)

        except (PermissionError, OSError) as e:

            from qgis.core import QgsMessageLog, Qgis

            QgsMessageLog.logMessage(

                f"[COMAC_PCM] Impossible d'ecrire le rapport: {nom_fichier} - {e}",

                "PoleAerien", MSG_CRITICAL

            )

            raise



        return len(anomalies), len(supports_pm)

