# Youcef ADDA | GEODECI
# FR : Plugins QGIS, solutions SIG et administration PostgreSQL/PostGIS ; audit, automatisation et accompagnement.
# EN : QGIS plugins, GIS solutions and PostgreSQL/PostGIS administration; audits, workflow automation and business support.
# AR : تطوير إضافات QGIS وحلول نظم المعلومات الجغرافية وإدارة PostgreSQL/PostGIS، والتدقيق والأتمتة ومرافقة المؤسسات.
# Site / Website / الموقع : https://geodeci.xyz/
# Contact professionnel / Business enquiries / للتواصل المهني : youcef.geodesien@gmail.com | contact@geodeci.xyz
# Détails / Details / التفاصيل : SIGNATURE.md
# ============================================================================
"""
Dezippage de livrables GeoPackage.

Scenario cible : un plugin annexe livre un .zip contenant un GeoPackage
(eventuellement avec des fichiers auxiliaires -wal / -shm / -journal).
Le sous-traitant doit pouvoir :
    1. Indiquer le chemin du .zip
    2. Extraire automatiquement le .gpkg
    3. Le charger dans QGIS ou dans le pipeline de decoupage

API principale :
    extract_gpkg_from_zip(zip_path, dest_dir=None) -> chemin du .gpkg extrait
    extract_gpkg_tempdir(zip_path) -> context manager avec nettoyage auto
"""
from __future__ import annotations

import contextlib
import os
import shutil
import tempfile
import zipfile
from typing import Iterator, List, Optional


GPKG_EXTENSIONS = (".gpkg",)
GPKG_AUX_EXTENSIONS = ("-wal", "-shm", "-journal")


# -----------------------------------------------------------------------------
# Validation
# -----------------------------------------------------------------------------


def _is_valid_zip(path: str) -> bool:
    """Verifie si le fichier est un ZIP lisible."""
    try:
        with zipfile.ZipFile(path, "r") as zf:
            bad = zf.testzip()
            return bad is None
    except (zipfile.BadZipFile, FileNotFoundError, IsADirectoryError, OSError):
        return False


def list_gpkg_in_zip(zip_path: str) -> List[str]:
    """Liste les .gpkg presents dans un ZIP (sans les extraire).

    Retourne les chemins relatifs tels que stockes dans l'archive.
    """
    if not os.path.isfile(zip_path):
        raise FileNotFoundError(f"ZIP introuvable : {zip_path}")
    if not _is_valid_zip(zip_path):
        raise zipfile.BadZipFile(f"Fichier corrompu ou non-ZIP : {zip_path}")
    with zipfile.ZipFile(zip_path, "r") as zf:
        return [
            n for n in zf.namelist()
            if n.lower().endswith(GPKG_EXTENSIONS) and not n.startswith("__MACOSX")
        ]


# -----------------------------------------------------------------------------
# Extraction controlee (anti-Zip-Slip)
# -----------------------------------------------------------------------------


def _safe_join(base_dir: str, rel_path: str) -> str:
    """Empeche Zip-Slip (path traversal via '../' dans le nom d'archive).

    Raises ValueError si le chemin resolu sort de base_dir.
    """
    base = os.path.realpath(base_dir)
    target = os.path.realpath(os.path.join(base, rel_path))
    if not target.startswith(base + os.sep) and target != base:
        raise ValueError(
            f"Entree ZIP refusee (path traversal) : {rel_path}"
        )
    return target


def _flatten_name(rel_path: str) -> str:
    """Transforme un chemin relatif en nom plat (sans sous-dossiers).

    Evite les conflits si le ZIP contient des chemins imbriques et facilite
    la resolution du chemin de sortie (toujours a plat dans dest_dir).
    """
    return os.path.basename(rel_path.replace("\\", "/"))


def _extract_single_member(
    zf: zipfile.ZipFile, member: str, dest_dir: str
) -> str:
    """Extrait UN membre du ZIP vers dest_dir/basename(member)."""
    target = _safe_join(dest_dir, _flatten_name(member))
    os.makedirs(os.path.dirname(target) or dest_dir, exist_ok=True)
    with zf.open(member) as src, open(target, "wb") as dst:
        shutil.copyfileobj(src, dst)
    return target


def _pick_primary_gpkg(candidates: List[str], zip_path: str) -> str:
    """Choisit le GPKG principal parmi plusieurs.

    Regle : si un seul -> ce dernier. Sinon leve ValueError avec la liste
    pour forcer un choix explicite du caller.
    """
    if not candidates:
        raise ValueError(f"Aucun .gpkg dans {zip_path}")
    if len(candidates) == 1:
        return candidates[0]
    raise ValueError(
        f"{len(candidates)} .gpkg dans le ZIP ; preciser lequel extraire : "
        f"{candidates}"
    )


def _aux_siblings(zf: zipfile.ZipFile, main_member: str) -> List[str]:
    """Retourne les fichiers auxiliaires (-wal, -shm, -journal) associes."""
    base = main_member.rsplit("/", 1)[-1]  # nom de fichier
    prefix_path = main_member.rsplit("/", 1)[0] if "/" in main_member else ""
    aux: List[str] = []
    for name in zf.namelist():
        flat = name.rsplit("/", 1)[-1]
        parent = name.rsplit("/", 1)[0] if "/" in name else ""
        if parent != prefix_path:
            continue
        for ext in GPKG_AUX_EXTENSIONS:
            if flat == base + ext:
                aux.append(name)
    return aux


