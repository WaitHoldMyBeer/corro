# OpenAI API brief for the digest (GPT-6.1-sol)

Written 2026-10-02. **docs** = from OpenAI's developer documentation, fetched today through a page-summarising tool, so wording is paraphrased: re-check any number that drives a decision against the page before relying on it. **unverified** = not stated; probe with a live call. Nothing here was run against the API (no key yet).

Base URL for all pages: `https://developers.openai.com/api/docs/` (the older `platform.openai.com/docs/...` redirects there).

## 1. Models (docs: .../models, .../models/gpt-6.1-sol)

| Model id | Context | Max output | Price per 1M tokens (input / cached input / output) | Image input |
| --- | --- | --- | --- | --- |
| `gpt-6.1-sol` | 1,050,000 (max input 922,000) | 128,000 | $2 / $0.10 / $10 (cache writes $2.50); prompts above 272K input tokens priced at 2x input and cache rates | yes |
| `gpt-6-luna` | 1,050,000 | 128,000 | $0.10 / (cached not stated) / $0.50 | yes |
| `gpt-6-astra` | 1,050,000 | 128,000 | $10 / (not stated) / $50 | yes |

- Exact id string: `gpt-6.1-sol` (snapshot id is the same string). Modalities: text and image in, text out; audio and video unsupported. Endpoints: Responses, Chat Completions, Batch.
- Cheaper sibling that accepts images, for bulk page extraction: `gpt-6-luna` (listed as the efficient high-volume model). Whether it is accurate enough on scanned medical pages is **unmeasured**; test on a handful of pages and compare before committing. Models' knowledge cutoffs are irrelevant here.
- Secondary sources (news and blogs) agree with these numbers but are not the basis.

## 2. Which API and the Python call shape

Use the **Responses API** (`client.responses`); the docs' own examples for images, files, structured output and reasoning use it. Chat Completions is also supported for this model (docs) but there is no reason to use it. Install: `pip install openai` (docs: reference page; the `uv` equivalent is `uv add openai`). Client reads `OPENAI_API_KEY` from the environment: `client = OpenAI()`.

The docs examples name `gpt-6-astra`; substitute the id from env (`DIGEST_MODEL`, `DIGEST_MODEL_BULK`).

```python
from openai import OpenAI
client = OpenAI()

# text in
r = client.responses.create(
    model=MODEL, input="...", instructions="...",   # instructions = system message
    max_output_tokens=..., reasoning={"effort": "low"},
)
text = r.output_text          # convenience accessor (unverified in the pages I read; otherwise walk r.output)

# image in (base64 data URL or https URL)
r = client.responses.create(
    model=MODEL,
    input=[{"role": "user", "content": [
        {"type": "input_text", "text": "Extract ..."},
        {"type": "input_image", "image_url": f"data:image/png;base64,{b64}", "detail": "high"},
    ]}],
)

# structured output against a JSON schema
r = client.responses.create(
    model=MODEL, input=[...],
    text={"format": {"type": "json_schema", "name": "page_extract", "strict": True, "schema": SCHEMA}},
)
# or, with a Pydantic model:
r = client.responses.parse(model=MODEL, input=[...], text_format=PageExtract)
parsed = r.output_parsed       # (unverified in the pages I read; check on first call)
```

Source for each shape: docs: .../guides/images-vision, .../guides/structured-outputs, .../api/reference (Responses create).

Strict structured output (docs): every property must be in `required`; `additionalProperties` must be `false`; some JSON Schema keywords are unsupported and nesting depth has practical limits. The exact unsupported keyword list and size limits: **unverified**, read the structured-outputs page and probe with the real schema. Refusals arrive as a separate output item of type `refusal` (docs), so check for it before parsing. Also check `r.status`: `incomplete` with reason `max_output_tokens` means truncated JSON (docs: reasoning guide).

## 3. Image input (docs: .../guides/images-vision)

- Formats: PNG, JPEG, WEBP, non-animated GIF.
- Limits: up to 512 MB total payload per request and up to 1,500 images per request (as summarised; read the page to confirm these are per-request figures). **Per-image file size limit: not stated.** Minimum dimensions not stated. Maximum 65,535 px per side.
- `detail` values: `low`, `high`, `original`, `auto` (default `auto`). `low` caps at 512 x 512 px; `high` caps by patches (docs example for a different model: 2,500 patches); `original` keeps dimensions up to 65,535 px per side, with a 30,000-patch rejection limit.
- Token counting: 32 x 32 px patches times a model-specific multiplier (the page lists 1.2 to 2.46 for other models), rounded up. **The multipliers for `gpt-6.1-sol` and `gpt-6-luna` are not stated** on the page, so the image token cost per page cannot be computed from docs. **Probe:** send one page at each `detail` setting and read `usage.input_tokens` (section 7).
- Page rendering is ours: render PDF pages to PNG at a size that keeps text legible (not stated by docs what resolution a scanned page needs; test legibility on a dense page).

