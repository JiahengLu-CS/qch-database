"""Tests for ordered optimization pass sequences (qch.optimization)."""

import hashlib
import sqlite3

import pytest
from qiskit import QuantumCircuit
from qiskit.circuit import Gate
from qiskit.quantum_info import Operator
from qiskit.transpiler import PassManager
from qiskit.transpiler.basepasses import TransformationPass
from qiskit.transpiler.passes import (
    InverseCancellation,
    Optimize1qGatesDecomposition,
)

from qch import QCHDatabase
from qch import optimization as opt


REDUNDANT_QASM = """OPENQASM 2.0;
include "qelib1.inc";
qreg q[3];
creg c[3];
h q[0];
h q[0];
x q[1];
rz(0.25) q[1];
rz(0.5) q[1];
cx q[0],q[1];
cx q[0],q[1];
ccx q[0],q[1],q[2];
barrier q[0],q[1];
measure q[0] -> c[0];
"""

UNITARY_QASM = """OPENQASM 2.0;
include "qelib1.inc";
qreg q[2];
h q[0];
rz(0.3) q[0];
sx q[0];
x q[1];
x q[1];
cx q[0],q[1];
rz(0.7) q[1];
rz(-0.2) q[1];
"""

O1Q = {
    "name": "Optimize1qGatesDecomposition",
    "params": {"basis": ["rz", "sx", "x", "cx"]},
}
IC = {"name": "InverseCancellation"}
RIE = {"name": "RemoveIdentityEquivalent"}


def make_db(tmp_path, qasm=REDUNDANT_QASM, name="demo", db_name="opt.db"):
    db = QCHDatabase(tmp_path / db_name)
    path = tmp_path / f"{name}.qasm"
    path.write_text(qasm, encoding="utf-8")
    db.add_circuit(name, path)
    return db


def steps_of(db, run_id):
    return opt.list_optimization_steps(db, run_id)


class FailingPass(TransformationPass):
    """A transformation pass that always raises."""

    def run(self, dag):
        raise RuntimeError("injected pass failure")


@pytest.fixture
def failing_pass_registered(monkeypatch):
    registry = dict(opt.PASS_REGISTRY)
    registry["FailingPass"] = opt._RegistryEntry(
        normalize=lambda params: dict(params),
        build=lambda params: FailingPass(),
    )
    monkeypatch.setattr(opt, "PASS_REGISTRY", registry)


# 1. Metrics ---------------------------------------------------------------


def test_step_metrics_manual_circuit():
    qc = QuantumCircuit(3, 2)
    qc.h(0)
    qc.x(1)
    qc.cx(0, 1)
    qc.ccx(0, 1, 2)
    qc.barrier(0, 1)
    qc.barrier()
    qc.measure(0, 0)
    qc.reset(2)

    metrics = opt.compute_step_metrics(qc)

    assert metrics == {
        "num_qubits": 3,
        "num_clbits": 2,
        "gate_count": qc.size(),
        "depth": qc.depth(),
        "single_qubit_gate_count": 2,
        "two_qubit_gate_count": 1,
        "multi_qubit_gate_count": 1,
        "measure_count": 1,
        "reset_count": 1,
        "barrier_count": 2,
        "opaque_gate_count": 0,
    }
    # gate_count keeps the legacy definition: barriers excluded,
    # measure and reset included.
    assert metrics["gate_count"] == 6
    assert (
        metrics["single_qubit_gate_count"]
        + metrics["two_qubit_gate_count"]
        + metrics["multi_qubit_gate_count"]
        + metrics["measure_count"]
        + metrics["reset_count"]
    ) == metrics["gate_count"]


def test_step_metrics_count_opaque_gates_separately():
    qc = QuantumCircuit.from_qasm_str(
        'OPENQASM 2.0;\ninclude "qelib1.inc";\nopaque ecr q0,q1;\n'
        "qreg q[2];\necr q[0],q[1];\nsx q[0];\n"
    )
    metrics = opt.compute_step_metrics(qc)
    assert metrics["two_qubit_gate_count"] == 1
    assert metrics["single_qubit_gate_count"] == 1
    assert metrics["opaque_gate_count"] == 1


