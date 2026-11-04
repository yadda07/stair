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
Preflight Data Quality Checks - Vérifications qualité données.

Module autonome appelable depuis:
- batch_orchestrator._preflight_data_quality() (auto au démarrage)
- dialog_v2._run_diagnostic() (bouton Diagnostic)

Chaque check retourne un dict:
  {'id': 'PF-01', 'level': 'BLOCKER'|'WARN'|'OK', 'message': str}
"""

import os
import csv
from typing import List, Dict, Optional

from qgis.core import (
    QgsVectorLayer, QgsFeatureRequest, QgsSpatialIndex, NULL
)


def run_data_quality_checks(
    lyr_pot: Optional[QgsVectorLayer],
    lyr_cap: Optional[QgsVectorLayer],
    lyr_com: Optional[QgsVectorLayer],
    module_keys: List[str],
    auto_field_fn=None,
    comac_dir: str = '',
    capft_dir: str = '',
) -> List[Dict]:
    """Execute all preflight data quality checks.
    
    Args:
        lyr_pot: infra_pt_pot layer (or None)
        lyr_cap: etude_cap_ft layer (or None)
        lyr_com: etude_comac layer (or None)
        module_keys: list of module keys selected (['comac', 'capft', ...])
        auto_field_fn: callable(layer) -> field_name for study name field detection
        comac_dir: path to COMAC Excel directory
        capft_dir: path to CAP_FT directory
    
    Returns:
        List of check results, each: {'id', 'level', 'message'}
        level is 'BLOCKER', 'WARN', or 'OK'
    """
    results = []
    mods = set(module_keys)

    # === PF-01 + PF-02: Doublons noms d'etudes & polygones sans geometrie (passe unique) ===
    pf02_results = []
    for lyr, label, concerned_pf01, concerned_pf02 in [
        (lyr_cap, 'etude_cap_ft', {'capft', 'c6bd', 'police_c6'}, {'capft'}),
        (lyr_com, 'etude_comac', {'comac'}, {'comac'}),
    ]:
        if not lyr or not lyr.isValid():
            continue
        do_pf01 = bool(mods & concerned_pf01)
        do_pf02 = bool(mods & concerned_pf02)
        if not do_pf01 and not do_pf02:
            continue

        field_name = auto_field_fn(lyr) if (do_pf01 and auto_field_fn) else None
        idx_field = lyr.fields().indexFromName(field_name) if field_name else -1
        req_zone = QgsFeatureRequest()
        if idx_field >= 0:
            req_zone.setSubsetOfAttributes([idx_field])
        else:
            req_zone.setSubsetOfAttributes([])

        names = {}
        no_geom = 0
        total_zone = lyr.featureCount()
        for feat in lyr.getFeatures(req_zone):
            if do_pf02 and not feat.hasGeometry():
                no_geom += 1
            if do_pf01 and idx_field >= 0:
                val = feat[idx_field]
                if val and val != NULL:
                    name = str(val).strip()
                    names[name] = names.get(name, 0) + 1

        if do_pf01:
            if not field_name or idx_field < 0:
                results.append({
                    'id': 'PF-01', 'level': 'WARN',
                    'message': f'{label} : champ nom d\'etude non identifie, verification des doublons ignoree.'
                })
            else:
                doublons = {n: c for n, c in names.items() if c > 1}
                if doublons:
                    for name, count in doublons.items():
                        results.append({
                            'id': 'PF-01', 'level': 'WARN',
                            'message': f'{label} : nom d\'etude "{name}" present {count} fois '
                                       f'(normal si l\'etude couvre plusieurs communes).'
                        })
                else:
                    results.append({
                        'id': 'PF-01', 'level': 'OK',
                        'message': f'{label}: aucun doublon d\'etude'
                    })

        if do_pf02:
            if no_geom > 0:
                pf02_results.append({
                    'id': 'PF-02', 'level': 'WARN',
                    'message': f'{label} : {no_geom}/{total_zone} zones d\'etude sans contour '
                               f'(les appuis dans ces zones ne seront pas detectes).'
                })
            else:
                pf02_results.append({
                    'id': 'PF-02', 'level': 'OK',
                    'message': f'{label}: {total_zone} polygones valides'
                })

    # === PF-01b + PF-03 + PF-04: infra_pt_pot qualite (single pass) ===
    if lyr_pot and lyr_pot.isValid():
        idx_inf_num = lyr_pot.fields().indexFromName('inf_num')
        has_inf_num = idx_inf_num >= 0
        req_pot = QgsFeatureRequest()
        req_pot.setSubsetOfAttributes([idx_inf_num] if has_inf_num else [])
        inf_nums = {}
        nb_null = 0
        nb_no_geom = 0
        total_pot = lyr_pot.featureCount()

        for feat in lyr_pot.getFeatures(req_pot):
            if not feat.hasGeometry():
                nb_no_geom += 1
            if has_inf_num:
                val = feat[idx_inf_num]
                if val and val != NULL and str(val).strip():
                    key = str(val).strip()
                    inf_nums[key] = inf_nums.get(key, 0) + 1
                else:
                    nb_null += 1

        # PF-01b
        dup_inf = {n: c for n, c in inf_nums.items() if c > 1}
        if dup_inf:
            sample = list(dup_inf.keys())[:5]
            results.append({
                'id': 'PF-01b', 'level': 'WARN',
                'message': f'infra_pt_pot: {len(dup_inf)} inf_num en doublon '
                           f'(ex: {", ".join(sample)}). Resultats potentiellement faux.'
            })
        else:
            results.append({
                'id': 'PF-01b', 'level': 'OK',
                'message': f'infra_pt_pot: aucun inf_num en doublon ({len(inf_nums)} uniques)'
            })

        # PF-03
        if nb_null > 0:
            results.append({
                'id': 'PF-03', 'level': 'WARN',
                'message': f'infra_pt_pot : {nb_null}/{total_pot} appuis sans numero '
                           f'(exclus de l\'analyse).'
            })
        else:
            results.append({
                'id': 'PF-03', 'level': 'OK',
                'message': f'infra_pt_pot: tous les poteaux ont un inf_num ({total_pot} total)'
            })

        # PF-04
        if nb_no_geom > 0:
            results.append({
                'id': 'PF-04', 'level': 'WARN',
                'message': f'infra_pt_pot : {nb_no_geom}/{total_pot} appuis sans coordonnees GPS '
                           f'(comparaison spatiale des cables impossible pour ceux-ci).'
            })
        else:
            results.append({
                'id': 'PF-04', 'level': 'OK',
                'message': f'infra_pt_pot: tous les poteaux ont une geometrie ({total_pot} total)'
            })

    # === PF-02: Polygones sans geometrie ===
    results.extend(pf02_results)

    # === PF-05: CRS coherent ===
    crs_map = {}
    for lyr, label in [(lyr_pot, 'infra_pt_pot'), (lyr_cap, 'etude_cap_ft'), (lyr_com, 'etude_comac')]:
        if lyr and lyr.isValid() and lyr.crs().isValid():
            crs_map[label] = lyr.crs().authid()
    unique_crs = set(crs_map.values())
    if len(unique_crs) > 1:
        details = ', '.join(f'{k}={v}' for k, v in crs_map.items())
        results.append({
            'id': 'PF-05', 'level': 'BLOCKER',
            'message': f'Systeme de coordonnees different entre les couches : {details}. '
                       f'Toutes les couches doivent etre en Lambert-93 (EPSG:2154).'
        })
    elif len(unique_crs) == 1 and len(crs_map) >= 2:
        results.append({
            'id': 'PF-05', 'level': 'OK',
            'message': f'CRS coherent: {unique_crs.pop()} ({len(crs_map)} couches)'
        })

    # === PF-06: Dossier COMAC ===
    if 'comac' in mods and comac_dir and os.path.isdir(comac_dir):
        comac_xlsx = 0
        for _root, _, _files in os.walk(comac_dir):
            comac_xlsx += sum(1 for f in _files if f.endswith('.xlsx') and '~$' not in f)
        if comac_xlsx == 0:
            results.append({
                'id': 'PF-06', 'level': 'WARN',
                'message': f'Dossier COMAC: 0 fichier .xlsx trouve dans {os.path.basename(comac_dir)}.'
            })
        else:
            results.append({
                'id': 'PF-06', 'level': 'OK',
                'message': f'Dossier COMAC: {comac_xlsx} fichier(s) .xlsx'
            })

    # === PF-07: Dossier CAP_FT ===
    if 'capft' in mods and capft_dir and os.path.isdir(capft_dir):
        fiche_count = 0
        for _root, _, _files in os.walk(capft_dir):
            fiche_count += sum(1 for f in _files if 'FicheAppui' in f and f.endswith('.xlsx'))
        if fiche_count == 0:
            results.append({
                'id': 'PF-07', 'level': 'WARN',
                'message': 'Dossier CAP_FT: aucun fichier FicheAppui_*.xlsx detecte.'
            })
        else:
            results.append({
                'id': 'PF-07', 'level': 'OK',
                'message': f'Dossier CAP_FT: {fiche_count} FicheAppui detecte(s)'
            })

    # PF-09 a PF-10e supprimes (checks GraceTHD/Axione obsoletes)

    return results


def _check_gracethd_mcd_DEPRECATED(gdir: str) -> List[Dict]:
    """Obsolete : checks MCD GraceTHD supprimes avec la BDD Axione."""
    results = []

    # PF-09: Required/optional files
    required = {'t_noeud.shp', 't_cableline.shp'}
    optional = {'t_cable.csv', 't_ptech.csv', 't_ebp.csv'}
    missing_req = [f for f in required if not os.path.isfile(os.path.join(gdir, f))]
    missing_opt = [f for f in optional if not os.path.isfile(os.path.join(gdir, f))]

    if missing_req:
        results.append({
            'id': 'PF-09', 'level': 'BLOCKER',
            'message': f'GraceTHD: fichiers requis manquants: {", ".join(missing_req)}. '
                       f'Placez-les dans {os.path.basename(gdir)}/.'
        })
        return results  # Can't check joins without required files
    else:
        results.append({'id': 'PF-09', 'level': 'OK', 'message': 'GraceTHD: fichiers requis presents'})

    if missing_opt:
        results.append({
            'id': 'PF-09', 'level': 'WARN',
            'message': f'GraceTHD: fichiers optionnels absents: {", ".join(missing_opt)}'
        })

    # PF-10 + PF-10b: Cables DI + join integrity
    t_cable_path = os.path.join(gdir, 't_cable.csv')
    t_cableline_path = os.path.join(gdir, 't_cableline.shp')
    if os.path.isfile(t_cable_path) and os.path.isfile(t_cableline_path):
        try:
            with open(t_cable_path, 'r', encoding='utf-8', errors='replace') as f:
                cables = list(csv.DictReader(f, delimiter=';'))
            cables_di = [r for r in cables if r.get('cb_typelog', '').upper() == 'DI']
            nb_di = len(cables_di)

            if nb_di == 0:
                results.append({
                    'id': 'PF-10', 'level': 'BLOCKER',
                    'message': f'GraceTHD: 0 cables DI dans t_cable.csv ({len(cables)} cables total). '
                               f'Verification cables impossible.'
                })
            else:
                results.append({
                    'id': 'PF-10', 'level': 'OK',
                    'message': f'GraceTHD t_cable.csv: {nb_di} cables DI / {len(cables)} total'
                })

                # PF-10b: Join cables -> cableline
                cl_layer = QgsVectorLayer(t_cableline_path, 'cl_check', 'ogr')
                if cl_layer.isValid():
                    cl_codes = {str(f['cl_cb_code']).strip() for f in cl_layer.getFeatures() if f['cl_cb_code']}
                    del cl_layer
                    cb_codes_di = {r.get('cb_code', '').strip() for r in cables_di}
                    missing_join = cb_codes_di - cl_codes
                    if missing_join:
                        results.append({
                            'id': 'PF-10b', 'level': 'WARN',
                            'message': f'GraceTHD jointure : {len(missing_join)}/{nb_di} cables distribution '
                                       f'sans trace geometrique (cb_code absent de t_cableline).'
                        })
                    else:
                        results.append({
                            'id': 'PF-10b', 'level': 'OK',
                            'message': f'GraceTHD jointure: {nb_di}/{nb_di} cables DI ont une geometrie'
                        })

                # PF-10e: Capacites
                valid_capas = {6, 12, 24, 36, 48, 72, 96, 144, 288, 576}
                invalid = sum(1 for r in cables_di if _invalid_capa(r, valid_capas))
                if invalid:
                    results.append({
                        'id': 'PF-10e', 'level': 'WARN',
                        'message': f'GraceTHD: {invalid} cables DI avec capacite non standard.'
                    })

        except Exception as e:
            results.append({
                'id': 'PF-10', 'level': 'WARN',
                'message': f'GraceTHD: erreur lecture t_cable.csv: {e}'
            })

    # PF-10c: Join BPE (t_ebp -> t_ptech -> t_noeud)
    t_ebp = os.path.join(gdir, 't_ebp.csv')
    t_ptech = os.path.join(gdir, 't_ptech.csv')
    t_noeud = os.path.join(gdir, 't_noeud.shp')
    if all(os.path.isfile(p) for p in [t_ebp, t_ptech, t_noeud]):
        try:
            with open(t_ebp, 'r', encoding='utf-8', errors='replace') as f:
                ebp_rows = list(csv.DictReader(f, delimiter=';'))
            with open(t_ptech, 'r', encoding='utf-8', errors='replace') as f:
                ptech_rows = list(csv.DictReader(f, delimiter=';'))

            pt_codes = {r.get('pt_code', '').strip() for r in ptech_rows}
            pt_nd_map = {r.get('pt_code', '').strip(): r.get('pt_nd_code', '').strip() for r in ptech_rows}

            nd_layer = QgsVectorLayer(t_noeud, 'nd_check', 'ogr')
            nd_codes = {str(f['nd_code']).strip() for f in nd_layer.getFeatures() if f['nd_code']} if nd_layer.isValid() else set()
            del nd_layer

            nb_broken = 0
            for ebp in ebp_rows:
                bp_pt = ebp.get('bp_pt_code', '').strip()
                if bp_pt not in pt_codes:
                    nb_broken += 1
                elif pt_nd_map.get(bp_pt, '') not in nd_codes:
                    nb_broken += 1

            if nb_broken > 0:
                results.append({
                    'id': 'PF-10c', 'level': 'WARN',
                    'message': f'GraceTHD jointure BPE : {nb_broken}/{len(ebp_rows)} boitiers (BPE) '
                               f'sans localisation (lien entre les tables GraceTHD rompu).'
                })
            else:
                results.append({
                    'id': 'PF-10c', 'level': 'OK',
                    'message': f'GraceTHD jointure BPE: {len(ebp_rows)} BPE valides'
                })
        except Exception as e:
            results.append({
                'id': 'PF-10c', 'level': 'WARN',
                'message': f'GraceTHD: erreur verification BPE: {e}'
            })

    return results


def _invalid_capa(row: dict, valid: set) -> bool:
    try:
        c = int(row.get('cb_capafo', '0') or '0')
        return c not in valid and c != 0
    except (ValueError, TypeError):
        return True


def run_gpkg_intelligence(cables, bpe_list, attaches) -> List[Dict]:
    """Analyse statistique du contenu GPKG charge.

    Retourne une liste de checks GI-xx avec distribution capacites,
    pose mode, longueurs, types BPE et coherence SRO.
    """
    results = []
    if not cables:
        results.append({
            'id': 'GI-00', 'level': 'WARN',
            'message': 'Aucun cable dans le cache GPKG.'
        })
        return results

    # GI-01 : Distribution capacites FO
    capa_dist = {}
    for c in cables:
        key = c.cab_capa
        capa_dist[key] = capa_dist.get(key, 0) + 1
    dist_str = '  '.join(f"{k}fo:{v}" for k, v in sorted(capa_dist.items()) if k > 0)
    results.append({
        'id': 'GI-01', 'level': 'OK',
        'message': f'Capacites cables: {dist_str or "non renseignees"}'
    })

    # GI-02 : Distribution pose mode
    _pose_names = {0: 'souterrain', 1: 'aerien', 2: 'facade', 4: 'orphelin'}
    pose_dist = {}
    for c in cables:
        name = _pose_names.get(c.posemode, f'mode_{c.posemode}')
        pose_dist[name] = pose_dist.get(name, 0) + 1
    pose_str = '  '.join(f"{k}:{v}" for k, v in sorted(pose_dist.items()))
    results.append({
        'id': 'GI-02', 'level': 'OK',
        'message': f'Pose cables: {pose_str}'
    })

    # GI-03 : Statistiques longueurs
    lengths = [c.length for c in cables if c.length and c.length > 0]
    if lengths:
        avg_l = sum(lengths) / len(lengths)
        mn_l, mx_l = min(lengths), max(lengths)
        outliers = sum(1 for l in lengths if l > avg_l * 3)
        msg = f'Longueurs: min={mn_l:.0f}m, moy={avg_l:.0f}m, max={mx_l:.0f}m'
        if outliers:
            msg += f' ({outliers} segment(s) > 3x moyenne)'
        results.append({
            'id': 'GI-03',
            'level': 'WARN' if outliers > 0 else 'OK',
            'message': msg,
        })

    # GI-04 : Types BPE
    if bpe_list:
        noe_types = {}
        for b in bpe_list:
            nt = b.get('noe_type', '?')
            noe_types[nt] = noe_types.get(nt, 0) + 1
        nt_str = '  '.join(f"{k}:{v}" for k, v in sorted(noe_types.items()))
        results.append({
            'id': 'GI-04', 'level': 'OK',
            'message': f'Types BPE: {nt_str}'
        })

    # GI-05 : Coherence SRO
    sro_set = {c.sro for c in cables if c.sro}
    if len(sro_set) > 1:
        results.append({
            'id': 'GI-05', 'level': 'WARN',
            'message': f'SRO multiples dans cables: {", ".join(sorted(sro_set))}'
        })
    elif len(sro_set) == 1:
        results.append({
            'id': 'GI-05', 'level': 'OK',
            'message': f'SRO coherent: {sro_set.pop()} (tous les cables)'
        })

    # GI-06 : Cab types
    type_dist = {}
    for c in cables:
        t = c.cab_type or '?'
        type_dist[t] = type_dist.get(t, 0) + 1
    type_str = '  '.join(f"{k}:{v}" for k, v in sorted(type_dist.items()))
    results.append({
        'id': 'GI-06', 'level': 'OK',
        'message': f'Types cables: {type_str}'
    })

    # GI-07 : Resume attaches
    if attaches:
        results.append({
            'id': 'GI-07', 'level': 'OK',
            'message': f'Attaches: {len(attaches)} enregistrement(s)'
        })

    return results


def run_cross_validation(lyr_pot, lyr_cap, lyr_com) -> List[Dict]:
    """Validation croisee entre les couches QGIS.

    Verifie la coherence spatiale : poteaux dans les etudes,
    etudes vides, couverture du perimetre.
    """
    results = []

    if not lyr_pot or not lyr_pot.isValid():
        return results

    req_pot = QgsFeatureRequest().setSubsetOfAttributes([])
    pot_geoms = [
        feat.geometry()
        for feat in lyr_pot.getFeatures(req_pot)
        if feat.hasGeometry()
    ]

    xv02_results = []

    for lyr_zone, label in [(lyr_cap, 'etude_cap_ft'), (lyr_com, 'etude_comac')]:
        if not lyr_zone or not lyr_zone.isValid():
            continue

        zone_count = lyr_zone.featureCount()
        if zone_count == 0:
            results.append({
                'id': 'XV-01', 'level': 'WARN',
                'message': f'{label}: couche vide (0 polygone)'
            })
            xv02_results.append({
                'id': 'XV-02', 'level': 'OK',
                'message': f'{label}: toutes les 0 zones contiennent des poteaux'
            })
            continue

        field_name = None
        for fld in lyr_zone.fields():
            if fld.name().lower() in ('nom_etude', 'etude', 'name', 'nom'):
                field_name = fld.name()
                break
        idx_field = lyr_zone.fields().indexFromName(field_name) if field_name else -1
        req_zone = QgsFeatureRequest()
        req_zone.setSubsetOfAttributes([idx_field] if idx_field >= 0 else [])

        zone_index = QgsSpatialIndex()
        zone_geoms = {}
        zone_names = {}
        for feat in lyr_zone.getFeatures(req_zone):
            if not feat.hasGeometry():
                continue
            fid = feat.id()
            zone_index.addFeature(feat)
            zone_geoms[fid] = feat.geometry()
            zone_names[fid] = (
                str(feat[idx_field]).strip()
                if idx_field >= 0 and feat[idx_field] != NULL
                else f'FID {fid}'
            )

        inside = 0
        outside = 0
        zones_with_poles = set()

        for pt_geom in pot_geoms:
            candidates = zone_index.intersects(pt_geom.boundingBox())
            found = False
            for cand_id in candidates:
                z_geom = zone_geoms.get(cand_id)
                if z_geom and z_geom.contains(pt_geom):
                    found = True
                    zones_with_poles.add(cand_id)
            if found:
                inside += 1
            else:
                outside += 1

        total = inside + outside
        pct = (inside / total * 100) if total else 0
        if outside == 0:
            results.append({
                'id': 'XV-01', 'level': 'OK',
                'message': f'{label}: {inside}/{total} poteaux dans le perimetre ({pct:.0f}%)'
            })
        elif pct >= 90:
            results.append({
                'id': 'XV-01', 'level': 'OK',
                'message': f'{label}: {inside}/{total} poteaux couverts ({pct:.0f}%), '
                           f'{outside} hors perimetre'
            })
        else:
            results.append({
                'id': 'XV-01', 'level': 'WARN',
                'message': f'{label}: seulement {inside}/{total} poteaux couverts ({pct:.0f}%), '
                           f'{outside} hors perimetre - verifier les zonages'
            })

        empty_zones = [
            name for fid, name in zone_names.items()
            if fid not in zones_with_poles
        ]
        if empty_zones:
            sample = ', '.join(empty_zones[:5])
            suffix = f' (+{len(empty_zones) - 5})' if len(empty_zones) > 5 else ''
            xv02_results.append({
                'id': 'XV-02', 'level': 'WARN',
                'message': f'{label}: {len(empty_zones)} zone(s) sans aucun poteau: {sample}{suffix}'
            })
        else:
            xv02_results.append({
                'id': 'XV-02', 'level': 'OK',
                'message': f'{label}: toutes les {zone_count} zones contiennent des poteaux'
            })

    results.extend(xv02_results)
    return results


def estimate_analysis_time(pot_count, n_etudes_cap, n_etudes_com, n_cables, modules):
    """Estime la duree d'analyse en secondes (heuristique calibree)."""
    total = 0.0
    mods = set(modules)
    if 'capft' in mods:
        total += 1.0 + n_etudes_cap * 0.15 + pot_count * 0.001
    if 'comac' in mods:
        total += 1.0 + n_etudes_com * 0.15 + pot_count * 0.001
    if 'police_c6' in mods:
        total += 2.0 + n_etudes_cap * 0.5 + n_cables * 0.0005
    if 'c6c3a' in mods:
        total += 1.5 + n_etudes_cap * 0.3
    if 'gespot_c6' in mods:
        total += 1.0 + n_etudes_cap * 0.2
    return max(total, 1.0)


def format_check_log(check: Dict) -> tuple:
    """Format a check result for log display.
    
    Returns:
        (message_str, log_level_str) where log_level_str is 'success'|'warning'|'error'
    """
    level = check['level']
    msg = check['message']
    if level == 'OK':
        return f"  [OK] {msg}", 'success'
    elif level == 'WARN':
        return f"  [WARN] {msg}", 'warning'
    else:
        return f"  [ERREUR] {msg}", 'error'
