'use client';

import { useEffect, useState } from 'react';
import { Button, Popover } from '@heroui/react';
import { CalendarDays, ChevronLeft, ChevronRight } from 'lucide-react';
import { DayPicker, type DateRange } from 'react-day-picker';
import { enGB } from 'react-day-picker/locale';
import { lastMonthRange, lastWeekRange, monthEnd, monthStart, mondayOf, prettyRange, shiftDay, shiftMonth, todayWib } from '@/lib/format';
import { cn } from '@/lib/utils';

export type Period = { preset: string; from: string; to: string };
export type Preset = { id: string; label: string; range: () => { from: string; to: string } };

/* ------------------------------------------------------------------ presets */

/** Ready-made periods. Calendar periods ("Last month") differ from rolling ones ("Past month" = the last 30-ish days). */
export type PresetId =
  | 'all' | 'today' | 'yesterday' | 'today_yesterday' | 'week' | 'last_week' | 'month' | 'last_month'
  | 'last_7_days' | 'last_30_days' | 'last_1_month' | 'last_3_months';

export const PRESET_LABELS: Record<PresetId, string> = {
  all: 'All dates', today: 'Today', yesterday: 'Yesterday', today_yesterday: 'Today and yesterday', week: 'This week', last_week: 'Last week',
  month: 'This month', last_month: 'Last month', last_7_days: 'Last 7 days', last_30_days: 'Last 30 days',
  last_1_month: 'Past month', last_3_months: 'Past 3 months',
};

/** The date range of a preset, in Jakarta time. "This week"/"This month" run to their end; pass `untilToday` to stop at today. */
export function presetRange(id: PresetId, today = todayWib(), untilToday = false): { from: string; to: string } {
  const monday = mondayOf(today);
  switch (id) {
    case 'all': return { from: '', to: '' };
    case 'today': return { from: today, to: today };
    case 'yesterday': return { from: shiftDay(today, -1), to: shiftDay(today, -1) };
    case 'today_yesterday': return { from: shiftDay(today, -1), to: today };
    case 'week': return { from: monday, to: untilToday ? today : shiftDay(monday, 6) };
    case 'last_week': return lastWeekRange(today);
    case 'month': return { from: monthStart(today), to: untilToday ? today : monthEnd(today) };
    case 'last_month': return lastMonthRange(today);
    case 'last_7_days': return { from: shiftDay(today, -6), to: today };
    case 'last_30_days': return { from: shiftDay(today, -29), to: today };
    case 'last_1_month': return { from: shiftMonth(today, -1), to: today };
    case 'last_3_months': return { from: shiftMonth(today, -3), to: today };
  }
}

/** Preset buttons for PeriodPicker, in the order given: periodPresets(['today', 'yesterday', 'week', 'last_week']). */
export function periodPresets(ids: readonly PresetId[], options: { untilToday?: boolean; labels?: Partial<Record<PresetId, string>> } = {}): Preset[] {
  return ids.map((id) => ({ id, label: options.labels?.[id] ?? PRESET_LABELS[id], range: () => presetRange(id, todayWib(), options.untilToday) }));
}

/** A ready Period value for a preset id. */
export function periodFor(id: PresetId, untilToday = false): Period {
  return { preset: id, ...presetRange(id, todayWib(), untilToday) };
}

/**
 * Home (dashboard) periods. The dashboard opens on LAST WEEK, not this week: a finished week is complete
 * and comparable, while "this week" is half empty early in the week. The API takes the preset key as
 * `period` (week | last_week | month | last_month) and only reads dates for 'custom'.
 */
export const DASHBOARD_PRESET_IDS = ['week', 'last_week', 'month', 'last_month'] as const satisfies readonly PresetId[];
export const DEFAULT_DASHBOARD_PRESET = 'last_week' satisfies PresetId;
export const DEFAULT_DASHBOARD_PERIOD: Period = { preset: DEFAULT_DASHBOARD_PRESET, from: '', to: '' };
/** Dashboard preset buttons; dates stay empty because the API resolves preset keys itself. */
export const dashboardPresets = (): Preset[] => DASHBOARD_PRESET_IDS.map((id) => ({ id, label: PRESET_LABELS[id], range: () => ({ from: '', to: '' }) }));

