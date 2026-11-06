# CivicRoute backend

FastAPI backend for the CivicRoute Karnataka public-service navigator. Its intended catalogue can cover identity and government ID services, revenue certificates, transport, forest, Gram Panchayat and other local bodies, Grama One, Nemmadi, Karnataka One, hospitals, women and child welfare, minority welfare, taxation, legal/notary services, and other relevant Karnataka public services. These are scope categories, not a fixed service limit. Existing IDs (`aadhaar_update`, `income_certificate`, `civic_complaint`) remain supported; additional services/departments are added through validated catalogue data. The checked-in trusted catalogue is intentionally empty and explicitly unverified, so a clean checkout returns the unsupported response safely.

## Install and run locally (PowerShell)

From the repository root:

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
$env:CORS_ORIGINS = "http://localhost:5173"
Remove-Item Env:GEMINI_API_KEY -ErrorAction SilentlyContinue
python -m uvicorn backend.main:app --reload --host 127.0.0.1 --port 8000
```

The `Remove-Item` line ensures this session runs with Gemini disabled, even if a key was already present in the shell. With no key, clear deterministic intent matches and validated catalog entries use stored steps. The current empty catalog therefore returns an unsupported response for every request; add only data verified by the data team before expecting supported answers.

Interactive API documentation: [http://127.0.0.1:8000/docs](http://127.0.0.1:8000/docs). Health check: `GET http://127.0.0.1:8000/health` returns only `{"status":"ok"}`.

## Environment configuration

The application reads these environment variables. It does not automatically load `.env`; configure them in the shell or the deployment platform's secret/configuration manager. `.env` is ignored by Git. Keep real values out of source files, examples, logs, and tickets.

| Name | Required | Purpose |
| --- | --- | --- |
| `GEMINI_API_KEY` | No | Enables Gemini classification of related paraphrases and step composition. Leave unset to use deterministic matching and stored text. |
| `GEMINI_MODEL` | No | Structured-output text model. There is no hard-coded model fallback; Gemini is disabled unless both this and `GEMINI_API_KEY` are set. Select a model available to the configured project that supports structured output. |
| `CORS_ORIGINS` | No | Comma-separated explicit frontend origins, including scheme and port, for example `http://localhost:5173,https://civicroute.example`. Empty means no browser origins are allowed. Wildcards and paths are rejected. |
| `CIVICROUTE_VERIFIED_GENERAL_PORTAL_URL` | No | Optional general portal URL. Set only after the team verifies it; must be an absolute HTTPS URL. |
| `LOG_LEVEL` | No | Logging level; defaults to `INFO`. |
| `OFFICIAL_SOURCE_PROVIDER` | No | Set to `brave` to enable Brave Web Search. Empty or unsupported values disable live retrieval. |
| `BRAVE_SEARCH_API_KEY` | No | Backend-only Brave Search token. Required with the provider name and an explicit host allowlist. |
| `OFFICIAL_SOURCE_APPROVED_HOSTS` | No | Comma-separated approved hostnames, for example the official government domains reviewed for this deployment. Results outside these hosts are discarded. |

The application loads the repository-root `.env` file without overriding process environment variables. Both `.env.example` files list names with blank values only. Keep all API credentials in the ignored repository-root `.env` or a deployment secret manager; do not put them in frontend environment variables, README, tests, or committed files. Production CORS should contain only the deployed frontend origins.

To enable Brave after the team approves provider use, copy the root example with `Copy-Item .env.example .env`, then set `OFFICIAL_SOURCE_PROVIDER=brave`, add the provider-issued token to `BRAVE_SEARCH_API_KEY`, and set `OFFICIAL_SOURCE_APPROVED_HOSTS` to the reviewed official hostnames. The integration makes no account, billing, or key setup automatically. Leave any of these settings blank to keep live retrieval disabled.

## Tests

```powershell
python -m pytest backend/tests -q
```

## API contract

`POST /ask` accepts only `question` (nonblank string, maximum 2,000 characters) and optional `language` (`"en"` or `"kn"`, default `"en"`). Blank/oversized input and unsupported language values receive a sanitized validation error. Extra fields are rejected.

Example request:

```json
{"question":"I need an income certificate","language":"en"}
```

Current response with the empty development catalog:

