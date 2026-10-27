'use client';

import { useState } from 'react';
import { Button, Input, Label, TextField, toast } from '@heroui/react';
import { Building2, Save, Users } from 'lucide-react';
import { Panel, Segmented } from '@/components/app/primitives';
import { errorMessage } from '@/lib/api';
import { useCompany } from '@/lib/session';
import type { GroupBy } from '@/lib/types';
import { useApi } from '@/lib/workspace-context';

/** Settings → General: the company name and how listings are grouped by sales. */
export function GeneralTab() {
  const api = useApi();
  const { company, refresh } = useCompany();
  const admin = !!company?.permissions.manage_settings;
  const [edited, setEdited] = useState<string | null>(null);
  const name = edited ?? company?.company_name ?? '';
  const [busy, setBusy] = useState('');

  async function run(label: string, job: () => Promise<unknown>, done: string) {
    setBusy(label);
    try { await job(); toast.success(done); refresh(); } catch (reason) { toast.danger(errorMessage(reason, 'The change could not be saved. Please try again.')); } finally { setBusy(''); }
  }

  return (
    <div className="space-y-6">
      <Panel title={<span className="flex items-center gap-2"><Building2 className="size-5 text-accent" aria-hidden="true" />Company name</span>}
        description="Shown in the app for everyone in your company.">
        <form className="flex flex-col gap-3 sm:flex-row sm:items-end" onSubmit={(event) => {
          event.preventDefault();
          if (!admin || !name.trim() || name.trim() === company?.company_name) return;
          void run('name', async () => { await api.put('/company/settings', { company_name: name.trim() }); setEdited(null); }, 'Company name saved');
        }}>
          <TextField value={name} onChange={setEdited} isDisabled={!admin} fullWidth className="sm:max-w-md">
            <Label className="text-sm font-semibold">Name</Label>
            <Input maxLength={120} autoComplete="organization" />
          </TextField>
          {admin && <Button type="submit" className="shrink-0" isPending={busy === 'name'} isDisabled={!name.trim() || name.trim() === company?.company_name}><Save className="size-4" aria-hidden="true" />Save name</Button>}
        </form>
        {!admin && <p className="mt-3 text-caption text-muted">Only company admins can change this.</p>}
      </Panel>

      <Panel title={<span className="flex items-center gap-2"><Users className="size-5 text-accent" aria-hidden="true" />Group listings by</span>}
        description="How listings are grouped per sales person on the Match page, in exports and in the “Listings per sales” chart.">
        <div className="space-y-4">
          <Segmented<GroupBy> label="Group listings by" value={company?.listing_group_by ?? 'sender'} fullWidth isDisabled={!admin || busy === 'group'}
            onChange={(next) => { if (admin && next !== company?.listing_group_by) void run('group', () => api.put('/company/settings', { listing_group_by: next }), 'Grouping saved for the whole company'); }}
            options={[{ id: 'sender', label: 'Sender' }, { id: 'phone', label: 'Phone number in the message' }]} />
          <dl className="space-y-2 text-sm leading-relaxed">
            <div><dt className="inline font-semibold text-foreground">Sender: </dt><dd className="inline text-muted">the WhatsApp contact who posted the message in the group. This is the default.</dd></div>
            <div><dt className="inline font-semibold text-foreground">Phone number in the message: </dt><dd className="inline text-muted">the sales contact in the signature of each listing. Useful when one person posts listings for many sales.</dd></div>
          </dl>
          {!admin && <p className="text-caption text-muted">Only company admins can change this.</p>}
        </div>
      </Panel>
    </div>
  );
}