/* ------------------------------------------------------------------ pickers */

const pad = (value: number) => String(value).padStart(2, '0');
const toIso = (date: Date) => `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`;
const fromIso = (iso: string) => { const [y, m, d] = iso.split('-').map(Number); return new Date(y, m - 1, d); };

/**
 * Pick a day or a range. Days that carry something (new matches, posts) can show a count,
 * which makes it obvious where to look.
 */
export function RangeButton({ value, onChange, max, markers, onMonthChange, active, label = 'Pick dates' }: {
  value: { from: string; to: string };
  onChange: (range: { from: string; to: string }) => void;
  max?: string;
  markers?: Record<string, number>;
  onMonthChange?: (monthStartIso: string) => void;
  active?: boolean;
  label?: string;
}) {
  const [open, setOpen] = useState(false);
  const [months, setMonths] = useState(2);
  useEffect(() => {
    const update = () => setMonths(window.innerWidth >= 900 ? 2 : 1);
    update();
    window.addEventListener('resize', update);
    return () => window.removeEventListener('resize', update);
  }, []);

  const [draft, setDraft] = useState<DateRange | undefined>();
  // After the first tap, the days up to the hovered one light up ("snake") so the range is visible before the second tap.
  const [hover, setHover] = useState<Date | undefined>();
  const preview = draft?.from && !draft.to && hover && hover.getTime() !== draft.from.getTime()
    ? (hover < draft.from ? { from: hover, to: draft.from } : { from: draft.from, to: hover }) : undefined;
  const [month, setMonth] = useState<Date>(() => (value.to ? fromIso(value.to) : new Date()));
  const maxDate = max ? fromIso(max) : undefined;
  // The saved range is only highlighted; selection state starts empty so the first tap always starts a new range.
  const saved = value.from && value.to && !draft ? { from: fromIso(value.from), to: fromIso(value.to) } : undefined;

  function changeMonth(next: Date) {
    setMonth(next);
    onMonthChange?.(toIso(new Date(next.getFullYear(), next.getMonth(), 1)));
  }
  function pick(day: Date) {
    if (!draft?.from || draft.to) { setDraft({ from: day, to: undefined }); setHover(undefined); return; }
    const [from, to] = day < draft.from ? [day, draft.from] : [draft.from, day];
    onChange({ from: toIso(from), to: toIso(to) });
    setOpen(false);
  }

  return (
    <Popover isOpen={open} onOpenChange={(next) => { setOpen(next); if (next) setDraft(undefined); }}>
      <Popover.Trigger>
        <Button variant={active ? 'primary' : 'secondary'} className="shrink-0" aria-label={`Pick dates. Current: ${active && value.from ? prettyRange(value.from, value.to) : 'none selected'}`}>
          <CalendarDays className="size-4" aria-hidden="true" />
          {active && value.from ? prettyRange(value.from, value.to) : label}
        </Button>
      </Popover.Trigger>
      <Popover.Content placement="bottom start" className="max-w-[calc(100vw-1rem)]">
        <Popover.Dialog aria-label="Pick a date range" className="p-3 sm:p-4">
          <DayPicker
            mode="range"
            locale={enGB}
            weekStartsOn={1}
            numberOfMonths={months}
            month={month}
            onMonthChange={changeMonth}
            selected={draft}
            onDayClick={pick}
            onDayMouseEnter={(day) => setHover(day)}
            onDayMouseLeave={() => setHover(undefined)}
            modifiers={{ saved: saved ?? [], preview: preview ?? [], preview_end: preview ? [hover as Date] : [],
              preview_start: preview && draft?.from ? [draft.from] : [] }}
            modifiersClassNames={{
              saved: 'rounded-full bg-accent-soft font-semibold text-accent',
              preview: 'xm-snake bg-accent/15 text-accent transition-colors duration-150',
              preview_start: 'xm-snake-start rounded-l-full [&>button]:bg-accent [&>button]:text-accent-foreground',
              preview_end: 'xm-snake-end rounded-r-full [&>button]:ring-2 [&>button]:ring-accent [&>button]:bg-accent-soft',
            }}
            disabled={maxDate ? { after: maxDate } : undefined}
            endMonth={maxDate}
            classNames={{
              root: 'xm-daypicker',
              months: 'relative flex flex-col gap-6 sm:flex-row',
              month: 'w-[17.5rem] space-y-2',
              month_caption: 'flex h-10 items-center justify-center text-body font-semibold',
              nav: 'absolute inset-x-0 top-0 flex h-10 items-center justify-between',
              button_previous: 'flex size-10 items-center justify-center rounded-full hover:bg-default disabled:opacity-30',
              button_next: 'flex size-10 items-center justify-center rounded-full hover:bg-default disabled:opacity-30',
              month_grid: 'w-full border-collapse',
              weekday: 'h-9 w-10 text-center text-xs font-semibold text-muted uppercase',
              day: 'relative size-10 p-0 text-center',
              day_button: 'relative flex size-10 w-full items-center justify-center rounded-full text-sm font-medium hover:bg-default',
              today: 'font-bold text-accent',
              outside: 'text-muted/50',
              disabled: 'text-muted/40 line-through',
              selected: 'bg-accent-soft',
              range_start: 'rounded-l-full bg-accent-soft [&>button]:bg-accent [&>button]:text-accent-foreground',
              range_end: 'rounded-r-full bg-accent-soft [&>button]:bg-accent [&>button]:text-accent-foreground',
              range_middle: 'rounded-none',
            }}
            components={{
              Chevron: ({ orientation }) => (orientation === 'left' ? <ChevronLeft className="size-5" aria-hidden="true" /> : <ChevronRight className="size-5" aria-hidden="true" />),
              DayButton: ({ day, modifiers, children, ...rest }) => {
                void modifiers;
                const count = markers?.[toIso(day.date)];
                return (
                  <button type="button" {...rest}>
                    {children}
                    {!!count && <span aria-label={`${count} ${count === 1 ? 'item' : 'items'}`} className="absolute -top-1 -right-1 min-w-4 rounded-full bg-success px-1 text-center text-[0.65rem] leading-4 font-bold text-success-foreground">{count > 99 ? '99+' : count}</span>}
                  </button>
                );
              },
            }}
          />
          <p className="mt-3 max-w-[36rem] text-caption text-muted">Tap a start date, then an end date. For a single day, tap the same date twice.</p>
        </Popover.Dialog>
      </Popover.Content>
    </Popover>
  );
}

