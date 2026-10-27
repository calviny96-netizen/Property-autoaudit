'use client';

import { useState } from 'react';
import { Button, Input, Label, ListBox, Select, TextField, toast } from '@heroui/react';
import { Collapsible, ErrorNotice, FilterGroup } from '@/components/app/primitives';
import { useApi } from '@/lib/workspace-context';
import { useSession } from '@/lib/session';
import { useData } from '@/lib/use-data';
import { errorMessage } from '@/lib/api';

type Prompt = { id: string; name: string; content: string; owner_id: string | null };
type Catalog = { prompts: Prompt[]; system_prompt: string; decision_questions: Record<string, unknown> };

export function PromptPicker({ value, onChange }: { value: string; onChange: (text: string) => void }) {
  const api = useApi();
  const { user } = useSession();
  const catalog = useData((signal) => api.get<Catalog>('/ai/prompts', signal), []);
  const [selected, setSelected] = useState('');
  const [name, setName] = useState('');
  const [visibility, setVisibility] = useState<'account' | 'company'>('account');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const canShare = user.role === 'admin' || user.role === 'company_admin';
  const current = catalog.data?.prompts.find((p) => p.id === selected);
  const canEdit = !!current && (!!current.owner_id || canShare);

  async function save(update: boolean) {
    setBusy(true); setError('');
    try {
      const body = { name, content: value, visibility };
      const saved = update ? await api.put<Prompt>(`/ai/prompts/${selected}`, body) : await api.post<Prompt>('/ai/prompts', body);
      catalog.reload(); setSelected(saved.id); onChange(saved.content);
      toast.success('Prompt saved');
    } catch (reason) { setError(errorMessage(reason)); } finally { setBusy(false); }
  }

  async function remove() {
    setBusy(true); setError('');
    try {
      await api.del(`/ai/prompts/${selected}`);
      setSelected(''); setName(''); onChange(''); catalog.reload(); toast.success('Prompt deleted');
    } catch (reason) { setError(errorMessage(reason)); } finally { setBusy(false); }
  }

  return <Collapsible title="Review prompt" description="Use the standard instructions, or save additional instructions for this company or your account.">
    <div className="space-y-4">
      <ErrorNotice message={catalog.error || error} />
      <FilterGroup label="Saved prompt">
        <Select aria-label="Saved prompt" value={selected || 'default'} onChange={(key) => {
          const prompt = catalog.data?.prompts.find((p) => p.id === key);
          setSelected(prompt?.id ?? ''); setName(prompt?.name ?? ''); onChange(prompt?.content ?? '');
          setVisibility(prompt?.owner_id === null ? 'company' : 'account');
        }} fullWidth>
          <Select.Trigger><Select.Value /><Select.Indicator /></Select.Trigger>
          <Select.Popover><ListBox>
            <ListBox.Item id="default" textValue="Standard / custom">Standard / custom<ListBox.ItemIndicator /></ListBox.Item>
            {(catalog.data?.prompts ?? []).map((p) => <ListBox.Item key={p.id} id={p.id} textValue={p.name}>{p.name} · {p.owner_id ? 'My account' : 'Company'}<ListBox.ItemIndicator /></ListBox.Item>)}
          </ListBox></Select.Popover>
        </Select>
      </FilterGroup>
      <label className="block space-y-2 text-sm"><span>Additional instructions</span>
        <textarea className="w-full min-h-36 rounded-xl border border-border bg-surface p-3" maxLength={12000} value={value} onChange={(event) => onChange(event.target.value)} placeholder="Optional: priorities or checks for this company. Leave empty for the standard prompt." />
      </label>
      <TextField value={name} onChange={setName} maxLength={100}><Label>Prompt name</Label><Input placeholder="e.g. Strict budget check" /></TextField>
      <FilterGroup label="Save for">
        <Select aria-label="Save prompt for" value={visibility} onChange={(key) => setVisibility(key === 'company' ? 'company' : 'account')} fullWidth>
          <Select.Trigger><Select.Value /><Select.Indicator /></Select.Trigger>
          <Select.Popover><ListBox>
            <ListBox.Item id="account">My account<ListBox.ItemIndicator /></ListBox.Item>
            {canShare && <ListBox.Item id="company">This company<ListBox.ItemIndicator /></ListBox.Item>}
          </ListBox></Select.Popover>
        </Select>
      </FilterGroup>
      <div className="flex flex-wrap gap-2">
        <Button variant="secondary" isDisabled={busy || !name.trim() || !value.trim()} onPress={() => save(false)}>Save as new prompt</Button>
        {canEdit && <Button variant="secondary" isDisabled={busy || !name.trim() || !value.trim()} onPress={() => save(true)}>Update saved prompt</Button>}
        {canEdit && <Button variant="tertiary" isDisabled={busy} onPress={remove}>Delete saved prompt</Button>}
      </div>
      {user.role === 'admin' && catalog.data && <Collapsible title="Standard prompt and decision questions" description="Text models use the system prompt; decision models use the questions. Additional instructions above are appended to either format.">
        <pre className="max-h-96 overflow-auto whitespace-pre-wrap break-words text-xs">{catalog.data.system_prompt}</pre>
        <pre className="mt-4 max-h-96 overflow-auto whitespace-pre-wrap break-words text-xs">{JSON.stringify(catalog.data.decision_questions, null, 2)}</pre>
      </Collapsible>}
    </div>
  </Collapsible>;
}
