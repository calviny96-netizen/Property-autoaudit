export type Role = 'admin' | 'company_admin' | 'user';
export type EntityStatus = 'ready' | 'on_hold' | 'sold' | 'deleted';
export type Direction = 'buyer' | 'property';
/**
 * Stored status of one buyer ⇄ listing pair. `custom` = everything that is stated fits, but a requirement the buyer
 * insists on (location, tower, legal status, unit type) is not stated in the listing, or it is a nearby alternative.
 */
export type Temperature = 'hot' | 'warm' | 'custom';
/** Status filter values used everywhere in the UI and API: the pair statuses plus sources without any match. */
export type MatchStatus = Temperature | 'unmatched';
/** Alias kept for existing pages; same as MatchStatus. */
export type MatchFilter = MatchStatus;
export type TransactionType = 'sale' | 'rent' | 'both' | 'unknown';
export type PriceBasis = 'total' | 'per_year' | 'per_month' | 'per_m2';
export type GroupBy = 'sender' | 'phone';
export type MatchingMode = 'company' | 'sales';
/**
 * Who a recommended buyer or listing belongs to, seen from the selected source. Computed by the API when the list is
 * read (never stored), and every recommendation list comes back sorted by it first:
 * `own` = same person as the source (same phone, or the same WhatsApp sender when a side has no phone),
 * `team` = the owner's number is on the company's sales watchlist,
 * `company` = the message mentions one of the company keywords, `other` = everyone else.
 */
export type OwnershipTier = 'own' | 'team' | 'company' | 'other';
export type ImportStatus = 'queued' | 'processing' | 'completed' | 'failed';

export type CompanyStatus = 'pending' | 'active' | 'inactive' | 'rejected';
/** Menus / features a company admin can grant to a member (api/access.py FEATURES). */
export type Feature = 'home' | 'match' | 'recent' | 'stock' | 'buyers' | 'mark_status' | 'upload' | 'settings' | 'team' | 'jev';
export const FEATURE_KEYS: readonly Feature[] = ['home', 'match', 'recent', 'stock', 'buyers', 'mark_status', 'upload', 'settings', 'team', 'jev'];
export const DEFAULT_MEMBER_FEATURES: readonly Feature[] = ['home', 'match', 'recent', 'stock', 'buyers', 'mark_status'];
export type FeatureInfo = { id: Feature; label: string; description: string };

export type User = {
  id: string;
  email: string;
  display_name: string;
  role: Role;
  is_locked: boolean;
  company_name?: string | null;
  workspace_id?: string | null;
  /** Registration state of the account's company; anything but `active` blocks the app (platform admin excepted). */
  company_status?: CompanyStatus;
  /** Signed in with a password an admin set: a new password must be chosen before anything else. */
  must_change_password?: boolean;
};

export type Permissions = {
  platform: boolean;
  manage_team: boolean;
  manage_settings: boolean;
  upload_data: boolean;
  edit_company_search: boolean;
  edit_personal_search: boolean;
  mark_status: boolean;
  /** AI Review (AI keys, review runs, Jev badges): super admins only. */
  jev_review?: boolean;
  /** Main pages this account may open (members: what their company admin granted). */
  pages?: Record<'home' | 'match' | 'recent' | 'stock' | 'buyers', boolean>;
  features?: Feature[];
  all_features?: boolean;
};

export type CompanySettings = {
  company_name: string | null;
  listing_group_by: GroupBy;
  matching_mode: MatchingMode;
  search_locked: boolean;
  search_terms: string[];
  personal_terms: string[] | null;
  effective_terms: string[];
  tracked_phones: string[];
  role: Role;
  permissions: Permissions;
};

export type Preferences = { direction: Direction; statuses: MatchStatus[] };

