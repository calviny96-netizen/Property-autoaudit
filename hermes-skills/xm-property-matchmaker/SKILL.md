---
name: xm-property-matchmaker
description: Search XM buyer requests, property listings, and automatically calculated matches stored in PostgreSQL and Qdrant.
---

# XM Property Matchmaker

Use this skill whenever the user asks about a buyer request, available property, matching candidates, agent traffic, or property inventory.

The internal service is available at `http://xm-api:8000`.

## Search

```bash
curl -sG 'http://xm-api:8000/agent/search' --data-urlencode 'q=QUERY' --data-urlencode 'limit=10'
```

Optionally add `document_type=buyer_request` or `document_type=property_listing`.

## Existing matches

```bash
curl -sG 'http://xm-api:8000/agent/matches' --data-urlencode 'min_score=60' --data-urlencode 'limit=20'
```

Optionally add `contact=NAME`.

## Response rules

- Treat PostgreSQL fields as the source of truth and Qdrant score as retrieval assistance.
- State the category, location, area, price, match score, and source contact when available.
- Explain which requirements match and which are only within tolerance.
- Never invent missing dimensions, prices, contacts, or property availability.
- Category and transaction mismatches are not valid matches.

