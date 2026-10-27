CREATE SCHEMA IF NOT EXISTS xm;

CREATE TABLE IF NOT EXISTS xm.users (
    id uuid PRIMARY KEY,
    email text NOT NULL UNIQUE,
    display_name text NOT NULL,
    password_hash text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS xm.sessions (
    id uuid PRIMARY KEY,
    user_id uuid NOT NULL REFERENCES xm.users(id) ON DELETE CASCADE,
    token_hash text NOT NULL UNIQUE,
    expires_at timestamptz NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS sessions_expiry_idx ON xm.sessions(expires_at);

CREATE TABLE IF NOT EXISTS xm.user_preferences (
    user_id uuid PRIMARY KEY REFERENCES xm.users(id) ON DELETE CASCADE,
    preferences jsonb NOT NULL DEFAULT '{}'::jsonb,
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS xm.imports (
    id uuid PRIMARY KEY,
    company_id text NOT NULL DEFAULT 'xm',
    agent_name text NOT NULL,
    file_name text NOT NULL,
    file_path text NOT NULL,
    file_sha256 text NOT NULL,
    status text NOT NULL DEFAULT 'queued',
    total_messages integer NOT NULL DEFAULT 0,
    processed_messages integer NOT NULL DEFAULT 0,
    request_count integer NOT NULL DEFAULT 0,
    listing_count integer NOT NULL DEFAULT 0,
    ignored_count integer NOT NULL DEFAULT 0,
    duplicate_count integer NOT NULL DEFAULT 0,
    qdrant_points integer NOT NULL DEFAULT 0,
    error text,
    started_at timestamptz,
    finished_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (company_id, agent_name, file_sha256)
);

CREATE TABLE IF NOT EXISTS xm.raw_messages (
    id uuid PRIMARY KEY,
    company_id text NOT NULL DEFAULT 'xm',
    agent_name text NOT NULL,
    import_id uuid NOT NULL REFERENCES xm.imports(id) ON DELETE CASCADE,
    chat_id text NOT NULL,
    chat_name text NOT NULL,
    is_group boolean NOT NULL DEFAULT false,
    message_position integer NOT NULL,
    sent_at timestamp,
    author text,
    raw_text text NOT NULL,
    message_hash text NOT NULL,
    classification text NOT NULL,
    confidence numeric(5,4) NOT NULL DEFAULT 0,
    duplicate_of uuid,
    extracted jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE(import_id, chat_id, message_position)
);

CREATE INDEX IF NOT EXISTS raw_messages_import_idx ON xm.raw_messages(import_id);
CREATE INDEX IF NOT EXISTS raw_messages_classification_idx ON xm.raw_messages(company_id, classification);
CREATE INDEX IF NOT EXISTS raw_messages_hash_idx ON xm.raw_messages(company_id, message_hash);
CREATE INDEX IF NOT EXISTS raw_messages_text_group_idx ON xm.raw_messages(company_id, md5(raw_text));

CREATE TABLE IF NOT EXISTS xm.documents (
    id uuid PRIMARY KEY,
    company_id text NOT NULL DEFAULT 'xm',
    agent_name text NOT NULL,
    raw_message_id uuid NOT NULL REFERENCES xm.raw_messages(id) ON DELETE CASCADE,
    import_id uuid NOT NULL REFERENCES xm.imports(id) ON DELETE CASCADE,
    document_type text NOT NULL CHECK (document_type IN ('buyer_request', 'property_listing')),
    transaction_type text NOT NULL DEFAULT 'unknown',
    categories text[] NOT NULL DEFAULT '{}',
    primary_category text,
    locations text[] NOT NULL DEFAULT '{}',
    land_area_min numeric,
    land_area_max numeric,
    building_area_min numeric,
    building_area_max numeric,
    price_min bigint,
    price_max bigint,
    price_basis text,
    negotiable boolean NOT NULL DEFAULT false,
    facing text[] NOT NULL DEFAULT '{}',
    exclusions text[] NOT NULL DEFAULT '{}',
    requirements text[] NOT NULL DEFAULT '{}',
    contact_name text,
    contact_phone text,
    normalized_text text NOT NULL,
    extraction_confidence numeric(5,4) NOT NULL DEFAULT 0,
    review_status text NOT NULL DEFAULT 'auto',
    active boolean NOT NULL DEFAULT true,
    qdrant_point_id uuid,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS documents_type_idx ON xm.documents(company_id, document_type, active);
CREATE INDEX IF NOT EXISTS documents_category_idx ON xm.documents USING gin(categories);
CREATE INDEX IF NOT EXISTS documents_locations_idx ON xm.documents USING gin(locations);
CREATE INDEX IF NOT EXISTS documents_import_idx ON xm.documents(import_id);

CREATE OR REPLACE VIEW xm.buyer_requests AS
SELECT * FROM xm.documents WHERE document_type = 'buyer_request';

CREATE OR REPLACE VIEW xm.property_listings AS
SELECT * FROM xm.documents WHERE document_type = 'property_listing';

CREATE TABLE IF NOT EXISTS xm.match_settings (
    company_id text PRIMARY KEY,
    land_tolerance_pct numeric NOT NULL DEFAULT 10,
    building_tolerance_pct numeric NOT NULL DEFAULT 20,
    price_tolerance_pct numeric NOT NULL DEFAULT 10,
    location_radius_km numeric NOT NULL DEFAULT 3,
    location_extended_radius_km numeric NOT NULL DEFAULT 5,
    location_weight_pct numeric NOT NULL DEFAULT 35,
    land_weight_pct numeric NOT NULL DEFAULT 20,
    building_weight_pct numeric NOT NULL DEFAULT 15,
    price_weight_pct numeric NOT NULL DEFAULT 20,
    semantic_weight_pct numeric NOT NULL DEFAULT 5,
    data_quality_weight_pct numeric NOT NULL DEFAULT 5,
    updated_at timestamptz NOT NULL DEFAULT now()
);

INSERT INTO xm.match_settings(company_id)
VALUES ('xm') ON CONFLICT (company_id) DO NOTHING;

CREATE TABLE IF NOT EXISTS xm.matches (
    id uuid PRIMARY KEY,
    company_id text NOT NULL DEFAULT 'xm',
    buyer_request_id uuid NOT NULL REFERENCES xm.documents(id) ON DELETE CASCADE,
    property_listing_id uuid NOT NULL REFERENCES xm.documents(id) ON DELETE CASCADE,
    score numeric(6,2) NOT NULL,
    location_score numeric(6,2) NOT NULL DEFAULT 0,
    land_score numeric(6,2) NOT NULL DEFAULT 0,
    building_score numeric(6,2) NOT NULL DEFAULT 0,
    price_score numeric(6,2) NOT NULL DEFAULT 0,
    semantic_score numeric(6,2) NOT NULL DEFAULT 0,
    explanation jsonb NOT NULL DEFAULT '[]'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE(company_id, buyer_request_id, property_listing_id)
);

CREATE INDEX IF NOT EXISTS matches_buyer_score_idx ON xm.matches(company_id, buyer_request_id, score DESC);
CREATE INDEX IF NOT EXISTS matches_score_idx ON xm.matches(company_id, score DESC);
CREATE INDEX IF NOT EXISTS matches_listing_idx ON xm.matches(property_listing_id);

CREATE TABLE IF NOT EXISTS xm.audit_events (
    id bigserial PRIMARY KEY,
    company_id text NOT NULL DEFAULT 'xm',
    event_type text NOT NULL,
    entity_type text,
    entity_id text,
    details jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS xm.glossary (
  alias text PRIMARY KEY, canonical text NOT NULL
);
CREATE TABLE IF NOT EXISTS xm.location_indexes (
 company_id text PRIMARY KEY,
 data jsonb NOT NULL DEFAULT '{"clusters":[],"edges":[]}'::jsonb,
 sources jsonb NOT NULL DEFAULT '[]'::jsonb,
 updated_at timestamptz NOT NULL DEFAULT now()
);
ALTER TABLE xm.glossary ADD COLUMN IF NOT EXISTS company_id text NOT NULL DEFAULT 'xm';
DO $$ BEGIN
 IF EXISTS (SELECT 1 FROM pg_constraint WHERE conrelid='xm.glossary'::regclass AND contype='p' AND array_length(conkey,1)=1) THEN
  ALTER TABLE xm.glossary DROP CONSTRAINT glossary_pkey;
  ALTER TABLE xm.glossary ADD PRIMARY KEY(company_id,alias);
 END IF;
END $$;
INSERT INTO xm.glossary(company_id, alias, canonical) VALUES
('xm','regensi','regency'),('xm','rgcy','regency'),('xm','nashos','national hospital'),('xm','nathos','national hospital')
ON CONFLICT DO NOTHING;
CREATE TABLE IF NOT EXISTS xm.maintenance_jobs (
 id uuid PRIMARY KEY, status text NOT NULL DEFAULT 'queued', result jsonb, error text,
 created_at timestamptz NOT NULL DEFAULT now(), finished_at timestamptz
);
ALTER TABLE xm.maintenance_jobs ADD COLUMN IF NOT EXISTS company_id text NOT NULL DEFAULT 'xm';
DROP INDEX IF EXISTS xm.maintenance_one_running;
CREATE UNIQUE INDEX IF NOT EXISTS maintenance_one_running_per_workspace ON xm.maintenance_jobs(company_id) WHERE status IN ('queued','processing');

ALTER TABLE xm.documents ADD COLUMN IF NOT EXISTS contact_phones text[] NOT NULL DEFAULT '{}';
CREATE INDEX IF NOT EXISTS documents_recent_idx ON xm.documents(company_id,created_at DESC) WHERE active;

ALTER TABLE xm.match_settings ADD COLUMN IF NOT EXISTS location_weight_pct numeric NOT NULL DEFAULT 35;
ALTER TABLE xm.match_settings ADD COLUMN IF NOT EXISTS land_weight_pct numeric NOT NULL DEFAULT 20;
ALTER TABLE xm.match_settings ADD COLUMN IF NOT EXISTS building_weight_pct numeric NOT NULL DEFAULT 15;
ALTER TABLE xm.match_settings ADD COLUMN IF NOT EXISTS price_weight_pct numeric NOT NULL DEFAULT 20;
ALTER TABLE xm.match_settings ADD COLUMN IF NOT EXISTS semantic_weight_pct numeric NOT NULL DEFAULT 5;
ALTER TABLE xm.match_settings ADD COLUMN IF NOT EXISTS data_quality_weight_pct numeric NOT NULL DEFAULT 5;

CREATE TABLE IF NOT EXISTS xm.document_groups (
 group_id uuid PRIMARY KEY, company_id text NOT NULL, document_type text NOT NULL,
 duplicate_count bigint NOT NULL, last_seen_at timestamp,
 hot_count bigint NOT NULL DEFAULT 0,warm_count bigint NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS document_groups_kind_idx ON xm.document_groups(company_id,document_type,last_seen_at DESC);
CREATE TABLE IF NOT EXISTS xm.document_group_members (
 document_id uuid PRIMARY KEY,group_id uuid NOT NULL,company_id text NOT NULL
);
CREATE INDEX IF NOT EXISTS group_members_group_idx ON xm.document_group_members(group_id);
CREATE TABLE IF NOT EXISTS xm.group_matches (
 company_id text NOT NULL,buyer_group_id uuid NOT NULL,property_group_id uuid NOT NULL,
 match_id uuid NOT NULL,score numeric NOT NULL,
 PRIMARY KEY(buyer_group_id,property_group_id)
);
CREATE INDEX IF NOT EXISTS group_matches_property_idx ON xm.group_matches(property_group_id,score DESC);
CREATE TABLE IF NOT EXISTS xm.workspace_cache_state(company_id text PRIMARY KEY,refreshed_at timestamptz NOT NULL);


ALTER TABLE xm.users ADD COLUMN IF NOT EXISTS role text NOT NULL DEFAULT 'user' CHECK (role IN ('admin', 'user'));
ALTER TABLE xm.users ADD COLUMN IF NOT EXISTS is_locked boolean NOT NULL DEFAULT false;
CREATE TABLE IF NOT EXISTS xm.app_preferences (
 company_id text PRIMARY KEY,
 search_terms text[] NOT NULL DEFAULT ARRAY['XM Darmo'],
 updated_at timestamptz NOT NULL DEFAULT now()
);
INSERT INTO xm.app_preferences(company_id, search_terms)
SELECT 'xm', ARRAY[coalesce((SELECT nullif(trim(p.preferences->>'default_search'),'')
 FROM xm.user_preferences p JOIN xm.users u ON u.id=p.user_id
 WHERE u.email='admin@autoaudit.id'), 'XM Darmo')]
ON CONFLICT (company_id) DO NOTHING;


-- Preserve legacy rows under 'xm' (admin); new inserts inherit the trusted scope.
ALTER TABLE xm.users ADD COLUMN IF NOT EXISTS workspace_id text UNIQUE;
ALTER TABLE xm.imports ALTER COLUMN company_id SET DEFAULT current_setting('xm.workspace_id');
ALTER TABLE xm.raw_messages ALTER COLUMN company_id SET DEFAULT current_setting('xm.workspace_id');
ALTER TABLE xm.documents ALTER COLUMN company_id SET DEFAULT current_setting('xm.workspace_id');
ALTER TABLE xm.matches ALTER COLUMN company_id SET DEFAULT current_setting('xm.workspace_id');
ALTER TABLE xm.audit_events ALTER COLUMN company_id SET DEFAULT current_setting('xm.workspace_id');
ALTER TABLE xm.glossary ALTER COLUMN company_id SET DEFAULT current_setting('xm.workspace_id');
ALTER TABLE xm.maintenance_jobs ALTER COLUMN company_id SET DEFAULT current_setting('xm.workspace_id');
CREATE INDEX IF NOT EXISTS maintenance_workspace_recent_idx ON xm.maintenance_jobs(company_id,created_at DESC);


-- ============================================================================
-- v4.0: companies with several accounts, public IDs, listing/buyer status,
-- recent-match history, tracked-sales stock log and company settings.
-- Everything below is idempotent and DDL-only (data backfill lives in
-- entities.migrate_v4 so that startup stays fast and restartable).
-- ============================================================================

-- Several accounts may share one company workspace. Roles: 'admin' (platform
-- administrator), 'company_admin' (super admin of one company), 'user' (member).
ALTER TABLE xm.users DROP CONSTRAINT IF EXISTS users_workspace_id_key;
CREATE INDEX IF NOT EXISTS users_workspace_idx ON xm.users(workspace_id);
DO $$ BEGIN
 IF EXISTS (SELECT 1 FROM pg_constraint WHERE conrelid='xm.users'::regclass AND conname='users_role_check'
            AND pg_get_constraintdef(oid) NOT LIKE '%company_admin%') THEN
  ALTER TABLE xm.users DROP CONSTRAINT users_role_check;
 END IF;
 IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conrelid='xm.users'::regclass AND conname='users_role_check') THEN
  ALTER TABLE xm.users ADD CONSTRAINT users_role_check CHECK (role IN ('admin','company_admin','user'));
 END IF;
END $$;

-- Company-level settings live in the existing per-workspace preferences row.
ALTER TABLE xm.app_preferences ADD COLUMN IF NOT EXISTS company_name text;
ALTER TABLE xm.app_preferences ADD COLUMN IF NOT EXISTS search_locked boolean NOT NULL DEFAULT false;
ALTER TABLE xm.app_preferences ADD COLUMN IF NOT EXISTS listing_group_by text NOT NULL DEFAULT 'sender';
ALTER TABLE xm.app_preferences ADD COLUMN IF NOT EXISTS matching_mode text NOT NULL DEFAULT 'company'
 CHECK (matching_mode IN ('company','sales'));
-- Null on historical imports means use the current company mode.
ALTER TABLE xm.imports ADD COLUMN IF NOT EXISTS matching_mode text
 CHECK (matching_mode IN ('company','sales'));
DO $$ BEGIN
 IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conrelid='xm.app_preferences'::regclass AND conname='app_preferences_group_by_check') THEN
  ALTER TABLE xm.app_preferences ADD CONSTRAINT app_preferences_group_by_check CHECK (listing_group_by IN ('sender','phone'));
 END IF;
END $$;

-- Human-friendly public IDs: L-AB908 / B-AB908. Letters skip I and O so they
-- cannot be confused with 1 and 0; the letter block grows (AA..ZZ, AAA..ZZZ,
-- ...) while the three digits roll over, so the sequence never runs out.
CREATE OR REPLACE FUNCTION xm.public_id(prefix text, seq bigint) RETURNS text
LANGUAGE plpgsql IMMUTABLE AS $$
DECLARE
  alphabet constant text := 'ABCDEFGHJKLMNPQRSTUVWXYZ';
  base constant bigint := 24;
  blk bigint := seq / 1000;
  digits int := (seq % 1000)::int;
  width int := 2;
  span bigint := 576;
  letters text := '';
BEGIN
  WHILE blk >= span LOOP
    blk := blk - span;
    width := width + 1;
    span := span * base;
  END LOOP;
  FOR i IN 1..width LOOP
    letters := substr(alphabet, (blk % base)::int + 1, 1) || letters;
    blk := blk / base;
  END LOOP;
  RETURN prefix || '-' || letters || lpad(digits::text, 3, '0');
END $$;

CREATE TABLE IF NOT EXISTS xm.entity_counters (
 company_id text NOT NULL,
 document_type text NOT NULL,
 next_seq bigint NOT NULL DEFAULT 0,
 PRIMARY KEY (company_id, document_type)
);

-- One entity = one unique complete message text per company and kind. It is
-- what a card represents in the UI and what keeps its ID and status when the
-- matching tables are rebuilt.
CREATE TABLE IF NOT EXISTS xm.entities (
 entity_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
 company_id text NOT NULL DEFAULT current_setting('xm.workspace_id'),
 document_type text NOT NULL CHECK (document_type IN ('buyer_request','property_listing')),
 text_hash text NOT NULL,
 seq bigint NOT NULL,
 public_id text NOT NULL,
 status text NOT NULL DEFAULT 'ready' CHECK (status IN ('ready','on_hold','sold','deleted')),
 status_note text,
 status_changed_at timestamptz,
 status_changed_by uuid,
 first_seen_at timestamp,
 created_at timestamptz NOT NULL DEFAULT now(),
 UNIQUE (company_id, document_type, text_hash),
 UNIQUE (company_id, document_type, seq),
 UNIQUE (company_id, public_id)
);
CREATE INDEX IF NOT EXISTS entities_status_idx ON xm.entities(company_id, document_type, status);

ALTER TABLE xm.documents ADD COLUMN IF NOT EXISTS entity_id uuid;
CREATE INDEX IF NOT EXISTS documents_entity_idx ON xm.documents(entity_id);
CREATE INDEX IF NOT EXISTS documents_unassigned_idx ON xm.documents(company_id) WHERE entity_id IS NULL AND active;
CREATE INDEX IF NOT EXISTS documents_contact_phones_idx ON xm.documents USING gin(contact_phones);
CREATE INDEX IF NOT EXISTS documents_contact_phone_idx ON xm.documents(company_id, contact_phone) WHERE contact_phone IS NOT NULL;

ALTER TABLE xm.document_groups ADD COLUMN IF NOT EXISTS entity_id uuid;
ALTER TABLE xm.document_groups ADD COLUMN IF NOT EXISTS public_id text;
ALTER TABLE xm.document_groups ADD COLUMN IF NOT EXISTS status text NOT NULL DEFAULT 'ready';
CREATE INDEX IF NOT EXISTS document_groups_status_idx ON xm.document_groups(company_id, document_type, status, last_seen_at DESC);
CREATE INDEX IF NOT EXISTS document_groups_entity_idx ON xm.document_groups(entity_id);
CREATE INDEX IF NOT EXISTS document_groups_public_idx ON xm.document_groups(company_id, public_id);

-- History of buyer/listing pairs. 'import' rows are what "Match terbaru" shows;
-- 'baseline' marks pairs that already existed before this feature or before a
-- settings-driven recompute, so they are never announced as new.
CREATE TABLE IF NOT EXISTS xm.match_events (
 id bigserial PRIMARY KEY,
 company_id text NOT NULL DEFAULT current_setting('xm.workspace_id'),
 buyer_entity uuid NOT NULL,
 listing_entity uuid NOT NULL,
 first_score numeric(6,2) NOT NULL,
 last_score numeric(6,2) NOT NULL,
 temperature text NOT NULL CHECK (temperature IN ('hot','warm')),
 source text NOT NULL CHECK (source IN ('import','recompute','baseline')),
 import_id uuid,
 agent_name text,
 active boolean NOT NULL DEFAULT true,
 found_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 hot_at timestamptz,
 last_seen_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 UNIQUE (company_id, buyer_entity, listing_entity)
);
CREATE INDEX IF NOT EXISTS match_events_found_idx ON xm.match_events(company_id, found_at DESC) WHERE source='import';
CREATE INDEX IF NOT EXISTS match_events_buyer_idx ON xm.match_events(company_id, buyer_entity);
CREATE INDEX IF NOT EXISTS match_events_listing_idx ON xm.match_events(company_id, listing_entity);
CREATE INDEX IF NOT EXISTS match_events_import_idx ON xm.match_events(import_id) WHERE import_id IS NOT NULL;

-- Sales phone numbers a company wants to monitor, and the automatic stock log.
CREATE TABLE IF NOT EXISTS xm.tracked_sales (
 company_id text NOT NULL DEFAULT current_setting('xm.workspace_id'),
 phone text NOT NULL,
 label text,
 created_at timestamptz NOT NULL DEFAULT now(),
 created_by uuid,
 PRIMARY KEY (company_id, phone)
);
CREATE TABLE IF NOT EXISTS xm.stock_log (
 id bigserial PRIMARY KEY,
 company_id text NOT NULL DEFAULT current_setting('xm.workspace_id'),
 phone text NOT NULL,
 logged_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 event_type text NOT NULL CHECK (event_type IN ('import','status','manual','tracking')),
 import_id uuid,
 agent_name text,
 total integer NOT NULL,
 ready integer NOT NULL,
 on_hold integer NOT NULL,
 sold integer NOT NULL,
 deleted integer NOT NULL,
 delta_total integer NOT NULL DEFAULT 0,
 delta_ready integer NOT NULL DEFAULT 0,
 note text
);
CREATE INDEX IF NOT EXISTS stock_log_phone_idx ON xm.stock_log(company_id, phone, logged_at DESC);
CREATE INDEX IF NOT EXISTS stock_log_time_idx ON xm.stock_log(company_id, logged_at DESC);


-- Backend-only workflow integration. Raw API keys are never stored.
CREATE TABLE IF NOT EXISTS xm.integration_keys (
 id uuid PRIMARY KEY,
 company_id text NOT NULL,
 created_by uuid NOT NULL REFERENCES xm.users(id) ON DELETE CASCADE,
 name text NOT NULL,
 token_hash text NOT NULL UNIQUE,
 created_at timestamptz NOT NULL DEFAULT now(),
 last_used_at timestamptz,
 revoked_at timestamptz
);
ALTER TABLE xm.integration_keys ADD COLUMN IF NOT EXISTS scope text NOT NULL DEFAULT 'property:export';
CREATE INDEX IF NOT EXISTS integration_keys_company_idx ON xm.integration_keys(company_id);
CREATE TABLE IF NOT EXISTS xm.export_jobs (
 id uuid PRIMARY KEY,
 company_id text NOT NULL,
 request_id text NOT NULL,
 payload jsonb NOT NULL,
 status text NOT NULL DEFAULT 'queued' CHECK (status IN ('queued','processing','completed','failed','cancelled','expired')),
 progress jsonb NOT NULL DEFAULT '{}'::jsonb,
 files jsonb NOT NULL DEFAULT '[]'::jsonb,
 error text,
 created_at timestamptz NOT NULL DEFAULT now(),
 started_at timestamptz,
 finished_at timestamptz,
 expires_at timestamptz,
 UNIQUE(company_id, request_id)
);
CREATE INDEX IF NOT EXISTS export_jobs_pending_idx ON xm.export_jobs(created_at) WHERE status IN ('queued','processing');
CREATE INDEX IF NOT EXISTS export_jobs_company_idx ON xm.export_jobs(company_id, created_at DESC);

-- Delivery receipts survive cache rebuilds, reposts and PDF retention cleanup.
CREATE TABLE IF NOT EXISTS xm.match_deliveries (
 company_id text NOT NULL,
 delivery_scope text NOT NULL,
 buyer_entity_id uuid NOT NULL REFERENCES xm.entities(entity_id) ON DELETE CASCADE,
 listing_entity_id uuid NOT NULL REFERENCES xm.entities(entity_id) ON DELETE CASCADE,
 delivered_at timestamptz NOT NULL DEFAULT now(),
 delivery_id text NOT NULL,
 PRIMARY KEY(company_id, delivery_scope, buyer_entity_id, listing_entity_id)
);

-- v4.1 matching strategy -------------------------------------------------------
-- Listings that are offered for sale AND rent keep the rent price separately;
-- price_* always holds the price of the primary transaction (sale for 'both').
ALTER TABLE xm.documents ADD COLUMN IF NOT EXISTS rent_price_min bigint;
ALTER TABLE xm.documents ADD COLUMN IF NOT EXISTS rent_price_max bigint;
ALTER TABLE xm.documents ADD COLUMN IF NOT EXISTS rent_price_basis text;
-- Buyers who accept only the named area ("Citraland only", "selain itu tidak mau").
ALTER TABLE xm.documents ADD COLUMN IF NOT EXISTS location_strict boolean NOT NULL DEFAULT false;
ALTER TABLE xm.documents ADD COLUMN IF NOT EXISTS strict_locations text[] NOT NULL DEFAULT '{}';

-- Match status is stored, not derived from the score alone: 'custom' marks pairs
-- that pass every other check but cannot prove a strict buyer location.
ALTER TABLE xm.matches ADD COLUMN IF NOT EXISTS status text NOT NULL DEFAULT 'warm';
ALTER TABLE xm.group_matches ADD COLUMN IF NOT EXISTS status text NOT NULL DEFAULT 'warm';
ALTER TABLE xm.match_events ADD COLUMN IF NOT EXISTS last_status text NOT NULL DEFAULT 'warm';
UPDATE xm.matches SET status='hot' WHERE score>=80 AND status<>'hot';
UPDATE xm.group_matches SET status='hot' WHERE score>=80 AND status<>'hot';
UPDATE xm.match_events SET last_status='hot' WHERE last_score>=80 AND last_status<>'hot';
DO $$ BEGIN
 IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname='matches_status_check') THEN
  ALTER TABLE xm.matches ADD CONSTRAINT matches_status_check CHECK (status IN ('hot','warm','custom'));
 END IF;
 IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname='group_matches_status_check') THEN
  ALTER TABLE xm.group_matches ADD CONSTRAINT group_matches_status_check CHECK (status IN ('hot','warm','custom'));
 END IF;
 IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname='match_events_last_status_check') THEN
  ALTER TABLE xm.match_events ADD CONSTRAINT match_events_last_status_check CHECK (last_status IN ('hot','warm','custom'));
 END IF;
 IF EXISTS (SELECT 1 FROM pg_constraint WHERE conname='match_events_temperature_check') THEN
  ALTER TABLE xm.match_events DROP CONSTRAINT match_events_temperature_check;
  ALTER TABLE xm.match_events ADD CONSTRAINT match_events_temperature_check CHECK (temperature IN ('hot','warm','custom'));
 END IF;
END $$;

-- Structured glossary: 'synonym' rewrites text, 'location' names a place
-- (optionally inside a parent area), 'category' maps a word to an asset type.
ALTER TABLE xm.glossary ADD COLUMN IF NOT EXISTS kind text NOT NULL DEFAULT 'synonym';
ALTER TABLE xm.glossary ADD COLUMN IF NOT EXISTS parent text;
ALTER TABLE xm.glossary ADD COLUMN IF NOT EXISTS note text;
DO $$ BEGIN
 IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname='glossary_kind_check') THEN
  ALTER TABLE xm.glossary ADD CONSTRAINT glossary_kind_check CHECK (kind IN ('synonym','location','category'));
 END IF;
