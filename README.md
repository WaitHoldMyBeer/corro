<p>
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="web/brand/corro-wordmark-dark.svg">
    <img src="web/brand/corro-wordmark.svg" alt="Corro" height="44">
  </picture>
</p>

The mark is an open C closed by a tick: a statement, and the source that corroborates it.

# Corro

Corro (from corroborate: every statement in the file is checked against the file) turns a Clio matter into a sourced model of the money (what the case is worth, what coverage can actually pay it, and which open facts stand between the two) and shows that one model to the firm in full and to each treating provider through a disclosure policy the attorney controls.

Built for the Swans Applied AI Hackathon, 2026-10-02. It reads one matter live from Clio Manage and never writes to Clio.

This public repository is a single-commit snapshot of our working repository at submission time; the working history holds case-handling notes and internal planning that do not belong in public, so it is not published.

## What you see

A standalone case dashboard. Clio is an import source: the product reads one matter live and read-only through Clio's API and presents it as its own dashboard; it is not a Clio add-on and never writes to Clio. The interface opens at `/` (redirecting to `/v2/`); the earlier interface is at `/classic.html`.

The loop: see, decide, write, share, reply.

- **Search and graph:** a search bar over the whole file drives a map of every document and record; hits light up. Dropping a file on the map uploads it to our own store (never to Clio).
- **Your dashboard:** a left navigation like a practice-management app and a dashboard of cards the lawyer can add, remove, reorder and describe in words. Colour schemes (6 backgrounds by 7 accents) apply to the firm side and the provider page. Record tabs (tasks, notes, communications, documents, calendar, activities, bills) have filter chips with counts, date presets, sort and group.
- **Case:** the money river (case value, reachable coverage, what is held back and why), ranked moves, the agenda, the timeline, and review cards. Every figure opens its source. "Since you last opened" moves only after a four-hour gap or "Mark as seen".
- **Write:** a live fact-checker for what the lawyer is drafting and for typed notes of a call with a provider's office. Verdicts: supported, contradicted, out of date, not in file, don't send. Tier 1 matches money amounts and dates in code; tier 2 sends the sentence and the most relevant claims to `gpt-6-luna`, which returns claim aliases and a verdict only, so quotes, pages and replacement values come from our own ledger.
- **Share:** per-provider category toggles, a preview of exactly what leaves the firm, a leak guard on free text with a logged override, a frozen snapshot per send, a share log, and an inbox for provider requests and replies.
- **Provider page:** a tokenised link showing only allowed categories: whether the case is alive, a coverage band, what the firm needs from that office, their own bills and records, and a place to ask the firm and to reply.

The checker is a tool for the lawyer, not for examining a witness. The interactive checker and the digest's reconciliation (which produces the review cards) are two call paths over one claims ledger, with one vocabulary of verdicts. It reports what the file says, not what is true. Measured on our own evaluation, which moves between runs: on a synthetic set of 30 sentences with known answers (written before the checker's prompt was tuned against it, so no longer blind) the checker with its model tier got 29 or 30 right in each of six runs (code tier alone: 26 of 30). On about 200 sentences built at run time from the real file's claims, the full checker was right 92% to 94% of the time across runs; every valuation figure addressed to a provider was locked in 38 to 40 of 40; a changed amount was caught with the file's value offered in 38 to 40 of 40 (so "every changed amount" is not true of every run); a changed date was caught in 30 to 31 of 40; the code tier alone got 174 and 185 of 200 on two digests. Median model call 1.5 s with reasoning effort none and 2.4 s at low; about 0.04 cents per call; the code tier answers in about a millisecond. Caveats: the real-file sentences repeat the file's own wording, so these are upper bounds, not a measure of paraphrase; paraphrase and strategy wording are tested only on the 30 synthetic sentences; one matter.

### What is verified and what is only built

