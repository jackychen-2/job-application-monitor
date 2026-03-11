"""One-off migration helper from local SQLite to Postgres."""

from __future__ import annotations

import argparse
from pathlib import Path

from sqlalchemy import MetaData, Table, create_engine, inspect, select, text

ORDERED_TABLES = [
    "users",
    "google_accounts",
    "journeys",
    "applications",
    "status_history",
    "processed_emails",
    "scan_state",
    "application_merge_events",
    "application_merge_items",
]

SKIPPED_TABLES = {"auth_sessions"}


def _normalize_database_url(database_url: str) -> str:
    if database_url.startswith("postgresql://"):
        return f"postgresql+psycopg://{database_url[len('postgresql://'):]}"
    return database_url


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--sqlite-path",
        default="job_monitor.db",
        help="Path to the source SQLite database file.",
    )
    parser.add_argument(
        "--database-url",
        required=True,
        help="Target Postgres DATABASE_URL.",
    )
    parser.add_argument(
        "--truncate",
        action="store_true",
        help="Delete existing rows in target tables before inserting migrated data.",
    )
    return parser.parse_args()


def _reset_sequence(conn, table_name: str) -> None:
    conn.execute(
        text(
            "SELECT setval("
            "pg_get_serial_sequence(:table_name, 'id'), "
            "COALESCE((SELECT MAX(id) FROM " + table_name + "), 1), "
            "(SELECT MAX(id) IS NOT NULL FROM " + table_name + ")"
            ")"
        ),
        {"table_name": table_name},
    )


def main() -> None:
    args = _parse_args()
    sqlite_path = Path(args.sqlite_path).expanduser().resolve()
    if not sqlite_path.exists():
        raise SystemExit(f"SQLite database not found: {sqlite_path}")

    database_url = _normalize_database_url(args.database_url)
    source_engine = create_engine(f"sqlite:///{sqlite_path}")
    target_engine = create_engine(database_url)

    source_metadata = MetaData()
    target_metadata = MetaData()
    source_metadata.reflect(bind=source_engine)
    target_metadata.reflect(bind=target_engine)

    source_tables = set(source_metadata.tables)
    target_tables = set(target_metadata.tables)

    deferred_user_journeys: list[tuple[int, int]] = []
    valid_application_ids: set[int] = set()

    with source_engine.connect() as source_conn, target_engine.begin() as target_conn:
        for table_name in ORDERED_TABLES:
            if table_name in SKIPPED_TABLES:
                continue
            if table_name not in source_tables:
                continue
            if table_name not in target_tables:
                raise RuntimeError(f"Target table missing: {table_name}")

            source_table: Table = source_metadata.tables[table_name]
            target_table: Table = target_metadata.tables[table_name]

            if args.truncate:
                target_conn.execute(target_table.delete())
            else:
                existing_count = target_conn.execute(
                    select(text("COUNT(*)")).select_from(target_table)
                ).scalar_one()
                if existing_count:
                    raise RuntimeError(
                        f"Target table {table_name} is not empty; "
                        "rerun with --truncate if intended."
                    )

            rows = [dict(row._mapping) for row in source_conn.execute(select(source_table))]
            if table_name == "users":
                deferred_user_journeys = [
                    (int(row["id"]), int(row["active_journey_id"]))
                    for row in rows
                    if row.get("active_journey_id") is not None
                ]
                for row in rows:
                    row["active_journey_id"] = None
            elif table_name == "applications":
                valid_application_ids = {
                    int(row["id"]) for row in rows if row.get("id") is not None
                }
            elif table_name == "status_history":
                rows = [
                    row
                    for row in rows
                    if row.get("application_id") in valid_application_ids
                ]
            if rows:
                target_conn.execute(target_table.insert(), rows)

        if deferred_user_journeys:
            users_table = target_metadata.tables["users"]
            for user_id, journey_id in deferred_user_journeys:
                target_conn.execute(
                    users_table.update()
                    .where(users_table.c.id == user_id)
                    .values(active_journey_id=journey_id)
                )

        inspector = inspect(target_engine)
        for table_name in ORDERED_TABLES:
            if table_name in SKIPPED_TABLES or table_name not in target_tables:
                continue
            columns = {column["name"] for column in inspector.get_columns(table_name)}
            if "id" in columns:
                _reset_sequence(target_conn, table_name)


if __name__ == "__main__":
    main()
