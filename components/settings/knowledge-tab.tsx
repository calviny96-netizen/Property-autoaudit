'use client';

import { useState } from 'react';
import { Button, toast } from '@heroui/react';
import { Download } from 'lucide-react';
import { Panel } from '@/components/app/primitives';
import { GlossaryEditor, useGlossary } from '@/components/settings/glossary-editor';
import { LocationIndexPanel } from '@/components/settings/location-index-panel';
import { LocationSuggestions } from '@/components/settings/location-suggestions';
import { PropertyTerms } from '@/components/settings/property-terms';
import { errorMessage } from '@/lib/api';
import { useCompany } from '@/lib/session';
import { useApi } from '@/lib/workspace-context';

/**
 * Settings → Words & places: the glossary, the built-in property terms (read-only), place names nobody
 * recognises yet, the cluster list, and CSV exports of the glossary and the clusters.
 */
export function KnowledgeTab() {
  const { company } = useCompany();
  const admin = !!company?.permissions.manage_settings;
  const glossary = useGlossary();
  const [locationsVersion, setLocationsVersion] = useState(0);
  return (
    <div className="space-y-6">
      <ExportPanel />
      <GlossaryEditor state={glossary} canEdit={admin} />
      <PropertyTerms />
      {admin && <LocationSuggestions glossary={glossary} onLocationsChanged={() => setLocationsVersion((value) => value + 1)} />}
      <LocationIndexPanel canEdit={admin} refreshKey={locationsVersion} />
    </div>
  );
}

/** Download the company's glossary or cluster table as CSV, in the same columns the import dialogs accept. */
function ExportPanel() {
  const api = useApi();
  const [busy, setBusy] = useState('');

  async function download(path: string, label: string) {
    setBusy(path);
    try {
      const blob = await api.file(path);
      const url = URL.createObjectURL(blob);
      const link = document.createElement('a');
      link.href = url; link.download = label; link.click();
      window.setTimeout(() => URL.revokeObjectURL(url), 10_000);
      toast.success(`${label} downloaded`);
    } catch (reason) { toast.danger(errorMessage(reason, 'The file could not be downloaded. Please try again.')); } finally { setBusy(''); }
  }

  return (
    <Panel title={<span className="flex items-center gap-2"><Download className="size-5 text-accent" aria-hidden="true" />Export as CSV</span>}
      description="Download your glossary or your cluster table. The files use the same columns as the import dialogs, so another company can import them.">
      <div className="flex flex-wrap gap-2">
        <Button variant="secondary" isPending={busy === '/glossary/export.csv'} onPress={() => void download('/glossary/export.csv', 'glossary.csv')}>
          <Download className="size-4" aria-hidden="true" />Glossary CSV
        </Button>
        <Button variant="secondary" isPending={busy === '/location-index/export.csv'} onPress={() => void download('/location-index/export.csv', 'clusters.csv')}>
          <Download className="size-4" aria-hidden="true" />Clusters CSV
        </Button>
      </div>
    </Panel>
  );
}
