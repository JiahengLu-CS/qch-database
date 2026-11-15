"""Tests for batch comparison of explicit pass sequences (qch.batch)."""

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
from qch import batch
from qch import optimization as opt

ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = ROOT / "examples"


def _load(name):
    spec = importlib.util.spec_from_file_location(name, EXAMPLES / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


order = _load("pass_order_acceptance")
A, B = order.PASS_A, order.PASS_B
NAME = order.CIRCUIT_NAME


def make_db(tmp_path, file_name="batch.db"):
    db = QCHDatabase(tmp_path / file_name)
    path = tmp_path / f"{file_name}.qasm"
    path.write_text(order.CIRCUIT_QASM, encoding="utf-8")
    db.add_circuit(NAME, path)
    return db


def final_metrics(result):
    return tuple(result.final_metrics[m] for m in batch.COMPARED_METRICS)


class FailingPass(TransformationPass):
    def run(self, dag):
        raise RuntimeError("injected pass failure")


@pytest.fixture
def failing_pass(monkeypatch):
    registry = dict(opt.PASS_REGISTRY)
    registry["FailingPass"] = opt._RegistryEntry(
        normalize=lambda params: dict(params),
        build=lambda params: FailingPass(),
    )
    monkeypatch.setattr(opt, "PASS_REGISTRY", registry)


# Acceptance ----------------------------------------------------------------------


def test_acceptance_ab_and_ba(tmp_path):
    db = make_db(tmp_path)
    result = batch.run_sequence_batch(db, NAME, {"A->B": [A, B], "B->A": [B, A]})

    ab, ba = result.results
    assert [r.label for r in result.results] == ["A->B", "B->A"]
    assert ab.pass_names == [
        "CommutativeCancellation", "Optimize1qGatesDecomposition",
    ]
    assert ba.pass_names == list(reversed(ab.pass_names))
    assert ab.pass_sequence[0]["params"] == {"basis_gates": order.BASIS}
    assert ab.status == ba.status == "SUCCEEDED"
    assert ab.run_id != ba.run_id

    # Both step 0 records are the same input.
    assert result.input_metrics == {
        "gate_count": 10, "two_qubit_gate_count": 4, "depth": 7,
    }
    assert ab.input_metrics == ba.input_metrics == result.input_metrics
    assert ab.input_representation_sha256 == ba.input_representation_sha256
    stored_input = QuantumCircuit.from_qasm_str(db.get_circuit(NAME))
    for item in (ab, ba):
        step0 = opt.load_step_circuit(db, item.run_id, 0)
        assert Operator(step0).equiv(Operator(stored_input))

    assert final_metrics(ab) == (13, 2, 9)
    assert final_metrics(ba) == (11, 2, 8)
    assert ab.deltas == {"gate_count": 3, "two_qubit_gate_count": -2, "depth": 2}
    assert ba.deltas == {"gate_count": 1, "two_qubit_gate_count": -2, "depth": 1}

    assert result.best_labels("gate_count") == ["B->A"]
    assert result.best_labels("depth") == ["B->A"]
    assert result.best_labels("two_qubit_gate_count") == ["A->B", "B->A"]

    for item in (ab, ba):
        assert item.pass_time_complete
        assert item.untimed_steps == []
        steps = opt.list_optimization_steps(db, item.run_id)
        assert item.pass_time_recorded_s == pytest.approx(
            sum(step["pass_time_s"] for step in steps[1:])
        )
    db.close()


def test_reconnect_and_restore_by_run_id(tmp_path):
    db = make_db(tmp_path)
    result = batch.run_sequence_batch(db, NAME, {"A->B": [A, B], "B->A": [B, A]})
    run_ids = {item.label: item.run_id for item in result.results}
    expected_rows = result.rows()
    db.close()

    connection = opt.open_readonly(tmp_path / "batch.db")
    try:
        again = batch.summarize_runs(connection, NAME, run_ids)
        assert again.rows() == expected_rows

        steps = opt.list_optimization_steps(connection, run_ids["B->A"])
        circuit = opt.load_step_circuit(connection, run_ids["B->A"], 1)
        recomputed = opt.compute_step_metrics(circuit)
        for metric in batch.COMPARED_METRICS:
            assert recomputed[metric] == steps[1][metric]
        assert (steps[1]["gate_count"], steps[1]["depth"]) == (16, 11)
    finally:
        connection.close()


# Isolation and order -----------------------------------------------------------


def test_sequences_do_not_influence_each_other(tmp_path):
    db = make_db(tmp_path)
    forward = batch.run_sequence_batch(
        db, NAME, {"A->B": [A, B], "B->A": [B, A]}
    )
    backward = batch.run_sequence_batch(
        db, NAME, {"B->A": [B, A], "A->B": [A, B]}
    )
    alone = {}
    for label, passes in {"A->B": [A, B], "B->A": [B, A]}.items():
        fresh = make_db(tmp_path, file_name=f"alone_{label[0]}.db")
        alone[label] = batch.run_sequence_batch(fresh, NAME, {label: passes})
        fresh.close()

    def final_fp(database, item):
        return opt.representation_fingerprint(
            opt.load_step_circuit(database, item.run_id, len(item.pass_names))
        )

    by_label = {r.label: r for r in backward.results}
    for item in forward.results:
        assert final_metrics(item) == final_metrics(by_label[item.label])
        assert final_fp(db, item) == final_fp(db, by_label[item.label])
        assert final_metrics(item) == final_metrics(alone[item.label].results[0])
    assert [r.label for r in backward.results] == ["B->A", "A->B"]
    db.close()


def test_repeated_passes_are_kept(tmp_path):
    db = make_db(tmp_path)
    result = batch.run_sequence_batch(
        db, NAME, [{"label": "A->B->A", "passes": [A, B, A]}]
    )
    item = result.results[0]
    assert item.pass_names == [
        "CommutativeCancellation", "Optimize1qGatesDecomposition",
        "CommutativeCancellation",
    ]
    steps = opt.list_optimization_steps(db, item.run_id)
    assert [s["step_index"] for s in steps] == [0, 1, 2, 3]
    assert final_metrics(item) == (11, 2, 8)
    assert item.deltas["gate_count"] == 1
    db.close()


# Failures -----------------------------------------------------------------------


def test_pass_failure_is_kept_and_batch_continues(tmp_path, failing_pass):
    db = make_db(tmp_path)
    result = batch.run_sequence_batch(db, NAME, [
        ("broken", [A, {"name": "FailingPass"}, B]),
        ("B->A", [B, A]),
    ])
    broken, ok = result.results

    assert broken.status == "PASS_FAILED"
    assert broken.failed_step_index == 2
    assert broken.error_type == "builtins.RuntimeError"
    assert broken.final_metrics is None
    assert broken.deltas is None
    # Only the step before the failure was timed; the batch says so.
    assert broken.pass_time_complete is False
    assert broken.untimed_steps == [3]
    assert broken.pass_time_recorded_s is not None

    assert ok.status == "SUCCEEDED"
    assert final_metrics(ok) == (11, 2, 8)
    for metric in batch.COMPARED_METRICS:
        assert result.best_labels(metric) == ["B->A"]
    assert [row["final_gate_count"] for row in result.rows()] == [None, 11]

    steps = opt.list_optimization_steps(db, broken.run_id)
    assert [s["status"] for s in steps] == [
        "SUCCEEDED", "SUCCEEDED", "PASS_FAILED", "NOT_RUN",
    ]
    restored = opt.load_step_circuit(db, broken.run_id, 1)
    assert restored.size() == steps[1]["gate_count"] == 7
    table = result.format_table()
    assert "missing" not in table
    assert "incomplete pass timing" in table
    db.close()


def test_complete_timing_has_no_footnote(tmp_path):
    db = make_db(tmp_path)
    result = batch.run_sequence_batch(db, NAME, {"A->B": [A, B]})
    assert "incomplete pass timing" not in result.format_table()
    db.close()


def test_all_sequences_failing_gives_empty_best(tmp_path, failing_pass):
    db = make_db(tmp_path)
    result = batch.run_sequence_batch(
        db, NAME, {"only": [{"name": "FailingPass"}]}
    )
    item = result.results[0]
    assert item.pass_time_recorded_s is not None
    assert result.best_by_metric() == {m: [] for m in batch.COMPARED_METRICS}
    db.close()


def test_recording_failure_stops_the_batch(tmp_path, monkeypatch):
    db = make_db(tmp_path)
    calls = {"n": 0}
    original = opt.serialize_snapshot

    def flaky(*args, **kwargs):
        calls["n"] += 1
        # Run 1 stores 3 snapshots; fail on the second snapshot of run 2.
        if calls["n"] == 5:
            raise OSError("injected snapshot write failure")
        return original(*args, **kwargs)

    monkeypatch.setattr(opt, "serialize_snapshot", flaky)
    with pytest.raises(batch.BatchExecutionError) as info:
        batch.run_sequence_batch(db, NAME, {
            "A->B": [A, B], "B->A": [B, A], "A->B->A": [A, B, A],
        })
    error = info.value
    assert error.failed_label == "B->A"
    assert [item.label for item in error.completed] == ["A->B"]
    assert isinstance(error.__cause__, opt.RecordingError)
    assert error.run_id == error.__cause__.run_id

    runs = opt.list_optimization_runs(db, NAME)
    assert sorted(r["label"] for r in runs) == ["A->B", "B->A"]
    assert {r["label"]: r["status"] for r in runs}["B->A"] == "RECORDING_FAILED"
    db.close()


# Validation ---------------------------------------------------------------------


@pytest.mark.parametrize("sequences", [
    {},
    [],
    "A->B",
    {"": [A]},
    {" A": [A]},
    {"A": []},
    {"A": [{"name": "TemplateOptimization"}]},
    {"A": [A], "B": [{"name": "Optimize1qGatesDecomposition"}]},
    [("A", [A]), ("A", [B])],
    [{"label": "A"}],
    [{"label": "A", "passes": [A], "extra": 1}],
    [42],
])
def test_invalid_batches_write_nothing(tmp_path, sequences):
    db = make_db(tmp_path)
    with pytest.raises(batch.BatchConfigError):
        batch.run_sequence_batch(db, NAME, sequences)
    assert opt.get_optimization_schema_state(db) == "absent"
    db.close()


def test_invalid_second_sequence_names_it_and_writes_nothing(tmp_path):
    db = make_db(tmp_path)
    with pytest.raises(batch.BatchConfigError, match="'second'"):
        batch.run_sequence_batch(db, NAME, {
            "first": [A], "second": [{"name": "Unknown"}],
        })
    assert opt.get_optimization_schema_state(db) == "absent"
    db.close()


def test_missing_or_unsupported_circuit_writes_nothing(tmp_path):
    db = make_db(tmp_path)
    with pytest.raises(opt.CircuitNotFoundError):
        batch.run_sequence_batch(db, "missing", {"A": [A]})
    with db.connection:
        db.connection.execute(
            "INSERT INTO circuits (name, qasm) VALUES ('broken', 'not qasm')"
        )
    with pytest.raises(opt.UnsupportedCircuitError):
        batch.run_sequence_batch(db, "broken", {"A": [A]})
    assert opt.get_optimization_schema_state(db) == "absent"
    db.close()


def test_open_transaction_is_rejected(tmp_path):
    db = make_db(tmp_path)
    db.connection.execute("INSERT INTO logical_circuits (name) VALUES ('x')")
    with pytest.raises(opt.TransactionConflictError):
        batch.run_sequence_batch(db, NAME, {"A": [A]})
    assert db.connection.in_transaction
    db.connection.rollback()
    db.close()


def test_summarize_runs_rejects_mixed_circuits(tmp_path):
    db = make_db(tmp_path)
    other = tmp_path / "other.qasm"
    other.write_text(
        'OPENQASM 2.0;\ninclude "qelib1.inc";\nqreg q[1];\nh q[0];\n',
        encoding="utf-8",
    )
    db.add_circuit("other", other)
    first = batch.run_sequence_batch(db, NAME, {"A": [A]}).results[0]
    second = batch.run_sequence_batch(db, "other", {"A": [A]}).results[0]
    with pytest.raises(ValueError):
        batch.summarize_runs(db, NAME, [("x", first.run_id), ("y", second.run_id)])
    with pytest.raises(LookupError):
        batch.summarize_run(db, 999)
    db.close()


# Example script -----------------------------------------------------------------


def test_demo_script_in_separate_processes(tmp_path):
    db_path = tmp_path / "demo.db"
    env = dict(os.environ)
    src = str(Path(qch.__file__).resolve().parents[1])
    env["PYTHONPATH"] = src + os.pathsep + env.get("PYTHONPATH", "")
    script = str(EXAMPLES / "batch_sequence_demo.py")

    demo = subprocess.run(
        [sys.executable, script, "demo", "--db", str(db_path)],
        capture_output=True, text=True, env=env, check=False,
    )
    assert demo.returncode == 0, demo.stderr
    assert "gate_count: B->A, A->B->A" in demo.stdout

    before = db_path.read_bytes()
    show = subprocess.run(
        [sys.executable, script, "show", "--db", str(db_path),
         "--circuit", NAME, "--run", "A->B=1", "--run", "B->A=2",
         "--restore", "B->A", "1"],
        capture_output=True, text=True, encoding="utf-8", env=env,
        check=False,
    )
    assert show.returncode == 0, show.stderr
    assert "stored:     gates=16, 2q=4, depth=11" in show.stdout
    assert "recomputed: gates=16, 2q=4, depth=11" in show.stdout
    assert db_path.read_bytes() == before


def test_show_restore_with_legacy_output_encoding(tmp_path):
    """A redirected stdout in a code page without pi (Windows cp1252)."""
    db_path = tmp_path / "legacy.db"
    env = dict(os.environ)
    src = str(Path(qch.__file__).resolve().parents[1])
    env["PYTHONPATH"] = src + os.pathsep + env.get("PYTHONPATH", "")
    env["PYTHONIOENCODING"] = "cp1252"
    script = str(EXAMPLES / "batch_sequence_demo.py")
    demo = subprocess.run(
        [sys.executable, script, "demo", "--db", str(db_path)],
        capture_output=True, env=env, check=False,
    )
    assert demo.returncode == 0, demo.stderr
    show = subprocess.run(
        [sys.executable, script, "show", "--db", str(db_path),
         "--circuit", NAME, "--run", "A->B=1", "--run", "B->A=2",
         "--restore", "B->A", "1"],
        capture_output=True, env=env, check=False,
    )
    assert show.returncode == 0, show.stderr.decode("utf-8", "replace")
    text = show.stdout.decode("utf-8")
    assert "\u03c0" in text
    assert "recomputed: gates=16, 2q=4, depth=11" in text