# -----------------------------------------------------------------------------
# API publique
# -----------------------------------------------------------------------------


def extract_gpkg_from_zip(
    zip_path: str,
    dest_dir: Optional[str] = None,
    gpkg_member: Optional[str] = None,
) -> str:
    """Extrait un GeoPackage d'un ZIP et retourne son chemin.

    Args:
        zip_path : chemin du .zip source.
        dest_dir : dossier cible. Si None, utilise le dossier du ZIP.
        gpkg_member : nom du .gpkg dans le ZIP a extraire. Si None, auto-detecte
                      (erreur si plusieurs sans precision).

    Returns:
        Chemin absolu du fichier .gpkg extrait.

    Raises:
        FileNotFoundError : ZIP absent.
        zipfile.BadZipFile : archive corrompue.
        ValueError : aucun ou plusieurs GPKG sans precision, ou Zip-Slip.
    """
    if not os.path.isfile(zip_path):
        raise FileNotFoundError(f"ZIP introuvable : {zip_path}")
    if not _is_valid_zip(zip_path):
        raise zipfile.BadZipFile(f"Fichier corrompu ou non-ZIP : {zip_path}")

    target_dir = dest_dir or os.path.dirname(os.path.abspath(zip_path))
    os.makedirs(target_dir, exist_ok=True)

    with zipfile.ZipFile(zip_path, "r") as zf:
        all_gpkgs = [
            n for n in zf.namelist()
            if n.lower().endswith(GPKG_EXTENSIONS) and not n.startswith("__MACOSX")
        ]
        main = gpkg_member or _pick_primary_gpkg(all_gpkgs, zip_path)
        if main not in all_gpkgs:
            raise ValueError(
                f"gpkg_member '{main}' absent de l'archive "
                f"(disponibles : {all_gpkgs})"
            )
        extracted = _extract_single_member(zf, main, target_dir)
        # Auxiliaires : extrait silencieusement s'ils existent
        for aux in _aux_siblings(zf, main):
            try:
                _extract_single_member(zf, aux, target_dir)
            except OSError:
                continue
    return extracted


@contextlib.contextmanager
def extract_gpkg_tempdir(
    zip_path: str,
    gpkg_member: Optional[str] = None,
) -> Iterator[str]:
    """Context manager : extrait un GPKG dans un dossier temporaire.

    Le dossier est supprime automatiquement a la sortie du `with`.

    Usage :
        with extract_gpkg_tempdir("livrable.zip") as gpkg_path:
            stats = split_cables_from_gpkg(gpkg_path)

    Args:
        zip_path : chemin du .zip.
        gpkg_member : membre cible (optionnel).

    Yields:
        Chemin absolu du GPKG extrait (valide uniquement dans le with).
    """
    tmp_dir = tempfile.mkdtemp(prefix="stair_gpkg_")
    try:
        gpkg_path = extract_gpkg_from_zip(zip_path, tmp_dir, gpkg_member)
        yield gpkg_path
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


# -----------------------------------------------------------------------------
# Integration QGIS (optionnelle)
# -----------------------------------------------------------------------------


def load_gpkg_layers_into_qgis(
    gpkg_path: str,
    layer_names: Optional[List[str]] = None,
    group_name: Optional[str] = None,
) -> int:
    """Charge les couches d'un GPKG dans le projet QGIS courant.

    Helper pour le plugin : apres dezippage, appeler cette fonction pour
    ajouter automatiquement les couches dans la legende.

    Args:
        gpkg_path : chemin du GeoPackage extrait.
        layer_names : liste des couches a charger. None = toutes les couches.
        group_name : si fourni, cree un groupe dans la legende.

    Returns:
        Nombre de couches effectivement ajoutees.

    Raises:
        RuntimeError : si QGIS n'est pas disponible (execution hors plugin).
    """
    try:
        from qgis.core import QgsProject, QgsVectorLayer
    except ImportError as exc:
        raise RuntimeError(
            "load_gpkg_layers_into_qgis necessite l'environnement QGIS"
        ) from exc

    # Import local pour eviter cycle d'imports (pipeline.py importe gpkg_io)
    from .gpkg_io import list_layers

    if not os.path.isfile(gpkg_path):
        raise FileNotFoundError(f"GeoPackage introuvable : {gpkg_path}")

    available = set(list_layers(gpkg_path))
    targets = list(layer_names) if layer_names else sorted(available)

    project = QgsProject.instance()
    root = project.layerTreeRoot()
    target_group = root.addGroup(group_name) if group_name else None

    nb = 0
    for name in targets:
        if name not in available:
            continue
        uri = f"{gpkg_path}|layername={name}"
        layer = QgsVectorLayer(uri, name, "ogr")
        if not layer.isValid():
            continue
        project.addMapLayer(layer, addToLegend=(target_group is None))
        if target_group is not None:
            target_group.addLayer(layer)
        nb += 1
    return nb
