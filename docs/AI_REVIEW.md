# AI Review sources and saved prompts

AI Review defaults to **Recent Match**. Its discovery date filter reads the same import-only match events, company ownership rules, pair statuses and ready stock as Recent Match/export. A message's posting date does not exclude a pair discovered in that date range. The company's analysis window determines which stored messages can produce matches (the default is the current month plus the previous three months).

Choose **Match · custom dates** for independent buyer/listing posting windows. Existing API clients that send posting windows without a `source` retain this manual behavior. New clients should explicitly send `source: "recent"` with `found_from`/`found_to`, or `source: "match"` with posting windows. Dates use Jakarta time. Both directions may show the same pair twice under different people; identical buyer/listing inputs are sent to the model once. The selected pair limit and available pair count remain separate.

Under **Review prompt**, select a saved prompt or write additional instructions. Save multiple named prompts for **My account** (private to that account in the selected company) or **This company** (shared). Only company/platform administrators may change shared prompts. Saved prompts can be updated and deleted; edits to the textarea are used for the next review even before saving. Platform admins can inspect the standard system prompt and decision questions in the same panel.

Each run snapshots the standard prompt/questions and additional instructions. Text models receive the additional instructions in their system message; decision models receive them in each question. **Prompt used** on the run shows the snapshot, which survives edits/deletion of the saved prompt. The custom instructions contribute to the cache version, so a changed prompt requires a fresh review.

API: `GET/POST /ai/prompts`, `PUT/DELETE /ai/prompts/{id}`. Review estimates/runs accept `custom_prompt` or `prompt_id`; a saved ID is resolved within the requesting account and company before the run is queued. The database table is created idempotently by `api/schema.sql`.

Regression tests: `api/test_review_sources_prompts.py` uses an isolated PostgreSQL database and fake OpenRouter responses. No paid model calls are needed.
