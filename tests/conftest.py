"""Configuration pytest : ajoute le dossier parent du plugin a sys.path
pour permettre l'import absolu `PoleAerien_shp.*` dans les tests."""
from __future__ import annotations

import sys
from pathlib import Path

# Ajoute le dossier `plugins/` pour permettre `import PoleAerien_shp.xxx`
_PLUGINS_DIR = Path(__file__).resolve().parents[2]
if str(_PLUGINS_DIR) not in sys.path:
    sys.path.insert(0, str(_PLUGINS_DIR))