END $$;
UPDATE xm.glossary SET kind='location' WHERE kind='synonym' AND canonical IN ('regency','national hospital');

-- Saving settings no longer reprocesses immediately: changes are collected as
-- "pending" until a company admin presses Reprocess. Uploads still process.
ALTER TABLE xm.app_preferences ADD COLUMN IF NOT EXISTS reprocess_pending_since timestamptz;
ALTER TABLE xm.app_preferences ADD COLUMN IF NOT EXISTS reprocess_reasons jsonb NOT NULL DEFAULT '[]'::jsonb;
ALTER TABLE xm.document_groups ADD COLUMN IF NOT EXISTS custom_count bigint NOT NULL DEFAULT 0;

-- v4.2: one WhatsApp bubble can hold several requests/listings ("#Buyer 1 … #Renter 1",
-- numbered offers). Each part is its own document; segment_text is that part (NULL = whole bubble).
ALTER TABLE xm.documents ADD COLUMN IF NOT EXISTS segment_index integer NOT NULL DEFAULT 0;
ALTER TABLE xm.documents ADD COLUMN IF NOT EXISTS segment_count integer NOT NULL DEFAULT 1;
ALTER TABLE xm.documents ADD COLUMN IF NOT EXISTS segment_text text;
CREATE UNIQUE INDEX IF NOT EXISTS documents_raw_segment_idx ON xm.documents(raw_message_id, segment_index);
-- Price band: a listing may cost at most budget × (1 + price_tolerance_pct) and at least
-- budget × price_floor_pct (when the buyer gives a single budget). 2 M → 1 M … 2.3 M.
ALTER TABLE xm.match_settings ADD COLUMN IF NOT EXISTS price_floor_pct numeric NOT NULL DEFAULT 50;
ALTER TABLE xm.match_settings ALTER COLUMN price_tolerance_pct SET DEFAULT 15;
DO $$ BEGIN
 IF NOT EXISTS (SELECT 1 FROM xm.audit_events WHERE event_type='v42_price_band_defaults') THEN
  UPDATE xm.match_settings SET price_tolerance_pct=15;
  INSERT INTO xm.audit_events(company_id, event_type, entity_type, details)
  VALUES ('xm', 'v42_price_band_defaults', 'match_settings', '{"price_tolerance_pct":15,"price_floor_pct":50}'::jsonb);
 END IF;
