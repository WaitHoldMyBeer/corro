# Integrations: none are built

**No integration with other legal software is built.** What follows is a note on how one would be approached: the kinds of tools a personal-injury firm may already use, and what each kind publicly documents for connecting to it. Nothing here is a partnership, a certification or a working data flow. In the product, the tiles read "Available as a connector, not connected", and that is all they mean.

Written 2026-10-02. Sources are vendor pages and secondary summaries; several vendor sites would not load, so treat any detail as something to confirm before relying on it.

## Kinds of tools, and what is publicly documented

| Kind | What the tools do | Public API or integration programme |
| --- | --- | --- |
| AI case-analysis and medical-chronology tools | Read a case file and produce chronologies, case economics, gap flags and demand drafts for plaintiff firms. | Several list connectors to case-management systems on their own pages; a public developer API is generally not found. |
| Legal research and drafting suites | Research, document analysis and drafting for lawyers. | Some vendors publish developer portals or partner programmes; availability for third parties varies and is not assumed. |
| Drafting assistants | Draft or review documents inside a word processor or a case record. | Mostly not found. |
| Intake and client-relationship tools | Answer calls and web enquiries, screen leads, and create matters. | Native integrations with case-management systems and Zapier are described by the vendors. |
| Practice and case management | Hold the matter: contacts, documents, tasks, billing. | Clio Manage has a documented REST API v4 (the source this product reads, read-only). Other case-management systems document REST APIs or integration programmes. |

Examples named in public pages, for orientation only: Clio Manage (API: https://docs.developers.clio.com/), Filevine (API: https://developer.filevine.io), CASEpeer, SmartAdvocate, Litify and Smokeball (practice management); EvenUp and Eve (case analysis and demand drafting: https://www.evenuplaw.com/products/integrations/ , https://www.eve.legal/platform); Smith.ai and Lawmatics (intake: https://smith.ai/blog/legal-intake-software); Harvey and Lexis+ with Protege (research and drafting; Harvey describes an API for organisations: https://agentsapis.com/harvey/, a secondary source); Spellbook (drafting in Word).

## How a connector would be approached (not built)

- Read the matter through each practice-management system's API, as Clio is read today: GET only, into our own store.
- Export the product's cited documents (a chronology, a records summary) to a drafting tool, with every reference carried as a page and quote.
- For each vendor, start from what it documents publicly and confirm it directly; do not assume any API exists.
- No tile, label or sentence should imply an existing integration.
