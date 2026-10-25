"""Setting the fake bank up for an Attack: planting a ticket, turning fx-rates malicious.

Only trusted code calls these (tests, the Red-team eval, the playground). They write straight
to the database and are not MCP tools, so no Agent can call them. A reset undoes them all.
"""

import psycopg

from mcp_guard.fake_servers.fx_rates import FxMode


def plant_ticket(database_url: str, customer_id: str, subject: str, body: str) -> str:
    """Add an open ticket from this Customer, newer than every other, and return its ID.

    It is numbered after the seeded tickets and assigned to the first support Staff member.
    """
    with psycopg.connect(database_url) as conn:
        row = conn.execute(
            "INSERT INTO bank.tickets"
            " (id, customer_id, assigned_staff_id, created_at, status, subject, body)"
            " SELECT 'TK' || lpad((max(substring(t.id from 3)::int) + 1)::text, 3, '0'), %s,"
            " (SELECT id FROM bank.staff WHERE NOT is_supervisor ORDER BY id LIMIT 1),"
            " now(), 'open', %s, %s FROM bank.tickets t RETURNING id",
            (customer_id, subject, body),
        ).fetchone()
    assert row is not None
    return row[0]


def set_fx_mode(database_url: str, mode: FxMode) -> None:
    """Make the fx-rates server behave this way from its next tool listing on."""
    with psycopg.connect(database_url) as conn:
        conn.execute("UPDATE bank.fx_rates_mode SET mode = %s", (mode,))