END $$;
-- v4.2 tolerance model: every numeric factor has its own "below" and "above" tolerance,
-- and nearby-cluster alternatives use a radius in km (the location table holds pairs up to 4 km).
ALTER TABLE xm.match_settings ADD COLUMN IF NOT EXISTS land_down_pct numeric;
ALTER TABLE xm.match_settings ADD COLUMN IF NOT EXISTS land_up_pct numeric;
ALTER TABLE xm.match_settings ADD COLUMN IF NOT EXISTS building_down_pct numeric;
ALTER TABLE xm.match_settings ADD COLUMN IF NOT EXISTS building_up_pct numeric;
ALTER TABLE xm.match_settings ADD COLUMN IF NOT EXISTS price_down_pct numeric;
ALTER TABLE xm.match_settings ADD COLUMN IF NOT EXISTS price_up_pct numeric;
ALTER TABLE xm.match_settings ADD COLUMN IF NOT EXISTS location_radius_km numeric;
UPDATE xm.match_settings SET land_down_pct = coalesce(land_down_pct, land_tolerance_pct),
  land_up_pct = coalesce(land_up_pct, land_tolerance_pct),
  building_down_pct = coalesce(building_down_pct, building_tolerance_pct),
  building_up_pct = coalesce(building_up_pct, building_tolerance_pct),
  price_down_pct = coalesce(price_down_pct, price_floor_pct),
  price_up_pct = coalesce(price_up_pct, price_tolerance_pct),
  location_radius_km = coalesce(location_radius_km, 4)
