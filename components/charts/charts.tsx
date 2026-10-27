'use client';

import { useMemo } from 'react';
import { Bar, Doughnut, Line } from 'react-chartjs-2';
import { areaGradient, axisStyle, categorical, centerText, colors, legend, tooltip, valueLabels, type ChartOptions } from '@/components/charts/chart-setup';
import { number } from '@/lib/format';
import { cn } from '@/lib/utils';

type Series = { label: string; data: number[]; color: string; fill?: boolean; hidden?: boolean };

/** Lines with a soft area; hover shows every series for the same day. */
export function TrendChart({ labels, series, height = 300, ariaLabel }: { labels: string[]; series: Series[]; height?: number; ariaLabel: string }) {
  const data = useMemo(() => ({
    labels,
    datasets: series.map((item) => ({
      label: item.label, data: item.data, borderColor: item.color, backgroundColor: item.fill === false ? 'transparent' : areaGradient(item.color),
      fill: item.fill !== false, tension: 0.35, borderWidth: 2.5, pointRadius: labels.length > 40 ? 0 : 3, pointHoverRadius: 6,
      pointBackgroundColor: '#fff', pointBorderColor: item.color, pointBorderWidth: 2, hidden: item.hidden,
    })),
  }), [labels, series]);
  const options = useMemo<ChartOptions<'line'>>(() => ({
    responsive: true, maintainAspectRatio: false, interaction: { mode: 'index', intersect: false },
    plugins: { legend: legend(), tooltip: tooltip({ callbacks: { label: (item: { dataset: { label?: string }; parsed: { y: number | null } }) => ` ${item.dataset.label}: ${number(item.parsed.y)}` } }),
      valueLabels: { display: labels.length <= 40 } },
    layout: { padding: { top: 14 } },
    scales: axisStyle,
  }), [labels.length]);
  return <figure className="w-full" style={{ height }} aria-label={ariaLabel}><Line data={data} options={options} plugins={[valueLabels]} /></figure>;
}

export type Slice = { label: string; value: number; color?: string };

/** A ring with the total in the middle and a readable legend beside it (better than Chart.js's on a phone). */
export function RingChart({ items, caption, ariaLabel, className }: { items: Slice[]; caption: string; ariaLabel: string; className?: string }) {
  const total = items.reduce((sum, item) => sum + item.value, 0);
  const data = useMemo(() => ({
    labels: items.map((item) => item.label),
    datasets: [{ data: items.map((item) => item.value), backgroundColor: items.map((item, index) => item.color ?? categorical[index % categorical.length]), borderColor: '#ffffff', borderWidth: 3, hoverOffset: 6, borderRadius: 6 }],
  }), [items]);
  const options = useMemo<ChartOptions<'doughnut'>>(() => ({
    responsive: true, maintainAspectRatio: false, cutout: '70%',
    plugins: {
      legend: { display: false },
      tooltip: tooltip({ callbacks: { label: (item: { label?: string; parsed: number }) => ` ${item.label}: ${number(item.parsed)} (${total ? Math.round((item.parsed / total) * 100) : 0}%)` } }),
      centerText: { text: number(total), caption },
    },
  }), [total, caption]);
  return (
    <div className={cn('@container', className)}>
     <div className="flex flex-col items-center gap-5 @md:flex-row">
      <figure className="relative size-44 shrink-0 @md:size-48" aria-label={ariaLabel}><Doughnut data={data} options={options} plugins={[centerText]} /></figure>
      <ul className="w-full min-w-0 flex-1 space-y-2">
        {items.slice(0, 8).map((item, index) => (
          <li key={item.label} className="flex items-center gap-2.5 text-[0.95rem]">
            <span aria-hidden="true" className="size-3 shrink-0 rounded-full" style={{ background: item.color ?? categorical[index % categorical.length] }} />
            <span className="min-w-0 flex-1 truncate font-medium">{item.label}</span>
            <span className="font-bold tabular-nums">{number(item.value)}</span>
            <span className="w-11 text-right text-sm text-muted tabular-nums">{total ? Math.round((item.value / total) * 100) : 0}%</span>
          </li>
        ))}
      </ul>
     </div>
    </div>
  );
}

type BarSeries = { label: string; data: number[]; color: string };

/** Rounded bars: vertical or horizontal, grouped or stacked. */
export function BarsChart({ labels, series, horizontal, stacked, height = 280, ariaLabel }: {
  labels: string[]; series: BarSeries[]; horizontal?: boolean; stacked?: boolean; height?: number; ariaLabel: string;
}) {
  const data = useMemo(() => ({
    labels,
    datasets: series.map((item) => ({
      label: item.label, data: item.data, backgroundColor: item.color, borderRadius: 8, borderSkipped: false as const, maxBarThickness: horizontal ? 22 : 38, barPercentage: 0.8, categoryPercentage: 0.8,
    })),
  }), [labels, series, horizontal]);
  const options = useMemo<ChartOptions<'bar'>>(() => {
    const category = horizontal ? 'y' : 'x';
    const value = horizontal ? 'x' : 'y';
    return {
      indexAxis: horizontal ? 'y' : 'x', responsive: true, maintainAspectRatio: false, interaction: { mode: 'index', intersect: false },
      plugins: { legend: legend(series.length > 1), tooltip: tooltip({ callbacks: { label: (item: { dataset: { label?: string }; parsed: { x: number | null; y: number | null } }) => ` ${item.dataset.label}: ${number(horizontal ? item.parsed.x : item.parsed.y)}` } }),
        valueLabels: { stacked: !!stacked } },
      layout: { padding: horizontal ? { right: 36 } : { top: 16 } },
      scales: {
        [category]: { ...(horizontal ? axisStyle.y : axisStyle.x), stacked, beginAtZero: undefined, grid: { display: false }, ticks: { ...(horizontal ? axisStyle.y.ticks : axisStyle.x.ticks), padding: 6, autoSkip: horizontal ? false : labels.length > 14, maxRotation: 0 } },
        [value]: { ...(horizontal ? axisStyle.x : axisStyle.y), stacked, beginAtZero: true, grid: { color: colors.grid }, ticks: { precision: 0, font: { size: 12.5 } } },
      },
    } as ChartOptions<'bar'>;
  }, [horizontal, stacked, series.length, labels.length]);
  return <figure className="w-full" style={{ height }} aria-label={ariaLabel}><Bar data={data} options={options} plugins={[valueLabels]} /></figure>;
}

/** A tiny trend line for a card; no axes, no legend. */
export function Sparkline({ values, color = colors.blue, className }: { values: number[]; color?: string; className?: string }) {
  const data = useMemo(() => ({ labels: values.map((_, index) => String(index)), datasets: [{ data: values, borderColor: color, backgroundColor: areaGradient(color), fill: true, tension: 0.35, borderWidth: 2, pointRadius: 0 }] }), [values, color]);
  const options = useMemo<ChartOptions<'line'>>(() => ({
    responsive: true, maintainAspectRatio: false, plugins: { legend: { display: false }, tooltip: { enabled: false } },
    scales: { x: { display: false }, y: { display: false, beginAtZero: false } }, events: [],
  }), []);
  if (values.length < 2) return <div className={cn('flex items-center text-sm text-muted', className)}>Not enough history yet</div>;
  return <div className={className} aria-hidden="true"><Line data={data} options={options} /></div>;
}
