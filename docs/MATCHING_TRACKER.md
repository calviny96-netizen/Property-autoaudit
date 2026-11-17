# Matching tracker and sales inventory

The **Matching tracker** page is available to company administrators and members with the **Match** feature. All endpoints use the authenticated company workspace; a supplied entity from another company returns 404.

## What the numbers mean

- Switch between **Listings → Buyers** and **Buyers → Listings** and filter a sales watchlist number.
- Inventory consists of distinct buyer/listing entities belonging to current watchlist phone numbers. Duplicate chat posts count once. By default it includes active documents with Ready, On hold or Sold status. **Include archived / deleted inventory** also includes inactive documents and deleted entities.
- The date range filters the **first discovery timestamp** of each pair, in WIB. It does not filter posting dates or treat every reprocess as a new match.
- **Matching rate = unique inventory entities with a pair first discovered in the selected period / inventory entities in the selected scope.** The denominator is today's inventory (or recorded inventory when archived items are included), not a reconstructed historical stock balance. This definition is shown on the page.
- Totals deduplicate items shared by several sales. Per-sales counts may therefore add up to more than the total.
- Expand an inventory ID to see the counterpart IDs and messages, first match date/score/status, latest confirmed date/score/status, origin, and active/inactive state. The existing persistent `match_events` log supplies this history; it survives matching rebuilds. Dates before recording began are not backfilled with invented events.
- Daily bars count unique newly discovered pairs. Choosing **All dates** shows all recorded history. The tracker does not make paid AI calls or rerun matching.

API routes: `GET /matching-tracker`, `/matching-tracker/sales`, `/matching-tracker/entities`, `/matching-tracker/history`. Entity and history lists paginate at 50 by default, maximum 100.

## Sales stock drill-down

`GET /stock/listings?phone=...` reads the same active documents, entity statuses and signature phone membership as the stock counter. It does not depend on company matching keywords, analysis windows, recommendations or the group cache. It returns `rows`, `total`, and `has_more` (default 100, maximum 200). The modal displays shown / total and supports additional pages. Members only need Sales stock access to open it.

Read-only checks on the existing KONIG local instance on 17 November 2026 found 47 tracked numbers, 33 with stock. All 33 had matching counter/drill-down totals (pagination considered). Ivan entries had 159, 54 and 17 listings; the reported empty state was not reproducible in that local snapshot. The dedicated endpoint removes the dependency that could otherwise make the views disagree.

## UI and PDF

Upload navigation and direct UI route access are limited to the platform `admin` role; company accounts do not see the Upload menu. Existing server integration upload behavior is unchanged.

Buyer and listing cards, the selected source summary, and matching PDFs display `Last updated at : 17 Nov 2026 - 18:34 WIB` beneath the public ID. This is the latest recorded source posting (`last_seen_at`, falling back to `sent_at`), not the PDF generation time. Naive chat timestamps are already WIB; timezone-aware timestamps are converted to WIB.