WHERE land_down_pct IS NULL OR land_up_pct IS NULL OR building_down_pct IS NULL OR building_up_pct IS NULL
   OR price_down_pct IS NULL OR price_up_pct IS NULL OR location_radius_km IS NULL;
ALTER TABLE xm.match_settings ALTER COLUMN land_down_pct SET DEFAULT 10, ALTER COLUMN land_down_pct SET NOT NULL;
ALTER TABLE xm.match_settings ALTER COLUMN land_up_pct SET DEFAULT 10, ALTER COLUMN land_up_pct SET NOT NULL;
ALTER TABLE xm.match_settings ALTER COLUMN building_down_pct SET DEFAULT 20, ALTER COLUMN building_down_pct SET NOT NULL;
ALTER TABLE xm.match_settings ALTER COLUMN building_up_pct SET DEFAULT 20, ALTER COLUMN building_up_pct SET NOT NULL;
ALTER TABLE xm.match_settings ALTER COLUMN price_down_pct SET DEFAULT 50, ALTER COLUMN price_down_pct SET NOT NULL;
ALTER TABLE xm.match_settings ALTER COLUMN price_up_pct SET DEFAULT 15, ALTER COLUMN price_up_pct SET NOT NULL;
ALTER TABLE xm.match_settings ALTER COLUMN location_radius_km SET DEFAULT 4, ALTER COLUMN location_radius_km SET NOT NULL;
-- location_radius_km existed unused (default 3) while matching used a fixed 4 km: keep 4 km once.
DO $$ BEGIN
 IF NOT EXISTS (SELECT 1 FROM xm.audit_events WHERE event_type='v42_location_radius_default') THEN
  UPDATE xm.match_settings SET location_radius_km = 4 WHERE location_radius_km = 3;
  INSERT INTO xm.audit_events(company_id, event_type, entity_type, details)
  VALUES ('xm', 'v42_location_radius_default', 'match_settings', '{"location_radius_km":4}'::jsonb);
 END IF;