def test_step_metrics_differ_from_legacy_two_qubit_count(tmp_path):
    qasm = (
        'OPENQASM 2.0;\ninclude "qelib1.inc";\nqreg q[2];\n'
        "cx q[0],q[1];\nbarrier q[0],q[1];\n"
    )
    db = make_db(tmp_path, qasm=qasm)
    # The legacy feature counts every two-qubit instruction, barriers
    # included; the step metric counts gates only.
    assert db.get_features("demo")["two_qubit_gate_count"] == 2
    metrics = opt.compute_step_metrics(QuantumCircuit.from_qasm_str(qasm))
    assert metrics["two_qubit_gate_count"] == 1
    assert metrics["barrier_count"] == 1
    db.close()


def test_unsupported_operations_are_rejected():
    custom = QuantumCircuit(1, name="custom")
    custom.h(0)
    qc = QuantumCircuit(1)
    qc.append(custom.to_gate(), [0])
    with pytest.raises(opt.UnsupportedCircuitError, match="custom"):
        opt.compute_step_metrics(qc)

    qc = QuantumCircuit(1)
    qc.delay(10, 0)
    with pytest.raises(opt.UnsupportedCircuitError):
        opt.compute_step_metrics(qc)

    from qiskit.circuit import Parameter

    qc = QuantumCircuit(1)
    qc.rz(Parameter("theta"), 0)
    with pytest.raises(opt.UnsupportedCircuitError):
        opt.compute_step_metrics(qc)

    qc = QuantumCircuit(1, 1)
    qc.measure(0, 0)
    with qc.if_test((qc.clbits[0], 1)):
        qc.x(0)
    with pytest.raises(opt.UnsupportedCircuitError):
        opt.compute_step_metrics(qc)


# 2. A real pass removes redundant gates -----------------------------------


def test_inverse_cancellation_removes_redundant_gates(tmp_path):
    db = make_db(tmp_path)
    result = opt.run_pass_sequence(db, "demo", [IC])

    assert result.status == "SUCCEEDED"
    step0, step1 = steps_of(db, result.run_id)
    assert step0["status"] == step1["status"] == "SUCCEEDED"
    # h-h and cx-cx pairs are cancelled.
    assert step1["changed"] == 1
    assert step0["single_qubit_gate_count"] - step1[
        "single_qubit_gate_count"] == 2
    assert step0["two_qubit_gate_count"] - step1["two_qubit_gate_count"] == 2
    assert step1["multi_qubit_gate_count"] == 1
    assert step1["gate_count"] == step0["gate_count"] - 4

    restored = opt.load_step_circuit(db, result.run_id, 1)
    assert [i.operation.name for i in restored.data].count("cx") == 0
    db.close()


# 3. A -> B -> A ------------------------------------------------------------


def test_aba_sequence_records_every_position(tmp_path):
    db = make_db(tmp_path)
    result = opt.run_pass_sequence(db, "demo", [IC, O1Q, IC], label="aba")
    steps = steps_of(db, result.run_id)

    assert [s["step_index"] for s in steps] == [0, 1, 2, 3]
    assert [s["pass_name"] for s in steps] == [
        None,
        "InverseCancellation",
        "Optimize1qGatesDecomposition",
        "InverseCancellation",
    ]
    assert all(s["status"] == "SUCCEEDED" for s in steps)
    # Every sequence position actually executed and was timed.
    assert all(s["pass_time_s"] is not None for s in steps[1:])
    assert all(s["qiskit_reported_time_s"] is not None for s in steps[1:])
    assert steps[0]["pass_time_s"] is None
    assert steps[2]["pass_params"] == {"basis": ["rz", "sx", "x", "cx"]}

    run = opt.get_optimization_run(db, result.run_id)
    assert run["label"] == "aba"
    assert run["num_steps"] == 3
    assert [p["name"] for p in run["pass_sequence"]] == [
        "InverseCancellation",
        "Optimize1qGatesDecomposition",
        "InverseCancellation",
    ]
    db.close()


def test_each_position_gets_its_own_pass_instance(tmp_path, monkeypatch):
    built = []
    original = opt.PreparedPass.build

    def recording_build(self):
        instance = original(self)
        built.append(instance)
        return instance

    monkeypatch.setattr(opt.PreparedPass, "build", recording_build)
    db = make_db(tmp_path)
    opt.run_pass_sequence(db, "demo", [IC, IC, IC])
    run_instances = built[-3:]
    assert len({id(instance) for instance in run_instances}) == 3
    db.close()