```json
{
  "status": "unsupported",
  "service_id": "unknown",
  "service_name": null,
  "message": "I could not confidently match this request to a CivicRoute supported service. Try naming one of the suggested services below.",
  "official_portal_url": null,
  "suggested_services": ["aadhaar_update", "income_certificate", "civic_complaint"],
  "warnings": ["No service steps or details are provided for an unsupported request."]
}
```

When verified data exists for the matched service, the supported response has `status`, `service_id`, `service_name`, `language`, `steps`, `documents` (`name`, `status`, `note`), `fee` (`amount`, `currency`, `status`, `note`), `eligibility` (`status`, `criteria`, `note`), `online_option` (`available`, `url`, `status`), `office_type`, `grievance`, `sources` (`title`, `url`), `last_verified`, and `warnings`. The additive `eligibility` object is `verified` only when criteria and source provenance are present in the approved bundle, `verified_unavailable` only when that is explicitly sourced, and otherwise `not_verified` with an empty criteria list. Multiple detected task types share this supported response; each structured field remains sourced and validated independently. Structured facts come from the validated catalog; generated wording can affect only `steps`.

When the service is recognizable but the requested task is unclear, or when a nearby-office request has no locality, the backend may return the additive clarification shape already handled by the frontend:

```json
{"status":"clarification_needed","service_id":"income_certificate","service_name":null,"language":"en","question":"What do you need for this service? Please include the service name in your reply.","clarification_question":"What do you need for this service? Please include the service name in your reply.","missing_fields":["task_type"]}
```

The frontend sends the clarification response as a new `/ask` question and has no conversation token. Clarification text therefore asks the user to repeat the service name; no server-side conversation state is implied.

## Expandable service catalogue and retrieval

`backend/data/services.json` separates trusted `services` records from unreviewed `discoveries`. Only `services` are validated into the trusted in-memory catalogue; discovery entries are never promoted automatically. A trusted service record can describe a dynamic slug ID, aliases, departments, categories, service channels, task types, jurisdiction, office/provider types, ordered English/Kannada steps, document statuses, fee verification, online option, official links, source provenance, and verification date. Every trusted record needs at least one titled HTTPS source. Unknown fees remain `null` with `not_verified` status.

The retrieval provider interface is ordered: trusted bundled catalogue first, followed by `ReviewedCacheProvider` and `OfficialOnlineProvider` extension points. The optional live adapter uses Brave's documented Web Search REST endpoint with Python's standard-library HTTP client; no SDK or extra dependency is required. It is disabled unless `OFFICIAL_SOURCE_PROVIDER=brave`, `BRAVE_SEARCH_API_KEY`, and `OFFICIAL_SOURCE_APPROVED_HOSTS` are all set. Brave currently documents the Search plan at $5 per 1,000 requests, with $5 monthly credits and 50 requests/second; its quickstart requires activating a plan and entering a credit card. Pricing/terms can change; review Brave's [official pricing](https://api-dashboard.search.brave.com/documentation/pricing), [quickstart](https://api-dashboard.search.brave.com/documentation/quickstart), [authentication guide](https://api-dashboard.search.brave.com/documentation/guides/authentication), and [Web Search API reference](https://api-dashboard.search.brave.com/api-reference/web/search/get) before enabling it. No account or paid plan is created by this project. Configure only team-reviewed official government hostnames in the allowlist; the adapter asks for India/Karnataka results and discards every returned URL outside that allowlist. Requests have a five-second timeout, ask for at most eight results, and read at most 1 MB of response data. Brave documents a 600-character/75-word query limit and a maximum result count of 20; CivicRoute applies lower bounds. Provider output is only a discovery candidate, not proof of a government claim.

`/ask` uses trusted bundled data first and searches only when a requested trusted field is missing or the service record is missing. Successful candidates are persisted separately to `backend/data/discoveries.json`, remain `pending_review`/unverified, and preserve their provenance. `/ask` never uses candidate excerpts as guidance or promotes them into trusted facts; it returns only an honest unsupported/partial response with warnings. All candidates must pass the same absolute-HTTPS and configured-host validation. Provider failures, malformed responses, and oversized bodies are sanitized. Tests mock all provider transport and include an autouse safeguard that disables live retrieval for the full suite. Unreviewed discoveries and generated text are not trusted retrieval sources. Add new service IDs and approved aliases as data, not in a fixed allowlist. `ProviderCandidateSource` is a maps-owned adapter boundary, and provider matches are filtered by verified service capability with cited HTTPS provenance before distance ranking. No centres endpoint or provider records are implemented here; a nearby location alone does not establish authorization.