END $$;

-- v4.3 (user rules): Hot = price 80–100 % of the budget and sizes inside the request;
-- Warm = up to N % looser than Hot for price and sizes only (category, area, legal status,
-- tower and unit type must match 100 %). The *_down_pct / *_up_pct columns now hold that
-- Warm allowance beyond the Hot band (default 15 % each).
ALTER TABLE xm.match_settings ADD COLUMN IF NOT EXISTS hot_price_floor_pct numeric NOT NULL DEFAULT 80;
DO $$ BEGIN
 IF NOT EXISTS (SELECT 1 FROM xm.audit_events WHERE event_type='v43_warm_allowance_defaults') THEN
  UPDATE xm.match_settings SET hot_price_floor_pct=80, price_down_pct=15, price_up_pct=15,
    land_down_pct=15, land_up_pct=15, building_down_pct=15, building_up_pct=15;
  INSERT INTO xm.audit_events(company_id, event_type, entity_type, details)
  VALUES ('xm', 'v43_warm_allowance_defaults', 'match_settings',
          '{"hot_price_floor_pct":80,"warm_allowance_pct":15}'::jsonb);
 END IF;
END $$;
ALTER TABLE xm.match_settings ALTER COLUMN price_down_pct SET DEFAULT 15;
ALTER TABLE xm.match_settings ALTER COLUMN land_down_pct SET DEFAULT 15;
ALTER TABLE xm.match_settings ALTER COLUMN land_up_pct SET DEFAULT 15;
ALTER TABLE xm.match_settings ALTER COLUMN building_down_pct SET DEFAULT 15;
ALTER TABLE xm.match_settings ALTER COLUMN building_up_pct SET DEFAULT 15;

