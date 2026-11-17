# AI Review sources and saved prompts

AI Review defaults to **Recent Match** until a platform administrator saves shared posting periods in Settings → Matching. After that, it opens in **Match · custom dates**, using the company's saved buyer/listing periods. Recent Match remains available: its discovery date filter reads the same import-only match events, company ownership rules, pair statuses and ready stock as Recent Match/export. A message's posting date does not exclude a pair discovered in that date range. The company's analysis window determines which stored messages can produce matches (the default is the current month plus the previous three months).

Choose **Match · custom dates** for independent buyer/listing posting windows. Existing API clients that send posting windows without a `source` retain this manual behavior. New clients should explicitly send `source: "recent"` with `found_from`/`found_to`, or `source: "match"` with posting windows. Dates use Jakarta time. Both directions may show the same pair twice under different people; identical buyer/listing inputs are sent to the model once. The selected pair limit and available pair count remain separate.

Under **Review prompt**, select a saved prompt or write additional instructions. Save multiple named prompts for **My account** (private to that account in the selected company) or **This company** (shared). Only company/platform administrators may change shared prompts. Saved prompts can be updated and deleted; edits to the textarea are used for the next review even before saving. Platform admins can inspect the standard system prompt and decision questions in the same panel.

Each run snapshots the standard prompt/questions and additional instructions. Text models receive the additional instructions in their system message; decision models receive them in each question. **Prompt used** on the run shows the snapshot, which survives edits/deletion of the saved prompt. The custom instructions contribute to the cache version, so a changed prompt requires a fresh review.

API: `GET/POST /ai/prompts`, `PUT/DELETE /ai/prompts/{id}`. Review estimates/runs accept `custom_prompt` or `prompt_id`; a saved ID is resolved within the requesting account and company before the run is queued. The database table is created idempotently by `api/schema.sql`.

Regression tests: `api/test_review_sources_prompts.py` uses an isolated PostgreSQL database and fake OpenRouter responses. No paid model calls are needed.

## Shared default posting periods

The first panel in Settings → Matching lets the platform administrator set independent listing and buyer posting periods for the current company. Both Matching and new AI Review forms use them on entry. Users can change their current filters without changing the shared defaults. Direct links to a specific ID or sales phone retain their full history. Saving defaults does not reprocess matches or start an AI run.

`GET /company/settings` returns `matching_filter_defaults` (null until configured). Platform admins may write it with `PUT /company/settings`, for example `{"matching_filter_defaults":{"listing_period":"last_1_month","buyer_period":"last_3_days"}}`. Both fields are required; unsupported presets are rejected. Company admins and members can read the values but cannot write them. The existing tenant context scopes reads and writes; the payload never accepts a company ID.

Allowed presets: `today`, `today_yesterday`, `last_3_days`, `last_7_days`, `last_14_days`, `last_1_month`, `last_3_months`, `all`. Day ranges include today in WIB (3 days = today and the previous 2 days). Month ranges count back by calendar months, clamping to the last valid day. Actual dates are calculated when the page opens, so saved defaults do not become fixed historical dates. An unset value preserves each page's earlier defaults. Deploy API, worker and UI together; the schema adds a nullable JSONB column without changing existing company selections.

## Manual review credits (17 November 2026)

- Default model: `z-ai/glm-5.3-flash`. Default `max_pairs`: **100** (the existing configurable maximum stays 2,000).
- Each company starts with **40 shared credits**. No automatic reset is configured. Historical runs before this update do not consume the new balance.
- A successfully queued manual run from a company account consumes **1 credit**, regardless of its pair count or cache hits. An estimate, empty selection, invalid key or rolled-back transaction consumes none. Once queued, a later failure/cancellation does not refund the credit.
- Platform administrator and internal automation runs are exempt. `GET /ai/credits` returns `total`, `used`, `remaining`, `cost_per_run`, `resets_at` and `unlimited`.
- The UI shows remaining credits, the one-credit start action and credits charged in run history. Company accounts see credits in place of token/currency estimates.
- `POST /ai/review-runs` accepts an optional UUID `request_id`. Repeating that ID within the company returns the existing run without charging again. The UI keeps the ID for a retry of the same submission. A per-company transaction lock serializes credit checks and run creation, including simultaneous requests from separate accounts/tabs.
- Deploy API, worker and UI together. The idempotent schema adds `app_preferences.ai_review_credits_used`, `ai_review_runs.request_id`, and its unique company/request index. No existing review data is removed.

Regression coverage: `test_review_tracker_update.py` includes quota exhaustion, request retry, parallel use of the final credit, tenant isolation and PDF timestamps. Database-backed tests require `XM_TEST_DATABASE_URL` targeting a disposable PostgreSQL database.
