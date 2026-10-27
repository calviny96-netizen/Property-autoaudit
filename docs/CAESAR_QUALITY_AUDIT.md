# Property → Buyer: Caesar quality feedback loop

Local original archive, 11 September 2026. No remote deployment.

## Baseline observed in the application

- Search `caesar`: 64 unique property texts (230 postings), 188 unique previously stored recommendation pairs, including 12 HOT pairs.
- Search request observed in BrowserOS Neo: 1,902 ms; unfiltered HOT property list: 44,045 ms. These are individual observations, not percentile benchmarks.
- All 12 HOT pairs had a concrete contradiction or missing mandatory evidence. The most important failures were: an advertisement classified as a buyer; warehouse amenities classified as the asset; 75 m² offered against 120 m²; 1,049 m² offered against a 300–400 m² requirement; excluded Woodland treated as a desired location; frontage 12 m offered against 15 m; and unverified parking, occupancy and bargain/condition requirements.

## Feedback iterations

1. Isolate glossary explanations from message evidence. Parse actual asset wording, preserve numeric specifications and exclude nearby landmarks/contact-office addresses from the actual property location.
2. Reassess all saved pairs, then inspect newly emerging HOT candidates. This exposed money spellings (Milyard, jutaaan, bare under 3M), fenced-cluster requirements, renovation-dependent prices and broader parent-location overlaps.
3. Test every combination of 64 unique Caesar listings and 2,136 newly parsed unique buyers (136,704 pairs) with semantic score forced to 100. No pair could become HOT through similarity overriding unresolved/contradictory constraints. This is a rule stress test, not a recall estimate.
4. Preserve positive controls: straightforward house, shophouse and warehouse requirements with explicit compatible location, area and budget still qualify as HOT.
5. Rerun original-archive parsing, matching and browser checks; record final measurements below.
6. Inspect WARM as well: numbered bundles of different properties are withheld below the display threshold instead of combining their specifications. Correct minimum/maximum bedroom bounds, strict “lebih dari”, fractional floors and the `2lt` abbreviation without confusing a following `LT` land-area line with a floor count. Recompute all candidates after these changes so valid matches can reappear as well as invalid ones disappear.

For the 188 old unique recommendation pairs, the offline reassessment produced 108 rejected/below-display-threshold pairs and 80 WARM pairs, with no HOT. A WARM result is an alternative requiring confirmation; it is not a verified suitable property.

## Read and rendering changes

- Group exact raw texts once after recomputation, atomically publish group membership, best group-pair scores and counts. Search/status/date/pagination queries read these groups rather than aggregate all raw-text match pairs repeatedly.
- Preserve all source postings and all contact signatures. Filtered-copy counts, latest posting timestamps and matches reachable through other copies remain available in both directions.
- Compute identical-text pairs once and batch database writes. Cache parsing during reindex and pipeline writes; retain periodic progress logs.
- Wait for saved workspace preferences before first search. Abort stale requests and guard state updates. Render long original messages only when expanded; skip layout of offscreen cards.
- Show price basis (`/m²`, `/tahun`) alongside the amount.
- Preserve up to three decimal places in compact price labels: Rp3.1 billion remains Rp3.1 billion rather than rounding to Rp3 billion.
- Cache/statistics changes do not replace the authoritative source records or mandatory eligibility checks.

## Repeatable verification

- `api/fixtures/caesar_quality_cases.json`: 12 adjudicated original false-HOT cases, with contact sections removed.
- `api/test_quality_loop.py`: false-HOT cases, positive controls, glossary contamination, monetary notation, mid-message contacts and unparsed-budget withholding.
- `api/test_workspace.py`: legacy-versus-cached equality for paging, statuses, dates, counts and reverse recommendations; calendar equivalence.
- `api/test_processing_pipeline.py`: real PostgreSQL pipeline/reindex, duplicate-aware matching and cached recommendation integration in an isolated test database.
- `api/audit_quality.py --search caesar --output /tmp/caesar-audit.json`: repeatable evidence export and timing audit against current local data. Output contains private source messages and must not be published.

43 unit tests and 6 native PostgreSQL integration tests passed (49 test methods total, with additional parameterized cases). UI build and TypeScript checks passed. The initial PGlite adapter could not represent transaction-scoped temporary tables, so cache/pipeline integration was validated against a separate real PostgreSQL test database instead.

## Original-data and browser verification

- Final full recomputation stored 50,249 canonical match edges, including below-display candidates. Caesar: **80 unique listings, 171 visible WARM pairs, 0 HOT pairs**. All 12 previously adjudicated false-HOT cases remain non-HOT. This does not assert that every WARM alternative has been individually approved.
- Reassessing all **1,420 stored HOT pairs across the whole archive** with the final rules found **0 rule-inconsistent HOT pairs**. This is a consistency check, not independent human ground truth or an estimate of precision.
- Final local CLI search timings after the final service restart: **1,576 / 430 / 328 ms**, median **430 ms** (three observations).
- Reprocessed all 46,075 original messages. The resulting source groups contain 2,187 unique buyer texts and 10,795 unique listing texts; original postings are preserved.
- The refreshed parser identifies 80 unique listing texts in the Caesar search, versus 64 in the old extraction. Consequently, overall pair counts before/after are not a fixed-cohort precision comparison.
- BrowserOS Neo confirmed an empty HOT-only Caesar search, grouped WARM results, lazy opening of the original Dian Istana message, the Rp3.1 billion price label, and buyer posting timestamps including 4 September 2026 at 21:43 WIB.
- The Dian Istana recommendation for a tutoring-space request is WARM because parking terms, intended use and handover of the tenanted property are unverified. The buyer asking for land for a five-storey office is not recommended.
- Completed Caesar search request observed in the browser: 899 ms, versus the baseline single observation of 1,902 ms. Rapidly changing the input was also checked; the final input and returned result both corresponded to `caesar`.
- A separate three-run native workspace measurement after heavy processing returned 1,379 / 124 / 43 ms for the unfiltered HOT listing query, and 470 / 363 / 363 ms for all-status Caesar search. The baseline browser observation for the unfiltered HOT list was 44,045 ms. Native function times and browser request times are different measurements; these are small local samples, not p95 benchmarks.
- Cold requests during archive processing were still slow (including a 21-second native search and a 30-second browser list request). The cache improves the interactive query plan; these results do not demonstrate uninterrupted latency under reindex load.
- A repeatable BrowserOS workflow is saved as `/neo-property-matching-audit`; the local CLI and fixtures remain the automated evidence loop.

## Scope and remaining product validation

These changes improve the audited cases and conservative eligibility rules; they do not establish market-wide accuracy or certify that the product is ready for sale. Unseen language, optional versus mandatory preferences, mixed multi-property messages, ambiguous prices and unknown location aliases still require review. Market-value claims such as “BU” need comparable-price evidence. Broader labelled validation should measure HOT precision and missed valid matches on fresh data before a commercial release.