-- Jev review (AI reviewer of rule-engine pairs; company admins only) ----------------
-- OpenRouter keys per company, encrypted at rest (Fernet, server secret XM_SECRET_KEY or
-- /data/xm-secret.key). The browser only ever sees the label and the last four characters.
CREATE TABLE IF NOT EXISTS xm.ai_keys (
 id uuid PRIMARY KEY,
 company_id text NOT NULL,
 provider text NOT NULL DEFAULT 'openrouter' CHECK (provider IN ('openrouter')),
 label text NOT NULL,
 secret_encrypted text NOT NULL,
 last4 text NOT NULL,
 is_default boolean NOT NULL DEFAULT false,
 created_by uuid REFERENCES xm.users(id) ON DELETE SET NULL,
 created_at timestamptz NOT NULL DEFAULT now(),
 last_used_at timestamptz
);
CREATE INDEX IF NOT EXISTS ai_keys_company_idx ON xm.ai_keys(company_id, created_at DESC);
CREATE UNIQUE INDEX IF NOT EXISTS ai_keys_one_default_idx ON xm.ai_keys(company_id) WHERE is_default;
-- The company's last chosen review model.
ALTER TABLE xm.app_preferences ADD COLUMN IF NOT EXISTS ai_review_model text;
CREATE TABLE IF NOT EXISTS xm.ai_review_runs (
 id uuid PRIMARY KEY,
 company_id text NOT NULL,
 created_by uuid REFERENCES xm.users(id) ON DELETE SET NULL,
 key_id uuid REFERENCES xm.ai_keys(id) ON DELETE SET NULL,
 model text NOT NULL,
 prompt_version text NOT NULL,
 group_by text NOT NULL DEFAULT 'sender',
 payload jsonb NOT NULL DEFAULT '{}'::jsonb,
 estimate jsonb NOT NULL DEFAULT '{}'::jsonb,
 status text NOT NULL DEFAULT 'queued' CHECK (status IN ('queued','running','completed','failed','cancelled')),
 error text,
 created_at timestamptz NOT NULL DEFAULT now(),
 started_at timestamptz,
 heartbeat_at timestamptz,
 finished_at timestamptz
);
CREATE INDEX IF NOT EXISTS ai_review_runs_company_idx ON xm.ai_review_runs(company_id, created_at DESC);
CREATE INDEX IF NOT EXISTS ai_review_runs_pending_idx ON xm.ai_review_runs(created_at) WHERE status IN ('queued','running');
-- One row per reviewed pair. buyer_text/listing_text are exactly the texts sent to the model
-- (part text without the signature); input_hash = sha256 of both, the cache key with model + prompt_version.
CREATE TABLE IF NOT EXISTS xm.ai_reviews (
 id bigserial PRIMARY KEY,
 run_id uuid NOT NULL REFERENCES xm.ai_review_runs(id) ON DELETE CASCADE,
 company_id text NOT NULL,
 position integer NOT NULL,
 direction text NOT NULL CHECK (direction IN ('buyer','property')),
 source_document_id uuid NOT NULL,
 target_document_id uuid NOT NULL,
 source_entity_id uuid,
 target_entity_id uuid,
 buyer_entity_id uuid,
 listing_entity_id uuid,
 source_public_id text,
 target_public_id text,
 group_key text NOT NULL DEFAULT '',
 group_title text,
 engine_status text,
 engine_score numeric,
 engine_explanation jsonb NOT NULL DEFAULT '[]'::jsonb,
 buyer_text text NOT NULL,
 listing_text text NOT NULL,
 hints jsonb NOT NULL DEFAULT '{}'::jsonb,
 model text NOT NULL,
 prompt_version text NOT NULL,
 input_hash text NOT NULL,
 status text NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','done','failed')),
 cached boolean NOT NULL DEFAULT false,
 decision text CHECK (decision IN ('recommend','reject','unsure')),
 confidence numeric,
 reasons jsonb NOT NULL DEFAULT '[]'::jsonb,
 evidence jsonb NOT NULL DEFAULT '{}'::jsonb,
 issues jsonb NOT NULL DEFAULT '[]'::jsonb,
 tokens_in integer,
 tokens_out integer,
 cost numeric,
 cost_estimated boolean NOT NULL DEFAULT false,
 error text,
 override_decision text CHECK (override_decision IN ('recommend','reject','unsure')),
 override_by uuid REFERENCES xm.users(id) ON DELETE SET NULL,
 override_at timestamptz,
 created_at timestamptz NOT NULL DEFAULT now(),
 reviewed_at timestamptz
);
CREATE INDEX IF NOT EXISTS ai_reviews_run_idx ON xm.ai_reviews(run_id, position);
CREATE INDEX IF NOT EXISTS ai_reviews_pending_idx ON xm.ai_reviews(run_id, position) WHERE status='pending';
CREATE INDEX IF NOT EXISTS ai_reviews_cache_idx ON xm.ai_reviews(company_id, input_hash, model, prompt_version) WHERE status='done' AND error IS NULL;
CREATE INDEX IF NOT EXISTS ai_reviews_pair_idx ON xm.ai_reviews(company_id, buyer_entity_id, listing_entity_id);
-- Decision models: P(true) per question (recommend, purpose_match, unit_match, area_match, price_match, size_match, condition_match).
ALTER TABLE xm.ai_reviews ADD COLUMN IF NOT EXISTS probabilities jsonb;

