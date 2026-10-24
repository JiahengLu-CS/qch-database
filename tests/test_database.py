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
        "depth": 3,
	  "two_qubit_gate_count": 2,
        "t_count": 0
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


def test_compare_versions(tmp_path):
    import pytest
    from qch import QCHDatabase

    db = QCHDatabase(tmp_path / "compare_versions.db")

    qasm1 = (
        'OPENQASM 2.0;\n'
        'include "qelib1.inc";\n'
        'qreg q[3];\n'
        'h q[0];\n'
        'cx q[0],q[1];\n'
        'cx q[1],q[2];\n'
    )

    qasm2 = qasm1 + 'x q[0];\nx q[0];\n'

    path1 = tmp_path / "v1.qasm"
    path2 = tmp_path / "v2.qasm"

    path1.write_text(qasm1, encoding="utf-8")
    path2.write_text(qasm2, encoding="utf-8")

    db.add_circuit("ghz_v1", path1)
    db.add_circuit("ghz_v2", path2)

    db.add_logical_circuit("ghz3")
    db.add_version("ghz3", "ghz_v1", label="original")
    db.add_version("ghz3", "ghz_v2", label="extra_gates")

    result = db.compare_versions("ghz3", 1, 2)

    assert result["logical_circuit"] == "ghz3"
    assert result["version_a"] == 1
    assert result["version_b"] == 2

    metrics = result["metrics"]

    assert metrics["num_qubits"]["delta"] == 0
    assert metrics["gate_count"]["delta"] == 2
    assert metrics["depth"]["delta"] == 1

    assert metrics["gate_count"]["version_a"] == 3
    assert metrics["gate_count"]["version_b"] == 5

    # Verify missing versions raise ValueError
    with pytest.raises(ValueError, match="Version not found"):
        db.compare_versions("ghz3", 1, 999)

    with pytest.raises(ValueError, match="Version not found"):
        db.compare_versions("ghz3", 999, 2)

    db.close()


def test_add_transformation(tmp_path):
    import pytest
    from qch import QCHDatabase

    db = QCHDatabase(tmp_path / "transformation.db")

    qasm = (
        'OPENQASM 2.0;\n'
        'include "qelib1.inc";\n'
        'qreg q[3];\n'
        'h q[0];\n'
        'cx q[0],q[1];\n'
        'cx q[1],q[2];\n'
    )

    path = tmp_path / "ghz3.qasm"
    path.write_text(qasm, encoding="utf-8")

    db.add_circuit("ghz_original", path)
    db.add_logical_circuit("ghz3")

    db.add_version("ghz3", "ghz_original", label="v1")
    db.add_version("ghz3", "ghz_original", label="v2")

    # Create a transformation edge
    edge_id = db.add_transformation(
        "ghz3",
        1,
        2,
        "rewrite",
        "Example circuit transformation"
    )

    assert edge_id > 0

    # Verify stored transformation
    row = db.connection.execute(
        """
        SELECT
            source.version_number,
            target.version_number,
            e.transformation_type,
            e.description
        FROM transformation_edges AS e
        JOIN circuit_versions AS source
            ON source.id = e.source_version_id
        JOIN circuit_versions AS target
            ON target.id = e.target_version_id
        WHERE e.id = ?
        """,
        (edge_id,)
    ).fetchone()

    assert row == (
        1,
        2,
        "rewrite",
        "Example circuit transformation"
    )

    # Self-transformation is forbidden
    with pytest.raises(ValueError):
        db.add_transformation("ghz3", 1, 1, "rewrite")

    # Missing versions are forbidden
    with pytest.raises(ValueError):
        db.add_transformation("ghz3", 1, 999, "rewrite")

    # Empty transformation type is forbidden
    with pytest.raises(ValueError):
        db.add_transformation("ghz3", 1, 2, "")

    # Duplicate transformation is forbidden
    with pytest.raises(Exception):
        db.add_transformation("ghz3", 1, 2, "rewrite")

    db.close()