# 4. Unchanged step ---------------------------------------------------------


def test_unchanged_step_is_recorded_and_reuses_snapshot(tmp_path):
    db = make_db(tmp_path)
    result = opt.run_pass_sequence(db, "demo", [IC, IC, RIE])
    steps = steps_of(db, result.run_id)

    assert steps[1]["changed"] == 1
    assert steps[2]["status"] == "SUCCEEDED"
    assert steps[2]["changed"] == 0
    assert steps[2]["snapshot_id"] == steps[1]["snapshot_id"]
    assert steps[3]["changed"] == 0
    assert steps[3]["snapshot_id"] == steps[1]["snapshot_id"]
    assert steps[2]["gate_count"] == steps[1]["gate_count"]

    count = db.connection.execute(
        "SELECT COUNT(*) FROM circuit_snapshots"
    ).fetchone()[0]
    assert count == 2
    db.close()


def test_noop_first_pass_is_unchanged_relative_to_step0(tmp_path):
    qasm = 'OPENQASM 2.0;\ninclude "qelib1.inc";\nqreg q[2];\nh q[0];\ncx q[0],q[1];\n'
    db = make_db(tmp_path, qasm=qasm)
    result = opt.run_pass_sequence(db, "demo", [IC])
    steps = steps_of(db, result.run_id)
    assert steps[1]["changed"] == 0
    assert steps[1]["snapshot_id"] == steps[0]["snapshot_id"]
    db.close()


# 5. Pass failure -----------------------------------------------------------


def test_pass_failure_keeps_previous_steps(tmp_path, failing_pass_registered):
    db = make_db(tmp_path)
    result = opt.run_pass_sequence(
        db, "demo", [IC, O1Q, {"name": "FailingPass"}, IC]
    )

    assert result.status == "PASS_FAILED"
    assert result.failed_step_index == 3
    assert result.error_type == "builtins.RuntimeError"
    assert "injected pass failure" in result.error_message

    steps = steps_of(db, result.run_id)
    assert [s["status"] for s in steps] == [
        "SUCCEEDED", "SUCCEEDED", "SUCCEEDED", "PASS_FAILED", "NOT_RUN",
    ]
    failed = steps[3]
    assert failed["error_type"] == "builtins.RuntimeError"
    assert failed["snapshot_id"] is None
    assert failed["gate_count"] is None
    assert failed["pass_time_s"] is not None
    assert steps[4]["pass_time_s"] is None
    assert steps[4]["error_type"] is None

    run = opt.get_optimization_run(db, result.run_id)
    assert run["status"] == "PASS_FAILED"
    assert run["failed_step_index"] == 3
    assert run["finished_at"] is not None

    # Results before the failure remain restorable.
    opt.load_step_circuit(db, result.run_id, 2)
    with pytest.raises(LookupError):
        opt.load_step_circuit(db, result.run_id, 3)
    db.close()


def test_pass_failure_at_first_step(tmp_path, failing_pass_registered):
    db = make_db(tmp_path)
    result = opt.run_pass_sequence(db, "demo", [{"name": "FailingPass"}, IC])
    steps = steps_of(db, result.run_id)
    assert [s["status"] for s in steps] == [
        "SUCCEEDED", "PASS_FAILED", "NOT_RUN",
    ]
    db.close()


# 6. Recording failures are not pass failures --------------------------------