The internal discovery review queue persists to `backend/data/discoveries.json`, separate from `backend/data/services.json`. It records candidate provenance, `pending_review`/`verified`/`rejected`/`stale` state, reviewer notes, and conflict links. A human `verified` review does not create or update a trusted service record; catalogue promotion remains a separate data-team action. Staleness is marked only when a caller supplies an explicit cutoff; no freshness period is assumed. Writes use atomic replacement and a process-local lock. This JSON store is intended for single-process MVP use and is not safe for concurrent multi-worker writes. The review API is internal Python only; there is no public review endpoint.

## Government-service bundle preparation

`backend/data_ingestion.py` accepts JSON bundle version 1, validates service and centre records independently, and produces a quality report. It does not write `services.json`, change `discoveries.json`, or add records to the runtime catalogue. Every valid input record is returned in memory and, if requested, in a separate staging artifact marked `pending_review_not_trusted`. Records marked `verified` by the source bundle still require review before catalogue integration. Invalid rows remain in the original bundle and appear in the report with row index, identity where available, field path, and sanitized reason. Duplicate IDs, possible duplicate names/aliases, same-ID field conflicts, centre ID/name/locality collisions, missing provenance, and stale/future verification dates are review findings.

Run validation from the repository root:

```powershell
python -m backend.data_ingestion path\to\team-bundle.json
python -m backend.data_ingestion path\to\team-bundle.json --report work\bundle-report.json --staging-output work\pending-bundle.json
```

Report and staging paths must not already exist; outputs use exclusive creation so existing files are never replaced. The input bundle itself is read-only. Staging output is not accepted by `load_services()` and cannot silently become trusted runtime data. The validator currently flags a verification date older than 365 days as stale; this is a data-quality threshold, not an assertion that government information expires on that schedule.

The bundle envelope is `{ "schema_version": 1, "services": [...], "centres": [...] }`. A service has stable slug `id` and `name`; aliases, department, responsible authority, category, jurisdiction, service channels, centre IDs and sources are optional. Optional fact fields use `{ "status": "verified" | "unverified" | "verified_unavailable", "value": ..., "note": null, "source_urls": [...] }`. Omitted fact means information is missing; `verified_unavailable` means a source explicitly confirms the information is unavailable and requires a note. Verified facts are flagged for review unless their source URLs refer to a source record in that same bundle. Missing fees remain missing; no zero fee is inferred. Ordered steps use `{ "order": 1, "text": "..." }`. Documents retain `required`, `conditional`, or `commonly_requested` status. Source URLs and application URLs must be absolute HTTPS. Each source can retain `title`, `url`, timezone-aware `retrieved_at`, and `publisher`.

Template only; replace all placeholders with teammate-supplied source data before validation. It is not a government-service record:

```json
{
  "schema_version": 1,
  "services": [
    {
      "id": "<stable_service_slug>",
      "name": "<source_name>",
      "aliases": [],
      "department": "<source_department>",
      "responsible_authority": "<source_authority>",
      "category": "<source_category>",
      "description": {"status": "unverified", "value": "<source_text>", "source_urls": []},
      "application_procedure": {"status": "unverified", "value": [{"order": 1, "text": "<source_step>"}], "source_urls": []},
      "documents": {"status": "unverified", "value": [{"name": "<source_document>", "status": "conditional"}], "source_urls": []},
      "eligibility": {"status": "unverified", "value": ["<source_criterion>"], "source_urls": []},
      "fees": {"status": "unverified", "value": null, "source_urls": []},
      "online_application_links": {"status": "unverified", "value": [], "source_urls": []},
      "offline_application_locations": {"status": "unverified", "value": [], "source_urls": []},
      "service_channels": [],
      "authorised_service_centres": [],
      "jurisdiction": {"state": "<source_jurisdiction>", "district": null, "taluk": null},
      "sources": [{"title": "<source_title>", "url": "https://<official-host>/<source-path>", "retrieved_at": "YYYY-MM-DDTHH:mm:ssZ"}],
      "verification_status": "pending_review",
      "last_verified": null
    }
  ],
  "centres": []
}
```

