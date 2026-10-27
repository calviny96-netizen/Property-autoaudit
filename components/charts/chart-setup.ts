import {
  ArcElement, BarElement, CategoryScale, Chart, Filler, Legend, LinearScale, LineElement, PointElement, Tooltip,
  type ChartData, type ChartOptions, type Plugin, type ScriptableContext,
} from 'chart.js';

/** One registration for every chart: only the pieces we use are bundled. */
Chart.register(ArcElement, BarElement, CategoryScale, Filler, Legend, LinearScale, LineElement, PointElement, Tooltip);

/** Hex values (charts append alpha suffixes such as `${color}44`). Match status colours mirror the CSS tokens. */
export const colors = {
  blue: '#0a3be0', cyan: '#14b8d4', navy: '#13317f', amber: '#f59e0b', red: '#ef4444', green: '#10b981',
  violet: '#7c3aed', pink: '#ec4899', teal: '#0d9488', orange: '#f97316', slate: '#64748b', ink: '#1e293b', grid: '#e8edf5',
  hot: '#dc2626', warm: '#f59e0b', custom: '#7c3aed', unmatched: '#94a3b8',
  ready: '#10b981', onHold: '#f59e0b', sold: '#0a3be0',
};
/** Series colours for Hot / Warm / Custom / No match, in that order everywhere. */
export const statusColors = { hot: colors.hot, warm: colors.warm, custom: colors.custom, unmatched: colors.unmatched } as const;
export const categorical = [colors.blue, colors.cyan, colors.amber, colors.violet, colors.green, colors.pink, colors.orange, colors.teal, colors.slate];

const FONT = "'Plus Jakarta Sans Variable', ui-sans-serif, system-ui, sans-serif";

Chart.defaults.font.family = FONT;
Chart.defaults.font.size = 13;
Chart.defaults.color = '#475569';
// Mutate, never replace: Chart.js keeps resolver state on the defaults object.
Object.assign(Chart.defaults.animation as object, { duration: 700, easing: 'easeOutQuart' });

export function tooltip(extra: Record<string, unknown> = {}) {
  return {
    backgroundColor: '#0f1b3d', titleColor: '#ffffff', bodyColor: '#e2e8f0', padding: 12, cornerRadius: 12, boxPadding: 5,
    titleFont: { family: FONT, size: 14, weight: 700 }, bodyFont: { family: FONT, size: 14 }, displayColors: true, usePointStyle: true,
    ...extra,
  } as const;
}

export function legend(display = true) {
  return { display, position: 'bottom' as const, labels: { usePointStyle: true, pointStyle: 'circle' as const, boxWidth: 8, boxHeight: 8, padding: 18, font: { family: FONT, size: 13, weight: 600 as const } } };
}

/** Soft area under a line, fading to transparent. */
export function areaGradient(color: string) {
  return (context: ScriptableContext<'line'>) => {
    const { chart } = context;
    if (!chart.chartArea) return `${color}22`;
    const gradient = chart.ctx.createLinearGradient(0, chart.chartArea.top, 0, chart.chartArea.bottom);
    gradient.addColorStop(0, `${color}44`);
    gradient.addColorStop(1, `${color}00`);
    return gradient;
  };
}

export const axisStyle = {
  x: { grid: { display: false }, border: { display: false }, ticks: { maxRotation: 0, autoSkipPadding: 14, font: { family: FONT, size: 12.5 } } },
  y: { beginAtZero: true, grid: { color: colors.grid }, border: { display: false, dash: [4, 4] as number[] }, ticks: { precision: 0, padding: 8, font: { family: FONT, size: 12.5 } } },
};

