# Negotiation: method, sources and limits

The Negotiation tab is an analysis of the file under assumptions the attorney sets. It is not legal advice and not a prediction, and it never states what a court or the other side will do. It is strategy and valuation: firm-only, behind the firm session guard, absent from every provider payload, with no share category.

Code: `server/negotiation/` (`model.py` arithmetic, `analysis.py` the response, `evidence.py` the one model call, `api.py` routes). Page: `web/v2/tabs/negotiation.js`. The page prints what the endpoint returns; the only arithmetic in the browser is where a bar sits on its track.

## Where each number comes from

| Term | Source | If absent |
| --- | --- | --- |
| `V` the firm's estimate | value node of the case model (a field in the matter) | no analysis; the page says so |
| `E` documented part of `V` | `river.evidence_backed_amount`: components whose status is confirmed by a document on file | 0 |
| coverage layers, counted or not | coverage nodes under the coverage node | only the "not limited by coverage" scenario |
| liens `L` | lien node | not counted as zero: swept from nothing to the client's whole net at the judgment, so every net figure is a range, and listed as an open fact |
| costs `C` | cost node | left out, and the page says none are recorded |
| fee `f`, and whether it applies after costs | the firm's setting | figures are before fee, and the page says which way that moves the walk-away |
| open facts | review cards, by the node each bears on (gate or coverage, value, lien) | the fact is listed with no cards |
| every probability, rate and cost below | the attorney, in the Assumptions drawer | see "Unset inputs" |

No probability, discount rate or cost is supplied by the software.

## The model

```
J(q)      = E + q (V - E)                         judgment if the client prevails
net(x)    = x - fee(x) - L - C, floored at 0      client's net from a gross recovery x
W(s)      = d * p * net(min(J, ceiling_s); C + c_client)
                                                  client's expected net from trying the case under scenario s
walk(s)   = (W + L + C) / (1 - f)                 gross settlement that nets the client W  (fee on gross)
          = (W + L) / (1 - f) + C                 (fee after costs)
top(s)    = min(ceiling_s, p * min(J, ceiling_s) + c_other)   or ceiling_s when c_other is unset
target(s) = walk + w (top - walk),   w = r_other / (r_client + r_other)
d         = exp(-r_client * months / 12)
tree      = (1 - g) W(counted) + g W(not limited by coverage)
```

`p` chance of a finding for the client, `q` share of the undocumented estimate that holds, `g` chance recovery is not limited to the counted coverage, `r` each side's cost of delay per year, `c` each side's further cost to try the case.