/** A buyer or listing card: one unique text, with every posting folded into it. */
export type Row = {
  id: string;
  entity_id?: string;
  public_id?: string;
  entity_status?: EntityStatus;
  document_type?: 'buyer_request' | 'property_listing';
  raw_text: string;
  normalized_text: string;
  contact_name?: string | null;
  contact_phone?: string | null;
  contact_phones?: string[];
  author?: string | null;
  chat_name?: string | null;
  transaction_type?: TransactionType;
  locations: string[];
  categories: string[];
  land_area_min?: number | null;
  land_area_max?: number | null;
  building_area_min?: number | null;
  building_area_max?: number | null;
  /** Price of the primary transaction (the sale price when transaction_type is 'both'). */
  price_min?: number | null;
  price_max?: number | null;
  price_basis?: PriceBasis | null;
  /** Rent price of a listing offered for sale AND rent. */
  rent_price_min?: number | null;
  rent_price_max?: number | null;
  rent_price_basis?: PriceBasis | null;
  /** Buyer accepts only the places in strict_locations ("Citraland only"). */
  location_strict?: boolean;
  strict_locations?: string[];
  /**
   * One WhatsApp message can hold several requests or offers; each becomes its own card.
   * `segment_index` is 0-based, `segment_count` is 1 for a single-context message, and `segment_text` is the
   * part's own text (null for a single-context message). `raw_text` always stays the full message.
   */
  segment_index?: number;
  segment_count?: number;
  segment_text?: string | null;
  sent_at?: string | null;
  last_seen_at?: string | null;
  hot_count?: number;
  warm_count?: number;
  custom_count?: number;
  match_count?: number;
  duplicate_count?: number;
  /** On a recommendation: the pair's score and stored status. Use matchStatusOf(row) to read the status. */
  score?: number;
  status?: Temperature;
  temperature?: Temperature;
  explanation?: string[];
  /** On a recommendation: whose buyer/listing it is relative to the source (the list's first sort key). */
  ownership_tier?: OwnershipTier;
};

export type Group = { source: Row; recommendations: Row[] };

export type RowGroupSummary = {
  key: string;
  count: number;
  hot: number;
  warm: number;
  custom?: number;
  latest_at: string | null;
  contact_name: string | null;
  sender_count: number;
  /** Clean display name for sender groups (raw WhatsApp author stays in `key`). */
  title?: string;
};

export type RecentMatch = {
  event_id: number;
  found_at: string;
  score: number;
  temperature: Temperature;
  import_id: string | null;
  agent_name: string | null;
  upgraded_to_hot: boolean;
  /** Ownership of the target relative to the source; may also arrive on `target.ownership_tier`. */
  ownership_tier?: OwnershipTier;
  target: Row;
};

export type RecentGroup = {
  source: Row;
  latest_found_at: string;
  pair_count: number;
  hot_count: number;
  warm_count: number;
  custom_count?: number;
  matches: RecentMatch[];
};

export type RecentResponse = {
  direction: Direction;
  date_from: string;
  date_to: string;
  totals: { pairs: number; hot: number; warm: number; custom?: number };
  groups: RecentGroup[];
  has_more: boolean;
};

export type RecentSummary = {
  unseen: number;
  unseen_hot: number;
  since?: string;
  last_import?: { import_id: string; found_at: string; total: number; hot: number; warm: number; custom?: number; agent_name: string | null; file_name: string | null } | null;
};

/**
 * Live progress of a background job (reprocess or upload), written by api/progress.py at most every 2 s.
 * `percent` is overall (0–100, stage-weighted); `eta_s` null = unknown; `stage` is `queued` before it starts and `done` after.
 */
export type JobProgress = {
  stage: string;
  stage_label: string;
  done: number;
  total: number;
  percent: number;
  eta_s: number | null;
  elapsed_s: number;
  started_at: string | null;
  rate?: number | null;
  /** Up-front estimate made when the job was queued (seconds). */
  estimate_s?: number;
  /** Learned on completion: seconds and item counts per stage. */
  durations?: Record<string, number>;
  items?: Record<string, number>;
};

