import pytest
from qch import QCHDatabase


GHZ3_QASM = """OPENQASM 2.0;
include "qelib1.inc";
qreg q[3];
h q[0];
cx q[0],q[1];
cx q[1],q[2];
"""


@pytest.fixture
def db(tmp_path):
    database = QCHDatabase(tmp_path / "test.db")
    yield database
    database.close()


@pytest.fixture
def ghz3_file(tmp_path):
    path = tmp_path / "ghz3.qasm"
    path.write_text(GHZ3_QASM, encoding="utf-8")
    return path


def test_add_circuit(db, ghz3_file):
    circuit_id = db.add_circuit("ghz3", ghz3_file)
    assert circuit_id > 0


def test_get_circuit(db, ghz3_file):
    db.add_circuit("ghz3", ghz3_file)
    assert db.get_circuit("ghz3") == GHZ3_QASM


def test_get_features(db, ghz3_file):
    db.add_circuit("ghz3", ghz3_file)

    features = db.get_features("ghz3")

    assert features["num_qubits"] == 3
    assert features["gate_count"] == 3
    assert features["depth"] == 3


def test_get_gates(db, ghz3_file):
    db.add_circuit("ghz3", ghz3_file)

    gates = db.get_gates("ghz3")

    assert len(gates) == 3
    assert [g["gate_type"] for g in gates] == ["h", "cx", "cx"]
    assert [g["qubits"] for g in gates] == [
        [0], [0, 1], [1, 2]
    ]

def test_persistence(tmp_path, ghz3_file):
    db_path = tmp_path / "persistent.db"

    db = QCHDatabase(db_path)
    db.add_circuit("ghz3", ghz3_file)
    db.close()

    # Reopen the existing database.
    db = QCHDatabase(db_path)

    features = db.get_features("ghz3")
    gates = db.get_gates("ghz3")

    assert features == {
        "num_qubits": 3,
        "gate_count": 3,
        "depth": 3
    }

    assert len(gates) == 3
    assert [g["gate_type"] for g in gates] == [
        "h", "cx", "cx"
    ]

    db.close()

def test_list_circuits(db, ghz3_file):
    db.add_circuit("ghz3", ghz3_file)

    assert db.list_circuits() == ["ghz3"]

def test_find_circuits(db, ghz3_file):
    db.add_circuit("ghz3", ghz3_file)

    assert db.find_circuits(min_qubits=3) == ["ghz3"]
    assert db.find_circuits(min_qubits=4) == []

def test_find_circuits_multiple_conditions(db, ghz3_file):
    db.add_circuit("ghz3", ghz3_file)

    assert db.find_circuits(
        min_qubits=3,
        max_depth=3
    ) == ["ghz3"]

    assert db.find_circuits(
        min_qubits=3,
        max_depth=2
    ) == []

    assert db.find_circuits(
        max_gates=2
    ) == []

    assert db.find_circuits(
        max_qubits=3,
        min_depth=3,
        max_gates=3
    ) == ["ghz3"]

def test_find_circuits_ordering(db, tmp_path):
    qasm2 = tmp_path / "bell2.qasm"
    qasm2.write_text(
        """OPENQASM 2.0;
include "qelib1.inc";
qreg q[2];
h q[0];
cx q[0],q[1];
""",
        encoding="utf-8"
    )

    qasm3 = tmp_path / "ghz3.qasm"
    qasm3.write_text(
        """OPENQASM 2.0;
include "qelib1.inc";
qreg q[3];
h q[0];
cx q[0],q[1];
cx q[1],q[2];
""",
        encoding="utf-8"
    )

    db.add_circuit("ghz3", qasm3)
    db.add_circuit("bell2", qasm2)

    assert db.find_circuits(
        order_by="depth"
    ) == ["bell2", "ghz3"]

    assert db.find_circuits(
        order_by="depth",
        descending=True
    ) == ["ghz3", "bell2"]

    assert db.find_circuits(
        order_by="gate_count"
    ) == ["bell2", "ghz3"]

    with pytest.raises(ValueError):
        db.find_circuits(order_by="invalid_column")



def test_add_logical_circuit(tmp_path):
    from qch import QCHDatabase

    db = QCHDatabase(tmp_path / "logical.db")

    logical_id = db.add_logical_circuit(
        "ghz3",
        "Three-qubit GHZ state preparation"
    )

    row = db.connection.execute(
        """
        SELECT name, description
        FROM logical_circuits
        WHERE id = ?
        """,
        (logical_id,)
    ).fetchone()

    assert row == (
        "ghz3",
        "Three-qubit GHZ state preparation"
    )

    db.close()