-- v4.10: one public ID per message *content*. content_key = md5 of the normalised part text (parser.content_hash:
-- no signature, links, phone numbers, emoji or punctuation; case-folded). NULL = not computed yet: grouping then
-- falls back to md5 of the exact part text (the pre-v4.10 key); the worker fills it (entities.backfill_content_keys).
ALTER TABLE xm.documents ADD COLUMN IF NOT EXISTS content_key text;
CREATE INDEX IF NOT EXISTS documents_content_key_idx ON xm.documents(company_id, document_type, content_key) WHERE active;
CREATE INDEX IF NOT EXISTS documents_unkeyed_idx ON xm.documents(company_id) WHERE content_key IS NULL AND active;
-- When copies of one content had several IDs, the entities merge into the oldest ID (entities.merge_entities);
-- a retired ID keeps resolving to the ID it was merged into.
CREATE TABLE IF NOT EXISTS xm.entity_aliases (
 company_id text NOT NULL,
 old_public_id text NOT NULL,
 public_id text NOT NULL,
 entity_id uuid NOT NULL,
 merged_at timestamptz NOT NULL DEFAULT now(),
 PRIMARY KEY (company_id, old_public_id)
);
CREATE INDEX IF NOT EXISTS entity_aliases_target_idx ON xm.entity_aliases(company_id, public_id);