export type ImportRow = {
  id: string;
  agent_name: string;
  file_name: string;
  matching_mode?: MatchingMode | null;
  status: ImportStatus;
  progress?: JobProgress | null;
  started_at?: string | null;
  total_messages: number;
  processed_messages: number;
  request_count: number;
  listing_count: number;
  ignored_count: number;
  duplicate_count: number;
  error?: string | null;
  created_at: string;
  finished_at?: string | null;
  new_matches: number;
  new_hot: number;
  new_warm: number;
  new_custom?: number;
};

export type StockTracked = {
  phone: string;
  label: string | null;
  contact_name: string | null;
  counts: { ready: number; on_hold: number; sold: number; deleted: number; total: number };
  new_7d: number;
  last_posted_at: string | null;
  history: { at: string; ready: number; total: number }[];
  tracked_since: string;
};

export type StockOverview = {
  tracked: StockTracked[];
  totals: { ready: number; on_hold: number; sold: number; deleted: number; total: number };
  last_logged_at: string | null;
  max_tracked: number;
};

/** POST /stock/tracked/parse: an uploaded CSV or Excel file as rows of text (nothing is saved). */
export type ParsedSheet = { name: string; rows: string[][]; total_rows: number };
export type ParsedSheetFile = { file_name: string; sheets: ParsedSheet[] };

export type StockLogRow = {
  id: number;
  phone: string;
  logged_at: string;
  event_type: 'import' | 'status' | 'manual' | 'tracking';
  import_id: string | null;
  agent_name: string | null;
  total: number;
  ready: number;
  on_hold: number;
  sold: number;
  deleted: number;
  delta_total: number;
  delta_ready: number;
  note: string | null;
  label: string | null;
};

export type TeamMember = {
  id: string;
  email: string;
  display_name: string;
  role: Role;
  is_locked: boolean;
  created_at: string;
  last_login_at?: string | null;
  must_change_password?: boolean;
  /** Effective grants (company admins: every feature). */
  features: Feature[];
  editable: boolean;
  is_self: boolean;
};

export type CompanyUser = { id: string; email: string; display_name: string; role: Role; is_locked: boolean; created_at: string; last_login_at?: string | null; must_change_password?: boolean };

export type CompanySummary = {
  company_id: string;
  name: string;
  company_name: string | null;
  owner_id: string | null;
  buyers: number;
  listings: number;
  search_locked: boolean;
  listing_group_by: GroupBy;
  status: CompanyStatus;
  registered_at: string | null;
  reviewed_at: string | null;
  contact_email: string | null;
  last_activity_at: string | null;
  /** Words & places the company has (for "copy from" choices). */
  glossary_entries?: number;
  clusters?: number | null;
  users: CompanyUser[];
};

/** POST /admin/companies/{id}/copy-knowledge */
export type CopyKnowledgeRequest = { source_company_id: string; glossary: boolean; locations: boolean; mode: 'merge' | 'replace' };
export type CopyKnowledgeResult = { glossary_added: number; glossary_total: number | null; clusters_added: number; links_added: number; clusters_total: number | null; mode: 'merge' | 'replace' };

/** POST /admin/companies/{id}/reset-password: the temporary password is shown once. */
export type PasswordReset = { user_id: string; email: string; display_name: string; password: string };

/** GET /admin/usage: per company, all-time and the last `days`. Costs are USD. */
export type CompanyUsage = {
  company_id: string; name: string; status: CompanyStatus; registered_at: string | null; contact_email: string | null;
  members: number; last_login_at: string | null; messages: number; buyers: number; listings: number;
  imports_total: number; imports_period: number; exports_period: number; logins_period: number; last_activity_at: string | null;
  ai_runs_total: number; ai_runs_period: number; ai_keys: number;
  tokens_in_total: number; tokens_out_total: number; cost_total: number; reviews_total: number;
  tokens_in_period: number; tokens_out_period: number; cost_period: number; reviews_period: number; cost_estimated: boolean;
};
export type UsageReport = {
  days: number; since: string; companies: CompanyUsage[];
  totals: Record<'tokens_in_total' | 'tokens_out_total' | 'cost_total' | 'reviews_total' | 'tokens_in_period' | 'tokens_out_period' | 'cost_period' | 'reviews_period' | 'imports_period' | 'exports_period' | 'logins_period', number>;
};
export type UsageMonthly = { from: string; rows: { company_id: string; name: string; month: string; tokens_in: number; tokens_out: number; cost: number; reviews: number }[] };