def test_list_transformations(tmp_path):
    from qch import QCHDatabase

    db = QCHDatabase(tmp_path / "list_transformations.db")

    qasm = (
        'OPENQASM 2.0;\n'
        'include "qelib1.inc";\n'
        'qreg q[3];\n'
        'h q[0];\n'
        'cx q[0],q[1];\n'
        'cx q[1],q[2];\n'
    )

    path = tmp_path / "ghz3.qasm"
    path.write_text(qasm, encoding="utf-8")

    db.add_circuit("ghz_original", path)

    db.add_logical_circuit("ghz3")
    db.add_logical_circuit("other")

    db.add_version("ghz3", "ghz_original", label="v1")
    db.add_version("ghz3", "ghz_original", label="v2")
    db.add_version("ghz3", "ghz_original", label="v3")

    db.add_version("other", "ghz_original", label="v1")
    db.add_version("other", "ghz_original", label="v2")

    # Add two branches to GHZ-3
    db.add_transformation(
        "ghz3", 1, 2,
        "optimization",
        "First transformation"
    )

    db.add_transformation(
        "ghz3", 1, 3,
        "decomposition",
        "Second transformation"
    )

    # Add an unrelated transformation
    db.add_transformation(
        "other", 1, 2,
        "rewrite",
        "Other logical circuit"
    )

    edges = db.list_transformations("ghz3")

    assert len(edges) == 2

    assert edges[0]["source_version"] == 1
    assert edges[0]["target_version"] == 2
    assert edges[0]["transformation_type"] == "optimization"
    assert edges[0]["description"] == "First transformation"

    assert edges[1]["source_version"] == 1
    assert edges[1]["target_version"] == 3
    assert edges[1]["transformation_type"] == "decomposition"

    # Verify logical-circuit isolation
    other_edges = db.list_transformations("other")

    assert len(other_edges) == 1
    assert other_edges[0]["transformation_type"] == "rewrite"

    # Unknown logical circuit returns an empty list
    assert db.list_transformations("unknown") == []

    db.close()


def test_transformation_cycle_detection(tmp_path):
    import pytest
    from qch import QCHDatabase

    db = QCHDatabase(tmp_path / "cycle_test.db")

    qasm = (
        'OPENQASM 2.0;\n'
        'include "qelib1.inc";\n'
        'qreg q[3];\n'
        'h q[0];\n'
        'cx q[0],q[1];\n'
        'cx q[1],q[2];\n'
    )

    path = tmp_path / "ghz3.qasm"
    path.write_text(qasm, encoding="utf-8")

    db.add_circuit("ghz_original", path)
    db.add_logical_circuit("ghz3")

    # Create three versions
    for i in range(1, 4):
        db.add_version(
            "ghz3",
            "ghz_original",
            label=f"v{i}"
        )

    # Create v1 -> v2 -> v3
    db.add_transformation("ghz3", 1, 2, "rewrite")
    db.add_transformation("ghz3", 2, 3, "optimization")

    # Reject v3 -> v1: would create a cycle
    with pytest.raises(ValueError, match="cycle"):
        db.add_transformation(
            "ghz3", 3, 1, "decomposition"
        )

    # Reject v2 -> v1: would create a smaller cycle
    with pytest.raises(ValueError, match="cycle"):
        db.add_transformation(
            "ghz3", 2, 1, "rewrite"
        )

    # Verify that failed inserts did not modify the graph
    edges = db.list_transformations("ghz3")

    assert len(edges) == 2

    assert [
        (e["source_version"], e["target_version"])
        for e in edges
    ] == [(1, 2), (2, 3)]

    # A valid branch should still be allowed
    db.add_transformation(
        "ghz3", 1, 3, "decomposition"
    )

    assert len(db.list_transformations("ghz3")) == 3

    db.close()

def test_get_ancestors(tmp_path):
    import pytest
    from qch import QCHDatabase

    db = QCHDatabase(tmp_path / "ancestors.db")

    qasm = (
        'OPENQASM 2.0;\n'
        'include "qelib1.inc";\n'
        'qreg q[3];\n'
        'h q[0];\n'
        'cx q[0],q[1];\n'
        'cx q[1],q[2];\n'
    )

    path = tmp_path / "ghz3.qasm"
    path.write_text(qasm, encoding="utf-8")

    db.add_circuit("ghz_original", path)
    db.add_logical_circuit("ghz3")

    # Create four versions.
    for i in range(1, 5):
        db.add_version(
            "ghz3", "ghz_original", label=f"v{i}"
        )

    # Construct a branching evolution graph.
    db.add_transformation("ghz3", 1, 2, "rewrite")
    db.add_transformation("ghz3", 1, 3, "decomposition")
    db.add_transformation("ghz3", 2, 4, "optimization")
    db.add_transformation("ghz3", 3, 4, "optimization")

    # v4 has three distinct ancestors.
    assert db.get_ancestors("ghz3", 4) == [1, 2, 3]

    # v2 and v3 share the same ancestor.
    assert db.get_ancestors("ghz3", 2) == [1]
    assert db.get_ancestors("ghz3", 3) == [1]

    # The root version has no ancestors.
    assert db.get_ancestors("ghz3", 1) == []

    # Unknown versions must be rejected.
    with pytest.raises(ValueError, match="Version not found"):
        db.get_ancestors("ghz3", 99)

    # Unknown logical circuits must also be rejected.
    with pytest.raises(ValueError, match="Version not found"):
        db.get_ancestors("unknown", 1)

    db.close()