## 4. PDF input (docs: .../guides/file-inputs)

- The Responses API accepts PDFs as an `input_file` content item via `file_id` (Files API upload), `file_data` (base64) or `file_url`.
- Limits: each file under 50 MB and the combined limit across all files in a request is 50 MB. Page limits for PDFs: not stated.
- On vision-capable models the API extracts both text and page images and sends both to the model (docs). Whether an image-only scan is read through those page images is **not stated explicitly**: probe with one scanned page.
- Consequence for this project: the live matter holds 31 documents, 361 pages and about 15 MB in total, of which only 10 pages are image-only (counted on the live matter). Most files have a text layer and are small, so whole-file requests fit under the 50 MB cap. For the image-only pages, and for any file where per-page provenance matters, render and send pages (or small page ranges) ourselves so each extraction carries a page number we control.

## 5. Context, output, reasoning (docs: .../guides/reasoning, model page)

- Context 1,050,000 tokens, max input 922,000, max output 128,000. Above 272K input tokens the price doubles (input and cache); avoid it by chunking.
- Reasoning effort: `reasoning={"effort": "low"}` in the Responses API. Values for `gpt-6.1-sol`: `low`, `medium` (default), `high`, `xhigh`, `max`; `none` and `minimal` are **not** supported. For per-page extraction use `low`; for reconciliation and summary use `medium` or higher after measuring. Reasoning tokens are billed as output tokens.
- OpenAI recommends reserving at least 25,000 tokens for reasoning plus output when starting out (docs); set `max_output_tokens` accordingly or JSON can be cut off.
- Temperature and sampling parameters on reasoning models: **unverified**; do not set `temperature` unless the call is accepted.

## 6. Rate limits, batch, parallelism

- Limits for `gpt-6.1-sol`, standard tier (docs: model page): Tier 1 500 RPM / 500,000 TPM; Tier 2 5,000 / 1,000,000; Tier 3 5,000 / 2,000,000; Tier 4 10,000 / 4,000,000; Tier 5 15,000 / 40,000,000. The team's tier depends on prior spend: Tier 1 needs $5 paid (docs: rate limits guide). A new account at Tier 1 allows 500,000 tokens per minute, which is the constraint for image-heavy bulk work. `gpt-6-luna` limits: not read.
- Headers (docs: .../guides/rate-limits): `x-ratelimit-limit-requests`, `x-ratelimit-remaining-requests`, `x-ratelimit-reset-requests`, `x-ratelimit-limit-tokens`, `x-ratelimit-remaining-tokens`, `x-ratelimit-limit-project-tokens`, and `Retry-After` on 429. The limit counts the larger of `max_output_tokens` and the estimated prompt size against TPM, so a large `max_output_tokens` reduces effective parallelism.
- On 429 follow `Retry-After`, then exponential backoff with jitter (docs). The Python SDK also retries by default (unverified count; set `max_retries` explicitly).
- Parallelism: a thread pool or `asyncio` with a semaphore sized from `remaining-tokens`; start at 4 to 8 concurrent page extractions and raise only while headers show headroom. Number is a starting point, not from docs.
- Batch API (docs: .../guides/batch): 50% discount against synchronous, completes within 24 hours, supports `/v1/responses`, up to 50,000 requests and 200 MB per input file, JSONL lines with `custom_id`, `method`, `url`, `body`. The docs recommend `image_url` references over base64 to stay under 200 MB. **24-hour completion makes Batch unsuitable for the live demo**; only consider it for an overnight pre-run, which this event does not allow. Skip.

## 7. Pricing and usage reporting

- Prices in section 1 (docs). Cost per case = (uncached input tokens x input price + cached input tokens x cached price + output tokens x output price) per model, summed across calls, computed in code from usage.
- Usage object on every response (docs: Responses reference): `input_tokens`, `output_tokens`, `total_tokens`; cached tokens under `input_tokens_details.cached_tokens` and reasoning tokens under `output_tokens_details.reasoning_tokens` (the reasoning page names the latter; the cached field name is **unverified**, check the first response). Reasoning tokens are already inside `output_tokens` for billing (docs: billed as output), so do not add them twice.
- Image tokens are included in `input_tokens`. Store per-call: model id, `input_tokens`, cached, `output_tokens`, so the UI cost badge and the README's cost per case come from measurement. Keep prices in config, not code, and record the date they were read (2026-10-02).
- Long-context rule: if a single call exceeds 272K input tokens the doubled rate applies to that whole call (docs wording: "prompts with more than 272K input tokens are priced at 2x input and cache rates").

