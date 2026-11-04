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

import os
import openpyxl
from .qgis_utils import extraire_poteaux_etude
from .core_utils import normalize_appui_num


class CapFt:
    """ Second pour contenir quelques fonctions qui seront utilisées dans le fichier principale CheckOptyce.py """

    def __init__(self):
        """Le constructeur de ma classe
        Il prend pour attribut de classe les *** """

    def extraire_donnees_capft(self, table_poteau, table_etude_cap_ft, colonne_cap_ft):
        """Extraction complete CAP_FT en une seule passe.
        
        Returns:
            tuple: (doublons, hors_etude, dico_qgis, dico_poteaux_prives)
        """
        return extraire_poteaux_etude(
            table_poteau, table_etude_cap_ft, colonne_cap_ft,
            'POT-FT', 'CAP_FT'
        )

    def LectureFichiersExcelsCap_ft(self, repertoire):
        """Fonction pour parcourir les fichiers Excel pour renseigner la référence des appuis."""
        dicoPoteauFt_SousTraitant = {}

        for subdir, _, files in os.walk(repertoire):
            for name in files:
                if "FicheAppui_" in name and name.endswith('.xlsx'):
                    cheminComplet = os.path.join(subdir, name)

                    # Récupère le dossier qui contient directement le fichier
                    dossier_parent = os.path.basename(os.path.dirname(cheminComplet))

                    # Ajoute le fichier au dictionnaire
                    if dossier_parent not in dicoPoteauFt_SousTraitant:
                        dicoPoteauFt_SousTraitant[dossier_parent] = []

                    dicoPoteauFt_SousTraitant[dossier_parent].append(name)

        return dicoPoteauFt_SousTraitant

    def traitementResultatFinauxCapFt(self, dicoEtudeCapFtPoteauQgis, dicoPoteauFt_SousTraitant,
                                      all_inf_nums=None):
        """Compare poteaux QGIS vs Excel CAP_FT.
        
        Args:
            all_inf_nums: Set de tous les inf_num normalises de infra_pt_pot.
                Permet de distinguer ABSENT vs HORS_PERIMETRE.
        
        Returns:
            tuple: (introuvables_excel, introuvables_qgis, existants, hors_perimetre)
        """
        if all_inf_nums is None:
            all_inf_nums = set()

        dicoPotFt_Excel_Introuvable = {}
        dicoPotFt_HorsPerimetre = {}
        dicoPotFtExistants = {}
        comptabilite = 0

        # Étape 1 : index rapide
        index_qgis = {}  # clé normalisée -> liste de (etude_cap_ft, inf_num_ft)
        for etude_cap_ft, listePoteauxQgis in dicoEtudeCapFtPoteauQgis.items():
            for inf_num_ft in listePoteauxQgis:
                cle = normalize_appui_num(inf_num_ft, strip_e_prefix=True)
                index_qgis.setdefault(cle, []).append((etude_cap_ft, inf_num_ft))

        # Étape 2 : traitement Excel
        for excel, listePoteau in dicoPoteauFt_SousTraitant.items():
            PotFtintrouvableSt = []
            PotFtHorsPerimetreSt = []

            for poteauSt in listePoteau:
                potSt = poteauSt.replace("FicheAppui_", "").replace(".xlsx", "").strip()
                potSt = normalize_appui_num(potSt, strip_e_prefix=True)

                infos_list = index_qgis.get(potSt)
                if infos_list:
                    # Pop la première correspondance
                    etude_cap_ft, inf_num_ft = infos_list.pop(0)
                    comptabilite += 1
                    dicoPotFtExistants[comptabilite] = [inf_num_ft, etude_cap_ft, poteauSt, excel]

                    # Supprimer le poteau traité de dicoEtudeCapFtPoteauQgis
                    if etude_cap_ft in dicoEtudeCapFtPoteauQgis:
                        if inf_num_ft in dicoEtudeCapFtPoteauQgis[etude_cap_ft]:
                            dicoEtudeCapFtPoteauQgis[etude_cap_ft].remove(inf_num_ft)
                        # Si plus de poteaux restants pour cette étude, supprimer la clé
                        if not dicoEtudeCapFtPoteauQgis[etude_cap_ft]:
                            dicoEtudeCapFtPoteauQgis.pop(etude_cap_ft)

                    # Si plus d'infos pour cette clé dans l'index, supprimer la clé
                    if not infos_list:
                        index_qgis.pop(potSt, None)
                else:
                    # Poteau non trouve: verifier s'il existe dans la couche (hors perimetre)
                    if potSt and potSt in all_inf_nums:
                        PotFtHorsPerimetreSt.append(poteauSt)
                    else:
                        PotFtintrouvableSt.append(poteauSt)

            if PotFtintrouvableSt:
                dicoPotFt_Excel_Introuvable[excel] = PotFtintrouvableSt
            if PotFtHorsPerimetreSt:
                dicoPotFt_HorsPerimetre[excel] = PotFtHorsPerimetreSt

        return dicoPotFt_Excel_Introuvable, dicoEtudeCapFtPoteauQgis, dicoPotFtExistants, dicoPotFt_HorsPerimetre

    def ecrireResultatsAnalyseExcelsCapFt(self, resultatsFinaux, nom):
        """Fonction qui permet d'écrire dans le fichier Excel à partir d'un fichier modèle'"""

        dicoPotFt_Excel_Introuvable = resultatsFinaux[0]
        dicoEtudeCapFtPotQgisIntrouvable = resultatsFinaux[1]
        dicoPotFtExistants = resultatsFinaux[2]

        # print("dicoPotFt_Excel_Introuvable:",dicoPotFt_Excel_Introuvable)
        # print("dicoEtudeCapFtPotQgisIntrouvable:",dicoEtudeCapFtPotQgisIntrouvable)
        # print("dicoPotFtExistants:",dicoPotFtExistants)

        # # Créer un set des inf_num_ft existants pour recherche rapide
        # inf_nums_existants = set(item['inf_num_ft'] for item in dicoPotFtExistants)
        #
        # # Supprimer directement dans la liste originale
        # for item in dicoEtudeCapFtPotQgisIntrouvable[:]:  # on parcourt une copie
        #     if item['inf_num_ft'] in inf_nums_existants:
        #         dicoEtudeCapFtPotQgisIntrouvable.remove(item)
        # # Supprimer directement dans la liste originale
        # for item in dicoPotFt_Excel_Introuvable[:]:  # on parcourt une copie
        #     if item['inf_num_ft'] in inf_nums_existants:
        #         dicoPotFt_Excel_Introuvable.remove(item)
        #
        fichierXlsx = openpyxl.workbook.Workbook()

        ############################## FEUILLE 1 : VALIDATION_COMPLET ################################################
        my_ligne = 1  # Ligne à partir de laquelle on commencera à écrire dans le fichier Annexe
        # Parcourir fichier C3A, pour comparer ses contenus aux fichiers C6
        feuille = fichierXlsx.create_sheet("ANALYSE CAP_FT", 0)

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

        ####################### SOUS-TRAITS INTROUVABLE #######################################"
        for fichierExcels, listesDesAppuisSous_traitant in dicoPotFt_Excel_Introuvable.items():
            for inf_num_excel in listesDesAppuisSous_traitant:
                # Si tiragle câble complètement validé
                my_ligne += 1

                feuille.cell(row=my_ligne, column=3, value=inf_num_excel).fill = openpyxl.styles.PatternFill(fill_type='solid', fgColor=red_color)
                feuille.cell(row=my_ligne, column=4, value=fichierExcels).fill = openpyxl.styles.PatternFill(fill_type='solid', fgColor=red_color)
                feuille.cell(row=my_ligne, column=5, value="infra inexistant dans QGIS").fill = openpyxl.styles.PatternFill(fill_type='solid', fgColor=red_color)

        ####################### QGIS INTROUVABLE #######################################
        for etudeQgis, listesDesAppuisQGis in dicoEtudeCapFtPotQgisIntrouvable.items():
            for inf_num in listesDesAppuisQGis:
                # Si tirage câble complètement validé
                my_ligne += 1

                feuille.cell(row=my_ligne, column=1, value=inf_num).fill = openpyxl.styles.PatternFill(fill_type='solid', fgColor=orange_color)
                feuille.cell(row=my_ligne, column=2, value=etudeQgis).fill = openpyxl.styles.PatternFill(fill_type='solid', fgColor=orange_color)
                feuille.cell(row=my_ligne, column=5, value="infra inexistant dans les Fiches Appuis").fill = openpyxl.styles.PatternFill(fill_type='solid', fgColor=orange_color)

        my_colonne = 1
        my_ligne += 1

        ####################### POTEAUX TROUVEES #######################################")
        for _, listesDesAppuisBT in dicoPotFtExistants.items():
            for valeur in listesDesAppuisBT:
                feuille.cell(row=my_ligne, column=my_colonne, value=valeur)
                my_colonne += 1

            feuille.cell(row=my_ligne, column=5, value="correspondance trouvée")

            # On passe à la ligne suivante
            # Si la première liste est terminé, on passe à la ligne suivante en récommençant par au toujours
            # au niveau de la colonne A
            my_ligne += 1
            my_colonne = 1

        if 'Sheet' in fichierXlsx.sheetnames:
            del fichierXlsx['Sheet']

        try:
            fichierXlsx.save(filename=nom)
        except (PermissionError, OSError) as e:
            from qgis.core import QgsMessageLog
            from .compat import MSG_CRITICAL
            QgsMessageLog.logMessage(
                f"[CAP_FT] Impossible d'ecrire le rapport: {nom} - {e}",
                "PoleAerien", MSG_CRITICAL
            )
            raise
