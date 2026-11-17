'use client';

import { useState } from 'react';
import { Button, Label, ListBox, Select, toast } from '@heroui/react';
import { Save } from 'lucide-react';
import { Panel } from '@/components/app/primitives';
import { periodFor } from '@/components/app/period-picker';
import { errorMessage } from '@/lib/api';
import { prettyRange } from '@/lib/format';
import { useCompany } from '@/lib/session';
import type { CompanySettings, MatchingFilterDefaults, MatchingFilterPeriod } from '@/lib/types';
import { useApi } from '@/lib/workspace-context';

const OPTIONS: { id: MatchingFilterPeriod; label: string }[] = [
  { id: 'today', label: 'Today' },
  { id: 'today_yesterday', label: 'Today and yesterday' },
  { id: 'last_3_days', label: 'Last 3 days' },
  { id: 'last_7_days', label: 'Last 7 days' },
  { id: 'last_14_days', label: 'Last 14 days' },
  { id: 'last_1_month', label: 'Past month' },
  { id: 'last_3_months', label: 'Past 3 months' },
  { id: 'all', label: 'All dates' },
];
const FIELDS = [{ key: 'listing_period', label: 'Listing posted' }, { key: 'buyer_period', label: 'Buyer posted' }] as const;

/** Platform admins set shared entry defaults; changing a page's filters never writes these values. */
export function MatchingDefaultsPanel() {
  const api = useApi();
  const { company, setCompany } = useCompany();
  const [saved, setSaved] = useState(company?.matching_filter_defaults);
  const [draft, setDraft] = useState<MatchingFilterDefaults>(() => saved ?? { listing_period: 'last_1_month', buyer_period: 'today' });
  const [busy, setBusy] = useState(false);
  const dirty = !saved || FIELDS.some(({ key }) => draft[key] !== saved[key]);

  async function save() {
    if (!dirty || busy) return;
    setBusy(true);
    try {
      const result = await api.put<CompanySettings>('/company/settings', { matching_filter_defaults: draft });
      setSaved(result.matching_filter_defaults);
      // Make the saved defaults available immediately if the next click opens Matching or AI Review.
      setCompany(result);
      toast.success('Default periods saved', { description: 'They apply the next time Matching or AI Review is opened.' });
    } catch (reason) {
      toast.danger(errorMessage(reason, 'The default periods could not be saved.'));
    } finally { setBusy(false); }
  }

  return (
    <Panel title="Default matching periods" description={`The starting filters for everyone in ${company?.company_name || 'this company'}, in both Matching and AI Review. Only the super admin can change these defaults.`}>
      <form className="space-y-4" onSubmit={(event) => { event.preventDefault(); void save(); }}>
        <div className="grid gap-4 sm:grid-cols-2">
          {FIELDS.map(({ key, label }) => {
            const range = periodFor(draft[key]);
            return (
              <div key={key} className="space-y-2">
                <Select value={draft[key]} onChange={(value) => {
                  const option = OPTIONS.find((item) => item.id === value);
                  if (option) setDraft((current) => ({ ...current, [key]: option.id }));
                }} isDisabled={busy} fullWidth>
                  <Label className="text-sm font-semibold">{label}</Label>
                  <Select.Trigger><Select.Value /><Select.Indicator /></Select.Trigger>
                  <Select.Popover>
                    <ListBox>{OPTIONS.map(({ id, label: text }) => <ListBox.Item key={id} id={id} textValue={text}>{text}<ListBox.ItemIndicator /></ListBox.Item>)}</ListBox>
                  </Select.Popover>
                </Select>
                <p className="text-caption text-muted">{range.from ? `${prettyRange(range.from, range.to)} · WIB` : 'No date limit'}</p>
              </div>
            );
          })}
        </div>
        <p className="text-sm text-muted">Periods move with today’s date in WIB. “Last 3 days” includes today and the previous 2 days. “Past month” starts one calendar month before today.</p>
        <p className="text-sm text-muted">AI Review opens in Match mode to use these posting dates. Users can adjust their filters or switch to Recent Match for discovery dates. Links to a specific buyer, listing or sales contact still show their full history.</p>
        <div className="flex flex-wrap items-center gap-3">
          <Button type="submit" isPending={busy} isDisabled={!dirty || busy}><Save className="size-4" aria-hidden="true" />Save defaults</Button>
          {!saved && <span className="text-caption text-muted">Not configured yet. Save to apply these periods to both menus.</span>}
        </div>
      </form>
    </Panel>
  );
}