def test_get_descendants(tmp_path):
    import pytest
    from qch import QCHDatabase

    db = QCHDatabase(tmp_path / "descendants.db")

    qasm = (
        'OPENQASM 2.0;\n'
        'include "qelib1.inc";\n'
        'qreg q[3];\n'
        'h q[0];\n'
        'cx q[0],q[1];\n'
        'cx q[1],q[2];\n'
    )

    path = tmp_path / "ghz3.qasm"
    path.write_text(qasm, encoding="utf-8")

    db.add_circuit("ghz_original", path)
    db.add_logical_circuit("ghz3")

    # Create four versions.
    for i in range(1, 5):
        db.add_version(
            "ghz3", "ghz_original", label=f"v{i}"
        )

    # Construct a branching and merging evolution graph.
    db.add_transformation("ghz3", 1, 2, "rewrite")
    db.add_transformation("ghz3", 1, 3, "decomposition")
    db.add_transformation("ghz3", 2, 4, "optimization")
    db.add_transformation("ghz3", 3, 4, "optimization")

    # v1 has three distinct descendants.
    assert db.get_descendants("ghz3", 1) == [2, 3, 4]

    # Both branches lead to v4.
    assert db.get_descendants("ghz3", 2) == [4]
    assert db.get_descendants("ghz3", 3) == [4]

    # v4 is a terminal version.
    assert db.get_descendants("ghz3", 4) == []

    # Unknown version.
    with pytest.raises(ValueError, match="Version not found"):
        db.get_descendants("ghz3", 99)

    # Unknown logical circuit.
    with pytest.raises(ValueError, match="Version not found"):
        db.get_descendants("unknown", 1)

    db.close()

def test_find_evolution_paths(tmp_path):
    import pytest
    from qch import QCHDatabase

    db = QCHDatabase(tmp_path / "paths.db")

    qasm = (
        'OPENQASM 2.0;\n'
        'include "qelib1.inc";\n'
        'qreg q[3];\n'
        'h q[0];\n'
        'cx q[0],q[1];\n'
        'cx q[1],q[2];\n'
    )

    path = tmp_path / "ghz3.qasm"
    path.write_text(qasm, encoding="utf-8")

    db.add_circuit("ghz_original", path)
    db.add_logical_circuit("ghz3")

    # Create four versions.
    for i in range(1, 5):
        db.add_version(
            "ghz3", "ghz_original", label=f"v{i}"
        )

    # Build two evolution paths.
    db.add_transformation("ghz3", 1, 2, "rewrite")
    db.add_transformation("ghz3", 1, 3, "decomposition")
    db.add_transformation("ghz3", 2, 4, "optimization")
    db.add_transformation("ghz3", 3, 4, "optimization")

    # Find all paths from v1 to v4.
    paths = db.find_evolution_paths("ghz3", 1, 4)

    assert paths == [
        [1, 2, 4],
        [1, 3, 4]
    ]

    # A version has a trivial path to itself.
    assert db.find_evolution_paths("ghz3", 2, 2) == [[2]]

    # No reverse path exists.
    assert db.find_evolution_paths("ghz3", 4, 1) == []

    # A single direct edge forms one path.
    assert db.find_evolution_paths("ghz3", 1, 2) == [[1, 2]]

    # Limit the number of returned paths.
    assert db.find_evolution_paths(
        "ghz3", 1, 4, max_paths=1
    ) == [[1, 2, 4]]

    # Invalid path limit.
    with pytest.raises(ValueError, match="max_paths"):
        db.find_evolution_paths(
            "ghz3", 1, 4, max_paths=0
        )

    # Unknown version.
    with pytest.raises(ValueError, match="Version not found"):
        db.find_evolution_paths("ghz3", 1, 99)

    db.close()


