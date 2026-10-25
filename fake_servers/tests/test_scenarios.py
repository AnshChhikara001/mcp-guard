import psycopg

from mcp_guard.fake_servers.reset import reset
from mcp_guard.fake_servers.scenarios import plant_ticket


def test_planted_tickets_are_numbered_after_the_newest_even_past_999(database_url: str) -> None:
    reset(database_url)
    first = plant_ticket(database_url, customer_id="C001", subject="Hi", body="One")
    with psycopg.connect(database_url) as conn:
        conn.execute(
            "INSERT INTO bank.tickets SELECT 'TK999', customer_id, assigned_staff_id,"
            " created_at, status, subject, body FROM bank.tickets WHERE id = %s",
            (first,),
        )

    after_999 = plant_ticket(database_url, customer_id="C001", subject="Hi", body="Two")

    assert (first, after_999) == ("TK011", "TK1000")