-- v5.0: self-service company registration reviewed by the platform admin, company lifecycle (active / inactive),
-- admin password resets that force a new password, per-member feature grants, the activity log and service areas.
ALTER TABLE xm.app_preferences ADD COLUMN IF NOT EXISTS status text NOT NULL DEFAULT 'active';
DO $$ BEGIN
 IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conrelid='xm.app_preferences'::regclass AND conname='app_preferences_status_check') THEN
  ALTER TABLE xm.app_preferences ADD CONSTRAINT app_preferences_status_check CHECK (status IN ('pending','active','inactive','rejected'));
 END IF;
END $$;
ALTER TABLE xm.app_preferences ADD COLUMN IF NOT EXISTS registered_at timestamptz;
ALTER TABLE xm.app_preferences ADD COLUMN IF NOT EXISTS reviewed_at timestamptz;
ALTER TABLE xm.app_preferences ADD COLUMN IF NOT EXISTS reviewed_by uuid;
ALTER TABLE xm.app_preferences ADD COLUMN IF NOT EXISTS contact_email text;
ALTER TABLE xm.users ADD COLUMN IF NOT EXISTS must_change_password boolean NOT NULL DEFAULT false;
-- NULL = the role's default grants (access.DEFAULT_MEMBER_FEATURES); company admins always have everything.
ALTER TABLE xm.users ADD COLUMN IF NOT EXISTS features jsonb;
ALTER TABLE xm.users ADD COLUMN IF NOT EXISTS last_login_at timestamptz;
CREATE TABLE IF NOT EXISTS xm.activity_log (
 id bigserial PRIMARY KEY,
 company_id text,
 user_id uuid,
 user_email text,
 action text NOT NULL,
 method text,
 path text,
 status integer,
 detail jsonb NOT NULL DEFAULT '{}'::jsonb,
 duration_ms integer,
 ip text,
 created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS activity_log_company_idx ON xm.activity_log(company_id, created_at DESC);
CREATE INDEX IF NOT EXISTS activity_log_time_idx ON xm.activity_log(created_at DESC);
CREATE INDEX IF NOT EXISTS activity_log_action_idx ON xm.activity_log(action, created_at DESC);
-- Service areas per unit category: {"house": ["surabaya"], "warehouse": ["surabaya","gresik","sidoarjo"]}.
-- A buyer or listing proven in a city outside the category's list is never matched; {} = no restriction.
ALTER TABLE xm.match_settings ADD COLUMN IF NOT EXISTS category_areas jsonb NOT NULL DEFAULT '{}'::jsonb;

-- v5.1: live progress + time estimate of every background job (api/progress.py), and the analysis window:
-- only messages sent on or after the window start are parsed, embedded and matched (older ones are stored).
-- analysis_from NULL = a rolling window of analysis_window_months calendar months (first day of the month, WIB).
ALTER TABLE xm.maintenance_jobs ADD COLUMN IF NOT EXISTS progress jsonb NOT NULL DEFAULT '{}'::jsonb;
ALTER TABLE xm.maintenance_jobs ADD COLUMN IF NOT EXISTS started_at timestamptz;
ALTER TABLE xm.imports ADD COLUMN IF NOT EXISTS progress jsonb NOT NULL DEFAULT '{}'::jsonb;
ALTER TABLE xm.imports ADD COLUMN IF NOT EXISTS started_at timestamptz;
ALTER TABLE xm.app_preferences ADD COLUMN IF NOT EXISTS analysis_from date;
ALTER TABLE xm.match_settings ADD COLUMN IF NOT EXISTS analysis_window_months integer NOT NULL DEFAULT 4;

-- Saved review instructions are tenant scoped; NULL owner shares with the company.
CREATE TABLE IF NOT EXISTS xm.ai_prompts (
 id uuid PRIMARY KEY,
 company_id text NOT NULL REFERENCES xm.app_preferences(company_id) ON DELETE CASCADE,
 owner_id uuid REFERENCES xm.users(id) ON DELETE CASCADE,
 name text NOT NULL,
 content text NOT NULL,
 created_by uuid REFERENCES xm.users(id) ON DELETE SET NULL,
 updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ai_prompts_company_idx ON xm.ai_prompts(company_id, owner_id);