def test_find_best_version(tmp_path):
    import pytest
    from qch import QCHDatabase

    db = QCHDatabase(tmp_path / "best_version.db")

    circuits = {
        "circuit_v1": [
            "h q[0];",
            "cx q[0],q[1];",
            "cx q[1],q[2];",
        ],
        "circuit_v2": [
            "h q[0];",
            "cx q[0],q[1];",
            "cx q[1],q[2];",
            "x q[2];",
            "x q[2];",
        ],
        "circuit_v3": [
            "h q[0];",
            "cx q[0],q[1];",
        ],
    }

    db.add_logical_circuit("ghz3")

    for version_number, (name, gates) in enumerate(
        circuits.items(), start=1
    ):
        qasm = (
            'OPENQASM 2.0;\n'
            'include "qelib1.inc";\n'
            'qreg q[3];\n'
            + "\n".join(gates)
            + "\n"
        )

        path = tmp_path / f"{name}.qasm"
        path.write_text(qasm, encoding="utf-8")

        db.add_circuit(name, path)
        db.add_version("ghz3", name, label=f"v{version_number}")

    # Best version by gate count.
    best_gates = db.find_best_version(
        "ghz3", metric="gate_count"
    )

    assert best_gates["version_number"] == 3
    assert best_gates["circuit_name"] == "circuit_v3"
    assert best_gates["gate_count"] == 2
    assert best_gates["optimized_metric"] == "gate_count"

    # Best version by circuit depth.
    best_depth = db.find_best_version(
        "ghz3", metric="depth"
    )

    assert best_depth["version_number"] == 3
    assert best_depth["depth"] == 2

    # All versions have three qubits.
    # The earliest version wins the tie.
    best_qubits = db.find_best_version(
        "ghz3", metric="num_qubits"
    )

    assert best_qubits["version_number"] == 1

    # Invalid metric.
    with pytest.raises(ValueError, match="Unsupported metric"):
        db.find_best_version("ghz3", metric="unknown")

    # Unknown logical circuit.
    assert db.find_best_version("unknown") is None

    db.close()


def test_find_pareto_versions(tmp_path):
    import pytest
    from qch import QCHDatabase

    db = QCHDatabase(tmp_path / "pareto.db")

    # All circuits have three qubits.
    # Each gate list is chosen to control gate count and depth.
    circuits = {
        "circuit_v1": [
            "h q[0];",
            "x q[0];",
            "z q[0];",
            "h q[0];",
        ],
        "circuit_v2": [
            "h q[0];",
            "x q[0];",
            "z q[0];",
        ],
        "circuit_v3": [
            "h q[0];",
            "x q[0];",
        ],
        "circuit_v4": [
            "h q[0];",
            "x q[1];",
            "z q[2];",
        ],
    }

    db.add_logical_circuit("pareto_demo")

    for i, (name, gates) in enumerate(circuits.items(), start=1):
        qasm = (
            'OPENQASM 2.0;\n'
            'include "qelib1.inc";\n'
            'qreg q[3];\n'
            + "\n".join(gates)
            + "\n"
        )

        path = tmp_path / f"{name}.qasm"
        path.write_text(qasm, encoding="utf-8")

        db.add_circuit(name, path)
        db.add_version("pareto_demo", name, label=f"v{i}")

    # Check actual metrics before testing Pareto dominance.
    versions = db.list_versions("pareto_demo")

    assert [
        (v["gate_count"], v["depth"])
        for v in versions
    ] == [
        (4, 4),
        (3, 3),
        (2, 2),
        (3, 1),
    ]

    # Both gate count and depth should be minimized.
    frontier = db.find_pareto_versions(
        "pareto_demo",
        metrics=["gate_count", "depth"]
    )

    assert [v["version_number"] for v in frontier] == [3, 4]

    # Optimizing only gate count selects v3.
    gate_frontier = db.find_pareto_versions(
        "pareto_demo",
        metrics=["gate_count"]
    )
    assert [v["version_number"] for v in gate_frontier] == [3]

    # Optimizing only depth selects v4.
    depth_frontier = db.find_pareto_versions(
        "pareto_demo",
        metrics=["depth"]
    )
    assert [v["version_number"] for v in depth_frontier] == [4]

    # Invalid metrics must be rejected.
    with pytest.raises(ValueError):
        db.find_pareto_versions(
            "pareto_demo", metrics=["unknown"]
        )

    with pytest.raises(ValueError):
        db.find_pareto_versions(
            "pareto_demo", metrics=[]
        )

    with pytest.raises(ValueError):
        db.find_pareto_versions(
            "pareto_demo", metrics=["depth", "depth"]
        )

    # Unknown logical circuit returns an empty frontier.
    assert db.find_pareto_versions("unknown") == []

    db.close()