def test_serialization_failure_is_a_recording_failure(tmp_path, monkeypatch):
    db = make_db(tmp_path)
    calls = {"n": 0}
    original = opt.serialize_snapshot

    def flaky(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 2:
            raise ValueError("injected serialization failure")
        return original(*args, **kwargs)

    monkeypatch.setattr(opt, "serialize_snapshot", flaky)

    with pytest.raises(opt.RecordingError) as info:
        opt.run_pass_sequence(db, "demo", [IC, O1Q, IC])

    error = info.value
    assert error.phase == "serialize"
    assert error.step_index == 1
    assert error.failure_persisted is True
    assert error.run_id is not None

    run = opt.get_optimization_run(db, error.run_id)
    assert run["status"] == "RECORDING_FAILED"
    assert run["failed_step_index"] == 1
    statuses = [s["status"] for s in run["steps"]]
    assert statuses == ["SUCCEEDED", "RECORDING_FAILED", "NOT_RUN", "NOT_RUN"]
    assert "PASS_FAILED" not in statuses
    assert run["steps"][1]["error_type"] == "builtins.ValueError"
    # The pass itself succeeded and was timed.
    assert run["steps"][1]["pass_time_s"] is not None
    db.close()


def test_database_failure_is_a_recording_failure(tmp_path, monkeypatch):
    db = make_db(tmp_path)
    original = opt._StepRecorder._insert_snapshot
    calls = {"n": 0}

    def failing_insert(self, snapshot):
        calls["n"] += 1
        if calls["n"] == 2:
            raise sqlite3.OperationalError("injected database failure")
        return original(self, snapshot)

    monkeypatch.setattr(opt._StepRecorder, "_insert_snapshot", failing_insert)

    with pytest.raises(opt.RecordingError) as info:
        opt.run_pass_sequence(db, "demo", [IC, IC])

    assert info.value.phase == "database"
    run = opt.get_optimization_run(db, info.value.run_id)
    assert run["status"] == "RECORDING_FAILED"
    assert run["error_type"] == "sqlite3.OperationalError"
    assert [s["status"] for s in run["steps"]] == [
        "SUCCEEDED", "RECORDING_FAILED", "NOT_RUN",
    ]
    db.close()


def test_unpersistable_failure_raises_with_context(tmp_path, monkeypatch):
    db = make_db(tmp_path)

    def always_fail(self, **kwargs):
        raise sqlite3.OperationalError("injected: cannot write steps")

    monkeypatch.setattr(opt._StepRecorder, "_insert_step", always_fail)

    with pytest.raises(opt.RecordingError) as info:
        opt.run_pass_sequence(db, "demo", [IC])

    error = info.value
    assert error.failure_persisted is False
    assert error.run_id is not None
    assert error.step_index == 0
    assert "could not be written" in str(error)
    # The run row exists but could not be finalized.
    row = db.connection.execute(
        "SELECT status FROM optimization_runs WHERE id = ?", (error.run_id,)
    ).fetchone()
    assert row == ("RUNNING",)
    assert not db.connection.in_transaction
    db.close()


# 7. Inputs are not modified; runs are independent --------------------------


def test_input_object_and_stored_qasm_are_unchanged(tmp_path):
    db = make_db(tmp_path)
    stored_before = db.get_circuit("demo")

    circuit = QuantumCircuit.from_qasm_str(stored_before)
    fingerprint_before = opt.representation_fingerprint(circuit)
    prepared = opt.prepare_pass_sequence([IC, O1Q, IC])
    opt._execute_sequence(
        db.connection,
        source_circuit_id=1,
        source_qasm_sha256="0" * 64,
        input_circuit=circuit,
        prepared=prepared,
        label=None,
        verify_snapshots=True,
    )
    assert opt.representation_fingerprint(circuit) == fingerprint_before

    first = opt.run_pass_sequence(db, "demo", [IC, O1Q, IC])
    second = opt.run_pass_sequence(db, "demo", [IC, O1Q, IC])

    assert db.get_circuit("demo") == stored_before
    gates_rows = db.connection.execute("SELECT COUNT(*) FROM gates").fetchone()
    assert gates_rows == (len(QuantumCircuit.from_qasm_str(stored_before)),)

    first_steps = steps_of(db, first.run_id)
    second_steps = steps_of(db, second.run_id)
    assert first.run_id != second.run_id
    # Each run starts from the same input and produces its own records.
    assert {s["snapshot_id"] for s in first_steps}.isdisjoint(
        {s["snapshot_id"] for s in second_steps}
    )
    for step_a, step_b in zip(first_steps, second_steps):
        for metric in ("gate_count", "depth", "two_qubit_gate_count"):
            assert step_a[metric] == step_b[metric]
    fingerprints = {
        opt.representation_fingerprint(
            opt.load_step_circuit(db, run.run_id, 3)
        )
        for run in (first, second)
    }
    assert len(fingerprints) == 1
    db.close()


def test_snapshots_do_not_touch_gates_versions_or_benchmarks(tmp_path):
    db = make_db(tmp_path)
    before = {
        table: db.connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()
        for table in (
            "circuits", "gates", "features", "logical_circuits",
            "circuit_versions", "transformation_edges", "benchmarks",
            "benchmark_circuits",
        )
    }
    opt.run_pass_sequence(db, "demo", [IC, O1Q])
    after = {
        table: db.connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()
        for table in before
    }
    assert before == after
    db.close()


# 8. Persistence across reconnects ------------------------------------------


def test_records_survive_reconnect_with_readonly_connection(tmp_path):
    db = make_db(tmp_path)
    result = opt.run_pass_sequence(db, "demo", [IC, O1Q, IC], label="persist")
    expected_steps = steps_of(db, result.run_id)
    db.close()

    connection = opt.open_readonly(tmp_path / "opt.db")
    try:
        runs = opt.list_optimization_runs(connection)
        assert [r["run_id"] for r in runs] == [result.run_id]
        assert runs[0]["source_circuit_name"] == "demo"
        assert runs[0]["status"] == "SUCCEEDED"
        assert runs[0]["compiler_version"]
        assert runs[0]["python_version"]
        assert runs[0]["metrics_version"] == opt.METRICS_VERSION
        assert runs[0]["seed"] is None

        run = opt.get_optimization_run(connection, result.run_id)
        assert run["steps"] == expected_steps
        assert all(
            step["db_write_time_s"] is not None for step in run["steps"]
        )
        circuit = opt.load_step_circuit(connection, result.run_id, 3)
        assert isinstance(circuit, QuantumCircuit)

        with pytest.raises(sqlite3.OperationalError):
            connection.execute("DELETE FROM optimization_runs")
    finally:
        connection.close()

    assert opt.list_optimization_runs(
        opt.open_readonly(tmp_path / "opt.db"), circuit_name="other"
    ) == []


# 9. QPY round trip ---------------------------------------------------------


def test_qpy_roundtrip_preserves_global_phase_and_semantics(tmp_path):
    qc = QuantumCircuit.from_qasm_str(UNITARY_QASM)
    qc.global_phase = 0.4321
    fingerprint = opt.representation_fingerprint(qc)

    snapshot = opt.serialize_snapshot(qc, fingerprint, verify=True)
    restored = opt._load_qpy(snapshot.qpy_bytes)
    assert restored.global_phase == qc.global_phase
    assert opt.representation_fingerprint(restored) == fingerprint
    assert snapshot.qasm2_text is not None

    # Only the global phase differs: different representation.
    shifted = qc.copy()
    shifted.global_phase = 0.4322
    assert opt.representation_fingerprint(shifted) != fingerprint

    db = make_db(tmp_path, qasm=UNITARY_QASM)
    result = opt.run_pass_sequence(db, "demo", [IC, O1Q, RIE])
    original = Operator(QuantumCircuit.from_qasm_str(UNITARY_QASM))
    for step_index in range(4):
        circuit = opt.load_step_circuit(db, result.run_id, step_index)
        assert Operator(circuit).equiv(original)
    db.close()


def test_opaque_gate_snapshot_roundtrip():
    qc = QuantumCircuit.from_qasm_str(
        'OPENQASM 2.0;\ninclude "qelib1.inc";\nopaque ecr q0,q1;\n'
        "qreg q[2];\ncreg c[2];\necr q[0],q[1];\nmeasure q[0] -> c[0];\n"
    )
    fingerprint = opt.representation_fingerprint(qc)
    snapshot = opt.serialize_snapshot(qc, fingerprint, verify=True)
    restored = opt._load_qpy(snapshot.qpy_bytes)
    assert type(restored.data[0].operation) is Gate
    assert opt.representation_fingerprint(restored) == fingerprint


def test_tampered_snapshot_is_detected(tmp_path):
    db = make_db(tmp_path)
    result = opt.run_pass_sequence(db, "demo", [IC])
    with db.connection:
        db.connection.execute(
            "UPDATE circuit_snapshots SET qpy_sha256 = ?", ("0" * 64,)
        )
    with pytest.raises(opt.SnapshotIntegrityError):
        opt.load_step_circuit(db, result.run_id, 1)
    db.close()


# 10. Same result as running the PassManager directly ------------------------


def test_final_result_matches_direct_pass_manager(tmp_path):
    db = make_db(tmp_path)
    result = opt.run_pass_sequence(db, "demo", [IC, O1Q, IC, RIE])
    final = opt.load_step_circuit(db, result.run_id, 4)

    direct = PassManager([
        InverseCancellation(),
        Optimize1qGatesDecomposition(basis=["rz", "sx", "x", "cx"]),
        InverseCancellation(),
        opt._build_remove_identity({"approximation_degree": 1.0}),
    ]).run(QuantumCircuit.from_qasm_str(db.get_circuit("demo")))

    assert opt.representation_fingerprint(final) == (
        opt.representation_fingerprint(direct)
    )
    db.close()


def test_repeated_runs_are_deterministic(tmp_path):
    db = make_db(tmp_path)
    runs = [
        opt.run_pass_sequence(db, "demo", [O1Q, IC, O1Q]) for _ in range(3)
    ]
    fingerprints = {
        tuple(
            opt.representation_fingerprint(
                opt.load_step_circuit(db, run.run_id, step)
            )
            for step in range(4)
        )
        for run in runs
    }
    assert len(fingerprints) == 1
    db.close()


# 11. Read-only queries ------------------------------------------------------


def test_readonly_queries_without_optimization_tables(tmp_path):
    db = make_db(tmp_path)
    db.close()
    db_path = tmp_path / "opt.db"
    digest_before = hashlib.sha256(db_path.read_bytes()).hexdigest()

    connection = opt.open_readonly(db_path)
    try:
        assert opt.get_optimization_schema_state(connection) == "absent"
        assert opt.list_optimization_runs(connection) == []
        assert opt.list_optimization_steps(connection, 1) == []
        assert opt.get_optimization_run(connection, 1) is None
        with pytest.raises(LookupError):
            opt.load_step_circuit(connection, 1, 0)
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        assert "optimization_runs" not in tables
    finally:
        connection.close()

    assert hashlib.sha256(db_path.read_bytes()).hexdigest() == digest_before

    missing = tmp_path / "missing.db"
    with pytest.raises(FileNotFoundError):
        opt.open_readonly(missing)
    assert not missing.exists()


def test_query_functions_on_writable_connection_do_not_create_tables(tmp_path):
    db = make_db(tmp_path)
    assert opt.list_optimization_runs(db) == []
    assert opt.get_optimization_run(db, 1) is None
    tables = {
        row[0]
        for row in db.connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        )
    }
    assert "optimization_runs" not in tables
    assert "qch_schema_components" not in tables
    db.close()


