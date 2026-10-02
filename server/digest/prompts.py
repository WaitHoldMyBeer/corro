"""Instructions sent to the model. They describe the task for any personal-injury
matter; nothing in them refers to a particular case. Changing a prompt changes
its version, which invalidates the cached results that were produced with it."""

PAGES_VERSION = "pages-1"
CLAIMS_VERSION = "claims-1"
RECONCILE_VERSION = "reconcile-2"

PAGES = """You read pages from a law firm's personal-injury case file. Each page is an image, labelled with its page number, sometimes followed by the page's own text layer.

Return one entry per page given, using the page numbers given. For each page:
- page_type: what kind of page it is.
- title: the heading printed on the page, if any.
- issuer: the organisation or person whose document this is (the provider, court, insurer, agency or firm named in the letterhead, caption or form header), exactly as printed; null if not shown.
- document_date: the date printed on the page as the date of the document, as YYYY-MM-DD; null if none.
- shows_photo_of_a_person: true only if the page contains a photograph of a person's face.
- checkboxes: every tick-box, checkbox or circled option on a form whose state records a fact about the incident, the people involved, insurance, injury or treatment. Give the printed label and whether it is marked. Look closely at the image; do not guess a state you cannot see.
- facts: the statements on this page that would matter to a lawyer or a medical provider on the case: injuries and diagnoses, procedures and visits with their dates, charges, totals and balances, how the incident happened, each person present or involved and their role, insurance carriers, policy numbers and limits, employment and income, court events, amounts demanded or offered. A marked checkbox that states such a fact is also a fact.

For every fact:
- statement: one plain sentence saying only what the page says. No inference, no legal conclusion.
- quote: the words on the page that support it, copied exactly as written, at most 200 characters. For a checkbox, quote its printed label.
- date: the date the fact refers to, YYYY-MM-DD, only if printed on the page.
- amount_usd: the dollar figure, only if printed on the page.
- party: the person or organisation the fact is about or attributed to, as printed.

Read handwriting and stamps if legible; if a word is illegible, leave the fact out rather than guessing. A blank, cover or fax-header page has no facts. Prefer fewer, accurate facts to many doubtful ones; a dense ledger page may be reported as its totals and date range rather than line by line."""

PHOTO = """The image is one page of a document that carries a photograph of a person's face, such as an identity card. Return the box around that photograph (the portrait only, not the whole card) as fractions of the page: left and right measured from the left edge, top and bottom measured from the top edge, each between 0 and 1. If there is no photograph of a face, return found = false and zeros."""

CLAIMS = """You read records from a law firm's case-management system for one personal-injury matter: notes, logged emails and calls, tasks, calendar entries and custom fields. Each record is given with a key such as note:123.

Extract the statements of fact or position each record makes that matter to understanding the case: what happened, injuries and treatment, who is involved, liability, insurance and coverage, liens, values and amounts, what has or has not been obtained or done, what is being waited on and from whom, and procedural status.

For every claim:
- item: the key of the record it comes from, exactly as given.
- kind: the kind of fact.
- topic: a short neutral noun phrase naming the subject, phrased so that two records about the same subject get the same topic (for example "date of incident", "policy limits", "records request to a provider", "client's income").
- statement: one plain sentence saying only what the record says.
- quote: the words of the record that support it, copied exactly, at most 200 characters.
- date: the date the statement is about, YYYY-MM-DD, only if the record gives it. Not the date the record was written.
- amount_usd: the dollar figure, only if the record gives it.
- party: the person or organisation the statement is about.
- category: who may see it. status = where the matter stands procedurally, no amounts or reasoning. bills = a treating provider's own charges or balance. records = a treating provider's own records and requests for them. attendance = the client's appointments with a treating provider. asks = a current request the firm makes of a treating provider. coverage = only whether coverage is established, no limits or amounts. valuation = any figure or reasoning about what the case is worth, limits, offers, liens, fees. strategy = counsel's impressions and plans, liability assessment, weaknesses, witnesses, why something is open. other_party = information about anyone other than the client and the provider concerned. internal = everything else. If unsure, or if a claim mixes categories, use the most restrictive: internal, strategy, valuation, other_party come before the rest.

Do not add anything the record does not say. Do not merge records."""

RECONCILE_DOCUMENT_VERSION = "reconcile-document-1"

RECONCILE_DOCUMENT = """A document has just been added to a personal-injury matter. You are given the claims extracted from its pages (origin document), and the claims already extracted from the firm's own case-management entries (origin notes, correspondence or field). Each has an id.

Refer to claims only by id. Do not state any fact that is not in a claim.

Find subjects where what the firm's entries say and what the new document shows do not agree, or where the entries say something is unknown, missing, not obtained or not looked into while the new document speaks to it. Each conflict lists the entry claim ids and the new document's claim ids. topic is a short neutral label. summary is one sentence in the form "Entries say ...; a document shows ...", with no legal conclusion and no view on which is right. entry_position says what the firm's own entries do with the subject, decided from the entry claims alone: outstanding when they say it is unknown, missing, not obtained, not done or still awaited; different when they state something and the document states something else. affects says which part of the case's economics the subject bears on. severity 3 = bears on whether or how much can be recovered; 2 = bears on proof; 1 = housekeeping. Only report a conflict when both sides are about the same subject. If the document agrees with the entries or does not touch them, return no conflicts."""

RECONCILE = """You are given every claim extracted from one personal-injury matter. Each has an id. Claims whose origin is notes, correspondence or field come from the firm's own case-management entries. Claims whose origin is document come from pages of documents in the file. You are also given the matter's contacts and the distinct issuers printed on document pages.

Refer to claims only by id. Do not state any fact that is not in a claim.

1. conflicts: find subjects where what the firm's entries say and what a document in the file shows do not agree, or where the entries say something is unknown, missing, not obtained or not looked into while a document in the file speaks to it. Each conflict lists the entry claim ids and the document claim ids. topic is a short neutral label. summary is one sentence in the form "Entries say ...; a document shows ...", with no legal conclusion and no view on which is right. entry_position says what the firm's own entries do with the subject, decided from the entry claims alone: outstanding when they say it is unknown, missing, not obtained, not done or still awaited; different when they state something and the document states something else. affects says which part of the case's economics the subject bears on. severity 3 = bears on whether or how much can be recovered; 2 = bears on proof; 1 = housekeeping (for example a date that differs). Only report a conflict when both sides are about the same subject. Fewer, well-founded conflicts are better than many weak ones.
2. key_facts: the few facts someone new to the case must know, at most ten, most important first: primary injuries and procedures, how liability stands, coverage, damages. Each cites the claim ids that support it, preferring document claims.
3. events: at most twelve dated claims that mark the turning points of the case, each with a short label and importance (3 = essential).
4. node_evidence: for each term of net = min(case value, reachable coverage) - liens - costs - fee, the claim ids that state or bear on its amount. For gate, give the claims about what would have to be true for recovery beyond the confirmed coverage, and in open_question a short neutral phrase naming that open fact; null when the claims name none.
5. economics: the components the firm's entries give for the case value (for example medical expenses, lost income), each as a label and the one claim id carrying its amount.
6. summary: three to five sentences bringing a lawyer up to speed, each citing the claim ids it rests on.
7. issuers: for each issuer string given, the id of the contact it is, or null if it is none of them."""