def test_quantum_resource_metrics(tmp_path):
    from qch import QCHDatabase

    db = QCHDatabase(tmp_path / "resource_metrics.db")

    qasm = """
OPENQASM 2.0;
include "qelib1.inc";
qreg q[3];

h q[0];
cx q[0],q[1];
t q[1];
cz q[1],q[2];
t q[2];
tdg q[0];
"""

    qasm_path = tmp_path / "resource_test.qasm"
    qasm_path.write_text(qasm, encoding="utf-8")

    db.add_circuit("resource_test", qasm_path)

    row = db.connection.execute(
        """
        SELECT
            gate_count,
            two_qubit_gate_count,
            t_count
        FROM features AS f
        JOIN circuits AS c ON c.id = f.circuit_id
        WHERE c.name = ?
        """,
        ("resource_test",)
    ).fetchone()

    assert row is not None

    gate_count, two_qubit_count, t_count = row

    assert gate_count == 6
    assert two_qubit_count == 2
    assert t_count == 2

    db.close()


def test_backfill_resource_metrics(tmp_path):
    from qch import QCHDatabase

    db = QCHDatabase(tmp_path / "backfill.db")

    qasm = """
OPENQASM 2.0;
include "qelib1.inc";
qreg q[3];

h q[0];
cx q[0],q[1];
t q[1];
cz q[1],q[2];
t q[2];
tdg q[0];
"""

    qasm_path = tmp_path / "backfill.qasm"
    qasm_path.write_text(qasm, encoding="utf-8")

    circuit_id = db.add_circuit("backfill_test", qasm_path)

    # Simulate a historical record with missing metrics.
    with db.connection:
        db.connection.execute(
            """
            UPDATE features
            SET two_qubit_gate_count = NULL,
                t_count = NULL
            WHERE circuit_id = ?
            """,
            (circuit_id,)
        )

    # First backfill should update one circuit.
    updated = db.backfill_resource_metrics()
    assert updated == 1

    row = db.connection.execute(
        """
        SELECT two_qubit_gate_count, t_count
        FROM features
        WHERE circuit_id = ?
        """,
        (circuit_id,)
    ).fetchone()

    assert row == (2, 2)

    # Second backfill should have nothing to update.
    updated_again = db.backfill_resource_metrics()
    assert updated_again == 0

    db.close()

def test_resource_metrics_schema_migration(tmp_path):
    import sqlite3
    from qch import QCHDatabase

    db_path = tmp_path / "legacy.db"

    # Simulate an old database schema.
    connection = sqlite3.connect(db_path)

    connection.executescript("""
        CREATE TABLE circuits (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE,
            qasm TEXT NOT NULL
        );

        CREATE TABLE features (
            circuit_id INTEGER PRIMARY KEY,
            num_qubits INTEGER,
            gate_count INTEGER,
            depth INTEGER,
            FOREIGN KEY (circuit_id) REFERENCES circuits(id)
        );

        INSERT INTO circuits (id, name, qasm)
        VALUES (1, 'legacy_circuit', 'OPENQASM 2.0;');

        INSERT INTO features
            (circuit_id, num_qubits, gate_count, depth)
        VALUES (1, 3, 5, 4);
    """)

    connection.close()

    # Opening the old database should migrate its schema.
    db = QCHDatabase(db_path)

    columns = {
        row[1]
        for row in db.connection.execute(
            "PRAGMA table_info(features)"
        ).fetchall()
    }

    assert "two_qubit_gate_count" in columns
    assert "t_count" in columns

    # Original data must be preserved.
    row = db.connection.execute(
        """
        SELECT
            num_qubits,
            gate_count,
            depth,
            two_qubit_gate_count,
            t_count
        FROM features
        WHERE circuit_id = 1
        """
    ).fetchone()

    assert row == (3, 5, 4, None, None)

    db.close()

    # Migration must also be safe when opening the database again.
    db = QCHDatabase(db_path)

    columns_again = [
        row[1]
        for row in db.connection.execute(
            "PRAGMA table_info(features)"
        ).fetchall()
    ]

    assert columns_again.count("two_qubit_gate_count") == 1
    assert columns_again.count("t_count") == 1

    db.close()

