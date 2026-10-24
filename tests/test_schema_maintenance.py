"""Tests for the list_versions field fix and the gates(circuit_id) index."""

import hashlib
import sqlite3

import pytest

from qch import QCHDatabase
from qch import migrations


QASM = """OPENQASM 2.0;
include "qelib1.inc";
qreg q[3];
h q[0];
cx q[0],q[1];
t q[1];
tdg q[1];
cz q[1],q[2];
"""

LEGACY_GATES_SCHEMA = """
    CREATE TABLE circuits (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL UNIQUE,
        qasm TEXT NOT NULL
    );
    CREATE TABLE gates (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        circuit_id INTEGER NOT NULL,
        gate_index INTEGER NOT NULL,
        gate_type TEXT NOT NULL,
        qubits TEXT NOT NULL,
        FOREIGN KEY (circuit_id) REFERENCES circuits(id)
    );
"""

GATES_QUERY = (
    "SELECT gate_index, gate_type, qubits FROM gates "
    "WHERE circuit_id = ? ORDER BY gate_index"
)


def write_qasm(tmp_path, name="c.qasm", text=QASM):
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return path


def make_legacy_db(path, circuits=3, gates_per_circuit=50):
    """Create an 'existing' database whose gates table has no index."""
    connection = sqlite3.connect(path)
    connection.executescript(LEGACY_GATES_SCHEMA)
    for circuit_id in range(1, circuits + 1):
        connection.execute(
            "INSERT INTO circuits (id, name, qasm) VALUES (?, ?, ?)",
            (circuit_id, f"c{circuit_id}", "OPENQASM 2.0;"),
        )
        connection.executemany(
            "INSERT INTO gates (circuit_id, gate_index, gate_type, qubits) "
            "VALUES (?, ?, ?, ?)",
            [
                (circuit_id, index, "h", f"[{index % 3}]")
                for index in range(gates_per_circuit)
            ],
        )
    connection.commit()
    connection.close()


def gates_snapshot(connection):
    rows = connection.execute(
        "SELECT id, circuit_id, gate_index, gate_type, qubits FROM gates "
        "ORDER BY id"
    ).fetchall()
    return hashlib.sha256(repr(rows).encode()).hexdigest()


def index_names(connection):
    return {
        row[1] for row in connection.execute("PRAGMA index_list(gates)")
    }


# list_versions --------------------------------------------------------------


def test_list_versions_returns_resource_metrics(tmp_path):
    db = QCHDatabase(tmp_path / "versions.db")
    db.add_circuit("c", write_qasm(tmp_path))
    db.add_logical_circuit("logical")
    db.add_version("logical", "c", label="v1")
    db.add_version("logical", "c", label="v2")

    versions = db.list_versions("logical")
    features = db.get_features("c")

    assert [v["version_number"] for v in versions] == [1, 2]
    for version in versions:
        assert set(version) == {
            "version_number", "version_label", "circuit_name",
            "num_qubits", "gate_count", "depth",
            "two_qubit_gate_count", "t_count",
        }
        for metric in (
            "num_qubits", "gate_count", "depth",
            "two_qubit_gate_count", "t_count",
        ):
            assert version[metric] == features[metric]
    # t_count keeps its definition: t only, not tdg.
    assert versions[0]["t_count"] == 1
    assert versions[0]["two_qubit_gate_count"] == 2
    db.close()


def test_list_versions_keeps_missing_metrics_null(tmp_path):
    db = QCHDatabase(tmp_path / "versions_null.db")
    circuit_id = db.add_circuit("c", write_qasm(tmp_path))
    db.add_logical_circuit("logical")
    db.add_version("logical", "c")
    with db.connection:
        db.connection.execute(
            "UPDATE features SET two_qubit_gate_count = NULL, t_count = NULL "
            "WHERE circuit_id = ?",
            (circuit_id,),
        )
    version = db.list_versions("logical")[0]
    assert version["two_qubit_gate_count"] is None
    assert version["t_count"] is None
    assert version["gate_count"] == 5

    # A version whose circuit has no features row at all.
    with db.connection:
        db.connection.execute(
            "INSERT INTO circuits (name, qasm) VALUES ('bare', 'x')"
        )
    db.add_version("logical", "bare")
    bare = db.list_versions("logical")[1]
    assert bare["two_qubit_gate_count"] is None
    assert bare["t_count"] is None
    assert bare["gate_count"] is None
    db.close()


# gates(circuit_id) index ----------------------------------------------------


def test_new_database_gets_gates_index(tmp_path):
    db = QCHDatabase(tmp_path / "new.db")
    assert migrations.GATES_CIRCUIT_ID_INDEX_NAME in index_names(db.connection)
    assert migrations.inspect_gates_circuit_id_index(db.connection) == {
        "status": "present",
        "index": "idx_gates_circuit_id",
        "columns": ["circuit_id"],
    }
    db.close()


def test_opening_existing_database_does_not_build_index(tmp_path):
    path = tmp_path / "legacy.db"
    make_legacy_db(path)

    db = QCHDatabase(path)
    assert migrations.GATES_CIRCUIT_ID_INDEX_NAME not in index_names(
        db.connection
    )
    db.close()