def test_version_numbering_and_sharing(tmp_path):
    from qch import QCHDatabase

    db = QCHDatabase(tmp_path / "versions.db")

    qasm_path = tmp_path / "ghz3.qasm"
    qasm_path.write_text(
        "OPENQASM 2.0;\n"
        'include "qelib1.inc";\n'
        "qreg q[3];\n"
        "h q[0];\n"
        "cx q[0],q[1];\n"
        "cx q[1],q[2];\n",
        encoding="utf-8"
    )

    db.add_circuit("ghz3_original", qasm_path)

    db.add_logical_circuit("ghz3")
    db.add_logical_circuit("benchmark_ghz3")

    v1 = db.add_version(
        "ghz3", "ghz3_original", label="original"
    )
    v2 = db.add_version(
        "ghz3", "ghz3_original", label="reused"
    )
    v3 = db.add_version(
        "benchmark_ghz3", "ghz3_original", label="baseline"
    )

    assert (v1, v2, v3) == (1, 2, 1)

    circuit_count = db.connection.execute(
        "SELECT COUNT(*) FROM circuits"
    ).fetchone()[0]

    version_count = db.connection.execute(
        "SELECT COUNT(*) FROM circuit_versions"
    ).fetchone()[0]

    assert circuit_count == 1
    assert version_count == 3

    db.close()


def test_add_version_invalid_references(tmp_path):
    import pytest
    from qch import QCHDatabase

    db = QCHDatabase(tmp_path / "invalid_versions.db")
    db.add_logical_circuit("ghz3")

    with pytest.raises(ValueError, match="Logical circuit not found"):
        db.add_version("unknown", "missing")

    with pytest.raises(ValueError, match="Circuit not found"):
        db.add_version("ghz3", "missing")

    count = db.connection.execute(
        "SELECT COUNT(*) FROM circuit_versions"
    ).fetchone()[0]

    assert count == 0
    db.close()


def test_version_number_constraint(tmp_path):
    import sqlite3
    import pytest
    from qch import QCHDatabase

    db = QCHDatabase(tmp_path / "version_constraint.db")

    logical_id = db.add_logical_circuit("ghz3")

    db.connection.execute(
        "INSERT INTO circuits (name, qasm) VALUES (?, ?)",
        ("c1", "placeholder")
    )
    circuit_id = db.connection.execute(
        "SELECT id FROM circuits WHERE name = 'c1'"
    ).fetchone()[0]

    db.connection.execute(
        """
        INSERT INTO circuit_versions
        (logical_circuit_id, version_number, circuit_id)
        VALUES (?, 1, ?)
        """,
        (logical_id, circuit_id)
    )
    db.connection.commit()

    with pytest.raises(sqlite3.IntegrityError):
        with db.connection:
            db.connection.execute(
                """
                INSERT INTO circuit_versions
                (logical_circuit_id, version_number, circuit_id)
                VALUES (?, 1, ?)
                """,
                (logical_id, circuit_id)
            )

    db.close()


def test_list_versions(tmp_path):
    from qch import QCHDatabase

    db = QCHDatabase(tmp_path / "list_versions.db")

    qasm_path = tmp_path / "ghz3.qasm"
    qasm_path.write_text(
        "OPENQASM 2.0;\n"
        'include "qelib1.inc";\n'
        "qreg q[3];\n"
        "h q[0];\n"
        "cx q[0],q[1];\n"
        "cx q[1],q[2];\n",
        encoding="utf-8"
    )

    db.add_circuit("ghz3_original", qasm_path)
    db.add_logical_circuit("ghz3")

    db.add_version("ghz3", "ghz3_original", label="original")
    db.add_version("ghz3", "ghz3_original", label="reused")

    versions = db.list_versions("ghz3")

    assert len(versions) == 2
    assert [v["version_number"] for v in versions] == [1, 2]
    assert [v["version_label"] for v in versions] == [
        "original", "reused"
    ]

    assert versions[0]["circuit_name"] == "ghz3_original"
    assert versions[0]["num_qubits"] == 3
    assert versions[0]["gate_count"] == 3
    assert versions[0]["depth"] == 3

    assert db.list_versions("unknown") == []

    db.close()


def test_get_version(tmp_path):
    from qch import QCHDatabase

    db = QCHDatabase(tmp_path / "get_version.db")

    qasm_path = tmp_path / "ghz3.qasm"
    qasm_path.write_text(
        "OPENQASM 2.0;\n"
        'include "qelib1.inc";\n'
        "qreg q[3];\n"
        "h q[0];\n"
        "cx q[0],q[1];\n"
        "cx q[1],q[2];\n",
        encoding="utf-8"
    )

    db.add_circuit("ghz3_original", qasm_path)
    db.add_logical_circuit("ghz3")
    db.add_version(
        "ghz3",
        "ghz3_original",
        label="original"
    )

    version = db.get_version("ghz3", 1)

    assert version == {
        "version_number": 1,
        "version_label": "original",
        "circuit_name": "ghz3_original",
        "num_qubits": 3,
        "gate_count": 3,
        "depth": 3
    }

    assert db.get_version("ghz3", 999) is None
    assert db.get_version("unknown", 1) is None

    db.close()