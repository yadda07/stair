"""
Couche de compatibilite QGIS 3.x (Qt5) / QGIS 4.x (Qt6).

Centralise la resolution des enums, flags et types qui changent
entre PyQt5/PyQt6 et entre QGIS 3.x/4.x.

Importer depuis ce module au lieu d'acceder directement aux enums.
Compatible QGIS 3.28+ (PyQt5 >= 5.15) et QGIS 4.0+ (PyQt6).
"""

from qgis.core import Qgis

QGIS_VERSION_INT = Qgis.versionInt()
IS_QGIS4 = QGIS_VERSION_INT >= 40000

# ---------------------------------------------------------------------------
# Qt enums (noms qualifies longs, compatibles PyQt5 >= 5.15 et PyQt6)
# ---------------------------------------------------------------------------
from qgis.PyQt.QtCore import Qt

# Orientations
HORIZONTAL = Qt.Orientation.Horizontal
VERTICAL = Qt.Orientation.Vertical

# Alignement
ALIGN_LEFT = Qt.AlignmentFlag.AlignLeft
ALIGN_RIGHT = Qt.AlignmentFlag.AlignRight
ALIGN_CENTER = Qt.AlignmentFlag.AlignCenter
ALIGN_HCENTER = Qt.AlignmentFlag.AlignHCenter
ALIGN_VCENTER = Qt.AlignmentFlag.AlignVCenter
ALIGN_TOP = Qt.AlignmentFlag.AlignTop
ALIGN_BOTTOM = Qt.AlignmentFlag.AlignBottom

# Curseurs
CURSOR_POINTING = Qt.CursorShape.PointingHandCursor
CURSOR_WAIT = Qt.CursorShape.WaitCursor
CURSOR_ARROW = Qt.CursorShape.ArrowCursor

# Window flags
WF_NO_HELP = Qt.WindowType.WindowContextHelpButtonHint

# Case sensitivity
CASE_INSENSITIVE = Qt.CaseSensitivity.CaseInsensitive
CASE_SENSITIVE = Qt.CaseSensitivity.CaseSensitive

# Match flags
MATCH_CONTAINS = Qt.MatchFlag.MatchContains
MATCH_STARTS_WITH = Qt.MatchFlag.MatchStartsWith
MATCH_EXACTLY = Qt.MatchFlag.MatchExactly

# ---------------------------------------------------------------------------
# QPalette roles
# ---------------------------------------------------------------------------
from qgis.PyQt.QtGui import QPalette

PAL_BASE = QPalette.ColorRole.Base
PAL_WINDOW = QPalette.ColorRole.Window
PAL_WINDOW_TEXT = QPalette.ColorRole.WindowText
PAL_MID = QPalette.ColorRole.Mid
PAL_MIDLIGHT = QPalette.ColorRole.Midlight
PAL_BRIGHT_TEXT = QPalette.ColorRole.BrightText
PAL_HIGHLIGHT = QPalette.ColorRole.Highlight

# ---------------------------------------------------------------------------
# QFrame shapes / shadows
# ---------------------------------------------------------------------------
from qgis.PyQt.QtWidgets import QFrame, QDialogButtonBox

FRAME_HLINE = QFrame.Shape.HLine
FRAME_SUNKEN = QFrame.Shadow.Sunken

BTN_OK = QDialogButtonBox.StandardButton.Ok
BTN_CANCEL = QDialogButtonBox.StandardButton.Cancel

# ---------------------------------------------------------------------------
# Qgis.MessageLevel (forme longue QGIS >= 3.36, fallback forme courte)
# ---------------------------------------------------------------------------
try:
    MSG_INFO = Qgis.MessageLevel.Info
    MSG_WARNING = Qgis.MessageLevel.Warning
    MSG_CRITICAL = Qgis.MessageLevel.Critical
    MSG_SUCCESS = Qgis.MessageLevel.Success
except AttributeError:
    MSG_INFO = Qgis.Info
    MSG_WARNING = Qgis.Warning
    MSG_CRITICAL = Qgis.Critical
    MSG_SUCCESS = Qgis.Success

# ---------------------------------------------------------------------------
# Qgis.GeometryType (remplace QgsWkbTypes.GeometryType deprece)
# ---------------------------------------------------------------------------
try:
    GEOM_POINT = Qgis.GeometryType.Point
    GEOM_LINE = Qgis.GeometryType.Line
    GEOM_POLYGON = Qgis.GeometryType.Polygon
except AttributeError:
    GEOM_POINT = 0
    GEOM_LINE = 1
    GEOM_POLYGON = 2

# ---------------------------------------------------------------------------
# QgsMapLayerProxyModel / Qgis.LayerFilter
# ---------------------------------------------------------------------------
try:
    LAYER_FILTER_POINT = Qgis.LayerFilter.PointLayer
    LAYER_FILTER_POLYGON = Qgis.LayerFilter.PolygonLayer
    LAYER_FILTER_LINE = Qgis.LayerFilter.LineLayer
    LAYER_FILTER_VECTOR = Qgis.LayerFilter.VectorLayer
except AttributeError:
    from qgis.core import QgsMapLayerProxyModel
    LAYER_FILTER_POINT = QgsMapLayerProxyModel.PointLayer
    LAYER_FILTER_POLYGON = QgsMapLayerProxyModel.PolygonLayer
    LAYER_FILTER_LINE = QgsMapLayerProxyModel.LineLayer
    LAYER_FILTER_VECTOR = QgsMapLayerProxyModel.VectorLayer

# ---------------------------------------------------------------------------
# QgsFeatureRequest flags
# ---------------------------------------------------------------------------
try:
    FR_NO_GEOMETRY = Qgis.FeatureRequestFlag.NoGeometry
except AttributeError:
    from qgis.core import QgsFeatureRequest as _QFR
    FR_NO_GEOMETRY = _QFR.NoGeometry

# ---------------------------------------------------------------------------
# QVariant / QMetaType pour QgsField
# ---------------------------------------------------------------------------
from qgis.PyQt.QtCore import QVariant

if IS_QGIS4:
    from qgis.PyQt.QtCore import QMetaType
    FIELD_TYPE_STRING = QMetaType.Type.QString
    FIELD_TYPE_INT = QMetaType.Type.Int
    FIELD_TYPE_DOUBLE = QMetaType.Type.Double
    FIELD_TYPE_LONGLONG = QMetaType.Type.LongLong
else:
    FIELD_TYPE_STRING = QVariant.String
    FIELD_TYPE_INT = QVariant.Int
    FIELD_TYPE_DOUBLE = QVariant.Double
    FIELD_TYPE_LONGLONG = QVariant.LongLong
