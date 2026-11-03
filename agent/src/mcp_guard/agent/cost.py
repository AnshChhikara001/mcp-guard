"""What a turn's tokens cost: a model's list price, per million input and output tokens.

The rule is the same for every model family: input tokens at the provider's list input price
plus output tokens at its list output price, in US dollars, with no caching or batch discounts.
The Red-team eval and the Agent API price tokens the same way, from the same list.
Prices change; each is dated. A model not listed here can have its price listed beside its
adapter (see adapters.py), or set when it runs.
"""

import math

from pydantic import BaseModel, ConfigDict


class Price(BaseModel):
    model_config = ConfigDict(frozen=True)

    input_per_million: float  # US dollars per million input tokens
    output_per_million: float  # US dollars per million output tokens

    def cost(self, input_tokens: int, output_tokens: int) -> float:
        return (
            input_tokens * self.input_per_million + output_tokens * self.output_per_million
        ) / 1_000_000

    @classmethod
    def parse(cls, setting: str) -> "Price":
        """The price a setting such as MODEL_PRICE=2,10 names: input, then output, per million."""
        try:
            input_price, output_price = (float(part) for part in setting.split(","))
            if not (0 <= input_price < math.inf and 0 <= output_price < math.inf):
                raise ValueError("a price can't be negative")
        except ValueError as e:
            raise ValueError(
                f"A price is two numbers, input and output per million tokens, like 2,10;"
                f" not {setting!r}"
            ) from e
        return cls(input_per_million=input_price, output_per_million=output_price)


FREE = Price(input_per_million=0, output_per_million=0)

# Checked 2026-10-25, standard paid tier, text.
LISTED_PRICES: dict[str, Price] = {
    "gemini:gemini-3.5-flash": Price(input_per_million=1.50, output_per_million=9.00),
    "openai:gpt-5-mini": Price(input_per_million=0.25, output_per_million=2.00),
}
