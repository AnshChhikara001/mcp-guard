"""The prices the eval knows without being told: the listed ones, and the fake model's.

How a price turns tokens into dollars, and the list itself, are in mcp_guard.agent.cost.
"""

from mcp_guard.agent.cost import FREE, LISTED_PRICES, Price
from mcp_guard.redteam.fake import FAKE

PRICES: dict[str, Price] = {FAKE: FREE, **LISTED_PRICES}