## 8. Probes to run first (6 calls)

1. Text call with `reasoning={"effort": "low"}` and `max_output_tokens`, print `r.usage` to learn exact field names and whether `temperature` is rejected.
2. One page PNG at `detail` low, high, original; record `input_tokens` each, to get image cost per page and decide the detail setting.
3. One scanned PDF page via `input_file` versus the same page as `input_image`; compare outputs and tokens.
4. The real extraction schema with `strict: True`; fix any rejected keyword.
5. The same page on `gpt-6-luna` and `gpt-6.1-sol`; compare extraction quality against a page whose content you can read yourself; pick the bulk model on evidence.
6. A deliberate 429 is not needed; instead log the rate-limit headers from call 1.

## 9. Gaps

- Image token multipliers for `gpt-6.1-sol` and `gpt-6-luna`; per-image size limit; PDF page limit; behaviour on image-only PDFs.
- `gpt-6-luna` rate limits and cached-input price; the existence of a `gpt-6.1` or `gpt-6-sol` id was referred to in passing and not confirmed (use `gpt-6.1-sol` only).
- Whether `output_parsed`, `output_text` and `cached_tokens` names are exactly as written above.
- Data-retention and zero-retention terms for medical records sent to the API: not read. The Responses API defaults `store` to true (docs); set `store=False` on every call and confirm that sending these records is acceptable under the engagement terms before the run (a decision for them, not for backend).

## 10. Data handling (client medical records go to the API)

Source for items marked docs: https://developers.openai.com/api/docs/guides/your-data (fetched 2026-10-02 through a summariser). OpenAI's enterprise-privacy page and Help Center BAA article returned HTTP 403 to my fetch tool, so BAA terms below rest on secondary sources and are **unverified**.

| Question | Answer | Basis |
| --- | --- | --- |
| Are API inputs used for training by default? | No. "Data sent to the OpenAI API is not used to train or improve OpenAI models (unless you explicitly opt in to share data with us)", stated as applying from 2023-03-01. | docs |
| Default retention of API requests | Abuse-monitoring logs are kept for up to 30 days, unless the law requires longer. | docs |
| What does `store=False` do? | On `/v1/responses` and `/v1/chat/completions` it stops OpenAI persisting the response as application state. With `store` true (and no zero data retention), application state is kept for 30 days by default. | docs |
| What does `store=False` not change? | Abuse-monitoring logs (the 30-day item above) still apply; I found no statement that `store=False` removes them. Images and files are scanned on submission, and a flagged item "will be retained for manual review, even if Zero Data Retention [or similar] is enabled". | docs (inference on the first part: the page lists the logs as a separate control) |
| Zero data retention (ZDR) | Excludes customer content from abuse-monitoring logs and forces `store` to false. Available only with prior approval by OpenAI and acceptance of additional requirements; request through OpenAI sales. `/v1/responses` and `/v1/chat/completions` are eligible "with limitations". Assistants, Threads, Vector Stores and Conversations are not. | docs |
| Business associate agreement (BAA) for health information | Reported to be available for the API on request to a BAA mailbox (baa@openai.com per secondary sources), case by case, and covering only ZDR-eligible endpoints. Not confirmed on an OpenAI page by me. | secondary: https://www.accountablehq.com/post/is-openai-hipaa-compliant-current-status-baas-and-secure-alternatives , https://community.openai.com/t/zero-data-retention-information/702540 (unverified) |

What this build does (to be confirmed against the code once the digest lands; at 11:45 `server/` had no digest module):
- Every model call must set `store=False`. Keys live in `.env` only. Records and extracted text are cached only in the local SQLite database and `data/` (both gitignored), never sent anywhere except to the model API. Prompts and logs must not contain client medical details.
- Consequence: even with `store=False`, request content can sit in OpenAI's abuse-monitoring logs for up to 30 days. This build has no ZDR approval and no BAA.

What a production deployment would need: the firm's own agreement with the model provider (ZDR and, where the firm is a business associate or handles PHI under its own policies, a BAA), a documented retention schedule, client consent where the engagement requires it, and a legal review. We have not established HIPAA compliance and make no such claim. Whether a law firm needs a BAA at all depends on its role (see `docs/DISCLOSURE.md`, which notes a firm is generally not a HIPAA covered entity); that is for the firm's counsel.

Open question before running scanned documents: is it acceptable under the sample matter's terms to send these records to the API for the hackathon, given a 30-day log retention and no BAA?