def test_gates_index_migration_is_idempotent_and_used(tmp_path):
    path = tmp_path / "legacy.db"
    make_legacy_db(path)
    db = QCHDatabase(path)
    connection = db.connection

    before_data = gates_snapshot(connection)
    before_results = connection.execute(GATES_QUERY, (2,)).fetchall()
    plan_before = " ".join(
        row[3]
        for row in connection.execute(
            "EXPLAIN QUERY PLAN " + GATES_QUERY, (2,)
        )
    )
    assert "idx_gates_circuit_id" not in plan_before

    assert migrations.migrate_gates_circuit_id_index(connection) == "created"
    assert migrations.migrate_gates_circuit_id_index(connection) == (
        "already_present"
    )
    assert [
        name for name in index_names(connection)
        if name == "idx_gates_circuit_id"
    ] == ["idx_gates_circuit_id"]

    assert gates_snapshot(connection) == before_data
    assert connection.execute(GATES_QUERY, (2,)).fetchall() == before_results
    assert len(db.get_gates("c2")) == 50

    plan = " ".join(
        row[3]
        for row in connection.execute(
            "EXPLAIN QUERY PLAN " + GATES_QUERY, (2,)
        )
    )
    assert "USING INDEX idx_gates_circuit_id" in plan

    backfill_plan = " ".join(
        row[3]
        for row in connection.execute(
            "EXPLAIN QUERY PLAN SELECT COUNT(*) FROM gates "
            "WHERE circuit_id = 1"
        )
    )
    assert "idx_gates_circuit_id" in backfill_plan
    db.close()


def test_index_with_wrong_definition_is_detected(tmp_path):
    path = tmp_path / "wrong.db"
    make_legacy_db(path)
    connection = sqlite3.connect(path)
    connection.execute(
        "CREATE INDEX idx_gates_circuit_id ON gates(gate_type)"
    )
    connection.commit()

    with pytest.raises(migrations.MigrationError, match="unexpected"):
        migrations.migrate_gates_circuit_id_index(connection)
    connection.close()


def test_name_used_by_other_object_is_detected(tmp_path):
    path = tmp_path / "other.db"
    make_legacy_db(path)
    connection = sqlite3.connect(path)
    connection.execute("CREATE TABLE idx_gates_circuit_id (x INTEGER)")
    connection.commit()
    with pytest.raises(migrations.MigrationError, match="already exists"):
        migrations.migrate_gates_circuit_id_index(connection)
    connection.close()


def test_existing_covering_index_avoids_duplicate(tmp_path):
    path = tmp_path / "covered.db"
    make_legacy_db(path)
    connection = sqlite3.connect(path)
    connection.execute(
        "CREATE INDEX idx_custom ON gates(circuit_id, gate_index)"
    )
    connection.commit()
    assert migrations.migrate_gates_circuit_id_index(connection) == (
        "covered_by:idx_custom"
    )
    assert "idx_gates_circuit_id" not in index_names(connection)
    connection.close()


def test_migration_refuses_open_transaction(tmp_path):
    path = tmp_path / "txn.db"
    make_legacy_db(path)
    connection = sqlite3.connect(path)
    connection.execute("INSERT INTO circuits (name, qasm) VALUES ('p', 'x')")
    with pytest.raises(migrations.MigrationError, match="transaction"):
        migrations.migrate_gates_circuit_id_index(connection)
    assert connection.in_transaction
    connection.rollback()
    connection.close()


# Command line ---------------------------------------------------------------


def test_cli_rejects_missing_database_without_creating_it(tmp_path, capsys):
    missing = tmp_path / "typo.db"
    code = migrations.main([
        "gates-circuit-id-index", "--db", str(missing), "--no-backup",
    ])
    assert code == 2
    assert not missing.exists()
    assert "not found" in capsys.readouterr().err


def test_cli_backup_then_migrate(tmp_path, capsys):
    path = tmp_path / "legacy.db"
    make_legacy_db(path)
    backup = tmp_path / "legacy.backup.db"

    code = migrations.main([
        "gates-circuit-id-index", "--db", str(path), "--backup", str(backup),
    ])
    output = capsys.readouterr().out
    assert code == 0
    assert "Migration result: created" in output
    assert "Backup quick_check: ok" in output

    # The backup is a consistent copy taken before the migration.
    copy = sqlite3.connect(backup)
    assert copy.execute("SELECT COUNT(*) FROM gates").fetchone() == (150,)
    assert "idx_gates_circuit_id" not in index_names(copy)
    copy.close()

    migrated = sqlite3.connect(path)
    assert "idx_gates_circuit_id" in index_names(migrated)
    migrated.close()

    # Running again never overwrites an existing backup.
    code = migrations.main([
        "gates-circuit-id-index", "--db", str(path), "--backup", str(backup),
    ])
    assert code == 2
    assert "will not be overwritten" in capsys.readouterr().err


def test_cli_dry_run_changes_nothing(tmp_path, capsys):
    path = tmp_path / "legacy.db"
    make_legacy_db(path)
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    code = migrations.main([
        "gates-circuit-id-index", "--db", str(path), "--no-backup",
        "--dry-run",
    ])
    assert code == 0
    assert "'status': 'absent'" in capsys.readouterr().out
    assert hashlib.sha256(path.read_bytes()).hexdigest() == digest


def test_cli_requires_backup_choice(tmp_path):
    path = tmp_path / "legacy.db"
    make_legacy_db(path)
    with pytest.raises(SystemExit):
        migrations.main(["gates-circuit-id-index", "--db", str(path)])
