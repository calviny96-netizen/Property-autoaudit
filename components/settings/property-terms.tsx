'use client';

/*
 * Settings → Words & places: the built-in property vocabulary the matcher reads (GET /glossary/terms) —
 * legal status (SHM, HGB, Surat Ijo, SHMRS …), apartment unit types (studio, 1BR, 2BR …), towers and blocks,
 * financing (KPR, KPA, cash), floor words and common abbreviations. Read-only and the same for every company;
 * it loads the first time the section is opened.
 */

import { useMemo, useState } from 'react';
import { Button, Chip, Label, SearchField } from '@heroui/react';
import { BookOpenText } from 'lucide-react';
import { Collapsible, EmptyState, SkeletonList } from '@/components/app/primitives';
import { countLabel, number } from '@/lib/format';
import type { PropertyTerm, PropertyTermGroup, PropertyTermsResponse } from '@/lib/types';
import { useData } from '@/lib/use-data';
import { useApi } from '@/lib/workspace-context';

const norm = (value?: string | null) => (value ?? '').trim().replace(/\s+/g, ' ').toLowerCase();

/** Keep only well-formed groups and terms, so an unexpected answer never breaks the page. */
function cleanGroups(data: unknown): PropertyTermGroup[] {
  const groups = (data as Partial<PropertyTermsResponse> | null)?.groups;
  if (!Array.isArray(groups)) return [];
  return groups.flatMap((group): PropertyTermGroup[] => {
    if (!group || typeof group.label !== 'string' || !Array.isArray(group.terms)) return [];
    const terms = group.terms.filter((term): term is PropertyTerm => !!term && typeof term.term === 'string' && !!term.term.trim())
      .map((term) => ({ ...term, aliases: Array.isArray(term.aliases) ? term.aliases.filter((alias) => typeof alias === 'string' && alias.trim()) : [], meaning: term.meaning ?? '' }));
    return terms.length ? [{ key: group.key || group.label, label: group.label, note: group.note ?? null, terms }] : [];
  });
}

function matches(term: PropertyTerm, needle: string) {
  return !needle || [term.term, term.meaning, ...term.aliases].some((value) => norm(value).includes(needle));
}

/** "Property terms the matcher understands": a read-only, searchable list grouped by topic. */
export function PropertyTerms() {
  const api = useApi();
  const [opened, setOpened] = useState(false);
  const [search, setSearch] = useState('');
  const loaded = useData((signal) => api.get<PropertyTermsResponse>('/glossary/terms', signal).then(cleanGroups), [], opened);
  const groups = useMemo(() => loaded.data ?? [], [loaded.data]);
  const total = groups.reduce((sum, group) => sum + group.terms.length, 0);
  const needle = norm(search);
  const shown = useMemo(
    () => groups.map((group) => ({ ...group, terms: group.terms.filter((term) => matches(term, needle)) })).filter((group) => group.terms.length),
    [groups, needle],
  );
  const found = shown.reduce((sum, group) => sum + group.terms.length, 0);

  return (
    <Collapsible
      title="Property terms the matcher understands"
      description="Built in for every company: legal status, apartment unit types, towers, financing and floor words. Read-only."
      icon={<BookOpenText />}
      badge={total > 0 ? <Chip size="sm" variant="soft">{number(total)}</Chip> : undefined}
      onOpenChange={(open) => { if (open) setOpened(true); }}
      bodyClassName="space-y-4"
    >
      {loaded.error ? (
        <EmptyState compact icon={<BookOpenText />} title="The term list is not available right now"
          description="Matching still understands these terms. Try again in a moment."
          action={<Button size="sm" variant="secondary" onPress={loaded.reload}>Try again</Button>} />
      ) : !loaded.data ? (
        <SkeletonList rows={2} />
      ) : groups.length === 0 ? (
        <EmptyState compact icon={<BookOpenText />} title="No built-in terms to show" description="Your team’s own words go in the glossary above." />
      ) : (
        <>
          <SearchField value={search} onChange={setSearch} aria-label="Search property terms" fullWidth>
            <Label className="text-sm font-semibold">Search</Label>
            <SearchField.Group><SearchField.SearchIcon /><SearchField.Input placeholder="e.g. SHM, surat ijo, 2BR, KPA" /><SearchField.ClearButton /></SearchField.Group>
          </SearchField>
          {needle && <p className="text-caption text-muted">{countLabel(found, 'term')} {found === 1 ? 'matches' : 'match'} “{search.trim()}”.</p>}
          {shown.length === 0 ? <p className="text-sm text-muted">No built-in term matches your search.</p> : shown.map((group) => <TermGroup key={group.key} group={group} />)}
        </>
      )}
    </Collapsible>
  );
}

function TermGroup({ group }: { group: PropertyTermGroup }) {
  return (
    <section className="min-w-0 space-y-2" aria-label={group.label}>
      <div>
        <h3 className="text-sm font-semibold text-foreground">{group.label}</h3>
        {group.note && <p className="mt-0.5 text-caption leading-relaxed text-muted">{group.note}</p>}
      </div>
      <dl className="divide-y divide-separator overflow-hidden rounded-xl border border-border">
        {group.terms.map((term) => (
          <div key={term.key || term.term} className="grid min-w-0 gap-x-4 gap-y-1 px-3 py-2.5 sm:grid-cols-[minmax(0,13rem)_minmax(0,1fr)] sm:px-4">
            <dt className="min-w-0">
              <span className="text-sm font-semibold break-words text-foreground">{term.term}</span>
              {term.aliases.length > 0 && <span className="block text-caption break-words text-muted">Also: {term.aliases.join(', ')}</span>}
            </dt>
            <dd className="min-w-0 text-sm break-words text-foreground/85">{term.meaning}</dd>
          </div>
        ))}
      </dl>
    </section>
  );
}
