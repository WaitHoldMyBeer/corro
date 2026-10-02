# Clio Manage API v4: read-only brief

Written 2026-10-02. Marking: **docs** = taken from Clio's official documentation or its published OpenAPI spec; **unverified** = not confirmed by docs, probe with a live call.

Primary source for endpoints, parameters and field names: the official OpenAPI 3 spec, https://docs.developers.clio.com/openapi.json (version "v4"; rendered at https://docs.developers.clio.com/clio-manage/api-reference/). Everything under "Endpoints" was read from that file, not from memory.

## 1. Auth (docs: https://docs.developers.clio.com/api-docs/clio-manage/authorization/)

- Authorization-code flow. Authorize: `GET https://app.clio.com/oauth/authorize?response_type=code&client_id=<key>&redirect_uri=<uri>`. Token: `POST https://app.clio.com/oauth/token` with `client_id`, `client_secret`, `grant_type=authorization_code`, `code`, `redirect_uri`. Auth code valid 10 min. (docs)
- Refresh: same token URL with `grant_type=refresh_token`. The field list is standard OAuth2 but **unverified** for Clio; probe. Docs: refresh tokens do not expire, so keep them in `.env` only.
- Access-token lifetime: **unverified**. A docs troubleshooting section reportedly says 30 days; read `expires_in` from the token response instead of hardcoding.
- Send `Authorization: Bearer <access_token>` on every API call.
- Redirect URI must match exactly (scheme, host, port, trailing slash) between the developer-app setting, the authorize call and the token call. Whether `http://127.0.0.1:8000/oauth/callback` is accepted is **not stated in the docs**. Probe order: register `http://127.0.0.1:8000/oauth/callback`; if the portal rejects it, `http://localhost:8000/oauth/callback`; fallback documented for desktop/mobile apps is `https://app.clio.com/oauth/approval` (copy the code from the page by hand).
- Read-only (docs: https://docs.developers.clio.com/api-docs/clio-manage/permissions/): permissions are chosen per resource in the developer portal, "read-only" or "read/write" each. Docs say these access permissions are interchangeable with OAuth scopes. Read access allows GET only. Changes to the app's permissions do not affect existing authorizations until the user re-authorizes. Tick Read for every resource, nothing else. This is the evidence for judges: a screenshot of the portal permissions, plus a client wrapper that only exposes GET.
- Deauthorize: `POST /oauth/deauthorize` (docs). This is a token-revocation call, not a case-data write, but nothing in the app should call it.
- Regions (docs, OpenAPI `servers`): US `https://app.clio.com/api/v4`, EU `https://eu.app.clio.com/api/v4`, CA `https://ca.app.clio.com/api/v4`, AU `https://au.app.clio.com/api/v4`. The OAuth endpoints for non-US regions are presumably on the same regional host (`https://eu.app.clio.com/oauth/...`): **unverified**. Make the host a config value. Which region the team's account is in: check the host the browser lands on after login.

## 2. Request conventions

- **`fields` parameter** (docs: https://docs.developers.clio.com/api-docs/fields/): confirmed. Without it, most endpoints return only `id` and `etag`. Comma-separated, nested with braces: `fields=id,etag,type,matter{id,description}`. Second-level nesting returns only default attributes, and asking for fields there returns `400`. An invalid field name returns `400`. So nested objects like `matter{id,display_number}` work, but `matter{client{name}}` does not; fetch the inner object separately.
- Optional `X-API-VERSION` header pins a minor version (`4.X.Y`); an invalid value returns `410 Gone` (docs, OpenAPI description). Pin it once the live probe works, to avoid shape drift mid-demo.
- Responses: list calls return `{ "data": [...], "meta": { "paging": {...} } }` (docs for `meta.paging.next`; the `data` wrapper is standard for v4 but treat as **unverified** until the first call).
- Suffix is `.json`: `GET /matters.json`.

## 3. Pagination (docs: https://docs.developers.clio.com/api-docs/paging/)

- Default and max page size: "limited to 200 results per request unless otherwise specified", so `limit` up to 200 (max value itself: **unverified**, the docs sentence reads as a default).
- Next page URL: `meta.paging.next` in the body. Loop on it until absent.
- Two modes. Cursor: requires `order=id(asc)` and no `offset`; unlimited records; serial only. Offset: `offset=N`, parallelisable, custom sort, hard cap of 10,000 records (422 beyond). The cursor token parameter is `page_token` in the OpenAPI spec. Folders `list` has no offset paging.
- This matter is a few hundred rows per object, so one cursor loop per endpoint is enough.

## 4. Rate limits (docs: https://docs.developers.clio.com/api-docs/rate-limits/)

- Per access token, default 50 requests/minute in peak hours (US/CA 04:00-19:00 Pacific, Mon-Fri), higher off-peak. The demo window is inside peak. "Rate limits may change without notice".
- Headers: `X-RateLimit-Limit`, `X-RateLimit-Remaining`, `X-RateLimit-Reset` (Unix timestamp). On `429`, honour `Retry-After` (seconds).
- Budget: with 200/page, a full pull of one matter is roughly 15-25 calls plus one download per document. Do the first full sync once, store everything in SQLite, then sync incrementally. Note 15 document downloads plus metadata fit in one minute only if throttled; respect `Remaining`.

## 5. Incremental sync and ETags

- Every list endpoint below accepts `updated_since` (ISO-8601) and `created_since` (docs: OpenAPI parameters). Use `updated_since` plus the stored high-water mark. Deleted records are not reported by this filter; for documents/folders `include_deleted=true` exists. Notes/communications/tasks deletions: **unverified**, probe by deleting nothing (we cannot write); accept a periodic full re-list instead.
- Every object has an `etag`; request `fields=id,etag` to detect change cheaply, then fetch changed ones. Single-resource GETs (e.g. `/matters/{id}.json`) accept `IF-NONE-MATCH` and `IF-MODIFIED-SINCE` headers (docs: OpenAPI). Whether a `304` counts against the rate limit: **unverified** (the ETags docs page could not be retrieved).

## 6. Endpoints (docs: OpenAPI spec; all GET)

Filter by matter is a query parameter on every list below. Parameter names are exactly as in the spec.

| Data | List endpoint | Matter filter | Useful fields (spec field names) |
| --- | --- | --- | --- |
| Matters | `GET /matters.json`, `GET /matters/{id}.json` | `ids[]`, `client_id`, `query`, `status` | `id,etag,display_number,description,status,open_date,pending_date,close_date,created_at,updated_at,last_activity_date,matter_stage_updated_at,client{id,name},practice_area{id,name},matter_stage{id,name},responsible_attorney{id,name},originating_attorney{id,name},statute_of_limitations{id,name,due_at},custom_field_values{id,field_name,field_type,value},contingency_fee` |
| Matter stages | `GET /matter_stages.json` | n/a | `id,name` and the practice-area linkage (nested fields **unverified**) |
| Contacts | `GET /contacts.json`, `GET /contacts/{id}.json` | no matter filter; use `GET /matters/{matter_id}/related_contacts.json` and `GET /relationships.json`, or fetch by `ids[]` | `id,etag,name,first_name,last_name,type,date_of_birth,primary_email_address,primary_phone_number,is_client,company{id,name},avatar{id,url},email_addresses,phone_numbers,addresses,custom_field_values` |
| Custom field definitions | `GET /custom_fields.json` (`parent_type`, `deleted`, `field_type`) | n/a | `id,name,parent_type,field_type,displayed,required,picklist_options` |
| Custom field values | not a standalone endpoint: nested under matter or contact as `custom_field_values{...}` | | Value fields: `id,field_name,field_type,value,custom_field,picklist_option`. Filter matters by value: `custom_field_values` and `custom_field_ids[]` params. |
| Custom field sets | `GET /custom_field_sets.json` | n/a | grouping of fields |
| Notes | `GET /notes.json` | `matter_id` (also `contact_id`, `type`) | `id,etag,type,subject,detail,detail_text_type,date,created_at,updated_at,author{id,name},matter{id},contact{id,name}` |
| Communications | `GET /communications.json` | `matter_id` (also `contact_id`, `user_id`, `type`, `received_since`) | `id,etag,subject,body,type,date,received_at,created_at,updated_at,user{id,name},senders,receivers,documents,communication_eml_file` |
| Tasks | `GET /tasks.json` | `matter_id` (also `status`, `statuses[]`, `complete`, `due_at_from`, `due_at_to`, `assignee_id`) | `id,etag,name,status,description,priority,due_at,completed_at,statute_of_limitations,created_at,updated_at,task_type{id,name},assignee,assignees,assigner{id,name}` |
| Calendar entries | `GET /calendar_entries.json` | `matter_id` (also `from`, `to`, `calendar_id`, `visible`) | `id,etag,summary,description,location,start_at,end_at,all_day,court_rule,created_at,updated_at,calendar_entry_event_type{id,name},attendees,matter{id}`. Note: calendar entry `id` is a **string** in the schema. `from`/`to` bounds likely required in practice to get a window; **unverified**, probe without them. |
| Documents | `GET /documents.json` | `matter_id` (also `parent_id`, `contact_id`, `document_category_id`, `include_deleted`) | `id,etag,name,filename,size,content_type,created_at,updated_at,received_at,parent{id,name},document_category{id,name},creator{id,name},latest_document_version{id,version_number,size,filename,fully_uploaded,created_at}` |
| Folders | `GET /folders.json` | `matter_id`, `parent_id` | `id,etag,name,root,parent{id},type,created_at,updated_at`. Listing a matter's root folders first, then children by `parent_id`, rebuilds the tree. `GET /documents.json` also returns subfolders via `type`; **unverified**, probe. |
| Document versions | `GET /documents/{id}/versions.json` (filter `fully_uploaded`) | via document | `id,document_id,version_number,filename,size,content_type,created_at,fully_uploaded` |
| Activities (time and expenses) | `GET /activities.json` | `matter_id` (also `type`, `expense_category_id`, `start_date`, `end_date`) | `id,etag,type,date,note,quantity,price,total,billed,non_billable,expense_category{id,name},user{id,name},vendor{id,name},matter{id}`. Expenses are activities with `type=ExpenseEntry` (type value **unverified**, probe). No separate `/expenses` endpoint exists in the spec. |
| Expense categories | `GET /expense_categories.json` | n/a | names |
| Users / who am I | `GET /users.json`, `GET /users/who_am_i.json` | n/a | cheap first call to prove the token works |
| Bills / balances | `GET /bills.json`, `GET /outstanding_client_balances.json` | | not needed unless "case spend" wants billed amounts |

Spec shows `Contact.addresses`, `.email_addresses`, `.phone_numbers` as arrays; nested sub-fields are **unverified** (use `addresses{id,street,city,province,postal_code,name}` only after probing, a wrong name is a 400).

## 7. Documents: file bytes

- `GET /documents/{id}/download.json` returns **303 See Other** redirecting to the file URL (docs, OpenAPI). Optional `document_version_id` query; defaults to the latest.
- Implementation notes (**unverified**, probe): follow the redirect, but do not forward the `Authorization` header to the redirect host (it is very likely a pre-signed object-storage URL; sending a Bearer to it can cause a 400). In httpx: request with `follow_redirects=False`, read `Location`, then GET that URL without auth.
- Store `etag` and `latest_document_version.id` with each file; skip re-download when unchanged. The live matter holds 31 documents (361 pages, about 15 MB in total, 10 pages image-only), so download once and cache on disk, outside git.
- Does a matter's document list include only the files, or also folders? Probe `type` on a first call.

## 8. Client photo

- Contacts carry `avatar{id,etag,created_at,updated_at,url}` (docs, OpenAPI `Avatar_base`). That is the primary place for a photo; request `fields=id,name,avatar{url}` on the client contact. Whether the organisers' setup populated it: the setup JSON has no avatar field, so **probably empty**. The avatar `url` access (public vs needing the Bearer) is **unverified**.
- The organiser case file includes a photo-ID scan as a matter document (image-only PDF). Fallback: download that document and crop/render the face, or show initials if neither exists. This choice is a product decision; note whichever is used.

## 9. Suggested first probe sequence (about 10 calls, GET only)

1. `GET /users/who_am_i.json?fields=id,name,account{id,name}` (proves token and region; the `account` nesting is **unverified**, drop it if 400).
2. `GET /matters.json?fields=id,display_number,description,status&limit=200` to find the matter id.
3. `GET /matters/{id}.json?fields=<matter fields above>`.
4. One list call per object in section 6 with `matter_id`, checking `meta.paging`.
5. `GET /documents/{id}/download.json` on the smallest document, `follow_redirects=False`.
6. Record `X-RateLimit-*` headers from any response and log them at debug level.

## 10. Gaps (cannot confirm without a live token)

- Redirect URI acceptance for `127.0.0.1`; non-US OAuth hosts; refresh-request field list; actual access-token lifetime.
- Whether deletions surface through `updated_since`.
- Whether `304` responses are free against the rate limit (ETags docs page was unreachable).
- Whether the avatar is populated and how its URL is authorised.
- Expense `type` literal, calendar `from`/`to` requirement, folders-vs-documents listing behaviour.
