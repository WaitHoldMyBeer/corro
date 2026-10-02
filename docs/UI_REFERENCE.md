# Interface references: Clio Manage and ClickUp

Written 2026-10-02 for the new shell. The goal is familiar names and order, not copying. Sources are the vendors' public help pages as returned by search summaries: my page-fetch tool was blocked for `help.clio.com` and `help.clickup.com` this afternoon, so page text below is paraphrased from search results, not read in full. Anything marked unverified should be confirmed on the live help page before it goes on stage. No case facts here.

## A. Clio Manage

### Left navigation (account level)
Sources: https://help.clio.com/hc/en-us/articles/9290390462875-Navigate-Clio-Manage , https://support.clio.com/hc/en-us/articles/360008092594-Can-I-Customize-the-Main-Navigation-Bar-
- Items named in the sources: Dashboard, Firm Feed (monitors account activity and events), Calendar, Matters, Contacts, Activities, Tasks; Bills, Accounts and Reports appear for users with those permissions (they cannot be removed from the bar otherwise).
- The exact order, and whether Communications and Documents sit at account level, is **unverified**; use the order above.
- Each tab opens with a header of subtabs and a table below it.

### A matter's tabs
Source: https://help.clio.com/hc/en-us/articles/9285920226075-Clio-Manage-Matters-Overview
In order: Dashboard (overview), Activities (time entries and expenses), Calendar (upcoming by default, past and all as options), Communications (phone, email, text, internal messages, client-portal messages), Notes, Documents, Tasks, Bills. Subtabs shown depend on the firm's subscription and the user's role.

### The matter dashboard
Sources: https://help.clio.com/hc/en-us/articles/16681289917595-Matter-s-Dashboard , https://support.clio.com/hc/en-us/articles/360000939634-What-Displays-on-the-Matter-Dashboard-
- Sections named: matter details (basic information, custom fields), Financials (work in progress, outstanding balance, trust funds, matter budget, time and expenses), Timeline of matter-related events, Tasks, Recent activity, contacts linked to the matter and related contacts.
- Customising: Primary Subscribers and Administrators click Customize, tick or untick sections to hide or show them, and drag them to reorder. Only visibility and order are controllable (no new section types).
- Section order on the default page: **unverified.**

### What a lawyer expects (translate to our shell)
- Left rail with Dashboard, Calendar, Matters, Contacts, Activities, Tasks; inside a matter, tabs Dashboard, Calendar, Communications, Notes, Documents, Tasks.
- A matter dashboard that opens with matter details and custom fields, a timeline, tasks, recent activity, linked contacts.
- Money shown as a few labelled totals (work in progress, outstanding, trust), each a number with a caption.
- A "Customize" mode where sections are ticked on or off and dragged.

## B. ClickUp dashboards

Sources: https://help.clickup.com/hc/en-us/articles/25757497269143-Intro-to-cards , https://help.clickup.com/hc/en-us/articles/6312164195095-Custom-cards , https://help.clickup.com/hc/en-us/articles/14237901038231-Create-a-Dashboard , https://help.clickup.com/hc/en-us/articles/26985557505303-Cards-feature-availability-and-limits

### Card catalogue (as named in the sources)
- **Task list cards:** Overdue Tasks, Tasks Due Soon (next 14 days), Priority Tasks (urgent or high), Milestones, Open Assigned Comments, and a customisable Task List. https://help.clickup.com/hc/en-us/articles/15822815173015-Task-List-cards
- **Chart cards:** Line, Bar, Pie, Battery (progress), and Calculation (sums, averages, ratios such as estimated against actual). https://help.clickup.com/hc/en-us/articles/21928345433879-Calculation-cards
- **Table card:** custom rows and columns for reporting. https://help.clickup.com/hc/en-us/articles/6312270515351-Table-cards
- **Portfolio card:** a table with one row per list or folder and its progress. https://help.clickup.com/hc/en-us/articles/6312200675991-Portfolio-cards
- **Text Block:** rich text and images (supports slash commands). **Embed:** content from another site or app. **Discussion** card.
- **Status cards**, **Tag cards**, **Sprint cards**, **Search cards** (Most Popular, My Trending Work, New Content, custom), **Time reporting / Timesheet** cards, **Workload** (team capacity).
- Plan tiers: Free, Standard, Advanced, AI cards. Details of which card is in which tier: **unverified.**

### What you can do with a card
- Add: "+ Card" top right; choose a pre-made card or build a custom one by category in a sidebar. https://help.clickup.com/hc/en-us/articles/14237901038231-Create-a-Dashboard
- Move and resize: drag a card; other cards reflow; resize by dragging sides or corners in edit mode; auto layout is available. https://help.clickup.com/hc/en-us/articles/34275916892951-Move-and-resize-cards-on-Dashboards
- Duplicate, delete, rename; set filters per card ("Filters" on the card); quick card settings; drill down from a card to the items behind it. https://help.clickup.com/hc/en-us/articles/6312246075159-Use-Dashboard-card-filters , https://help.clickup.com/hc/en-us/articles/14995002699927-Drill-down-view-for-Dashboard-cards
- Group by (in custom cards): by date, status, assignee, list and similar; exact options per card: **unverified.**

### What a lawyer expects (translate)
- A "+ Card" button, a gallery of ready-made cards, and a "Custom" option.
- Cards that look the same way: title, one number or short list, a "view all" link.
- Per-card filter, duplicate, delete, and drag to reorder.

## C. Suggested mapping for the new shell (our names)

| Their name | Our card |
| --- | --- |
| Overdue Tasks / Tasks Due Soon | Agenda: overdue, coming, waiting |
| Matter details + custom fields | Matter details (firm's Clio fields) |
| Financials | Money: value, coverage, held back, liens, costs |
| Timeline | What matters (dated entries with their source) |
| Recent activity / Firm Feed | Since you last opened |
| Pie / Bar / Calculation | Medical bills by provider; value breakdown |
| Table card | Providers: records, bills, attendance, open requests |
| Text Block | Notes you pin |
| Discussion | Provider inbox |
| Portfolio | Review cards ("differences for review") |
| Search card | Search the whole file (graph) |

## D. Ten lines for the card builders
1. Left rail order: Dashboard, Search, Calendar, Notes, Communications, Documents, Tasks, Write, Share (Clio's matter tabs: Dashboard, Activities, Calendar, Communications, Notes, Documents, Tasks, Bills).
2. The matter dashboard opens with matter details and custom fields, then money, timeline, tasks, recent activity, linked contacts.
3. Money cards use short captions: "Work in progress", "Outstanding", "Trust" in Clio; ours "Case value", "Coverage", "Held back".
4. Dashboard edit mode: "Customize" in Clio (tick sections, drag); "+ Card" in ClickUp (gallery, custom).
5. Cards move by drag and others reflow; resize from sides or corners.
6. Each card has duplicate, delete, rename and a filter control.
7. Name task cards as ClickUp does: Overdue, Due soon (14 days), Priority.
8. Chart cards: line, bar, pie, battery (progress), calculation (a figure computed from others).
9. Text Block (rich text) and Embed are the "free" cards; offer a text card.
10. Click a card's number to drill into the items behind it, each with its source.