# Schema, transactions, validation -------------------------------------------


def test_incompatible_schema_is_rejected(tmp_path):
    db = make_db(tmp_path)
    opt.run_pass_sequence(db, "demo", [IC])
    with db.connection:
        db.connection.execute(
            "ALTER TABLE optimization_steps ADD COLUMN extra INTEGER"
        )
    with pytest.raises(opt.SchemaCompatibilityError):
        opt.run_pass_sequence(db, "demo", [IC])
    with pytest.raises(opt.SchemaCompatibilityError):
        opt.list_optimization_runs(db)
    db.close()


def test_newer_schema_version_is_rejected(tmp_path):
    db = make_db(tmp_path)
    opt.ensure_optimization_schema(db)
    with db.connection:
        db.connection.execute(
            "UPDATE qch_schema_components SET version = 2 "
            "WHERE component = 'optimization'"
        )
    with pytest.raises(opt.SchemaCompatibilityError, match="version 2"):
        opt.ensure_optimization_schema(db)
    db.close()


def test_unregistered_tables_are_rejected(tmp_path):
    db = make_db(tmp_path)
    with db.connection:
        db.connection.execute("CREATE TABLE optimization_runs (id INTEGER)")
    with pytest.raises(opt.SchemaCompatibilityError, match="without"):
        opt.run_pass_sequence(db, "demo", [IC])
    db.close()