- **Reservation values and the zone of agreement.** Each side's walk-away is its expected outcome at trial adjusted for its own cost of getting there; a settlement exists between them (Raiffa 1982; Mnookin and Kornhauser 1979, written about divorce; Priest and Klein 1984). The range "plaintiff's expected judgment less its costs, up to defendant's expected judgment plus its costs" is the older Landes, Posner and Gould framework (not checked); Bebchuk (1984) is cited only for the point that settlement can fail when one side knows more than the other. The client's walk-away is computed on the client's net, not the gross, because liens, costs and fee come out of a settlement too. Because a loss nets nothing but a settlement still pays liens and costs first, the walk-away has a floor at the gross where the client nets nothing.
- **Coverage scenarios.** The ceiling is what can be collected, not what the case is worth. One row per scenario: the layer the file counts; every listed layer added together (labelled as unsupported by the file); and recovery not limited by the listed coverage.
- **Target: Nash and Rubinstein.** Nash (1950) picks the point that maximises the product of the two sides' gains over walking away. Rubinstein (1982) derives a unique split from alternating offers when delay is costly: with discount factors `d1` (first proposer) and `d2` the proposer takes `(1 - d2) / (1 - d1 d2)`, so a side's share rises with its own patience and falls with the other's, plus an advantage to whoever moves first. Binmore, Rubinstein and Wolinsky (1986) show the alternating-offers outcome converges to a Nash solution as the time between offers goes to zero, and note that unequal discount factors call for an asymmetric one. The code uses that limit: with `d = exp(-r * interval)`, the proposer's share tends to `r_other / (r_client + r_other)` as the interval goes to zero (derived here from Rubinstein's formula; the weight is not written in that form in the 1986 paper, and the textbook usually cited for it, Muthoo 1999, was not checked). The first-mover advantage vanishes in the limit. With equal rates this is the midpoint.
- **Decision tree.** Litigation valued as a tree over a small number of open facts (gate open or shut, finding for or against, estimate holds or not), in the manner of litigation risk analysis (Victor 1985 is an early, widely cited treatment).
- **What moves the number.** For each open fact the code computes the swing: expected net with the fact resolved one way minus the other, across the span of every unset input. This is the tornado-diagram measure, and it is an upper bound on Howard's (1966) expected value of perfect information, not the EVPI itself: EVPI needs a prior on each fact, which the file does not hold. The swing is also given per coverage scenario, which is what lets the plan say that a dispute over value moves nothing while the documented components already reach the ceiling.
- **Opening and concessions.** The opening is the scenario's ceiling: the highest figure the file gives a basis for. First offers anchor outcomes (Galinsky and Mussweiler 2001). Their abstract reports the advantage was eliminated when the other side focused on the offerer's alternatives or reservation price, so the opening is not a lever on its own. If the other side's offer is entered, the midpoint of the two openings is compared with the target (Raiffa's observation that the midpoint of the opening offers predicts the outcome; the qualifier that it must lie inside the zone is from memory). The concession path is `target + (opening - target) * ratio^k`, never below the walk-away; the ratio is the attorney's. Decreasing concessions are read by the recipient as an approaching limit (Tey et al. 2021); that is a finding about what the other side infers, not a way to read their limit, and the code infers nothing from the other side's moves.
- **Deadlines and risk attitudes.** Spier (1992) is a theoretical model predicting that much settlement happens just before trial; Rachlinski (1996), applying Kahneman and Tversky (1979), reports plaintiffs tending risk-averse and defendants risk-seeking. Neither is in the arithmetic: the code assumes a risk-neutral client and no deadline.

## Unset inputs

A probability that is not set is swept over 0 to 1 and every figure depending on it is a range (evaluated at the corners; each output is monotone in each probability). With either delay rate unset, the split `w` is swept over 0 to 1, so the target is the whole zone. A cost, the months to trial or the concession ratio that is not set is left out, and the drawer says which direction that moves the result. The dollar inputs are bounded (at most 1e12, finite), the analysis runs before an input is stored, and a stored value that cannot be read or computed is set aside with a note.

## The model call

One call, purpose `negotiation`, `store=False`, through `server/digest/llm.py`. Input: for each open fact with review cards, a generic description of the fact and the statements of the claims on those cards with their ids. Output, schema-validated: up to two sentences on what supports the higher outcome and up to two on the lower, each with claim ids. Code keeps only ids it sent and resolves them to sources; a side with no valid id is dropped. Stored against a hash of the prompt version and the statements sent, so it is rewritten only when the ledger behind the cards changes. The instruction text names no party, subject or amount. No figure depends on this call.

## Routes

`GET /api/matters/{id}/negotiation`, `PUT /api/matters/{id}/negotiation/inputs`, `POST /api/matters/{id}/negotiation/evidence`. Inputs and summaries live in our `settings` table, never in the source system.

## Weakest assumptions

1. **`E` and `q` stand in for damages.** "Documented" means a component has a document on file, not that a fact-finder would award it, and the undocumented remainder is scaled by one number. Non-economic damages have no model of their own. To check: compare against verdict and settlement data for the venue and injury, which the app does not have.
2. **A loss nets the client nothing, and the top of the zone is the ceiling unless the other side's cost is entered.** Whether the client owes costs after a loss depends on the retainer; and the other side's real limit depends on its own view of `p`, which is taken to equal the attorney's (common expectations). Priest and Klein's point is that trials happen when those views diverge, so the zone is optimistic when they do.
3. **The split uses the zero-interval limit and a risk-neutral client.** Real bargaining has discrete rounds, deadlines, insurer reserve and bad-faith dynamics, and a client who may prefer certainty. The listed layers are also simply added in one scenario, which the file does not support and state law may not allow.

Also: the swing is not EVPI (above); the gate is binary; liens are taken at the stated figure with no statutory reduction.

## Sources

