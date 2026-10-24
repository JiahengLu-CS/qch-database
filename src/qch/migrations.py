"""Explicit, repeatable schema migrations for existing QCH databases.

Currently provides one migration: a plain index on ``gates(circuit_id)``.

* New databases created by :class:`qch.QCHDatabase` get this index
  automatically when the ``gates`` table itself is created.
* Existing databases are never migrated implicitly (building the index on
  a large ``gates`` table can take a long time). Run the migration
  explicitly, preferably after a backup::

      python -m qch.migrations gates-circuit-id-index \\
          --db path/to/existing.db --backup path/to/new-backup.db

Command-line behavior:

* ``--db`` must name an existing file; it is opened with SQLite
  ``mode=rw`` so a mistyped path never creates an empty database.
* ``--backup`` must name a file that does not exist yet; it is created
  exclusively and filled with the SQLite online backup API, then checked
  with ``PRAGMA quick_check``. Use ``--no-backup`` to skip this step
  explicitly.
* ``--dry-run`` only reports the current index state.
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

GATES_CIRCUIT_ID_INDEX_NAME = "idx_gates_circuit_id"

GATES_CIRCUIT_ID_INDEX_SQL = (
    f"CREATE INDEX IF NOT EXISTS {GATES_CIRCUIT_ID_INDEX_NAME} "
    "ON gates(circuit_id)"
)


class MigrationError(Exception):
    """A migration cannot be applied safely."""


def _index_columns(connection, index_name):
    rows = connection.execute(
        f"PRAGMA index_info({_quote_identifier(index_name)})"
    ).fetchall()
    return [row[2] for row in sorted(rows, key=lambda row: row[0])]


def _quote_identifier(name):
    return '"' + name.replace('"', '""') + '"'


def inspect_gates_circuit_id_index(connection):
    """Describe how ``gates.circuit_id`` lookups are indexed.

    Returns a dict with ``status`` set to:

    * ``"present"``: ``idx_gates_circuit_id`` exists with the expected
      definition (non-unique, not partial, exactly ``(circuit_id)``).
    * ``"covered"``: another non-partial index on ``gates`` starts with
      ``circuit_id`` (``covering_index`` names it), so no new index is
      needed.
    * ``"absent"``: no usable index.

    Raises :class:`MigrationError` if ``gates`` does not exist or an
    object named ``idx_gates_circuit_id`` exists with another definition.
    Read-only: executes only ``SELECT`` and ``PRAGMA`` reads.
    """
    table = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'gates'"
    ).fetchone()
    if table is None:
        raise MigrationError("The database has no 'gates' table")

    named = connection.execute(
        "SELECT type, tbl_name FROM sqlite_master WHERE name = ?",
        (GATES_CIRCUIT_ID_INDEX_NAME,),
    ).fetchone()
    if named is not None and named != ("index", "gates"):
        raise MigrationError(
            f"An object named {GATES_CIRCUIT_ID_INDEX_NAME} already exists "
            f"but is a {named[0]} on {named[1]!r}, not an index on 'gates'"
        )

    covering = None
    for _, name, unique, _origin, partial in connection.execute(
        "PRAGMA index_list(gates)"
    ).fetchall():
        columns = _index_columns(connection, name)
        if name == GATES_CIRCUIT_ID_INDEX_NAME:
            if columns != ["circuit_id"] or unique or partial:
                raise MigrationError(
                    f"Index {GATES_CIRCUIT_ID_INDEX_NAME} exists with an "
                    f"unexpected definition (columns={columns}, "
                    f"unique={bool(unique)}, partial={bool(partial)}); "
                    "refusing to modify it"
                )
            return {"status": "present", "index": name, "columns": columns}
        if not partial and columns and columns[0] == "circuit_id":
            covering = covering or {"index": name, "columns": columns}

    if covering is not None:
        return {
            "status": "covered",
            "covering_index": covering["index"],
            "columns": covering["columns"],
        }
    return {"status": "absent"}


def migrate_gates_circuit_id_index(connection):
    """Create ``idx_gates_circuit_id`` if no usable index exists.

    Idempotent. Returns ``"created"``, ``"already_present"`` or
    ``"covered_by:<index name>"``. Refuses to run while the connection has
    an open transaction, so it never commits work it does not own.
    """
    if connection.in_transaction:
        raise MigrationError(
            "The connection has an open transaction; commit or roll it back "
            "before migrating"
        )
    state = inspect_gates_circuit_id_index(connection)
    if state["status"] == "present":
        return "already_present"
    if state["status"] == "covered":
        return f"covered_by:{state['covering_index']}"

    connection.execute("BEGIN IMMEDIATE")
    try:
        connection.execute(GATES_CIRCUIT_ID_INDEX_SQL)
        connection.commit()
    except BaseException:
        connection.rollback()
        raise

    if inspect_gates_circuit_id_index(connection)["status"] != "present":
        raise MigrationError("Index creation could not be verified")
    return "created"


def backup_database(source_path, backup_path):
    """Create a consistent copy with the SQLite online backup API.

    ``backup_path`` must not exist. ``source_path`` must exist and is
    opened read-only. Returns the ``PRAGMA quick_check`` result of the
    backup (``"ok"`` on success); raises :class:`MigrationError` otherwise.
    """
    source_path = Path(source_path)
    backup_path = Path(backup_path)
    if not source_path.is_file():
        raise MigrationError(f"Database file not found: {source_path}")
    if source_path.resolve() == backup_path.resolve():
        raise MigrationError("The backup path must differ from the database")

    try:
        with open(backup_path, "xb"):
            pass
    except FileExistsError:
        raise MigrationError(
            f"Backup file already exists and will not be overwritten: "
            f"{backup_path}"
        ) from None

    source = sqlite3.connect(
        source_path.resolve().as_uri() + "?mode=ro", uri=True
    )
    target = sqlite3.connect(backup_path)
    try:
        source.backup(target)
        result = target.execute("PRAGMA quick_check").fetchone()[0]
    finally:
        target.close()
        source.close()
    if result != "ok":
        raise MigrationError(f"Backup quick_check failed: {result}")
    return result


def _open_existing_read_write(db_path):
    path = Path(db_path)
    if not path.is_file():
        raise MigrationError(
            f"Database file not found: {path} (no file was created)"
        )
    return sqlite3.connect(path.resolve().as_uri() + "?mode=rw", uri=True)


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="python -m qch.migrations",
        description="Explicit schema migrations for existing QCH databases.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    index_parser = subparsers.add_parser(
        "gates-circuit-id-index",
        help="Create the gates(circuit_id) index if no usable index exists.",
    )
    index_parser.add_argument(
        "--db", required=True, help="Path of an existing QCH database."
    )
    backup_group = index_parser.add_mutually_exclusive_group(required=True)
    backup_group.add_argument(
        "--backup",
        help="Path of a NEW backup file, created before migrating.",
    )
    backup_group.add_argument(
        "--no-backup",
        action="store_true",
        help="Explicitly migrate without creating a backup.",
    )
    index_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Only report the current index state; change nothing.",
    )

    args = parser.parse_args(argv)

    try:
        db_path = Path(args.db)
        if not db_path.is_file():
            raise MigrationError(
                f"Database file not found: {db_path} (no file was created)"
            )

        if args.dry_run:
            connection = sqlite3.connect(
                db_path.resolve().as_uri() + "?mode=ro", uri=True
            )
            try:
                state = inspect_gates_circuit_id_index(connection)
            finally:
                connection.close()
            print(f"Database: {db_path}")
            print(f"Index state: {state}")
            print("Dry run: nothing was changed.")
            return 0

        if args.backup:
            print(f"Creating backup: {args.backup}")
            check = backup_database(db_path, args.backup)
            print(f"Backup quick_check: {check}")
        else:
            print("Skipping backup (--no-backup).")

        connection = _open_existing_read_write(db_path)
        try:
            before = inspect_gates_circuit_id_index(connection)
            print(f"Index state before: {before}")
            result = migrate_gates_circuit_id_index(connection)
            after = inspect_gates_circuit_id_index(connection)
        finally:
            connection.close()
        print(f"Migration result: {result}")
        print(f"Index state after: {after}")
        return 0
    except MigrationError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
