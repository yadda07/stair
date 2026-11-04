# -*- coding: utf-8 -*-
# Youcef ADDA | GEODECI
# FR : Plugins QGIS, solutions SIG et administration PostgreSQL/PostGIS ; audit, automatisation et accompagnement.
# EN : QGIS plugins, GIS solutions and PostgreSQL/PostGIS administration; audits, workflow automation and business support.
# AR : تطوير إضافات QGIS وحلول نظم المعلومات الجغرافية وإدارة PostgreSQL/PostGIS، والتدقيق والأتمتة ومرافقة المؤسسات.
# Site / Website / الموقع : https://geodeci.xyz/
# Contact professionnel / Business enquiries / للتواصل المهني : youcef.geodesien@gmail.com | contact@geodeci.xyz
# Détails / Details / التفاصيل : SIGNATURE.md
# ============================================================================
import math
from io import BytesIO

from matplotlib.backends.backend_agg import FigureCanvasAgg
from matplotlib.figure import Figure
from matplotlib.lines import Line2D
from matplotlib.patches import Circle, FancyArrowPatch, Rectangle


class PcmDrawingRenderer:
    FIGURE_SIZE = (7.4, 5.4)
    DEFAULT_DPI = 180
    PANEL_BG = '#F8FAFC'
    GRID_COLOR = '#CBD5E1'
    GRID_TEXT_COLOR = '#64748B'
    TITLE_COLOR = '#0F172A'
    TEXT_COLOR = '#334155'
    LABEL_BG_COLOR = '#FFFFFF'
    PHYSICAL_SPAN_COLOR = '#94A3B8'
    SUPPORT_EDGE_COLOR = '#0F172A'
    SUPPORT_FILL_COLOR = '#FFFFFF'
    BT_COLOR = '#7C2D12'
    TCF_COLOR = '#2563EB'
    TCF_NEW_COLOR = '#EA580C'
    EFFORT_COLOR = '#DC2626'
    ARMEMENT_COLOR = '#111827'
    ROUTE_MARKER_COLOR = '#475569'
    PHYSICAL_WIDTH = 1.4
    BT_WIDTH = 2.8
    TCF_WIDTH = 2.2
    EFFORT_WIDTH = 2.5
    ARMEMENT_WIDTH = 2.0
    MIN_RADIUS_METERS = 20.0
    GRID_STEP_METERS = 10.0
    RADIUS_PADDING_RATIO = 1.18
    LABEL_RADIUS_RATIO = 1.08
    LABEL_BAND_STEP_RATIO = 0.05
    SUPPORT_SIZE_RATIO = 0.045
    ARMEMENT_RADIUS_RATIO = 0.24
    ARMEMENT_HALF_LENGTH_RATIO = 0.09
    ROUTE_MARKER_RATIO = 0.72
    EFFORT_MIN_RATIO = 0.18
    EFFORT_MAX_RATIO = 0.42
    MAX_LABEL_LENGTH = 38

    def __init__(self, dpi=None):
        self._dpi = dpi or self.DEFAULT_DPI

    def render_supports(self, etudes_pcm, stop_requested=None):
        entries = self.build_support_entries(etudes_pcm, stop_requested)
        if entries is None:
            return []
        return list(self.render_entries(entries, stop_requested))

    def build_support_entries(self, etudes_pcm, stop_requested=None):
        entries = []
        for etude_name, etude in sorted((etudes_pcm or {}).items()):
            if callable(stop_requested) and stop_requested():
                return None
            bt_index = self._index_bt_spans(etude)
            tcf_index = self._index_tcf_spans(etude)
            spans_by_support = self._index_support_spans(etude, bt_index, tcf_index)
            for support_name, support in sorted(etude.supports.items()):
                if callable(stop_requested) and stop_requested():
                    return None
                if support.nature == 'BO':
                    continue
                spans = spans_by_support.get(support_name, ())
                if not spans:
                    continue
                entries.append({
                    'etude': etude_name,
                    'support_name': support_name,
                    'support_data': support,
                    'spans': spans,
                    'armements': self._build_armements(etude, support_name, spans),
                    'connections': len(spans),
                })
        return entries

    def render_entries(self, entries, stop_requested=None):
        if entries is None:
            return
        context = self._render_context()
        try:
            for entry in entries:
                if callable(stop_requested) and stop_requested():
                    return
                yield self.render_entry(entry, context)
        finally:
            context['figure'].clear()

    def render_entry(self, entry, context=None):
        return {
            'etude': entry['etude'],
            'support': entry['support_name'],
            'connections': entry['connections'],
            'image_bytes': self._render_card(
                entry['etude'],
                entry['support_data'],
                entry['spans'],
                entry['armements'],
                context,
            ),
        }

    def _index_bt_spans(self, etude):
        index = {}
        for ligne in etude.lignes_bt:
            supports = list(ligne.supports)
            if len(supports) < 2:
                continue
            for idx in range(len(supports) - 1):
                key = self._edge_key(supports[idx], supports[idx + 1])
                entry = index.setdefault(key, {'conducteurs': [], 'a_poser': False})
                conducteur = self._safe_text(ligne.conducteur, 24)
                if conducteur and conducteur not in entry['conducteurs']:
                    entry['conducteurs'].append(conducteur)
                entry['a_poser'] = entry['a_poser'] or bool(ligne.a_poser)
        return index

    def _index_tcf_spans(self, etude):
        index = {}
        for ligne in etude.lignes_tcf:
            supports = list(ligne.supports)
            if len(supports) < 2:
                continue
            for idx in range(len(supports) - 1):
                key = self._edge_key(supports[idx], supports[idx + 1])
                entry = index.setdefault(key, {'cables': [], 'a_poser': False})
                cable = self._safe_text(ligne.cable, 24)
                if cable and cable not in entry['cables']:
                    entry['cables'].append(cable)
                entry['a_poser'] = entry['a_poser'] or bool(ligne.a_poser)
        return index

    def _index_support_spans(self, etude, bt_index, tcf_index):
        spans_by_support = {}
        for portee in etude.portees_globales:
            left = str(portee.support_gauche or '').strip()
            right = str(portee.support_droit or '').strip()
            if not left or not right:
                continue
            key = self._edge_key(left, right)
            bt_data = bt_index.get(key, {})
            tcf_data = tcf_index.get(key, {})
            shared = {
                'radius_m': max(float(portee.longueur or 0.0), 1.0),
                'route': bool(portee.route),
                'bt_label': ', '.join(bt_data.get('conducteurs', [])[:2]),
                'bt_a_poser': bool(bt_data.get('a_poser')),
                'tcf_label': ', '.join(tcf_data.get('cables', [])[:2]),
                'tcf_a_poser': bool(tcf_data.get('a_poser')),
                'has_bt': bool(bt_data),
                'has_tcf': bool(tcf_data),
            }
            spans_by_support.setdefault(left, []).append(dict(
                shared,
                neighbor=right,
                angle_grades=self._normalize_grades(portee.angle),
            ))
            spans_by_support.setdefault(right, []).append(dict(
                shared,
                neighbor=left,
                angle_grades=self._normalize_grades(portee.angle + 200.0),
            ))
        for support_name, spans in spans_by_support.items():
            spans.sort(key=lambda item: item['angle_grades'])
        return spans_by_support

    def _build_armements(self, etude, support_name, spans):
        angles_by_neighbor = {span['neighbor']: span['angle_grades'] for span in spans}
        armements = []
        for ligne in etude.lignes_bt:
            if support_name not in ligne.supports or not ligne.armements:
                continue
            base_angle = self._resolve_base_angle(ligne.supports, support_name, angles_by_neighbor)
            if base_angle is None:
                continue
            for armement in ligne.armements:
                if armement.support != support_name:
                    continue
                label = self._safe_text(armement.nom_armement or str(armement.armement), 16)
                armements.append({
                    'label': label,
                    'angle_grades': self._normalize_grades(base_angle + float(armement.decal_accro or 0.0)),
                })
        return armements

    def _resolve_base_angle(self, line_supports, support_name, angles_by_neighbor):
        positions = [idx for idx, name in enumerate(line_supports) if name == support_name]
        for pos in positions:
            neighbors = []
            if pos + 1 < len(line_supports):
                neighbors.append(line_supports[pos + 1])
            if pos - 1 >= 0:
                neighbors.append(line_supports[pos - 1])
            for neighbor in neighbors:
                if neighbor in angles_by_neighbor:
                    return angles_by_neighbor[neighbor]
        bt_angles = list(angles_by_neighbor.values())
        return bt_angles[0] if bt_angles else None

    def _render_context(self):
        figure = Figure(figsize=self.FIGURE_SIZE, dpi=self._dpi, facecolor='white', constrained_layout=True)
        grid = figure.add_gridspec(1, 2, width_ratios=[3.25, 1.35])
        return {
            'figure': figure,
            'chart_ax': figure.add_subplot(grid[0, 0]),
            'info_ax': figure.add_subplot(grid[0, 1]),
            'canvas': FigureCanvasAgg(figure),
        }

    def _render_card(self, etude_name, support, spans, armements, context=None):
        max_radius = max(self.MIN_RADIUS_METERS, max(span['radius_m'] for span in spans))
        owned_context = context is None
        context = context or self._render_context()
        figure = context['figure']
        chart_ax = context['chart_ax']
        info_ax = context['info_ax']
        chart_ax.clear()
        info_ax.clear()
        self._setup_chart(chart_ax, max_radius)
        self._draw_grid(chart_ax, max_radius)
        self._draw_spans(chart_ax, spans, max_radius)
        self._draw_armements(chart_ax, armements, max_radius)
        self._draw_support(chart_ax, max_radius)
        self._draw_effort(chart_ax, support, max_radius)
        self._draw_panel(info_ax, etude_name, support, spans, armements)
        buffer = BytesIO()
        context['canvas'].print_png(buffer)
        if owned_context:
            figure.clear()
        return buffer.getvalue()

    def _setup_chart(self, chart_ax, max_radius):
        limit = max_radius * self.RADIUS_PADDING_RATIO
        chart_ax.set_facecolor(self.PANEL_BG)
        chart_ax.set_aspect('equal')
        chart_ax.set_xlim(-limit, limit)
        chart_ax.set_ylim(-limit, limit)
        chart_ax.set_xticks([])
        chart_ax.set_yticks([])
        for spine in chart_ax.spines.values():
            spine.set_visible(False)

    def _draw_grid(self, chart_ax, max_radius):
        limit = max_radius * self.RADIUS_PADDING_RATIO
        for radius in self._grid_radii(max_radius):
            chart_ax.add_patch(Circle((0, 0), radius, fill=False, lw=0.75, ec=self.GRID_COLOR, zorder=0))
            chart_ax.text(0, radius, f"{int(radius)} m", fontsize=7.5, color=self.GRID_TEXT_COLOR,
                          ha='left', va='bottom', zorder=1)
        for label, angle in [('N', 0), ('E', 100), ('S', 200), ('O', 300)]:
            x, y = self._vector(angle, limit * 0.92)
            chart_ax.plot([0, x], [0, y], color=self.GRID_COLOR, lw=0.9, ls='dashed', zorder=0)
            chart_ax.text(x, y, f"{label}\n{angle}g", fontsize=8, fontweight='bold',
                          color=self.GRID_TEXT_COLOR, ha='center', va='center', zorder=1)

    def _draw_spans(self, chart_ax, spans, max_radius):
        for idx, span in enumerate(spans):
            x_end, y_end = self._vector(span['angle_grades'], span['radius_m'])
            chart_ax.plot([0, x_end], [0, y_end], color=self.PHYSICAL_SPAN_COLOR,
                          lw=self.PHYSICAL_WIDTH, zorder=2)
            if span['has_bt']:
                chart_ax.plot([0, x_end], [0, y_end], color=self.BT_COLOR, lw=self.BT_WIDTH,
                              ls=self._line_style(span['bt_a_poser']), alpha=0.9, zorder=3)
            if span['has_tcf']:
                chart_ax.plot([0, x_end], [0, y_end],
                              color=self.TCF_NEW_COLOR if span['tcf_a_poser'] else self.TCF_COLOR,
                              lw=self.TCF_WIDTH, ls=self._line_style(span['tcf_a_poser']), zorder=4)
            if span['route']:
                x_mark = x_end * self.ROUTE_MARKER_RATIO
                y_mark = y_end * self.ROUTE_MARKER_RATIO
                chart_ax.scatter([x_mark], [y_mark], s=26, marker='o', facecolors='white',
                                 edgecolors=self.ROUTE_MARKER_COLOR, linewidths=1.2, zorder=5)
            label_radius = span['radius_m'] * self.LABEL_RADIUS_RATIO + (idx % 2) * max_radius * self.LABEL_BAND_STEP_RATIO
            lx, ly = self._vector(span['angle_grades'], label_radius)
            chart_ax.text(lx, ly, self._span_label(span), fontsize=7.8, color=self.TEXT_COLOR,
                          ha='left' if lx >= 0 else 'right',
                          va='bottom' if ly >= 0 else 'top',
                          bbox={'boxstyle': 'round,pad=0.25', 'fc': self.LABEL_BG_COLOR, 'ec': self.GRID_COLOR, 'lw': 0.8},
                          zorder=6)

    def _draw_support(self, chart_ax, max_radius):
        size = max_radius * self.SUPPORT_SIZE_RATIO
        chart_ax.add_patch(Rectangle((-size, -size), size * 2, size * 2,
                                     facecolor=self.SUPPORT_FILL_COLOR,
                                     edgecolor=self.SUPPORT_EDGE_COLOR,
                                     linewidth=1.8, zorder=8))
        chart_ax.add_patch(Circle((0, 0), size * 1.25, fill=False,
                                  ec=self.SUPPORT_EDGE_COLOR, lw=1.1, zorder=7))

    def _draw_armements(self, chart_ax, armements, max_radius):
        radius = max_radius * self.ARMEMENT_RADIUS_RATIO
        half_length = max_radius * self.ARMEMENT_HALF_LENGTH_RATIO
        for armement in armements:
            cx, cy = self._vector(armement['angle_grades'], radius)
            dx, dy = self._vector(armement['angle_grades'], half_length)
            chart_ax.plot([cx - dx, cx + dx], [cy - dy, cy + dy], color=self.ARMEMENT_COLOR,
                          lw=self.ARMEMENT_WIDTH, solid_capstyle='round', zorder=7)
            tx, ty = self._vector(armement['angle_grades'], radius + max_radius * 0.09)
            chart_ax.text(tx, ty, armement['label'], fontsize=7.2, color=self.ARMEMENT_COLOR,
                          ha='center', va='center', zorder=8)

    def _draw_effort(self, chart_ax, support, max_radius):
        effort = float(support.effort or 0.0)
        if effort <= 0:
            return
        ratio = min(max(effort / 10.0, self.EFFORT_MIN_RATIO), self.EFFORT_MAX_RATIO)
        x_end, y_end = self._vector(self._normalize_grades(float(support.orientation or 0.0)), max_radius * ratio)
        chart_ax.add_patch(FancyArrowPatch((0, 0), (x_end, y_end), arrowstyle='-|>',
                                           mutation_scale=14, color=self.EFFORT_COLOR,
                                           linewidth=self.EFFORT_WIDTH, zorder=9))
        tx, ty = self._vector(self._normalize_grades(float(support.orientation or 0.0)), max_radius * (ratio + 0.08))
        chart_ax.text(tx, ty, f"Effort {effort:.1f} kN", fontsize=8.2, fontweight='bold',
                      color=self.EFFORT_COLOR, ha='center', va='center', zorder=10)

    def _draw_panel(self, info_ax, etude_name, support, spans, armements):
        info_ax.axis('off')
        info_ax.set_facecolor(self.PANEL_BG)
        info_ax.text(0.0, 0.97, support.nom, fontsize=13, fontweight='bold',
                     color=self.TITLE_COLOR, ha='left', va='top', transform=info_ax.transAxes)
        info_ax.text(0.0, 0.90, etude_name, fontsize=9.5, color=self.TEXT_COLOR,
                     ha='left', va='top', transform=info_ax.transAxes)
        meta_lines = [
            f"Nature : {support.nature or '-'}",
            f"Classe : {support.classe or '-'}",
            f"Hauteur : {support.hauteur:.1f} m" if support.hauteur else "Hauteur : -",
            f"Orientation : {float(support.orientation or 0.0):.1f} g",
            f"Voisins : {len(spans)}",
            f"Armements BT : {len(armements)}",
        ]
        if support.effort:
            meta_lines.append(f"Effort resultant : {float(support.effort):.1f} kN")
        flags = self._support_flags(support)
        if flags:
            meta_lines.append(f"Etat : {', '.join(flags)}")
        info_ax.text(0.0, 0.80, '\n'.join(meta_lines), fontsize=9.1, color=self.TEXT_COLOR,
                     ha='left', va='top', transform=info_ax.transAxes)
        info_ax.legend(handles=self._legend_handles(), loc='lower left', frameon=False,
                       fontsize=8.1, bbox_to_anchor=(0.0, 0.02))

    def _legend_handles(self):
        return [
            Line2D([0], [0], color=self.PHYSICAL_SPAN_COLOR, lw=self.PHYSICAL_WIDTH, label='Portee physique'),
            Line2D([0], [0], color=self.BT_COLOR, lw=self.BT_WIDTH, label='Ligne BT'),
            Line2D([0], [0], color=self.TCF_COLOR, lw=self.TCF_WIDTH, label='TCF existant'),
            Line2D([0], [0], color=self.TCF_NEW_COLOR, lw=self.TCF_WIDTH, ls='--', label='TCF a poser'),
            Line2D([0], [0], color=self.EFFORT_COLOR, lw=self.EFFORT_WIDTH, label='Effort resultant'),
            Line2D([0], [0], color=self.ARMEMENT_COLOR, lw=self.ARMEMENT_WIDTH, label='Armement BT'),
            Line2D([0], [0], marker='o', color='w', markerfacecolor='white',
                   markeredgecolor=self.ROUTE_MARKER_COLOR, markersize=6, label='Traversee route'),
        ]

    def _span_label(self, span):
        lines = [self._safe_text(span['neighbor'], self.MAX_LABEL_LENGTH), f"{span['radius_m']:.1f} m"]
        if span['has_tcf'] and span['tcf_label']:
            lines.append(f"FO {self._safe_text(span['tcf_label'], self.MAX_LABEL_LENGTH)}")
        elif span['has_bt'] and span['bt_label']:
            lines.append(f"BT {self._safe_text(span['bt_label'], self.MAX_LABEL_LENGTH)}")
        return '\n'.join(lines)

    def _support_flags(self, support):
        flags = []
        if getattr(support, 'a_poser', False):
            flags.append('a poser')
        if getattr(support, 'facade', False):
            flags.append('facade')
        if getattr(support, 'portee_molle', False):
            flags.append('portee molle')
        if getattr(support, 'illisible', False):
            flags.append('illisible')
        if getattr(support, 'non_calcule', False):
            flags.append('non calcule')
        return flags

    def _grid_radii(self, max_radius):
        radii = []
        radius = self.GRID_STEP_METERS
        while radius < max_radius:
            radii.append(radius)
            radius += self.GRID_STEP_METERS
        if not radii or radii[-1] != max_radius:
            radii.append(round(max_radius, 1))
        return radii

    def _line_style(self, a_poser):
        return '--' if a_poser else '-'

    def _vector(self, angle_grades, radius):
        radians = math.pi * self._normalize_grades(angle_grades) / 200.0
        return math.sin(radians) * radius, math.cos(radians) * radius

    def _normalize_grades(self, angle_grades):
        return float(angle_grades or 0.0) % 400.0

    def _edge_key(self, left, right):
        return tuple(sorted((str(left or ''), str(right or ''))))

    def _safe_text(self, value, max_length):
        text = str(value or '').strip()
        if len(text) <= max_length:
            return text
        return text[: max_length - 1].rstrip() + '…'
