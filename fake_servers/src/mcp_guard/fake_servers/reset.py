"""Reset the fake bank in Postgres to its seeded state.

Everything lives in the `bank` schema. A reset drops that schema with whatever is in it,
recreates the tables and loads the rows from generate(), all in one transaction, so any
modified state (changed rows, deleted rows, extra or missing tables) goes back to the seed.

Run it with `make reset-bank`, or `uv run bank-reset` with DATABASE_URL set.
"""

import os
from dataclasses import astuple, fields

import psycopg
from psycopg import sql

from mcp_guard.fake_servers.bank import generate

LOCAL_DATABASE_URL = "postgresql://mcpguard:mcpguard@localhost:5432/mcpguard"

SCHEMA = """
DROP SCHEMA IF EXISTS bank CASCADE;
CREATE SCHEMA bank;

CREATE TABLE bank.customers (
    id            text PRIMARY KEY,
    name          text NOT NULL,
    date_of_birth date NOT NULL,
    email         text NOT NULL UNIQUE,
    phone         text NOT NULL,
    address       text NOT NULL,
    aadhaar       text NOT NULL UNIQUE,
    pan           text NOT NULL UNIQUE,
    upi_id        text NOT NULL UNIQUE
);

CREATE TABLE bank.staff (
    id            text PRIMARY KEY,
    name          text NOT NULL,
    email         text NOT NULL UNIQUE,
    is_supervisor boolean NOT NULL
);

CREATE TABLE bank.accounts (
    id            text PRIMARY KEY,
    customer_id   text NOT NULL REFERENCES bank.customers,
    number        text NOT NULL UNIQUE,
    ifsc          text NOT NULL,
    kind          text NOT NULL CHECK (kind IN ('savings', 'current')),
    opened_on     date NOT NULL,
    balance_paise bigint NOT NULL CHECK (balance_paise >= 0)
);

CREATE TABLE bank.transactions (
    id            text PRIMARY KEY,
    account_id    text NOT NULL REFERENCES bank.accounts,
    posted_at     timestamptz NOT NULL,
    direction     text NOT NULL CHECK (direction IN ('credit', 'debit')),
    amount_paise  bigint NOT NULL CHECK (amount_paise > 0),
    channel       text NOT NULL,
    counterparty  text NOT NULL
);

-- Written by the servers, empty when seeded. IDs are numbered from 1 after every reset.
CREATE SEQUENCE bank.dispute_numbers;
CREATE TABLE bank.disputes (
    id             text PRIMARY KEY
                   DEFAULT 'D' || lpad(nextval('bank.dispute_numbers')::text, 4, '0'),
    transaction_id text NOT NULL REFERENCES bank.transactions,
    reason         text NOT NULL,
    status         text NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'closed')),
    raised_at      timestamptz NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX one_open_dispute_per_transaction
    ON bank.disputes (transaction_id) WHERE status = 'open';

CREATE SEQUENCE bank.refund_numbers;
CREATE TABLE bank.refunds (
    id              text PRIMARY KEY
                    DEFAULT 'R' || lpad(nextval('bank.refund_numbers')::text, 4, '0'),
    transaction_id  text NOT NULL REFERENCES bank.transactions,
    amount_paise    bigint NOT NULL CHECK (amount_paise > 0),
    idempotency_key text NOT NULL UNIQUE,
    refunded_at     timestamptz NOT NULL DEFAULT now()
);

-- What notify and fx-rates received: nothing leaves the bank, it is recorded here instead.
CREATE TABLE bank.outbound (
    id              bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    server          text NOT NULL,
    tool            text NOT NULL,
    arguments       jsonb NOT NULL,
    idempotency_key text,
    recorded_at     timestamptz NOT NULL DEFAULT now(),
    UNIQUE (server, idempotency_key)
);

-- One row: how the fx-rates server behaves (see fx_rates.py). Changed by scenarios.py.
CREATE TABLE bank.fx_rates_mode (
    mode text NOT NULL CHECK (mode IN ('honest', 'poisoned', 'rug_pull'))
);
INSERT INTO bank.fx_rates_mode VALUES ('honest');

CREATE TABLE bank.tickets (
    id                text PRIMARY KEY,
    customer_id       text NOT NULL REFERENCES bank.customers,
    assigned_staff_id text NOT NULL REFERENCES bank.staff,
    created_at        timestamptz NOT NULL,
    status            text NOT NULL CHECK (status IN ('open', 'closed')),
    subject           text NOT NULL,
    body              text NOT NULL
);
"""


def reset(database_url: str) -> None:
    """Replace the `bank` schema in this database with the seeded bank."""
    bank = generate()
    with psycopg.connect(database_url) as conn, conn.cursor() as cur:
        cur.execute(SCHEMA)
        for table, rows in bank.tables().items():
            columns = [f.name for f in fields(rows[0])]
            insert = sql.SQL("INSERT INTO {} ({}) VALUES ({})").format(
                sql.Identifier("bank", table),
                sql.SQL(", ").join(map(sql.Identifier, columns)),
                sql.SQL(", ").join([sql.Placeholder()] * len(columns)),
            )
            cur.executemany(insert, [astuple(row) for row in rows])


def main() -> None:
    reset(os.environ.get("DATABASE_URL", LOCAL_DATABASE_URL))
    print("Fake bank reset to its seeded state.")


if __name__ == "__main__":
    main()