/** GET /admin/logs */
export type ActivityRow = {
  id: number; company_id: string | null; company_name: string | null; user_id: string | null; user_email: string | null; user_name: string | null;
  action: string; label: string; method: string | null; path: string | null; status: number | null; detail: Record<string, unknown>;
  duration_ms: number | null; ip: string | null; created_at: string;
};
export type ActivityLog = { rows: ActivityRow[]; total: number; limit: number; offset: number; actions: { id: string; label: string }[] };

export type DeployStatus = { enabled: false } | {
  enabled: true;
  status: 'idle' | 'running' | 'success' | 'failed';
  exit_code: number | null;
  started_at: string | null;
  finished_at: string | null;
  commit: { sha: string; subject: string; date: string } | null;
  log: string;
};

/** Period keys understood by GET /dashboard/overview. The UI default is 'last_week' (see DEFAULT_DASHBOARD_PERIOD). */
export type DashboardPeriodKey = 'week' | 'last_week' | 'month' | 'last_month' | 'last30' | 'custom';

export type Dashboard = {
  period: { key: string; label: string; date_from: string; date_to: string; compare_from: string; compare_to: string; days: number; bucket: 'day' | 'week'; in_future: boolean };
  latest_data_date: string | null;
  quick: { this_week: number; this_month: number };
  kpi: {
    buyers: number; listings: number; buyers_prev: number; listings_prev: number;
    buyers_change: number | null; listings_change: number | null;
    matches: number; matches_hot: number; matches_custom?: number; matches_prev: number; matches_change: number | null;
  };
  trend: { labels: string[]; buyers: number[]; listings: number[]; matches: number[]; matches_hot: number[]; matches_custom?: number[] };
  categories: { key: string; label: string; value: number }[];
  /** Ready listings per property type (older servers omit it). */
  stock_categories?: { key: string; label: string; value: number }[];
  transactions: { key: string; label: string; value: number }[];
  locations: { label: string; value: number; stock: number }[];
  /** `labels` = sale bands, `rent_labels` = yearly rent bands (older servers only send `labels`). */
  budget: { labels: string[]; rent_labels?: string[]; sale: number[]; rent: number[]; skipped: number };
  gap_categories: { key: string; label: string; buyers: number; listings: number; pressure: number }[];
  gap_locations: { key: string; label: string; buyers: number; listings: number; pressure: number }[];
  status: { buyer: Record<EntityStatus, number>; listing: Record<EntityStatus, number> };
  top_sales: { group_by: GroupBy; rows: { key: string; label: string; contact_name: string | null; value: number }[] };
  matches: { total: number; hot: number; warm: number; custom?: number };
  insights: string[];
};

export type Stats = {
  statistics_updated_at?: string;
  raw_messages: number;
  buyer_requests: number;
  listings: number;
  buyers_ready: number;
  listings_ready: number;
};

/**
 * The matching settings the UI edits (GET/PUT /settings; the response has more columns).
 * Hot: price `hot_price_floor_pct`–100 % of a single budget (50–100, default 80; a budget range counts as written)
 * and sizes inside the request. Warm: that Hot range widened by the Warm allowance `*_down_pct` below its floor and
 * `*_up_pct` above its ceiling (0–100 %, default 15). Nearby clusters within `location_radius_km` (0–4, 0 = same
 * cluster or area only) are Custom alternatives. The six weights add up to 100.
 */