def test_find_best_version_resource_metrics(tmp_path):
    from qch import QCHDatabase

    db = QCHDatabase(tmp_path / "best_resources.db")

    circuits = {
        "resource_v1": [
            "h q[0];",
            "cx q[0],q[1];",
            "cx q[1],q[2];",
            "t q[0];",
        ],
        "resource_v2": [
            "h q[0];",
            "cx q[0],q[1];",
            "t q[0];",
            "t q[1];",
        ],
        "resource_v3": [
            "h q[0];",
            "cx q[0],q[1];",
            "cx q[1],q[2];",
            "cz q[0],q[2];",
        ],
    }

    db.add_logical_circuit("resource_demo")

    for i, (name, gates) in enumerate(circuits.items(), start=1):
        qasm = (
            'OPENQASM 2.0;\n'
            'include "qelib1.inc";\n'
            'qreg q[3];\n'
            + "\n".join(gates)
            + "\n"
        )

        path = tmp_path / f"{name}.qasm"
        path.write_text(qasm, encoding="utf-8")

        db.add_circuit(name, path)
        db.add_version("resource_demo", name, label=f"v{i}")

    # Minimum two-qubit gate count: v2.
    best_two_qubit = db.find_best_version(
        "resource_demo",
        metric="two_qubit_gate_count"
    )

    assert best_two_qubit["version_number"] == 2
    assert best_two_qubit["optimized_metric"] == "two_qubit_gate_count"

    # Minimum T-count: v3.
    best_t = db.find_best_version(
        "resource_demo",
        metric="t_count"
    )

    assert best_t["version_number"] == 3
    assert best_t["optimized_metric"] == "t_count"

    db.close()


def test_find_pareto_versions_resource_metrics(tmp_path):
    from qch import QCHDatabase

    db = QCHDatabase(tmp_path / "resource_pareto.db")

    circuits = {
        "resource_v1": [
            "cx q[0],q[1];",
            "t q[0];",
            "t q[1];",
            "t q[2];",
        ],
        "resource_v2": [
            "cx q[0],q[1];",
            "cz q[1],q[2];",
            "t q[0];",
        ],
        "resource_v3": [
            "cx q[0],q[1];",
            "cx q[1],q[2];",
            "cz q[0],q[2];",
            "t q[0];",
            "t q[1];",
            "t q[2];",
        ],
        "resource_v4": [
            "cx q[0],q[1];",
            "t q[0];",
            "t q[1];",
            "t q[2];",
            "t q[0];",
        ],
    }

    db.add_logical_circuit("resource_pareto_demo")

    for i, (name, gates) in enumerate(circuits.items(), start=1):
        qasm = (
            'OPENQASM 2.0;\n'
            'include "qelib1.inc";\n'
            'qreg q[3];\n'
            + "\n".join(gates)
            + "\n"
        )

        path = tmp_path / f"{name}.qasm"
        path.write_text(qasm, encoding="utf-8")

        db.add_circuit(name, path)
        db.add_version(
            "resource_pareto_demo",
            name,
            label=f"v{i}"
        )

    frontier = db.find_pareto_versions(
        "resource_pareto_demo",
        metrics=["two_qubit_gate_count", "t_count"]
    )

    assert [v["version_number"] for v in frontier] == [1, 2]

    assert [
        (v["two_qubit_gate_count"], v["t_count"])
        for v in frontier
    ] == [
        (1, 3),
        (2, 1),
    ]

    db.close()


