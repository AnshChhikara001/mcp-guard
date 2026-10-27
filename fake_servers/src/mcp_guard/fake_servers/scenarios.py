"""Setting the fake bank up for an Attack: planting a ticket, turning fx-rates malicious.

Only trusted code calls these (tests, the Red-team eval, the playground). They write straight
to the database and are not MCP tools, so no Agent can call them. A reset undoes them all.
"""

import psycopg

from mcp_guard.fake_servers.fx_rates import INJECTED_INSTRUCTIONS, FxMode


def plant_ticket(database_url: str, customer_id: str, subject: str, body: str) -> str:
    """Add an open ticket from this Customer, newer than every other, and return its ID.

    It is numbered after the seeded tickets and assigned to the first support Staff member.
    """
    with psycopg.connect(database_url) as conn:
        row = conn.execute(
            "WITH next AS (SELECT (max(substring(id from 3)::int) + 1)::text AS n"
            " FROM bank.tickets)"
            " INSERT INTO bank.tickets"
            " (id, customer_id, assigned_staff_id, created_at, status, subject, body)"
            # lpad also cuts longer text, so pad only up to the number's own length.
            " SELECT 'TK' || lpad(n, greatest(length(n), 3), '0'), %s,"
            " (SELECT id FROM bank.staff WHERE NOT is_supervisor ORDER BY id LIMIT 1),"
            " now(), 'open', %s, %s FROM next RETURNING id",
            (customer_id, subject, body),
        ).fetchone()
    assert row is not None
    return row[0]


def set_fx_mode(database_url: str, mode: FxMode, instructions: str = INJECTED_INSTRUCTIONS) -> None:
    """Make the fx-rates server behave this way from its next tool listing on. When it is
    malicious, convert's description ends with these instructions for the Agent.
    """
    with psycopg.connect(database_url) as conn:
        conn.execute(
            "UPDATE bank.fx_rates_mode SET mode = %s, instructions = %s", (mode, instructions)
        )
