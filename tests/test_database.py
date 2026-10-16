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
