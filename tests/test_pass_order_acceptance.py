"""Acceptance tests: one circuit optimized in the orders A -> B and B -> A."""

import importlib.util
import os
import subprocess
import sys
from pathlib import Path

import pytest
from qiskit import QuantumCircuit
from qiskit.quantum_info import Operator
from qiskit.transpiler.basepasses import TransformationPass

import qch
from qch import QCHDatabase
from qch import optimization as opt

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "examples" / "pass_order_acceptance.py"


def load_script():
    spec = importlib.util.spec_from_file_location("pass_order_acceptance", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


demo = load_script()


def make_db(tmp_path):
    db = QCHDatabase(tmp_path / "order.db")
    path = tmp_path / "input.qasm"
    path.write_text(demo.CIRCUIT_QASM, encoding="utf-8")
    db.add_circuit(demo.CIRCUIT_NAME, path)
    return db


def metrics(step):
    return (step["gate_count"], step["two_qubit_gate_count"], step["depth"])


def test_input_circuit_has_no_measurements():
    circuit = QuantumCircuit.from_qasm_str(demo.CIRCUIT_QASM)
    assert circuit.num_clbits == 0
    assert set(circuit.count_ops()) <= {"h", "cx", "sdg", "t", "z"}


def test_both_orders_from_the_same_input(tmp_path):
    db = make_db(tmp_path)
    stored_qasm = db.get_circuit(demo.CIRCUIT_NAME)
    results = {
        label: opt.run_pass_sequence(db, demo.CIRCUIT_NAME, seq, label=label)
        for label, seq in demo.SEQUENCES.items()
    }
    assert db.get_circuit(demo.CIRCUIT_NAME) == stored_qasm

    steps = {
        label: opt.list_optimization_steps(db, result.run_id)
        for label, result in results.items()
    }
    # Both runs start from the same input representation.
    assert metrics(steps["A->B"][0]) == metrics(steps["B->A"][0]) == (10, 4, 7)
    inputs = {
        opt.representation_fingerprint(
            opt.load_step_circuit(db, result.run_id, 0)
        )
        for result in results.values()
    }
    assert len(inputs) == 1

    assert [s["pass_name"] for s in steps["A->B"]] == [
        None, "CommutativeCancellation", "Optimize1qGatesDecomposition",
    ]
    assert [s["pass_name"] for s in steps["B->A"]] == [
        None, "Optimize1qGatesDecomposition", "CommutativeCancellation",
    ]
    for label, run_steps in steps.items():
        assert all(s["status"] == "SUCCEEDED" for s in run_steps)
        assert all(s["pass_time_s"] is not None for s in run_steps[1:])
        assert run_steps[0]["pass_time_s"] is None

    # The two orders end in different circuits with different metrics.
    assert [metrics(s) for s in steps["A->B"]] == [
        (10, 4, 7), (7, 2, 5), (13, 2, 9),
    ]
    assert [metrics(s) for s in steps["B->A"]] == [
        (10, 4, 7), (16, 4, 11), (11, 2, 8),
    ]

    reference = Operator(QuantumCircuit.from_qasm_str(demo.CIRCUIT_QASM))
    for result in results.values():
        final = opt.load_step_circuit(db, result.run_id, 2)
        assert Operator(final).equiv(reference)
    db.close()


def test_run_and_verify_in_separate_processes(tmp_path):
    db_path = tmp_path / "separate.db"
    env = dict(os.environ)
    src = str(Path(qch.__file__).resolve().parents[1])
    env["PYTHONPATH"] = src + os.pathsep + env.get("PYTHONPATH", "")

    run = subprocess.run(
        [sys.executable, str(SCRIPT), "run", "--db", str(db_path)],
        capture_output=True, text=True, env=env, check=False,
    )
    assert run.returncode == 0, run.stderr
    assert "status SUCCEEDED" in run.stdout

    before = db_path.read_bytes()
    verify = subprocess.run(
        [sys.executable, str(SCRIPT), "verify", "--db", str(db_path)],
        capture_output=True, text=True, env=env, check=False,
    )
    assert verify.returncode == 0, verify.stdout + verify.stderr
    assert "All checks passed" in verify.stdout
    assert "identical: False" in verify.stdout
    # Verification is read-only.
    assert db_path.read_bytes() == before

    again = subprocess.run(
        [sys.executable, str(SCRIPT), "run", "--db", str(db_path)],
        capture_output=True, text=True, env=env, check=False,
    )
    assert again.returncode == 2
    assert db_path.read_bytes() == before


def test_restored_intermediate_matches_stored_metrics(tmp_path):
    db = make_db(tmp_path)
    result = opt.run_pass_sequence(
        db, demo.CIRCUIT_NAME, demo.SEQUENCES["B->A"], label="B->A"
    )
    stored = opt.list_optimization_steps(db, result.run_id)[1]
    db.close()

    connection = opt.open_readonly(tmp_path / "order.db")
    try:
        circuit = opt.load_step_circuit(connection, result.run_id, 1)
        recomputed = opt.compute_step_metrics(circuit)
        for column in demo.METRIC_COLUMNS:
            assert recomputed[column] == stored[column]
        assert dict(circuit.count_ops()) == {"rz": 9, "sx": 3, "cx": 4}
    finally:
        connection.close()


def test_aba_records_both_a_steps_separately(tmp_path):
    db = make_db(tmp_path)
    result = opt.run_pass_sequence(
        db, demo.CIRCUIT_NAME, [demo.PASS_A, demo.PASS_B, demo.PASS_A]
    )
    steps = opt.list_optimization_steps(db, result.run_id)
    assert [s["step_index"] for s in steps] == [0, 1, 2, 3]
    assert steps[1]["pass_name"] == steps[3]["pass_name"] == (
        "CommutativeCancellation"
    )
    assert steps[1]["pass_params"] == steps[3]["pass_params"]
    assert steps[1]["pass_time_s"] is not None
    assert steps[3]["pass_time_s"] is not None
    # The second A runs on B's output and changes it further.
    assert steps[3]["changed"] == 1
    assert metrics(steps[3]) == (11, 2, 8)
    db.close()


class FailingPass(TransformationPass):
    def run(self, dag):
        raise RuntimeError("injected failure after a successful step")


def test_failure_keeps_earlier_steps_queryable(tmp_path, monkeypatch):
    registry = dict(opt.PASS_REGISTRY)
    registry["FailingPass"] = opt._RegistryEntry(
        normalize=lambda params: dict(params),
        build=lambda params: FailingPass(),
    )
    monkeypatch.setattr(opt, "PASS_REGISTRY", registry)

    db = make_db(tmp_path)
    result = opt.run_pass_sequence(
        db, demo.CIRCUIT_NAME,
        [demo.PASS_A, {"name": "FailingPass"}, demo.PASS_B],
    )
    assert result.status == "PASS_FAILED"
    assert result.failed_step_index == 2
    db.close()

    connection = opt.open_readonly(tmp_path / "order.db")
    try:
        run = opt.get_optimization_run(connection, result.run_id)
        assert [s["status"] for s in run["steps"]] == [
            "SUCCEEDED", "SUCCEEDED", "PASS_FAILED", "NOT_RUN",
        ]
        circuit = opt.load_step_circuit(connection, result.run_id, 1)
        assert opt.compute_step_metrics(circuit)["gate_count"] == 7
        with pytest.raises(LookupError):
            opt.load_step_circuit(connection, result.run_id, 2)
    finally:
        connection.close()