def test_schema_creation_is_idempotent(tmp_path):
    db = make_db(tmp_path)
    assert opt.ensure_optimization_schema(db) is True
    assert opt.ensure_optimization_schema(db) is False
    assert opt.get_optimization_schema_state(db) == "current"
    db.close()


def test_caller_transaction_is_not_committed(tmp_path):
    db = make_db(tmp_path)
    db.connection.execute(
        "INSERT INTO logical_circuits (name) VALUES ('pending')"
    )
    assert db.connection.in_transaction

    with pytest.raises(opt.TransactionConflictError):
        opt.run_pass_sequence(db, "demo", [IC])

    # The caller's transaction is still open and untouched.
    assert db.connection.in_transaction
    db.connection.rollback()
    assert db.connection.execute(
        "SELECT COUNT(*) FROM logical_circuits"
    ).fetchone() == (0,)
    db.close()


def test_invalid_configuration_creates_no_records(tmp_path):
    db = make_db(tmp_path)
    invalid_sequences = [
        [],
        [{"name": "Unknown"}],
        [{"name": "Optimize1qGatesDecomposition"}],
        [{"name": "Optimize1qGatesDecomposition", "params": {"basis": []}}],
        [{"name": "Optimize1qGatesDecomposition",
          "params": {"basis": ["cx"]}}],
        [{"name": "Optimize1qGatesDecomposition",
          "params": {"basis": ["rz", "bogus"]}}],
        [{"name": "InverseCancellation",
          "params": {"gates_to_cancel": ["rz"]}}],
        [{"name": "InverseCancellation",
          "params": {"gates_to_cancel": [["t", "s"]]}}],
        [{"name": "InverseCancellation", "params": {"seed": 1}}],
        [{"name": "RemoveIdentityEquivalent",
          "params": {"approximation_degree": None}}],
        [{"name": "RemoveIdentityEquivalent",
          "params": {"approximation_degree": 1.5}}],
        [{"name": "InverseCancellation", "params": {"run_default": "yes"}}],
        [{"name": "InverseCancellation", "extra": 1}],
    ]
    for sequence in invalid_sequences:
        with pytest.raises(opt.PassConfigError):
            opt.run_pass_sequence(db, "demo", sequence)

    with pytest.raises(opt.CircuitNotFoundError):
        opt.run_pass_sequence(db, "missing", [IC])

    assert opt.get_optimization_schema_state(db) == "absent"
    db.close()


