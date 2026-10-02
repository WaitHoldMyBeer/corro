# Provider disclosure taxonomy

Product defaults the attorney can change. Not legal advice. Examples are invented.

Categories match `DisclosureCategory` in `shared/contract.py` (10 values). My review of that set is at the end.

## Rules for the classifier and the view builder

1. Allowlist, not blocklist. A provider sees an item only if its category is on that provider's allowlist **and** it is scoped to that provider (see "Scope"). Unclassified or low-confidence items are `internal`.
2. One category per item. If an item mixes categories (one note with a status update and a settlement figure), it takes the most restrictive one. v1 does not redact and share the remainder.
3. `valuation`, `strategy`, `other_party`, `internal` can never be on an allowlist (enforced in code, not in the prompt).
4. Scope: `bills`, `records`, `attendance`, `asks` are limited to the receiving provider's own contact. Another provider's data is `other_party` even if it is a bill or a record.

## Categories

| Category | One-line definition for the classifier | Default | Reason |
| --- | --- | --- | --- |
| `status` | Where the matter stands procedurally and whether it is active: stage, open/pending/closed, filing, hearing or trial dates, resolution having occurred. No amounts, no reasoning. | Shared | Providers asked "is the case alive" and "tell me when it moves" without emailing. A date or stage does not reveal how the attorney will proceed. |
| `bills` | The receiving provider's own charges, payments received, adjustments and outstanding balance on its lien or letter of protection. | Shared | Their own data, necessary to the lien relationship. |
| `records` | The receiving provider's own records, records requests and receipts; the existence and date of what the firm holds from them. | Shared | Their own data. The "more than just the records they sent" request is about status, not other providers' records (see `other_party`). |
| `asks` | A specific, current request the firm makes of this provider's office: missing records, an itemised bill, a narrative report, a lien balance. | Shared | Provider asked "what does the firm need from me now"; the firm wants it answered. Intended wording is written for the provider, not copied from internal notes; in the current build the ask text is the Clio task title as it appears in Clio, so the attorney must review it in the preview before sending (per-ask approval and editable wording requested from backend, not yet built). |
| `coverage` | Only a band stating whether coverage behind the case is established (see bands below). No limits, carriers' reasoning, or amounts. | Shared (band only) | Providers' first question. Per DECISIONS 11. A band discloses no number. |
| `attendance` | Dates the patient attended or was recorded at this provider's own appointments, as far as the matter records them. No judgments ("non-compliant"). | Attorney decides | Provider asked for it, but it is the client's information, and a missed-visit signal can change how a provider treats the patient. The attorney decides whether sharing it is authorised (the client's informed consent, or implied authorisation), so it is off until switched on. |
| `valuation` | Any figure or reasoning about what the case is worth: demand, offers, counteroffers, reserves, policy limits, expected net, fee, other liens, reduction targets. | Never | Work product and negotiating position. A provider's own balance is `bills`; what the firm intends to pay on it is `strategy`. |
| `strategy` | Counsel's impressions and plans: liability assessment and disputes, weaknesses, witness views, the plan for negotiation, litigation or timing, why a coverage question is open. | Never | Attorney work product and client confidence. |
| `other_party` | Health, billing, identity or contact information about anyone other than the receiving provider's own patient relationship: other providers' bills and records, the client's unrelated history, third parties. | Never | Client confidentiality, and the minimum-necessary idea for health information (below). |
| `internal` | Everything else: firm operations, staff notes, expenses, tasks, privileged communications with the client, personal data of the client not needed by a provider (SSN, full contact history, unrelated family matters). | Never | Default sink. Fails closed. |

Where common examples land: settlement offers and policy limits -> `valuation`; liability disputes -> `strategy`; other providers' bills -> `other_party`; the patient's attendance -> `attendance`; the fact that a settlement occurred -> `status`, and the amount -> `valuation`.

## Coverage bands

Three bands plus an off state. Each discloses one bit and no number.

| Band | Shown as | Discloses | Does not disclose |
| --- | --- | --- | --- |
| `confirmed` | "A source of coverage has been confirmed" | That at least one source of coverage has been verified in writing or by the carrier. | Carrier name, limits, whether it is enough, how many. |
| `being_confirmed` | "Coverage being confirmed" | That the firm is still verifying. | Why, any dispute, or what has been tried. |
| `not_established` | "No coverage established" | That none has been found to date. | Searches done, claim theories. |
| (off) | "Not shared by the firm" | Nothing about coverage; the line itself shows so the provider is not misled by silence. | |

Fixed disclaimer shown with every band: "A status note from the firm. Not a statement of the amount available and not a promise of payment." (so a provider does not read "confirmed" as "my bill will be paid"; wording agreed with critic and backend).

Notes: `not_established` is the most consequential band for a provider (it may stop treating or press the patient for payment), so the attorney should see an explicit warning in the preview when it is selected. Whether the band is derived or attorney-set is a build decision; it must be computed from sourced facts in the matter, with the source visible to the attorney only. "Confirmed" requires a source (document or logged confirmation), never inference from a note's tone. (The band names are my proposal.)

## What "alive" and "still showing up" may honestly rest on

Available from Clio (field names in `docs/CLIO_API.md`, from the OpenAPI spec): matter `status`, `matter_stage` and `matter_stage_updated_at`, `last_activity_date`, calendar entries (with event type), tasks, communications dates.

- **Alive:** show the stage and its date, matter status (open / pending / closed), and the last-activity date as a date. Do not output "active" or "stalled" as a judgment from a threshold; this file proposes no threshold. A closed status or a recorded resolution is the only thing that can say "ended". Absence of activity means only that nothing was logged.
- **Attendance:** may rest on appointments recorded in the matter (calendar entries, notes naming a visit) for this provider. Show "last recorded visit: date" and "next appointment on file: date". Must not infer: a missed visit from the absence of a calendar entry, a patient's reasons, a treatment plan, discharge, or compliance. Unknown must be displayed as unknown. Whether a provider's own records are the better source: yes, which is why the signal should be labelled "as recorded by the firm".

## Basis for the line (general terms, product rationale)

Professional-conduct, work-product and privacy rules vary by state and by engagement. The product therefore treats everything below as firm-editable defaults, and the attorney previews and consents per provider. None of this is legal advice.

- **Client confidentiality (general basis: ABA Model Rule 1.6).** A lawyer shall not reveal information relating to the representation unless the client gives informed consent, the disclosure is "impliedly authorized in order to carry out the representation", or an exception applies (https://www.americanbar.org/groups/professional_responsibility/publications/model_rules_of_professional_conduct/rule_1_6_confidentiality_of_information/). Sharing a provider's own bill and the matter's status is easy to defend as implied authorization for the lien relationship; sharing valuation or coverage detail is not obviously so. Whether implied authorization covers a given category in a given state is a judgment for the attorney (unverified for each category).
- **Client communication (ABA Model Rule 1.4).** The lawyer must keep the client reasonably informed (https://www.americanbar.org/groups/professional_responsibility/publications/model_rules_of_professional_conduct/rule_1_4_communications/comment_on_rule_1_4/). As a general reading of the Model Rules (unverified per state), disclosure decisions rest on the client's informed consent or on implied authorisation the attorney judges, hence "attorney decides" as a control and a share log as the audit trail.
- **Work product.** Protection for an attorney's mental impressions, conclusions and legal theories exists in some form in most U.S. jurisdictions, but its scope and the waiver effect of disclosing to a third party differ and were not researched (unverified). That is a further reason `strategy` is never shareable.
- **HIPAA minimum necessary** (45 CFR 164.502(b), 164.514(d); https://www.hhs.gov/hipaa/for-professionals/privacy/guidance/minimum-necessary-requirement). It binds covered entities (providers, health plans), requires reasonable efforts to limit disclosures to the minimum necessary, and does not apply to disclosures to a provider for treatment. A law firm is generally not a covered entity, so this is a design analogy for `other_party`, not an obligation on the firm (my reading; unverified for a firm acting as a business associate). Providers sending records to the firm rely on their own authorizations, which are separate from what the firm sends back.
- **One example jurisdiction, California.** Rule 1.6 of the California Rules of Professional Conduct bars revealing information protected by Business and Professions Code 6068(e)(1) without informed consent or a permitted exception; that duty is broader than the lawyer-client privilege and includes work product (https://www.calbar.ca.gov/sites/default/files/portals/0/documents/rules/Rules-of-Professional-Conduct.pdf). California Code of Civil Procedure 2018.030 protects a writing reflecting an attorney's impressions, conclusions, opinions or legal theories (as quoted in State Bar-related search results; primary text unverified here). Other states differ.

## Review of the five-category outline and the current ten

The first outline of five categories (status, bills_records, strategy, valuation, other_party_phi) was incomplete for a lien-based treating provider; the contract's ten values fix most of it:
- Splitting `bills` from `records` is right (different controls, e.g. a firm may share a balance but not a full record set).
- `coverage`, `asks`, `attendance` had to exist because providers asked for them.
- `other_party` should not be limited to PHI: a non-health fact about another party (an adverse party's identity detail, another lienholder's amount) must also not leak. The contract's `other_party` is the right scope.
- Missing but handled by the tie-break: a `liability`/fault category. I recommend folding it into `strategy` and not adding one; the classifier must treat "who was at fault" statements as `strategy`. Add it only if testing shows classifier confusion.
- Missing: lien reduction requests. They look like an ask but encode negotiation. Rule: the firm's reduction request to a provider is a `bills` conversation the attorney writes deliberately; the classifier must not auto-generate it from internal notes (`strategy`).
- Risk: `coverage` defaulting on discloses that coverage exists in some cases, and "none established" may harm the client. Preview warning recommended (above). This is a product call for the Manager and attorney.