def test_get_features_five_metrics(tmp_path):
    from qch import QCHDatabase

    db = QCHDatabase(tmp_path / "five_metrics.db")

    qasm = """
OPENQASM 2.0;
include "qelib1.inc";
qreg q[3];
h q[0];
cx q[0],q[1];
t q[1];
cz q[1],q[2];
t q[2];
tdg q[0];
"""

    qasm_path = tmp_path / "five_metrics.qasm"
    qasm_path.write_text(qasm, encoding="utf-8")

    db.add_circuit("five_metrics_demo", qasm_path)

    features = db.get_features("five_metrics_demo")

    assert features == {
        "num_qubits": 3,
        "gate_count": 6,
        "depth": 5,
        "two_qubit_gate_count": 2,
        "t_count": 2,
    }

    # A nonexistent circuit should return None.
    assert db.get_features("nonexistent") is None

    db.close()

def test_benchmark_schema(tmp_path):
    import sqlite3

    db_path = tmp_path / "benchmark.db"
    db = QCHDatabase(str(db_path))

    try:
        conn = db.connection

        # Verify benchmark tables exist.
        tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }

        assert "benchmarks" in tables
        assert "benchmark_circuits" in tables

        # Create one benchmark and one circuit.
        with conn:
            conn.execute(
                """
                INSERT INTO benchmarks (name, source_url, description)
                VALUES (?, ?, ?)
                """,
                ("TestBench", "https://example.org", "Test benchmark")
            )

            conn.execute(
                """
                INSERT INTO circuits (name, qasm)
                VALUES (?, ?)
                """,
                ("test_circuit", "OPENQASM 2.0;")
            )

            conn.execute(
                """
                INSERT INTO benchmark_circuits
                    (benchmark_id, circuit_id)
                VALUES (1, 1)
                """
            )

        # Verify the relationship.
        count = conn.execute(
            "SELECT COUNT(*) FROM benchmark_circuits"
        ).fetchone()[0]

        assert count == 1

        # Duplicate relationships must be rejected.
        import pytest

        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                """
                INSERT INTO benchmark_circuits
                    (benchmark_id, circuit_id)
                VALUES (1, 1)
                """
            )

    finally:
        db.connection.close()


def test_add_benchmark(tmp_path):
    import sqlite3
    import pytest

    db = QCHDatabase(str(tmp_path / "benchmark_api.db"))

    try:
        benchmark_id = db.add_benchmark(
            name="MQTBench",
            source_url="https://www.cda.cit.tum.de/mqtbench/",
            description="Quantum circuit benchmark collection"
        )

        assert isinstance(benchmark_id, int)
        assert benchmark_id > 0

        row = db.connection.execute(
            """
            SELECT name, source_url, description
            FROM benchmarks
            WHERE id = ?
            """,
            (benchmark_id,)
        ).fetchone()

        assert row == (
            "MQTBench",
            "https://www.cda.cit.tum.de/mqtbench/",
            "Quantum circuit benchmark collection"
        )

        # Duplicate benchmark names are not allowed.
        with pytest.raises(sqlite3.IntegrityError):
            db.add_benchmark("MQTBench")

        # Empty benchmark names are not allowed.
        with pytest.raises(ValueError):
            db.add_benchmark("")

        with pytest.raises(ValueError):
            db.add_benchmark("   ")

    finally:
        db.connection.close()

def test_add_circuit_to_benchmark(tmp_path):
    import sqlite3
    import pytest

    db = QCHDatabase(str(tmp_path / "benchmark_link.db"))

    try:
        db.add_benchmark("MQTBench")

        with db.connection:
            db.connection.execute(
                """
                INSERT INTO circuits (name, qasm)
                VALUES (?, ?)
                """,
                ("ghz3", "OPENQASM 2.0;")
            )

        # Associate the circuit with MQTBench.
        db.add_circuit_to_benchmark("MQTBench", "ghz3")

        row = db.connection.execute(
            """
            SELECT b.name, c.name
            FROM benchmark_circuits bc
            JOIN benchmarks b ON b.id = bc.benchmark_id
            JOIN circuits c ON c.id = bc.circuit_id
            """
        ).fetchone()

        assert row == ("MQTBench", "ghz3")

        # Duplicate associations must be rejected.
        with pytest.raises(sqlite3.IntegrityError):
            db.add_circuit_to_benchmark("MQTBench", "ghz3")

        # Missing benchmark.
        with pytest.raises(ValueError):
            db.add_circuit_to_benchmark("UnknownBench", "ghz3")

        # Missing circuit.
        with pytest.raises(ValueError):
            db.add_circuit_to_benchmark("MQTBench", "unknown_circuit")

    finally:
        db.connection.close()