def test_valid_parameter_forms_are_normalized():
    prepared = opt.prepare_pass_sequence([
        {"name": "InverseCancellation",
         "params": {"gates_to_cancel": ["cx", ["tdg", "t"]],
                    "run_default": True}},
        {"name": "RemoveIdentityEquivalent",
         "params": {"approximation_degree": 1}},
        opt.PassSpec("Optimize1qGatesDecomposition", {"basis": ["u"]}),
    ])
    assert prepared[0].params == {
        "gates_to_cancel": ["cx", ["tdg", "t"]], "run_default": True,
    }
    assert prepared[1].params == {"approximation_degree": 1.0}
    assert [item.position for item in prepared] == [1, 2, 3]


def test_unparsable_stored_qasm_creates_no_records(tmp_path):
    db = make_db(tmp_path)
    with db.connection:
        db.connection.execute(
            "INSERT INTO circuits (name, qasm) VALUES ('broken', 'not qasm')"
        )
    with pytest.raises(opt.UnsupportedCircuitError):
        opt.run_pass_sequence(db, "broken", [IC])
    assert opt.get_optimization_schema_state(db) == "absent"
    db.close()


def test_timing_wrapper_preserves_identity_result_and_exceptions():
    instance = InverseCancellation()
    reference = InverseCancellation()
    state = opt._StepState()
    original = instance.run
    opt._wrap_run(instance, state)

    assert hash(instance) == hash(reference)
    assert instance == reference
    assert instance.run.__wrapped__ == original

    from qiskit.converters import circuit_to_dag

    qc = QuantumCircuit(1)
    qc.h(0)
    qc.h(0)
    dag = circuit_to_dag(qc)
    returned = instance.run(dag)
    assert returned is dag
    assert state.succeeded and state.pass_time_s is not None

    failing = FailingPass()
    failing_state = opt._StepState()
    opt._wrap_run(failing, failing_state)
    with pytest.raises(RuntimeError, match="injected"):
        failing.run(dag)
    assert failing_state.failed
    assert isinstance(failing_state.exception, RuntimeError)


def test_foreign_keys_are_consistent(tmp_path, failing_pass_registered):
    db = make_db(tmp_path)
    opt.run_pass_sequence(db, "demo", [IC, O1Q])
    opt.run_pass_sequence(db, "demo", [IC, {"name": "FailingPass"}, IC])
    assert db.connection.execute("PRAGMA foreign_keys").fetchone() == (1,)
    assert db.connection.execute("PRAGMA foreign_key_check").fetchall() == []
    db.close()