- **Proven on a clean clone by our reviewer (one deviation: port 8001 with its redirect URI registered):** `uv sync` 153 s cold; sync 2 min 51 s, 78 GET requests, 243 items, 31 files, counts equal to production; first full digest from an empty database 7 min 17 s, 105 model calls, 1,378,727 input and 241,212 output tokens, 5.17 USD; 2,803 of 2,997 quotes found in source text, 58 read from scans; 15 review cards; header figures identical to production; a share and its provider link work without a session, and a firm route called from the provider side answers 401.
- **Built and seen working on the live matter by the team:** the header, river, review cards, agenda, timeline, source drawer, Share (toggles, preview, send, snapshot, log, revoke), provider page and replies, re-sync.
- **Built today, lightly tested, not claimed beyond that:** the colour schemes; "Your cards" in Add a card; record-tab filters, presets, sort and group; the four-hour "Since you last opened"; file drop on the map (passed against the real route on a private instance, never run on production); describing a card or a whole dashboard in words; changing what a provider sees by describing it (closed-set operations, never-shared categories refused in code); the negotiation analysis (firm only; method in [`docs/NEGOTIATION.md`](docs/NEGOTIATION.md)); the five-statements review queue; the integrations section (tiles read "Available as a connector, not connected").
- **Assistant section:** seen working on the live matter with the model before the account ran out of credit (not re-verified since): sourced answers, a medical chronology of 256 to 260 dated entries whose citations open the page, history, created files and a PDF viewer. It is a section beside the dashboard that drafts documents from the file, not the product and not a chat you have to ask.
- **Graph smoothness (measured by the graph's own tool, not independently verified):** frame cost under 1 ms at the 95th percentile; occasional late frames seen on a loaded machine; first paint can stall 2 to 3 s if the browser is cold-started onto the page.

## Run it

### Prerequisites

- Python 3.12 or newer, and [`uv`](https://docs.astral.sh/uv/) (it creates the virtual environment). No Node, no build step.
- To read a matter: a Clio Manage account holding the matter, a Clio developer application with read-only permissions ([`docs/CLIO_SETUP.md`](docs/CLIO_SETUP.md)), and an OpenAI API key.
- Without any of that you can still see the interface in mock mode and run the checks (below).

### Install

```
git clone <repository URL>
cd <clone>
uv sync
cp .env.example .env
```

The first `uv sync` downloads about 90 MB and can take two to three minutes with an empty cache; it has not hung.

### `.env` keys

| Key | Needed for | Notes |
| --- | --- | --- |
| `CLIO_CLIENT_ID`, `CLIO_CLIENT_SECRET` | reading Clio | The App Key and App Secret of your Clio developer application. |
| `CLIO_REDIRECT_URI` | reading Clio | Default `http://127.0.0.1:8000/oauth/callback`. Register exactly this URI on the Clio application (if Clio rejects it, `http://localhost:8000/oauth/callback`; both are in `docs/CLIO_SETUP.md`). |
| `CLIO_BASE_URL` | reading Clio | Default `https://app.clio.com` (US). Use `eu.`, `ca.` or `au.` hosts for other regions. |
| `CLIO_MATTER_ID` | optional | Clio matter id to open by default. If empty, the app uses the last one chosen or the only matter in the account; `uv run python -m server matters` lists them. |
| `OPENAI_API_KEY`, `DIGEST_MODEL` | digest (reading pages, records, reconciliation) | `DIGEST_MODEL` has no default and must be set. We ran `gpt-6.1-sol`. |
| `DIGEST_MODEL_BULK` | optional | Per-page extraction model; defaults to `DIGEST_MODEL`. |
| `CHECK_MODEL` | optional | Model for the live checker's second tier; defaults to `DIGEST_MODEL_BULK`. We ran `gpt-6-luna`. Without a working model only tier 1 (code) answers. |
| `CHECK_EFFORT` | optional | Reasoning effort for the live checker's model call; `none` was measured fastest with no loss on our evaluation set. |
| `FIRM_PASSCODE` | firm login | The passcode for the firm side of the app (everything except a provider's own link). If it is set in `.env`, that value is the passcode. If not, one is generated at first start-up, stored in `data/firm_passcode` (readable only by the owner) and printed on the server console at every start-up on the line beginning "FIRM_PASSCODE is not set."; it stays the same across restarts. The firm session cookie is per host, so open a provider link in a private window or a separate browser profile. |
| `VISIT_GAP_HOURS` | optional | Hours a matter must sit unopened before the next open counts as a new visit, which moves the "since you last opened" marker. Default 4. |
| `FIRM_TIMEZONE` | optional | IANA time zone for "today", overdue and coming. Defaults to this machine's. |
| `SWANS_DATA_DIR` | optional | Where the SQLite file, tokens and downloaded files live. Default `data/` (gitignored). |

### Connect, read, digest, open

1. Start the server: `uv run python -m server serve` (API and UI on http://127.0.0.1:8000; `/` opens the new interface at `/v2/`, the earlier interface is at `/classic.html`). To run on another port, start with `uv run python -m server serve --port 8001` and set `CLIO_REDIRECT_URI=http://127.0.0.1:8001/oauth/callback`; that exact URI must also be listed on the Clio developer application. If the URI in `.env` and the port you serve on differ, Clio sends the callback to the wrong instance and the sign-in fails with an OAuth state error.
2. Connect Clio once: open http://127.0.0.1:8000/oauth/start and click Allow on the Clio consent page, signed in as the account that holds the matter. Alternatively stop the server and run `uv run python -m server auth`, or, if the redirect URI is Clio's own approval page, `uv run python -m server auth --code <code shown by Clio>`.
3. List matters: `uv run python -m server matters`.
4. Read the matter into SQLite: `uv run python -m server sync [--matter <id>]`. It pulls the matter and everything attached with GET requests only, and downloads its documents.
5. Digest: `uv run python -m server digest` (or start it from the header panel in the UI). On the demo matter (361 pages) the first digest on the original install took about 13 minutes and cost about 5.83 USD; a second digest from an empty database on a clean clone took about 7 minutes and cost 5.17 USD (see "Models and cost per case"); a re-run with nothing changed sends nothing to the model.
6. Open http://127.0.0.1:8000 and pick the matter. Re-sync from the header re-reads Clio.

Timings measured from a clean clone on one machine: first `uv sync` 153 s (about 90 MB); `matters` under 1 s; `sync` 2 min 51 s (78 GET requests, 243 items, 31 document files, 14.7 MB). The firm side asks for the passcode once: with `FIRM_PASSCODE` unset the server prints "Sign in to the firm dashboard with this generated passcode: ..." on its console at start-up (look for it there; a judge who misses the line is locked out of their own install) and keeps it in `data/firm_passcode`.

Other commands: `uv run python -m server probe` (one GET per endpoint, status only), `uv run python -m server export-schema` (rewrites `shared/contract.schema.json`).

### Without a Clio account (mock mode)

Start `uv run python -m server serve`, then open:

- http://127.0.0.1:8000/?mock=1 : the new interface (and the firm screens) with placeholder data generated from the contract schema (banner says so; nothing is read from Clio);
- http://127.0.0.1:8000/?mock=empty : the state before any digest;
- http://127.0.0.1:8000/provider.html?mock=1&token=x : the provider page.

Mock mode shows the layout and flows only. It does not demonstrate the live read, the digest or the checker's model tier. The Write tab in mock mode returns canned verdicts.

### Checks

`scripts/check.sh` (add `--quick` to skip the type check; the full run fetches pyright's own tooling through `uvx` on first use, so it needs a network and the type check is optional) runs: lint (ruff, via `uvx`); types (pyright, advisory: reports problems without failing); that no non-GET request can reach the Clio API; that nothing about the demo matter is a literal in the repository; that no case material, secret or local data is tracked; the checker's evaluation on a synthetic ledger and on the local database's ledger; and the rest of `tests/`. It prints `ok`, `FAIL`, `advisory` or `SKIPPED` per step and exits non-zero on any `FAIL`.

`scripts/check.sh` exits 0 at the time of writing, and in a clean clone, with the hardcode step run against the clone's own data. `SKIPPED` means the step could not run and verified nothing. In a fresh clone the hardcode step prints "HARDCODE CHECK NOT RUN" and shows SKIPPED, because the forbidden terms are derived at run time from the organisers' reference export and from the local database of a synced matter, neither of which is in a clone. It is not a pass. After step 4 (sync) above, the step runs for real. The live-ledger checker evaluation also skips until a matter has been synced and digested.

## Stack

- Python 3.12 (`uv` manages the environment), FastAPI with uvicorn, httpx, pydantic, python-dotenv, PyMuPDF (page rendering and PDFs) and the OpenAI Python SDK.
- SQLite for our own storage.
- UI: static HTML, CSS and ES modules in `web/`, served by the same FastAPI process, calling the JSON API.
- API contract: `shared/contract.py` (pydantic), exported to `shared/contract.schema.json`.
- LLM: OpenAI GPT-6.1-sol through the OpenAI API (model ids are read from the environment). Details and limits: [`docs/LLM_API.md`](docs/LLM_API.md).

## Where data lives outside Clio

- SQLite at `data/swans.db` (gitignored; `SWANS_DATA_DIR` moves it): cached copies of Clio objects with content hashes, digest results and claims, the dashboard layout and card designs, provider-share policies, frozen share snapshots and the share log, provider messages and replies, assistant history and documents, uploaded files, cases created in our own store, and the model-call usage table.
- Downloaded documents are at `data/documents/<matter_id>/<document_id>.pdf` and rendered page images at `data/pages/`. OAuth tokens are in the SQLite file.
- Secrets are in `.env` only (gitignored). Nothing in `data/` or `.env` is committed.
- Clio remains the source of truth; a re-sync overwrites our cache.

## Clio is read-only

- The Clio client in this repository can only send GET requests; any other verb raises before a request is made, and a test asserts it. We do not claim the Clio application's token itself is read-only: its permissions screen has not been confirmed (the setup guide, [`docs/CLIO_SETUP.md`](docs/CLIO_SETUP.md), has it created with read permissions only).
- The Clio client exposes GET only: `ClioClient` has just `get`, `get_all` and `download`, and `_refuse_anything_but_get` in `server/clio/client.py` is an httpx request hook that raises `ClioWriteForbidden` before any non-GET leaves the machine. No code path sends POST, PUT, PATCH or DELETE to the Clio API.
- Check it yourself: `grep -rniE "\.(post|put|patch|delete)\(" server/clio` should return nothing outside the OAuth token exchange. The token exchange (`_token_request` in `server/clio/oauth.py`) is a POST to the OAuth token endpoint, required to obtain the token, and touches no case data.
- Endpoints, fields and rate limits are documented in [`docs/CLIO_API.md`](docs/CLIO_API.md), taken from Clio's published OpenAPI spec.

## Privacy

- Case records are read from Clio and cached only in a local SQLite file and `data/`, both gitignored. Secrets are in `.env` only.
- Documents and notes are sent to the OpenAI API for digestion. Every model call sets `store=False` (`server/digest/llm.py`); OpenAI states API data is not used for training by default, but abuse-monitoring logs can be kept up to 30 days (https://developers.openai.com/api/docs/guides/your-data).
- This build has no zero-data-retention approval and no business associate agreement, and makes no HIPAA-compliance claim. A real deployment would need the firm's own agreement with the model provider. Details: [`docs/LLM_API.md`](docs/LLM_API.md), section 10.
- Provider views are built from an attorney-controlled allowlist: [`docs/DISCLOSURE.md`](docs/DISCLOSURE.md).
- No case material is in this repository.

## Models and cost per case

| Item | Value |
| --- | --- |
| Digest and reconciliation (page reading, claim extraction, reconciliation) | `gpt-6.1-sol` |
| Live checker | tier 1 is code and uses no model; tier 2 uses `gpt-6-luna` (configured by `CHECK_MODEL`) |
| First full digest of the live matter on the original install (31 documents, 361 pages, 158 items) | 1,377,758 input tokens and 238,973 output tokens; about 5.83 USD; 13 min 3 s |
| Second full digest, from an empty database on a clean clone | 7 min 17 s, 105 model calls, 1,378,727 input and 241,212 output tokens; 5.17 USD; 2,803 of 2,997 quotes found in text, 58 from scans |
| Re-digest with nothing changed | 0 pages and 0 model calls sent, 0 USD on the clean clone (an earlier build spent 0.0017 USD on a client-photo step) |
| Checker, per model call | about 0.0004 USD on the configured model, `gpt-6-luna` (reasoning effort none) |

Live matter size: 16 custom fields, 15 contacts, 42 notes, 69 communications, 14 tasks, 17 calendar entries, 14 expense entries, 31 documents (361 pages, 14.7 MB, 10 image-only pages); the first sync on the original install made 68 GET requests and the clean-clone sync made 78 (the later build also reads bills and trust transactions); a re-sync on the unchanged matter makes 13 GET requests, reports 0 changed items and makes 0 model calls (the sync also reads bills and trust transactions; this matter has none). The digest produced 2,953 claims, 16 conflict cards (4 "answered gap": the firm's entries call something outstanding and a page in the file holds it; 5 differing records; 7 expert opinions), 10 key facts and 5 summary sentences. Page reading was 89 calls of 5 pages at 150 dpi (about 3,190 input tokens and 0.013 USD per page).

Cost per case: the first full digest cost 5.83 USD; one rebuild of the conflict cards added 0.58 USD (117 s), so the matter as it stands has cost about 6.40 USD. Both are one matter of 361 pages, not an average. Rebuilding the cards is an explicit action that states its time and cost first. Tokens come from the `usage` field the API returns on every call, stored per call in the `llm_calls` table; prices are transcribed from OpenAI's pricing page into `server/digest/prices.json` (read 2026-10-02) and applied in code. The cost badge in the UI header also includes the checker's calls, which share the same table, so it reads higher than the digest figure. The checker's per-call figures are from test calls on the live ledger, not from lawyers typing.

Digestion is incremental: each Clio item is hashed, and only new or changed items go to the model. The UI shows the running cost. Arithmetic (totals, dates, balances) is done in code, never by the model. Every AI-derived fact carries a source reference to a Clio object. Of 2,953 quotes extracted, 2,781 were found verbatim in the source text by code; 59 come from image-only pages and cannot be checked that way; 113 were not found (mostly table rows read across columns) and are marked on screen for the reader to check against the page.

## Notes for judges: half-done or hardcoded

- Nothing about the demo matter is hardcoded: no names, dates, amounts or injuries as literals in code, prompts, mocks or fixtures. Test fixtures are synthetic (mock data is generated from the contract; test fixtures are mostly written by hand). `scripts/check.sh` runs the check.
- Half-done:
  - The checker earlier matched a figure to the wrong fact (a wrong policy limit marked "supported"); a subject-match fix is in and covered by `tests/test_check_subject_match.py`. Wording that avoids figures and paraphrase remain weaker, and a verdict means "the file supports this", not "this is true".
  - The checker's model tier depends on API credit; without it only tier 1 (code) runs.
  - The firm login is one shared passcode, with no per-person identity or server-side session list. One passcode and one session open every case in an install: separation between cases is of data, not access.
  - The model account ran out of credit during the build, so model-backed features (the assistant, card design, negotiation evidence, upload reading, the checker's second tier) were last verified before that point and not re-verified afterwards.
  - "Your cases", case switching and creating a case from a zip were built today and are lightly tested.
  - A request reaches a provider only with wording the attorney approved; tests enforce this (`tests/test_provider_projection.py`, `tests/test_provider_routes.py`). A provider's reply is checked against the file when it arrives, and that stored check is not re-run when the file changes.
  - The microphone for call notes has not been tested by voice.
  - The offline fallback (showing the last synced copy when Clio is unreachable) is untested.
  - About half the review cards pit the firm's own treating doctors against the other side's experts; they are labelled as such and are not errors in the file.
  - Tested on one matter.
  - Known server gaps are marked as expected failures in the tests: an id past 64 bits answers 500; opening, marking seen and saving a dashboard store a row for a matter that was never imported; a case name of only white space is accepted.
  - Uploaded files of types the product cannot read are stored as inert downloads (never opened or executed). Importing a case from a zip holds the archive in memory, capped at 200 MB.
  - The send guard is assistive: in our tests it let through a client confidence the file does not record, in some phrasings. The incoming check on a provider's own figure depends on wording.
  - Uploads are stored only in our database and are marked "uploaded".
  - Reconciliation cards can differ between digest runs.
  - A review made on a card that later splits in a rebuild is carried to both successors.
  - A stored check on a provider's reply is not re-run when the file changes.
  - A re-sync re-reads only changed records, leaves the conflict cards, their order and the attorney's reviews in place, and marks the cards possibly out of date until an explicit rebuild (about two minutes and 0.58 USD on this matter).
  - Rebuilding the conflict cards is one model call and is not deterministic: the same matter can come back with a card more or fewer, or reworded (one card was seen in one rebuild and absent in another).
  - Whether a card is an "answered gap" or a "differing record" is the model's reading of the firm's entry, constrained to two values; "expert opinion" is decided in code from the document's folder. Nothing in the source data says which party produced a document.
  - Communications already in the matter from outside the firm are checked against the file as of the date they were written, never against their own text.
  - Review cards: each is meant to carry one of three kinds (the firm's entries call something missing, unknown or outstanding and a page in the file answers it; a record in the file differs from an entry; an expert's opinion in the file differs from the firm's reading), ranked in that order. The sixteen cards on this matter are differences for review, not errors.
  - The checker's model-tier evaluations skip unless `CHECK_EVAL_MODEL=1` is set, because they call the configured model and cost money; `scripts/check.sh` therefore reports the code tier's figures only.
  - The command-line digest and the running server read the same working tree, so a digest run after a code change can store results that the running server will not show until it is restarted.
  - By rule, not by model: contact roles, the header's field mapping and "who owes this task" are keyword rules on Clio's own text (marked `computed` in the data); coverage, lien and wage-loss amounts are the first dollar figure in a free-text Clio field; the fee percentage is one firm-wide setting, not per case, and is unset.
  - The conflict cards are the model's pairing of claims and stay "unreviewed" until an attorney rules; quotes on image-only pages cannot be checked in code.
- Call mode: typed call notes are the real path. The optional microphone button feeds the browser's speech recognition into the same box; by default Chrome sends audio to a web service (MDN), it hears only this machine's microphone, and recording a call needs other parties' consent where the law requires it (California is all-party for confidential communications). Demo-only; no compliance claim. See [`docs/CALLS.md`](docs/CALLS.md).
- Checker accuracy: the 92% to 94% on about 200 sentences built from the real file repeats the file's own wording, so it is an upper bound, not a measure of paraphrase; paraphrase and strategy wording are tested only on 30 synthetic sentences; one matter.
- Image-only pages (10 of the live matter's 361) are read by a vision-capable model; quality on handwritten or low-resolution pages is unmeasured.
- The firm side needs one shared passcode (not per-person accounts); a provider link needs none. The provider link is a tokenised URL on localhost, not an authenticated production portal. On-screen wording says "the matter record"; the data is read live and read-only from Clio.

## Repository map

`server/` (Python, FastAPI; each line says what the module does and how much we have seen it work):
- `clio/` (client, oauth, resources): the read-only Clio client; the GET-only guard is `clio/client.py`. Verified.
- `sync.py`, `db.py`, `config.py`, `cli.py`, `app.py`: read the matter into SQLite, storage, settings, the command line, the web app. Verified (clean clone).
- `case.py`, `river.py`, `rules.py`, `moves.py`, `sources.py`, `pages.py`: the case model computed in code (header, river, agenda, timeline, ranked moves, source pages). Verified on the live matter.
- `digest/`: the only code that calls the model for reading pages, claims and reconciliation. Verified (two full runs).
- `check/`: the live checker (tier 1 code, tier 2 model, leak guard). Measured as above; known weaknesses are in the notes for judges.
- `share.py`, `threads.py`, `firm_auth.py`: provider views built from an allowlist, requests and messages, the shared firm passcode. Verified on the clean clone: provider link works without a session, firm routes answer 401 from the provider side.
- `share_customise.py`: change what a provider sees by describing it. Built, lightly tested; closed set of operations.
- `dashboard.py`, `cards/`: the dashboard layout and cards described in words, checked against a catalogue derived from the contract. Built today, lightly tested.
- `graph/`: builds the case graph and the search index (also used by the assistant). Built today; frame cost measured as above.
- `ingest.py`: file upload with incremental ingestion into our own store. Run against the real route on a private instance only.
- `review_queue.py`: five statements every five days to keep, discard or comment on; selection is code. Built today.
- `negotiation/`: bargaining arithmetic over the case model (no model call) plus one model call for evidence sentences. Firm only. Built today.
- `costs/`: spend by purpose from the usage table, arithmetic only. Built today.
- `assistant/`: tool-using assistant with 12 read-only tools, structured answers whose sentences cite claim ids, documents assembled in code. Live and verified on the live matter (answers, chronology, history, files, PDF viewer).
- `cases.py`: many cases in one install (cases created in our own store, archive and restore, an overview from stored data only). Built today, lightly tested; separation between cases is of data, not access.

`web/`: `web/v2/` is the new interface (`js/`, `css/`, `cards/`, `tabs/`); `web/js/` and `web/css/` hold the shared modules and the earlier interface (`classic.html`); `provider.html` is the provider page.
`shared/`: the API contract (`contract.py`, `check_contract.py`, `assistant_contract.py`) exported to JSON Schema.
`docs/`: [Clio setup](docs/CLIO_SETUP.md), [Clio API brief](docs/CLIO_API.md), [disclosure taxonomy](docs/DISCLOSURE.md), [OpenAI brief and data handling](docs/LLM_API.md), [calls and consent](docs/CALLS.md), [lawyer feedback sheet](docs/FEEDBACK.md), [UI notes](docs/UI_NOTES.md), [UI references](docs/UI_REFERENCE.md), [integrations](docs/INTEGRATIONS.md), [negotiation method](docs/NEGOTIATION.md), [architecture](docs/ARCHITECTURE.md).
`tests/`, `scripts/check.sh`: checks.

The case files and client records the organisers provided are not part of this repository.