export type MatchSettings = {
  hot_price_floor_pct: number;
  price_down_pct: number; price_up_pct: number;
  land_down_pct: number; land_up_pct: number;
  building_down_pct: number; building_up_pct: number;
  location_radius_km: number;
  location_weight_pct: number; land_weight_pct: number; building_weight_pct: number;
  price_weight_pct: number; semantic_weight_pct: number; data_quality_weight_pct: number;
};

/** Service areas per unit category (GET/PUT /settings `category_areas`): category id -> allowed cities; missing = anywhere. */
export type CategoryAreas = Record<string, string[]>;
export type SettingsExtras = { category_areas: CategoryAreas; service_cities: string[]; categories: { id: string; label: string }[] };

/* ----------------------------------------------------------- background updates */

/** A queued recompute ("maintenance job"). Never show the word "job" to customers: say "Updating results…". */
export type MaintenanceJob = {
  id: string;
  status: ImportStatus;
  result?: { documents?: number; matches?: number } | null;
  error?: string | null;
  created_at?: string;
  started_at?: string | null;
  finished_at?: string | null;
  progress?: JobProgress | null;
};

/**
 * GET /reprocess/status and POST /reprocess. Saving a setting only saves it: `pending` says matches
 * still follow the earlier settings (with short English `reasons`) until a company admin reprocesses.
 */
export type ReprocessStatus = {
  pending: boolean; since: string | null; reasons: string[]; job: MaintenanceJob | null;
  /** Analysis window (v5.1): messages from this ISO date on are analysed and matched; older ones are only stored. */
  analysis_from?: string;
  analysis_window_months?: number;
  /** `analysis_from` was pinned earlier than the rolling window by "Analyse older data". */
  analysis_pinned?: boolean;
  /** Some stored messages are older than `analysis_from`. */
  has_older_data?: boolean;
};

/* -------------------------------------------------------------------- glossary */

export type GlossaryKind = 'synonym' | 'location' | 'category';
/** One company glossary row. `term` = how it is written in chats, `canonical` = the standard name / place / category key. */
export type GlossaryEntry = { term: string; canonical: string; kind: GlossaryKind; parent: string | null; note: string | null };
/** Built-in, read-only entries (default synonyms and the Surabaya place hierarchy). */
export type GlossaryDefault = { term: string; canonical: string; kind: GlossaryKind; parent: string | null };
export type GlossaryCategory = { key: string; label: string };
/** GET /glossary/entries */
export type GlossaryResponse = { entries: GlossaryEntry[]; defaults: GlossaryDefault[]; categories: GlossaryCategory[] };
/** PUT /glossary/entries body. */
export type GlossarySaveRequest = { entries: GlossaryEntry[] };
/** PUT /glossary/entries response. Saving never rebuilds results (`job` stays null); `reprocess` is the new banner status. */
export type GlossarySaveResponse = { entries: GlossaryEntry[]; job: MaintenanceJob | null; changed?: boolean; reprocess?: ReprocessStatus | null };
/** GET /glossary/suggestions?limit=50 — frequent place phrases nobody recognises yet. */
export type GlossarySuggestion = { phrase: string; count: number; kind_hint: GlossaryKind; sample: string };
export type GlossarySuggestionsResponse = { items: GlossarySuggestion[] };

/** One built-in property term: how agents write it (`term`, `aliases`) and what it means, in English. */
export type PropertyTerm = { key: string; term: string; aliases: string[]; meaning: string };
/** A group of built-in terms (legal status, apartment unit types, towers, financing, floor level, other). */
export type PropertyTermGroup = { key: string; label: string; note?: string | null; terms: PropertyTerm[] };
/** GET /glossary/terms — read-only, the same for every company. */
export type PropertyTermsResponse = { groups: PropertyTermGroup[] };

/* -------------------------------------------------------------- location index */