/** Writes a big number (and a caption) in the hole of a doughnut. */
export const centerText: Plugin<'doughnut'> = {
  id: 'centerText',
  afterDraw(chart, _args, options) {
    const { text, caption } = (options ?? {}) as { text?: string; caption?: string };
    if (!text) return;
    const { ctx, chartArea } = chart;
    const x = (chartArea.left + chartArea.right) / 2;
    const y = (chartArea.top + chartArea.bottom) / 2;
    ctx.save();
    ctx.textAlign = 'center';
    ctx.textBaseline = 'middle';
    ctx.fillStyle = colors.ink;
    ctx.font = `800 28px ${FONT}`;
    ctx.fillText(text, x, y - (caption ? 8 : 0));
    if (caption) {
      ctx.fillStyle = '#64748b';
      ctx.font = `600 13px ${FONT}`;
      ctx.fillText(caption, x, y + 18);
    }
    ctx.restore();
  },
};

export type { ChartData, ChartOptions };


/**
 * Prints the value of every bar (at its end) and every line point, so readers know the exact number without
 * hovering. Stacked bars print each segment inside it when there is room, and the total at the end.
 */
export const valueLabels: Plugin<'bar' | 'line'> = {
  id: 'valueLabels',
  afterDatasetsDraw(chart, _args, options) {
    const opts = (options ?? {}) as { display?: boolean; stacked?: boolean };
    if (opts.display === false) return;
    const { ctx } = chart;
    const horizontal = (chart.options as { indexAxis?: string }).indexAxis === 'y';
    const totals: number[] = [];
    ctx.save();
    ctx.font = `700 11.5px ${FONT}`;
    chart.data.datasets.forEach((dataset, datasetIndex) => {
      const meta = chart.getDatasetMeta(datasetIndex);
      if (meta.hidden || !meta.data.length) return;
      meta.data.forEach((element, index) => {
        const raw = dataset.data[index];
        const value = typeof raw === 'number' ? raw : Number(raw ?? 0);
        if (!Number.isFinite(value)) return;
        totals[index] = (totals[index] ?? 0) + value;
        if (value === 0) return;
        const pos = element.tooltipPosition(false);
        const x = pos.x ?? 0, y = pos.y ?? 0;
        const text = value.toLocaleString('id-ID');
        if (meta.type === 'line') {
          ctx.fillStyle = (dataset.borderColor as string) || '#1e293b';
          ctx.textAlign = 'center'; ctx.textBaseline = 'bottom';
          ctx.fillText(text, x, y - 7);
          return;
        }
        const bar = element as unknown as { base: number; width: number; height: number };
        if (opts.stacked) {
          // Inside the segment, white, only when it fits.
          const size = horizontal ? Math.abs(x - bar.base) : Math.abs(y - bar.base);
          if (size < 18) return;
          ctx.fillStyle = '#ffffff'; ctx.textAlign = 'center'; ctx.textBaseline = 'middle';
          ctx.fillText(text, horizontal ? (x + bar.base) / 2 : x, horizontal ? y : (y + bar.base) / 2);
          return;
        }
        ctx.fillStyle = '#1e293b';
        if (horizontal) { ctx.textAlign = 'left'; ctx.textBaseline = 'middle'; ctx.fillText(text, x + 6, y); }
        else { ctx.textAlign = 'center'; ctx.textBaseline = 'bottom'; ctx.fillText(text, x, y - 4); }
      });
    });
    if (opts.stacked) {
      // The total at the end of each stack.
      const last = chart.data.datasets.length - 1;
      const meta = chart.getDatasetMeta(last);
      ctx.fillStyle = '#1e293b';
      meta.data.forEach((element, index) => {
        const total = totals[index] ?? 0;
        if (!total) return;
        const pos = element.tooltipPosition(false);
        const x = pos.x ?? 0, y = pos.y ?? 0;
        if (horizontal) { ctx.textAlign = 'left'; ctx.textBaseline = 'middle'; ctx.fillText(total.toLocaleString('id-ID'), x + 6, y); }
        else { ctx.textAlign = 'center'; ctx.textBaseline = 'bottom'; ctx.fillText(total.toLocaleString('id-ID'), x, y - 4); }
      });
    }
    ctx.restore();
  },
};