/** Quick periods as pill buttons plus a free date range. Scrolls sideways on a phone. */
export function PeriodPicker({ value, onChange, presets, max, markers, onMonthChange, className, label = 'Period', customLabel }: {
  value: Period; onChange: (period: Period) => void; presets: Preset[]; max?: string;
  markers?: Record<string, number>; onMonthChange?: (monthStartIso: string) => void; className?: string; label?: string;
  /** Text on the calendar button while no custom range is chosen (default "Pick dates"). */
  customLabel?: string;
}) {
  // No m-0 here: it would beat a parent's space-y-* gap (e.g. FilterGroup's hint). Preflight already zeroes fieldset margins.
  return (
    <fieldset aria-label={label} className={cn('xm-scroll-x -mx-4 flex min-w-0 gap-2 border-0 px-4 pb-1 sm:mx-0 sm:flex-wrap sm:px-0', className)}>
      {presets.map((preset) => {
        const selected = value.preset === preset.id;
        return (
          <Button key={preset.id} variant={selected ? 'primary' : 'secondary'} className="shrink-0" aria-pressed={selected}
            onPress={() => onChange({ preset: preset.id, ...preset.range() })}>{preset.label}</Button>
        );
      })}
      <RangeButton value={value} active={value.preset === 'custom'} max={max} markers={markers} onMonthChange={onMonthChange} label={customLabel}
        onChange={(range) => onChange({ preset: 'custom', ...range })} />
    </fieldset>
  );
}