export type LocationCluster = { id: string; name: string; aliases: string[]; area: string; neighbor_count?: number; distance_km?: number };
/** GET /location-index?search=&offset= */
export type LocationCatalog = {
  clusters: LocationCluster[]; has_more: boolean; total: number; pairs: number; filtered_total: number;
  revision?: string; updated_at?: string | null; sources: string[];
};
/** GET /location-index/neighbors?cluster= */
export type LocationNeighbors = { cluster: LocationCluster; neighbors: LocationCluster[] };
/** POST /location-index/preview */
export type LocationPreview = { clusters: number; pairs: number; new_clusters: number; new_pairs: number; new_aliases: number; revision: string; source_name?: string };
/** POST /location-index/clusters body: create, or rename/update when original_id is given. */
export type LocationClusterInput = { name: string; aliases: string[]; area: string; original_id?: string };
/**
 * Fields every location-index write returns. `unchanged` = the edit changed nothing (nothing is marked
 * out of date); `job` stays null because saving never rebuilds results; `reprocess` is the new banner status.
 */
export type LocationWriteMeta = { revision?: string; unchanged?: boolean; job?: MaintenanceJob | null; reprocess?: ReprocessStatus | null };
/** POST /location-index/clusters response; `previous_id` is set when a rename changed the cluster id. */
export type LocationClusterSaveResponse = LocationWriteMeta & { cluster: LocationCluster; previous_id?: string; revision: string; job: MaintenanceJob | null };
/** POST /location-index/aliases body; DELETE /location-index/aliases?cluster_id=&alias= */
export type LocationAliasInput = { cluster_id: string; alias: string };
/** POST /location-index/edges body (0 < distance_km ≤ 4); DELETE /location-index/edges?a=&b= */
export type LocationEdgeInput = { a: string; b: string; distance_km: number };
/**
 * Response of every location-index write: alias add/delete → `{cluster}`, edge save → `{edge}`, edge delete →
 * `{deleted: {a, b}}`, cluster delete → `{deleted, removed_pairs}`, plus LocationWriteMeta. Edits take an optional
 * `revision` (POST body / DELETE query) and answer 409 when it is stale.
 */
export type LocationWriteResponse = LocationWriteMeta & { [key: string]: unknown };
/** GET /location-index/areas — `clusters` is the number of clusters in that area/development. */
export type LocationArea = { area: string; clusters: number };
export type LocationAreasResponse = { areas: LocationArea[] };

/* ------------------------------------------------------------------ export all */

/** One sender or sales-phone group in POST /export/all/plan. `key` is raw (send it back as group_keys), `title` is for display. */
export type ExportPlanGroup = {
  key: string; title: string; sources: number; contact_name?: string; phone?: string;
  hot: number; warm: number; custom?: number; unmatched: number; pages: number;
  /** Cover pages of this group: one per section. */
  covers?: number;
  /** The directions exported for this group, in file order (Listing → Buyer first in a combined export). */
  sections?: ExportPlanSection[];
};
/** One direction of one group: its own cover, then its pair pages. */
export type ExportPlanSection = {
  direction: Direction; sources: number; hot: number; warm: number; custom?: number; unmatched: number; pages: number;
};
export type ExportPlan = {
  token: string; group_by: GroupBy; groups: ExportPlanGroup[];
  /** Both directions per person (POST /export/all/plan with `combined: true`). */
  combined?: boolean;
  totals: { sources: number; hot: number; warm: number; custom?: number; unmatched: number; pages: number; covers?: number };
  max_pages: number;
};
/** POST /export/all/part answer: pages written, pair pages among them (for the next `first_pair`), next slice. */
export type ExportPartResult = { pages: number; pairs?: number; next_offset: number | null };
/** Extra filter for /export/all/plan and /part: only these group keys (max 500). Omit for every group. */
export type ExportGroupSelection = { group_keys?: string[] };

/* ------------------------------------------------------------------ AI Review (AI reviewer, super admins) */

