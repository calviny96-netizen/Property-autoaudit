# Audit matching — 11 September 2026

Scope: browser audit of the existing site, followed by **local-only** changes and sample testing. Production matching settings and source/match data were not changed. This is not a measured accuracy score for the full corpus.

## Observed failures

- BrowserOS Neo reproduced Dian Istana → Willy as **HOT 96%**. Its explanation said land area was unrestricted. The parser produced `office`, no land area, no building area, and no price requirement for a request explicitly asking for land around 500 m² to build a five-storey office.
- The listing's phrase “cocok untuk kantor” added `office` to its categories. Category intersection therefore passed, while failed extraction made the size checks look unrestricted.
- Active administrator settings: tolerances LT 10%, LB 20%, price 20%; weights location 30%, land 10%, building 10%, price 40%, text 7%, quality 3%. Unrestricted price received full price credit under the old rules.
- The Voila buyer appeared twice with identical complete raw text, each showing 38 HOT / 11 WARM.
- Existing text vectors are local feature hashes, not a language model that understands requirements. They should retrieve/rank candidates, never override eligibility.

## Local changes

1. Separate asset category from intended use in the reported land/office and shophouse cases. Parse approximate areas and prefer explicit square metres over rounded dimensions. Do not treat a storey count as building area.
2. Check eligibility before ranking: category, transaction, exclusions, requested area/budget bounds, dimensions, recognised room/storey counts, facing, location region, and explicit apartment/tower conflicts.
3. Unknown required evidence caps the score at 79 (WARM), including missing specifications, required view, uncertain tower, road access, and suitability for planned construction. An empty plot need not already contain five storeys; construction feasibility remains unverified.
4. Unrequested land/building/price factors contribute no points; re-normalise over active weights. Numeric score is displayed as points, not a probability.
5. Group complete, exactly identical raw texts before pagination, in both source directions and recommendation results. Keep distinct signatures separate and retain original messages. Aggregate matches over copies so an older posting's matching edges are not lost. Counts refer to unique text groups; badges show occurrences.
6. Retain all candidate document edges during recomputation; UI grouping now handles duplicates instead of dropping different contact signatures through contact-stripped text equality.

## Verification

- 20 parser/rule regression tests passed, including the reported land/office mismatch, 5 versus 3 storeys, area and dimension conflicts, 3–4 bedrooms, facing, studio/tower conflicts, view, missing evidence, and positive matching controls.
- 4 database regressions passed: 205 distinct buyer texts, each posted twice, produce pages of 200 and 5; reverse-direction grouping/counts; date-filtered copies; different contact signatures remain separate.
- The actual workspace SQL/schema was executed against an isolated PGlite PostgreSQL runtime. Python API functions used a local adapter. This verifies SQL/results but does not validate native PostgreSQL deployment, advisory-lock concurrency, or production query performance.
- The actual reindex function reparsed all 54 local source messages; vector writes were replaced by a local test adapter.
- Local matcher recomputation used the existing hash embeddings with in-process candidate retrieval; a running Qdrant service was not part of this test.
- BrowserOS Neo exercised login, selecting buyers, recommendations and grouped cards on the local application. The Dian Istana request pair is excluded. Additional API checks covered both directions, status/date filters, matching counts and PDF export.
- UI build and TypeScript checks passed.

## Sample results

For the **49 existing Voila recommendations**: old = 38 HOT + 11 WARM; revised = **0 HOT + 8 WARM + 41 excluded/below display threshold**. WARM entries require verification; these labels are not independently adjudicated ground truth.

A separately labelled **KONTROL UJI LOKAL** synthetic listing (Voila, 3 bedrooms, south facing, Yani Golf view) remains HOT, showing that the rule change does not simply suppress all HOT results. It is not an actual listing and is excluded from the 49-item comparison.

The initial local preview uses 54 sample messages, reparsed into their detected document types: two copies of the real Voila request, its 49 former recommendations, the two supplied Dian Istana/Willy texts, and the synthetic positive control. Four explicitly labelled DEMO JARAK messages were then added (58 total) to verify nearby alternatives and a mandatory area conflict. It does not contain the entire production database.

## Location index, timestamps and calendar

- Imported the supplied table through the local administrator UI: 692 clusters, 4,114 undirected distance pairs and 7 aliases. CSV additions merge with existing entries; preview, revision checks and conflict validation prevent silent replacement. Repeated identical imports are unchanged and do not queue unnecessary recomputation.
- Explicit alias and direct distance evidence supplement the glossary. Candidates within 4 km are considered independently of vector retrieval, with the imported distance shown in the explanation. Nearby alternatives remain WARM pending buyer location approval; mandatory category, area, budget and other checks still apply. Missing edges do not invent distances or imply a measured distance above 4 km. Distances are supplied data, not independently verified road distances.
- The local DEMO JARAK buyer receives the 1.1 km and 3.9 km listings, in that order; the 1.1 km listing with incompatible land area is rejected. The actual maintenance worker reparsed/recomputed local data after import, with vector-service calls stubbed for this isolated runtime.
- Recommendation cards display latest posting time across grouped matching copies, including full WIB date/time. Relative labels use WIB calendar days.
- Date picker shows two months on desktop, one on mobile, real unique posting counts, and inclusive start/end minute filters. One click followed by Apply selects one day. Hover previews a range; a second click commits its end. Cancel preserves the previous filter. BrowserOS Neo verified preview highlighting, range application, single-day results, cancellation and recommendation timestamps.
- Added 9 location and 4 date regression tests; 33 unit tests pass in total. The four database tests also passed separately through the local PostgreSQL adapter. API checks verified calendar counts, single-day/time-bound results, latest-copy timestamps, repeated import and stale/conflicting imports.

## Remaining work before any rollout

On 11 September 2026, at the user's request, the local preview was switched to the existing Docker database (46,075 original messages, archive ending 7 September), and the XM Hermes gateway was restarted. The original archive was backed up under `release-data/20260911-011722/` before processing. The location CSV was imported into that database and the full maintenance run completed: 46,075 messages processed, 230,875 match edges stored before UI grouping. Active documents comprise 3,697 buyer requests and 33,894 listings; distinct raw texts yield 2,138 buyers and 11,179 listings. Port 9046 now maps to the same local web gateway as 9004; the isolated sample server was stopped. No remote deployment was performed. XM database sessions disable parallel query workers and use 32 MB work memory to avoid exhausting the shared PostgreSQL container's small shared-memory allocation. Statistics share a 15-second cache across tab requests. Browser verification confirmed 830 unique listing postings on 7 September and disabled dates thereafter. Initial full-archive queries remain noticeably slow on this local runtime.

Reparse original documents, rebuild the vector index and recompute matches with the new rules. Changing the code or weights alone does not repair previously stored extraction/match results. Keep that operation local until deployment is separately requested.

The extractor remains conservative and rule-based. It does not fully understand every WhatsApp phrasing, multi-property bubble, soft preference, tower alias, price notation, or spatial relationship. Location matching is not a verified kilometre radius. More labelled examples are needed to measure precision among HOT results and detect missed valid matches. A future structured language-model extractor should retain evidence spans and explicit unknown values, while deterministic eligibility checks remain authoritative.
