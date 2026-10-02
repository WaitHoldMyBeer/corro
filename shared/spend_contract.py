"""What the model calls have cost: GET /api/spend. Firm side only.

Every figure is computed in code from `llm_calls`, the table each caller writes
its token usage to. Nothing here is estimated by a model.
"""

from __future__ import annotations

from pydantic import Field

from .contract import Model


class SpendLine(Model):
    group: str = Field(description="digest | check | card_design | dashboard_design | assistant | ingestion | negotiation | other")
    purposes: list[str] = Field(default_factory=list, description="The `llm_calls.purpose` values counted here.")
    models: list[str] = Field(default_factory=list)
    calls: int = 0
    failed: int = Field(0, description="Calls that were billed but returned nothing usable.")
    input_tokens: int = 0
    cached_tokens: int = Field(0, description="Input tokens the provider served from its prompt cache.")
    cache_write_tokens: int = Field(0, description="Input tokens the provider wrote to its prompt cache.")
    output_tokens: int = 0
    cached_share: float | None = Field(None, description="cached_tokens / input_tokens; null when there were no input tokens.")
    usd: float = Field(0.0, description="What the calls cost, from each call's usage and the price list.")
    usd_without_caching: float = Field(0.0, description="The same calls priced with every input token at the plain input rate.")
    caching_effect_usd: float = Field(
        0.0, description="usd_without_caching - usd. Positive: caching saved money. Negative: cache writes cost more than reads saved."
    )
    cache_priced: bool = Field(True, description="False when the price list has no cached rate for a model used here: the effect is then not counted.")


class CaseSpend(Model):
    """One case's share of the spend, for the all-cases view."""

    matter_id: int
    calls: int = 0
    usd: float = 0.0


class StoredWork(Model):
    """Results kept so the same question is never sent twice. For one case when a case is asked for."""

    pages_read: int = Field(0, description="Pages of this case's files that have been read (all files when no case is given), whichever case paid for the read.")
    pages_read_here: int | None = Field(None, description="One case only: page-read calls this case paid for. Lower than pages_read when another case had already read the same files.")
    records_read: int = 0
    check_answers: int = 0
    incoming_checks: int = 0


class SpendReport(Model):
    """Response of GET /api/spend."""

    as_of: str
    scope: str = Field("all_cases", description="case | all_cases. Every figure below is for that scope.")
    matter_id: int | None = None
    by_case: list[CaseSpend] = Field(default_factory=list, description="All-cases view only: each case's calls and spend.")
    lines: list[SpendLine] = Field(default_factory=list)
    total: SpendLine
    stored: StoredWork = Field(default_factory=lambda: StoredWork())  # pyright: ignore[reportCallIssue]
    prices_read_on: str | None = Field(None, description="The date the price list was read from the provider's pages.")
    notes: list[str] = Field(default_factory=list)
