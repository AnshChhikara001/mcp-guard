"""List prices of the models the eval runs, for its estimated cost.

How a price turns tokens into dollars is in mcp_guard.agent.cost. Prices change; each is
dated. A model not listed here needs its price passed to the run.
"""

from mcp_guard.agent.cost import Price
from mcp_guard.redteam.fake import FAKE

# Checked 2026-10-25, standard paid tier, text.
PRICES: dict[str, Price] = {
    FAKE: Price(input_per_million=0, output_per_million=0),
    "gemini:gemini-3.5-flash": Price(input_per_million=1.50, output_per_million=9.00),
    "openai:gpt-5-mini": Price(input_per_million=0.25, output_per_million=2.00),
}