/** An OpenRouter key of the company. The key itself never reaches the browser. */
export type AiKey = {
  id: string; label: string; provider: 'openrouter'; last4: string; masked: string; is_default: boolean;
  created_at: string; last_used_at: string | null; created_by_name: string | null;
};
/** USD per token (OpenRouter pricing); null when the price depends on the routed model. */
export type AiPricing = { prompt: number; completion: number };
export type AiModel = {
  id: string; name: string; context_length: number | null; pricing: AiPricing | null; price_varies: boolean;
  json_mode: boolean | null; preset?: boolean; listed?: boolean; default?: boolean;
  /** 'decision': answers typed yes/no questions with probabilities (OpenRouter Decisions API); 'text': chat completions. */
  kind?: 'decision' | 'text';
};
export type AiModels = { presets: AiModel[]; models: AiModel[]; default_model: string; last_model: string; error: string | null };
export type JevDirections = 'both' | 'property' | 'buyer';
export type JevDecision = 'recommend' | 'reject' | 'unsure';
export type JevScope = {
  source?: 'recent' | 'match'; found_from?: string; found_to?: string; custom_prompt?: string; prompt_id?: string;
  model: string; key_id?: string; directions: JevDirections; statuses: Temperature[]; group_keys?: string[]; max_pairs: number;
  buyer_date_from: string; buyer_date_to: string; listing_date_from: string; listing_date_to: string;
};
export type JevEstimate = {
  model: string; model_label: string; group_by: GroupBy; pairs: number; available: number; capped: boolean; scan_truncated: boolean;
  unique_inputs: number; cached: number; to_call: number; tokens_in: number; tokens_out: number;
  cost: number | null; price_varies: boolean; pricing: AiPricing | null;
  by_status: Record<Temperature, number>; by_direction: Record<Direction, number>;
  groups: { key: string; title: string; pairs: number }[];
};
export type JevRunStatus = 'queued' | 'running' | 'completed' | 'failed' | 'cancelled';
export type JevRun = {
  id: string; model: string; model_label: string; prompt_version: string; group_by: GroupBy; status: JevRunStatus; error: string | null;
  created_at: string; started_at: string | null; finished_at: string | null; key_id: string | null; key_label: string | null;
  key_masked: string | null; created_by_name: string | null;
  payload: Partial<JevScope> & { model_label?: string; system_prompt?: string; decision_questions?: Record<string, unknown> }; estimate: Partial<JevEstimate>;
  total: number; done: number; failed: number; pending: number; cached: number; recommend: number; reject: number; unsure: number;
  overrides: number; cost: number; cost_estimated: boolean; tokens_in: number; tokens_out: number;
};
export type JevReview = {
  id: number; position: number; direction: Direction; source_entity_id: string | null; target_entity_id: string | null;
  buyer_entity_id: string | null; listing_entity_id: string | null; source_public_id: string | null; target_public_id: string | null;
  group_key: string; group_title: string | null; engine_status: Temperature; engine_score: number | null; engine_explanation: string[];
  buyer_text: string; listing_text: string; status: 'pending' | 'done' | 'failed'; cached: boolean;
  decision: JevDecision | null; effective_decision: JevDecision | null; confidence: number | null; reasons: string[];
  evidence: { source?: string[]; target?: string[] }; issues: string[]; tokens_in: number | null; tokens_out: number | null;
  cost: number | null; cost_estimated: boolean; error: string | null; override_decision: JevDecision | null;
  override_at: string | null; override_by_name: string | null; model: string; reviewed_at: string | null;
};
export type JevResultGroup = {
  key: string; title: string | null; items: JevReview[];
  counts: Record<JevDecision | 'failed' | 'pending', number>;
};
export type JevResults = { run: JevRun; groups: JevResultGroup[]; total: number };
/** POST /ai/reviews/lookup: the latest review of a pair, for the Jev badge on Match cards. */
export type JevLookup = {
  id: number; run_id: string; buyer_entity_id: string; listing_entity_id: string; decision: JevDecision;
  override_decision: JevDecision | null; effective_decision: JevDecision; confidence: number | null; reasons: string[];
  model: string; model_label: string; reviewed_at: string;
};
