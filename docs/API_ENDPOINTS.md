# Katalog endpoint Property AutoAudit

Dihasilkan dari aplikasi FastAPI. Path di bawah memakai prefix `/api` pada web; port backend langsung tidak memakai prefix tersebut.

Kontrak request/response lengkap: [openapi.json](openapi.json). Panduan autentikasi dan alur sehari-hari: [API_ADMINISTRATIF.md](API_ADMINISTRATIF.md).

| Metode | Path | Fungsi |
| --- | --- | --- |
| POST | `/api/auth/login` | Login |
| POST | `/api/auth/register` | Register |
| GET | `/api/auth/me` | Me |
| POST | `/api/auth/logout` | Logout |
| PUT | `/api/auth/password` | Change Password |
| POST | `/api/auth/password/first` | First Password |
| GET | `/api/integration/keys` | List Keys |
| POST | `/api/integration/keys` | Create Key |
| DELETE | `/api/integration/keys/{key_id}` | Revoke Key |
| POST | `/api/integration/exports/plan` | Export Plan |
| GET | `/api/integration/exports` | List Exports |
| POST | `/api/integration/exports` | Create Export |
| POST | `/api/integration/exports/{job_id}/files/{file_id}/delivered` | Confirm Delivery |
| GET | `/api/integration/exports/{job_id}` | Export Status |
| DELETE | `/api/integration/exports/{job_id}` | Cancel Export |
| GET | `/api/integration/exports/{job_id}/files/{file_id}` | Download File |
| GET | `/api/integration/exports/{job_id}/download` | Download Export |
| GET | `/api/health` | Health |
| GET | `/api/stats` | Stats |
| GET | `/api/imports` | Imports |
| POST | `/api/imports` | Upload Import |
| POST | `/api/integration/imports` | Upload Import |
| GET | `/api/imports/estimate` | Import Estimate |
| GET | `/api/integration/imports/{import_id}` | Integration Import Status |
| GET | `/api/documents` | Documents |
| GET | `/api/matches` | Matches |
| GET | `/api/buyers` | Buyers |
| GET | `/api/buyers/{buyer_id}/recommendations` | Buyer Recommendations |
| POST | `/api/buyers/recommendations/batch` | Batch Buyer Recommendations |
| GET | `/api/settings` | Get Settings |
| PUT | `/api/settings` | Save Settings |
| POST | `/api/matches/recompute` | Recompute |
| GET | `/api/agent/search` | Agent Search |
| GET | `/api/agent/matches` | Agent Matches |
| GET | `/api/glossary` | Get Glossary |
| PUT | `/api/glossary` | Save Glossary |
| GET | `/api/preferences` | Get Preferences |
| PUT | `/api/preferences` | Save Preferences |
| GET | `/api/search-default` | Get Search Default |
| PUT | `/api/search-default` | Save Search Default |
| PUT | `/api/search-default/personal` | Save Personal Search |
| DELETE | `/api/search-default/personal` | Reset Personal Search |
| GET | `/api/reprocess/status` | Get Reprocess Status |
| POST | `/api/reprocess` | Reprocess |
| POST | `/api/index/recompute` | Rebuild Index |
| GET | `/api/index/status` | Index Status |
| GET | `/api/workspace/dates` | Workspace Dates |
| GET | `/api/workspace` | Workspace |
| GET | `/api/workspace/groups` | Workspace Groups |
| POST | `/api/workspace/recommendations` | Recommendations |
| POST | `/api/export/pdf` | Export Pdf |
| GET | `/api/location-index` | List Locations |
| GET | `/api/location-index/neighbors` | Neighbors |
| GET | `/api/location-index/areas` | Areas |
| POST | `/api/location-index/preview` | Preview |
| POST | `/api/location-index/import` | Import Locations |
| POST | `/api/location-index/clusters` | Save Cluster |
| DELETE | `/api/location-index/clusters/{cluster_id}` | Delete Cluster |
| POST | `/api/location-index/aliases` | Add Alias |
| DELETE | `/api/location-index/aliases` | Delete Alias |
| POST | `/api/location-index/edges` | Save Edge |
| DELETE | `/api/location-index/edges` | Delete Edge |
| GET | `/api/glossary/entries` | Get Entries |
| PUT | `/api/glossary/entries` | Save Entries |
| GET | `/api/glossary/terms` | Property Terms |
| GET | `/api/glossary/suggestions` | Suggestions |
| POST | `/api/entities/status` | Change Status |
| GET | `/api/entities/lookup` | Find |
| GET | `/api/entities/summary` | Summary |
| GET | `/api/buyers/list` | Buyer List |
| GET | `/api/matches/recent` | Recent Matches |
| GET | `/api/matches/recent/days` | Recent Days |
| GET | `/api/matches/recent/summary` | Recent Summary |
| POST | `/api/matches/recent/seen` | Mark Seen |
| GET | `/api/stock/overview` | Overview |
| PUT | `/api/stock/tracked` | Save Tracked |
| POST | `/api/stock/tracked/parse` | Parse Tracked File |
| POST | `/api/stock/tracked/item` | Add Tracked |
| PATCH | `/api/stock/tracked/item` | Edit Tracked |
| DELETE | `/api/stock/tracked/item` | Remove Tracked |
| POST | `/api/stock/snapshot` | Manual Snapshot |
| GET | `/api/stock/log` | Stock Log |
| GET | `/api/stock/export` | Export Listings |
| GET | `/api/dashboard/overview` | Overview |
| GET | `/api/dashboard/export.pdf` | Export Pdf |
| GET | `/api/team/features` | List Features |
| GET | `/api/team/users` | List Members |
| POST | `/api/team/users` | Add Member |
| PUT | `/api/team/users/{user_id}` | Edit Member |
| GET | `/api/company/settings` | Get Company Settings |
| PUT | `/api/company/settings` | Put Company Settings |
| GET | `/api/admin/companies` | List Companies |
| POST | `/api/admin/companies` | Create Company |
| PUT | `/api/admin/companies/{company_id}` | Rename Company |
| POST | `/api/admin/companies/{company_id}/status` | Set Company Status |
| POST | `/api/admin/companies/{company_id}/reset-password` | Reset Company Password |
| POST | `/api/export/all/plan` | Plan |
| POST | `/api/export/all/part` | Part |
| GET | `/api/export/all/{token}/download` | Download |
| POST | `/api/export/all/cancel` | Cancel |
| POST | `/api/export/selection` | Export Selection |
| GET | `/api/admin/deploy` | Get Deploy |
| POST | `/api/admin/deploy` | Post Deploy |
| GET | `/api/ai/keys` | List Keys |
| POST | `/api/ai/keys` | Create Key |
| POST | `/api/ai/keys/{key_id}/default` | Make Default |
| DELETE | `/api/ai/keys/{key_id}` | Delete Key |
| GET | `/api/ai/models` | List Models |
| POST | `/api/ai/review-runs/estimate` | Estimate Run |
| POST | `/api/ai/review-runs` | Create Run |
| GET | `/api/ai/review-runs` | List Runs |
| GET | `/api/ai/review-runs/{run_id}` | Get Run |
| POST | `/api/ai/review-runs/{run_id}/cancel` | Cancel Run |
| GET | `/api/ai/review-runs/{run_id}/results` | Run Results |
| PUT | `/api/ai/reviews/{review_id}/override` | Override Review |
| POST | `/api/ai/reviews/lookup` | Lookup Reviews |
| GET | `/api/ai/review-runs/{run_id}/export` | Export Approved |
| GET | `/api/admin/usage` | Usage |
| GET | `/api/admin/usage/monthly` | Usage Monthly |
| GET | `/api/admin/logs` | Logs |
| GET | `/api/admin/companies/{company_id}/activity` | Company Activity |
| GET | `/api/glossary/export.csv` | Export Glossary |
| GET | `/api/location-index/export.csv` | Export Clusters |
| POST | `/api/admin/companies/{company_id}/copy-knowledge` | Copy Knowledge |
