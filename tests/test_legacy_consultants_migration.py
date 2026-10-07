from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from app.main import _read_old_consultants


def _session_with(schema_sql, rows_sql):
    engine = create_engine("sqlite://")
    with engine.begin() as conn:
        conn.execute(text(schema_sql))
        conn.execute(text(rows_sql))
    return Session(engine)


def test_reads_a_legacy_table_without_user_id_or_active():
    session = _session_with(
        "CREATE TABLE consultants (id INTEGER PRIMARY KEY, name TEXT, email TEXT)",
        "INSERT INTO consultants (id, name, email) VALUES (1, 'Salah Maguiri', 's@example.com')",
    )
    assert _read_old_consultants(session) == [(1, "Salah Maguiri", "s@example.com", True, None)]


def test_reads_a_full_legacy_table():
    session = _session_with(
        "CREATE TABLE consultants (id INTEGER PRIMARY KEY, name TEXT, email TEXT, active BOOLEAN, user_id INTEGER)",
        "INSERT INTO consultants (id, name, email, active, user_id) VALUES (2, 'Loubna', 'l@example.com', 0, 7)",
    )
    assert _read_old_consultants(session) == [(2, "Loubna", "l@example.com", 0, 7)]
