# Dashboard cards: the contract

A card is one ES module in `web/v2/cards/<set>/<name>.js` with a **default export**:

```js
export default {
  id: 'overdue',                    // unique, kebab-case, stable (the saved layout stores it)
  title: 'Overdue',                 // shown in the card header and in the gallery
  group: 'Tasks',                   // gallery heading; use one of the groups below
  size: 's',                        // 's' | 'm' | 'l'  (grid columns: 1, 2, 3 of 3 on the dashboard)
  depends: ['agenda'],              // top-level keys of the case model the card reads
  summary(caseModel, ctx) { ... },  // returns a DOM Node: what is read at a glance
  detail(caseModel, ctx) { ... },   // optional Node: shown when the card is expanded
  empty(caseModel) { ... },         // optional: return a plain sentence when there is nothing to show, else falsy
};
```

Groups: `Case`, `Money`, `Tasks`, `Documents`, `Review`, `People`, `Communications`, `Providers`, `Activity`.

## Rules

1. **Summary reads in about five seconds.** At most five rows or three figures. Everything else goes in `detail`.
2. **Return DOM nodes, never HTML strings.** Build them with `ctx.el(...)` (or `document.createElement`) and put text in with `textContent`. Source text, emails and names are untrusted: no `innerHTML`, ever.
3. **Every number and date that comes from the file is a source button.** Use `ctx.chip(ref)` or call `ctx.openSource(ref)` from a click. A ref is a `SourceRef` from the case model.
4. **Never compute money, verdicts or rankings.** Print the server's value (`fact.display`, `node.amount` via `ctx.fmt.money`). Sort or limit lists, nothing more.
5. **Unknown is not zero.** A null amount is "not in the file"; an empty list is `empty()`'s sentence. `empty(c)` returning a string replaces the summary.
6. **No client facts in code, comments or fixtures.** Placeholders only.
7. **Do not read anything outside `depends`.** A card whose dependencies are missing from the response is hidden, not broken.
8. **Wording:** the product is standalone. The word "Clio" never appears in a card (Settings > Import is the only place).
9. A card must not throw. The shell wraps every call, but a thrown card shows an error tile and is counted as broken in review.
10. Keep it light: no timers, no network calls in `summary`. If a card needs more data than the case model has, use `ctx.api(path)` inside `detail` only.

## `ctx` (given to `summary` and `detail`)

| Member | What it is |
| --- | --- |
| `ctx.el(tag, props, ...kids)` | DOM builder. `props`: `class`, `text`, `onclick`, any attribute. Kids may be strings, Nodes, arrays or null. |
| `ctx.chip(ref)` | A small button that opens the source drawer for a `SourceRef`. |
| `ctx.chips(refs)` | Up to three chips. |
| `ctx.openSource(ref)` / `ctx.openSources(refs, title)` | Open the source drawer. |
| `ctx.openTab(name, params)` | Go to a shell tab: `dashboard`, `graph`, `calendar`, `communications`, `notes`, `documents`, `tasks`, `activities`, `fields`, `bills`, `transactions`, `cocounsel`, `write`, `share`. `params` is a plain object the tab reads (for example `{ id: '...' }`). |
| `ctx.openWrite(text, audience)` | Open Write with text in the box. `audience` is `{ kind: 'provider', contact_id }` or `{ kind: 'internal' }`. |
| `ctx.api(path, opts)` | Same-origin JSON call (GET unless `opts.method`). Throws `ApiError` with `.status`. |
| `ctx.matterId` | The open matter. |
| `ctx.fmt` | `money(n)` -> "$1,234" or null, `date(s)` -> "YYYY-MM-DD", `dateTime(s)`, `days(n)` -> "3 days overdue" / "in 5 days", `plural(n, 'item')`. |
| `ctx.pill(text, cls)` | A small label. `cls`: `ok`, `warn`, `st-contested`, `st-assumed`, `st-stale`. |
| `ctx.toast(text, kind)` | A short message. |

## Case model keys you can depend on

`meta`, `matter`, `brief`, `key_facts`, `custom_fields`, `contacts`, `nodes`, `river`, `moves`, `incoming`, `claims`, `conflicts`, `changes`, `agenda`, `timeline`, `spend`, `documents`, `providers`. Shapes are in `shared/contract.schema.json` (`case`). Any of them can be empty or null before the digest has run.

## Registering cards

Three manifests, one per card agent, so nobody edits the same file:

```js
// web/v2/cards/a.manifest.js
import overdue from './a/overdue.js';
import comingUp from './a/coming-up.js';
export default [overdue, comingUp];
```

`b.manifest.js` and `c.manifest.js` are the same. The registry (`web/v2/js/cards.js`) loads all three; a manifest that fails to load or a card that fails validation is skipped and listed under `registry.problems` (open `/v2/#cards` to see them). Card ids must be unique across manifests.

## The `custom` card (a lawyer's description turned into a card)

A custom card is data, not code. The server turns a description into a **spec**; the browser validates it and renders it. No code from the model is ever run.

```json
{
  "kind": "list",                  // stat | list | table | timeline | text
  "title": "Overdue and due this week",
  "source": "agenda.overdue",      // one allowed path (below)
  "fields": ["title", "due"],      // list/table/timeline: item fields to show, in order, max 5
  "limit": 5,                      // 1..25
  "sort": { "field": "due", "dir": "asc" },   // optional
  "filter": { "field": "title", "op": "contains", "value": "x" },  // optional: op is eq | contains | gt | lt
  "stat": "count",                 // stat only: count | value (value is for a single Fact)
  "text": "optional fixed sentence" // text only: a plain sentence, shown as written (max 280 chars)
}
```

Allowed `source` paths: `brief.case_value`, `brief.coverage`, `brief.firm_spend`, `brief.last_client_contact`, `brief.limitations`, `brief.stage`, `agenda.overdue`, `agenda.coming`, `agenda.waiting`, `timeline`, `conflicts`, `moves`, `documents`, `providers`, `spend.lines`, `custom_fields`, `key_facts`, `incoming`, `changes.items`, `contacts`.

Item fields are plain property names of the items at that path (`title`, `due`, `label`, `display`, `amount`, `date`, `name`, `topic`, `summary`, `subject`, `kind`, `source.label`). Dates and amounts are formatted from the field name (`due`, `date`, `*_at` -> date; `amount` -> money). A field that does not exist in an item prints nothing. `validateSpec(spec)` in `web/v2/js/cards.js` returns `{ ok, errors, spec }` and is the single place the rules live; the server's endpoint should enforce the same list.