Every DOI below was matched against the Crossref registry on 2026-10-02 (authors, title, journal, volume). What each paper is cited for was confirmed from its abstract or a secondary summary where the last column says so; no paper was read in full in this session except pages 186-187 of Binmore, Rubinstein and Wolinsky.

| Source | URL | What was confirmed |
| --- | --- | --- |
| Nash, "The Bargaining Problem", Econometrica 18(2):155-162, 1950 | https://doi.org/10.2307/1907266 | Crossref; product rule confirmed from the 1986 paper's statement of it |
| Rubinstein, "Perfect Equilibrium in a Bargaining Model", Econometrica 50(1):97-109, 1982 | https://doi.org/10.2307/1912531 | Crossref; formula confirmed from a secondary source, not the paper |
| Binmore, Rubinstein, Wolinsky, "The Nash Bargaining Solution in Economic Modelling", RAND J. Econ. 17(2):176-188, 1986 | https://doi.org/10.2307/2555382 | Crossref; convergence and the footnote on unequal discount factors read in the paper; the `r/(r+r)` weight is derived here, not quoted |
| Mnookin, Kornhauser, "Bargaining in the Shadow of the Law: The Case of Divorce", Yale L.J. 88(5):950, 1979 | https://doi.org/10.2307/795824 | Crossref; substance from secondary summaries |
| Priest, Klein, "The Selection of Disputes for Litigation", J. Legal Stud. 13(1):1-55, 1984 | https://doi.org/10.1086/467732 | Crossref; abstract as summarised in search results |
| Bebchuk, "Litigation and Settlement under Imperfect Information", RAND J. Econ. 15(3):404-415, 1984 | https://doi.org/10.2307/2555448 | Crossref; abstract |
| Spier, "The Dynamics of Pretrial Negotiation", Rev. Econ. Stud. 59(1):93-108, 1992 | https://doi.org/10.2307/2297927 | Crossref; abstract. A model's prediction, not a measured share |
| Howard, "Information Value Theory", IEEE Trans. Systems Science and Cybernetics 2(1):22-26, 1966 | https://doi.org/10.1109/TSSC.1966.300074 | Crossref; abstract. The after-minus-before definition is the standard reading, body not read |
| Galinsky, Mussweiler, "First Offers as Anchors", J. Personality and Social Psychology 81(4):657-669, 2001 | https://doi.org/10.1037/0022-3514.81.4.657 | Crossref; abstract |
| Kahneman, Tversky, "Prospect Theory", Econometrica 47(2):263-291, 1979 | https://doi.org/10.2307/1914185 | Crossref |
| Rachlinski, "Gains, Losses, and the Psychology of Litigation", S. Cal. L. Rev. 70(1):113, 1996 | https://papers.ssrn.com/sol3/papers.cfm?abstract_id=10549 | abstract; start page not confirmed |
| Tey, Schaerer, Madan, Swaab, "The Impact of Concession Patterns on Negotiations", Organizational Behavior and Human Decision Processes 165:153-166, 2021 | https://doi.org/10.1016/j.obhdp.2021.05.003 | Crossref; abstract |
| Raiffa, The Art and Science of Negotiation, Harvard University Press, 1982 | https://openlibrary.org/books/OL3486597M/The_art_and_science_of_negotiation | existence, and the framework from a secondary summary; URL seen in search results, not fetched |
| Victor, "The Proper Use of Decision Analysis to Assist Litigation Strategy", The Business Lawyer 40:617, 1985 | https://www.litigationrisk.com/m-ov-articles.htm | the author's own publication list; no DOI found |
| Syverud, "The Duty to Settle", Virginia L. Rev. 76(6):1113-1209, 1990 | https://doi.org/10.2307/1073190 | Crossref only. Not modelled |
| Hyman, Black, Silver, "Settlement at Policy Limits and the Duty to Settle: Evidence from Texas", J. Empirical Legal Stud. 8(1):48-84, 2011 | https://doi.org/10.1111/j.1740-1461.2010.01207.x | Crossref; abstract (payouts above limits are uncommon). Texas data; not modelled |

Not verified, and said so where used: Muthoo (1999) for the weight formula; Landes (1971), Posner (1973) and Gould (1973) for the settlement range; Raiffa's inside-the-zone qualifier. The duty to settle and any time-limited limits demand are state law; no source was checked for this matter's venue, and the code models neither.