Centre records can include provider type, department, address, district/taluk, pincode, optional coordinate pairs, source provenance, and `service_capabilities` entries with an explicit authorization status and source URLs. Proximity alone is not treated as service authorization. Only reviewed records should be mapped deliberately into the existing runtime service schema; this preparation layer does not add a public endpoint or change `/ask`.

### Explicit promotion

Promotion is a separate, opt-in command. Each service or centre ID must be named with `--approve`; the bundle's own `verification_status` never grants approval. The operator must provide reviewer identity and a review note. Stale, duplicate, conflicting, unverified, or provenance-incomplete entries are blocked and reported. Service records are mapped into the existing runtime schema while the full validated version-1 record, source timestamps, and approval audit are retained in `bundle_data`/`promotion_approval`. Centre records are added under the separate top-level `centres` array only when their own provenance and verification pass; each claimed service capability must be verified, sourced, and refer to an already trusted service ID. This does not create a centre endpoint or change Maps behavior.

```powershell
python -m backend.catalog_promotion path\to\team-bundle.json --approve <reviewed-service-id> --reviewer "<reviewer>" --approval-note "<review rationale>" --report work\promotion-report.json
```

Repeat `--approve` for each explicitly approved service or centre ID. The command creates a timestamped backup and atomically replaces the catalogue only when at least one record is imported. Identical repeated imports are skipped; existing IDs with different facts are rejected. Report paths are exclusive-create and cannot overwrite existing files. Inspect the report and backup after promotion. Unit tests use temporary files only; no synthetic fixture is intended for the real catalogue.

Smoke check after starting the app:

```powershell
Invoke-RestMethod -Method Get http://127.0.0.1:8000/health
Invoke-RestMethod -Method Post http://127.0.0.1:8000/ask -ContentType 'application/json' -Body '{"question":"I need an income certificate","language":"en"}'
```

## Gemini model and safe fallback

The Gemini wrapper is isolated in `backend/llm.py`. The default `gemini-3.8-flash` supports structured output according to the [official model guide](https://ai.google.dev/gemini-api/docs/models/gemini-3.8-flash) and [structured output guide](https://ai.google.dev/gemini-api/docs/structured-output). `GEMINI_MODEL` can select another project-available structured-output model. The wrapper uses an eight-second timeout, at most one retry, and `store=False`.

If `GEMINI_API_KEY` is absent, the deterministic classifier handles clear known phrases and answers from stored validated steps; uncertain or out-of-scope requests become unsupported. If Gemini times out, is rate-limited, returns malformed output, or is unavailable, classification falls back to deterministic matching/unknown, and composition falls back to stored verified steps. Kannada text is preferred from the catalog when present.

Maps is not integrated in this backend. If Maps or the maps-owned centre service is unavailable, there is no centre/map result or location-based fallback; this backend does not invent centres, coordinates, or service authorization.

## Key rotation if a key is exposed

1. Revoke/delete the exposed Gemini key immediately in the provider's key-management console; removing it from a file or Git history alone does not invalidate it.
2. Create a replacement key and update `GEMINI_API_KEY` only in the local environment or deployment secret manager.
3. Restart/redeploy the backend and confirm it works without printing the key. Remove the old value from current files and history where possible, and rotate any other credential that reused it.
4. Never paste the old or replacement key into source control, README, `working-Backend.txt`, logs, or chat.

## Current data, scope, and deployment limitations

- `backend/data/services.json` is an explicitly unverified, empty development fixture. No real government facts are included.
- Maps/centres endpoint and data are not implemented; ownership and approved integration contract are pending the maps/integration teammate.
- The API has no authentication, accounts, database/request persistence, application submission, or additional service journeys.
- Application-level rate limiting is not implemented. Put the service behind a rate-limiting gateway/reverse proxy with request-size limits before public exposure.
- Production hosting, frontend-owner contract confirmation, production CORS origins, monitoring, and deployment secret-store setup are not configured here.
- `app.py` and root `services.json` are retained for compatibility. The supported documented entry point is `backend.main:app`.
