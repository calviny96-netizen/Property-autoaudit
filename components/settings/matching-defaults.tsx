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
const FIELDS = [{ key: 'buyer_period', label: 'Buyer posting period' }, { key: 'listing_period', label: 'Listing posting period' }] as const;

/** Platform admins set shared entry defaults; changing a page's filters never writes these values. */
export function MatchingDefaultsPanel() {
  const api = useApi();
  const { company, setCompany } = useCompany();
  const [saved, setSaved] = useState(company?.matching_filter_defaults);
  const [draft, setDraft] = useState<MatchingFilterDefaults>(() => saved ?? { buyer_period: 'today', listing_period: 'last_3_months' });
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
    <Panel title="Default Buyer × Listing periods" description={`Saved posting periods for AI Review and Matching in ${company?.company_name || 'this company'}.`}>
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
        <p className="text-sm text-muted">Dates roll forward daily in WIB. AI Review opens in Match mode with these periods. Users can adjust them for each review or use Recent Match to filter by discovery date.</p>
        <div className="flex flex-wrap items-center gap-3">
          <Button type="submit" isPending={busy} isDisabled={!dirty || busy}><Save className="size-4" aria-hidden="true" />Save defaults</Button>
          {!saved && <span className="text-caption text-muted">Save to apply these periods to new reviews and the Matching page.</span>}
        </div>
      </form>
    </Panel>
  );
}
