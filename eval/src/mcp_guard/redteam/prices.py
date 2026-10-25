"""List prices of the models the eval runs, for its estimated cost.

The rule is the same for every model family: input tokens at the provider's list input price
plus output tokens at its list output price, in US dollars, with no caching or batch discounts.
Prices change; each is dated. A model not listed here needs its price passed to the run.
"""

from pydantic import BaseModel, ConfigDict

from mcp_guard.redteam.fake import FAKE


class Price(BaseModel):
    model_config = ConfigDict(frozen=True)

    input_per_million: float  # US dollars per million input tokens
    output_per_million: float  # US dollars per million output tokens

    def cost(self, input_tokens: int, output_tokens: int) -> float:
        return (
            input_tokens * self.input_per_million + output_tokens * self.output_per_million
        ) / 1_000_000


# Checked 2026-10-25, standard paid tier, text.
PRICES: dict[str, Price] = {
    FAKE: Price(input_per_million=0, output_per_million=0),
    "gemini:gemini-3.5-flash": Price(input_per_million=1.50, output_per_million=9.00),
    "openai:gpt-5-mini": Price(input_per_million=0.25, output_per_million=2.00),
}
