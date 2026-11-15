"""Tests for bounded permutation generation (qch.permutations)."""

import importlib.util
import os
import subprocess
import sys
from pathlib import Path

import pytest
from qiskit.transpiler.basepasses import TransformationPass

import qch
from qch import QCHDatabase
from qch import batch
from qch import optimization as opt
from qch import permutations as perm

ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = ROOT / "examples"


def _load(name):
    spec = importlib.util.spec_from_file_location(name, EXAMPLES / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


order = _load("pass_order_acceptance")
NAME = order.CIRCUIT_NAME
A, B = order.PASS_A, order.PASS_B
C = {"name": "CommutativeInverseCancellation", "params": {}}

SIX_LABELS = [
    "A->B->C", "A->C->B", "B->A->C", "B->C->A", "C->A->B", "C->B->A",
]


def make_db(tmp_path, file_name="perm.db"):
    db = QCHDatabase(tmp_path / file_name)
    path = tmp_path / f"{file_name}.qasm"
    path.write_text(order.CIRCUIT_QASM, encoding="utf-8")
    db.add_circuit(NAME, path)
    return db


def final_metrics(item):
    return tuple(item.final_metrics[m] for m in batch.COMPARED_METRICS)


def no_runs(db):
    return opt.get_optimization_schema_state(db) == "absent"


# Generation ---------------------------------------------------------------------


def test_two_passes_give_both_orders():
    preview = perm.preview_permutations({"A": A, "B": B})
    assert preview.count == 2
    assert preview.labels == ["A->B", "B->A"]
    assert preview.as_batch_sequences() == {"A->B": [A, B], "B->A": [B, A]}


def test_three_configs_give_six_reproducible_orders():
    first = perm.preview_permutations({"A": A, "B": B, "C": C})
    second = perm.preview_permutations([("A", A), ("B", B), ("C", C)])
    third = perm.preview_permutations(
        [{"id": "A", "pass": A}, {"id": "B", "pass": B}, {"id": "C", "pass": C}]
    )
    assert first.count == 6
    assert first.labels == SIX_LABELS
    assert first == second == third
    for label, passes in first.sequences:
        assert len(passes) == 3
        assert sorted(map(str, passes)) == sorted(map(str, [A, B, C]))


def test_input_order_defines_generation_order():
    preview = perm.preview_permutations({"C": C, "A": A, "B": B})
    assert preview.labels[0] == "C->A->B"
    assert preview.labels[-1] == "B->A->C"


def test_same_pass_with_different_parameters():
    b_ibm = B
    b_u = {"name": "Optimize1qGatesDecomposition", "params": {"basis": ["u", "cx"]}}
    preview = perm.preview_permutations({"B_ibm": b_ibm, "B_u": b_u})
    assert preview.labels == ["B_ibm->B_u", "B_u->B_ibm"]
    assert preview.sequences[1][1] == (b_u, b_ibm)


def test_single_config_gives_one_sequence():
    preview = perm.preview_permutations({"A": A})
    assert (preview.count, preview.labels) == (1, ["A"])


def test_count_permutations():
    assert perm.count_permutations({"A": A, "B": B, "C": C}) == 6


# Limits -------------------------------------------------------------------------


def test_default_limit_allows_four_configs():
    items = {
        "A": A, "B": B, "C": C,
        "D": {"name": "RemoveIdentityEquivalent", "params": {}},
    }
    preview = perm.preview_permutations(items)
    assert preview.count == 24 == perm.DEFAULT_MAX_PERMUTATIONS
    assert len(set(preview.labels)) == 24


def test_limit_is_checked_before_generation(monkeypatch):
    def must_not_run(pairs):
        raise AssertionError("generation must not start above the limit")

    monkeypatch.setattr(perm, "_generate", must_not_run)
    items = {f"P{index}": A for index in range(5)}
    with pytest.raises(perm.PermutationLimitError) as info:
        perm.preview_permutations(items)
    error = info.value
    assert (error.count, error.limit, error.num_items) == (120, 24, 5)
    message = str(error)
    assert "120 permutations" in message
    assert "limit of 24" in message
    assert "run_sequence_batch" in message


def test_limit_is_checked_before_pass_validation():
    # Five configurations exceed the limit even though one is invalid:
    # the size error comes first.
    items = {f"P{index}": A for index in range(4)}
    items["bad"] = {"name": "Unknown"}
    with pytest.raises(perm.PermutationLimitError):
        perm.preview_permutations(items)


def test_explicit_lower_limit(tmp_path):
    db = make_db(tmp_path)
    with pytest.raises(perm.PermutationLimitError) as info:
        perm.run_permutation_batch(
            db, NAME, {"A": A, "B": B, "C": C}, max_permutations=5
        )
    assert info.value.count == 6
    assert no_runs(db)
    db.close()


def test_raised_limit_is_honored():
    items = {f"P{index}": A for index in range(5)}
    assert perm.preview_permutations(items, max_permutations=120).count == 120


@pytest.mark.parametrize("limit", [0, -1, 2.5, True, "24", None])
def test_invalid_limit_values(limit):
    with pytest.raises(batch.BatchConfigError):
        perm.preview_permutations({"A": A}, max_permutations=limit)


# Validation and no-write guarantees ---------------------------------------------


@pytest.mark.parametrize("items", [
    {},
    [],
    "AB",
    {"": A},
    {" A": A},
    {"A->B": A},
    [("A", A), ("A", B)],
    [{"id": "A"}],
    [{"id": "A", "pass": A, "extra": 1}],
    [42],
    {1: A},
    {"A": {"name": "Unknown"}},
    {"A": A, "B": {"name": "Optimize1qGatesDecomposition"}},
])
def test_invalid_inputs_write_nothing(tmp_path, items):
    db = make_db(tmp_path)
    with pytest.raises(batch.BatchConfigError):
        perm.run_permutation_batch(db, NAME, items)
    assert no_runs(db)
    db.close()


def test_invalid_config_error_names_identifier():
    with pytest.raises(batch.BatchConfigError, match="'B'"):
        perm.preview_permutations({"A": A, "B": {"name": "Unknown"}})


def test_preview_does_not_touch_the_database(tmp_path):
    db = make_db(tmp_path)
    db.close()
    path = tmp_path / "perm.db"
    before = path.read_bytes()
    preview = perm.preview_permutations({"A": A, "B": B, "C": C})
    assert preview.count == 6
    assert path.read_bytes() == before
    connection = opt.open_readonly(path)
    try:
        assert opt.get_optimization_schema_state(connection) == "absent"
    finally:
        connection.close()


def test_missing_circuit_writes_nothing(tmp_path):
    db = make_db(tmp_path)
    with pytest.raises(opt.CircuitNotFoundError):
        perm.run_permutation_batch(db, "missing", {"A": A, "B": B})
    assert no_runs(db)
    db.close()


# Execution through stage 2A ------------------------------------------------------


def test_acceptance_two_passes(tmp_path):
    db = make_db(tmp_path)
    preview, result = perm.run_permutation_batch(db, NAME, {"A": A, "B": B})
    assert preview.labels == [item.label for item in result.results]

    ab, ba = result.results
    assert (ab.label, ba.label) == ("A->B", "B->A")
    assert ab.pass_names == [
        "CommutativeCancellation", "Optimize1qGatesDecomposition",
    ]
    assert ba.pass_names == list(reversed(ab.pass_names))
    assert final_metrics(ab) == (13, 2, 9)
    assert final_metrics(ba) == (11, 2, 8)
    assert ab.input_representation_sha256 == ba.input_representation_sha256
    assert result.best_labels("gate_count") == ["B->A"]

    runs = opt.list_optimization_runs(db, NAME)
    assert sorted(r["run_id"] for r in runs) == sorted([ab.run_id, ba.run_id])
    db.close()


def test_acceptance_three_configs_reproducible(tmp_path):
    db = make_db(tmp_path)
    items = {"A": A, "B": B, "C": C}
    first_preview, first = perm.run_permutation_batch(db, NAME, items)
    second_preview, second = perm.run_permutation_batch(db, NAME, items)

    assert first_preview == second_preview
    assert [r.label for r in first.results] == SIX_LABELS
    assert [r.label for r in second.results] == SIX_LABELS
    assert [final_metrics(r) for r in first.results] == [
        (11, 2, 8), (13, 2, 9), (11, 2, 8), (11, 2, 8), (13, 2, 9), (11, 2, 8),
    ]
    assert [final_metrics(r) for r in second.results] == [
        final_metrics(r) for r in first.results
    ]
    assert len({r.run_id for r in first.results + second.results}) == 12
    assert all(r.status == "SUCCEEDED" for r in first.results)
    assert result_sets_share_input(first.results + second.results)
    db.close()


def result_sets_share_input(results):
    return len({r.input_representation_sha256 for r in results}) == 1


def test_steps_are_queryable_by_run_id_after_reconnect(tmp_path):
    db = make_db(tmp_path)
    _, result = perm.run_permutation_batch(db, NAME, {"A": A, "B": B, "C": C})
    run_ids = {item.label: item.run_id for item in result.results}
    rows = result.rows()
    db.close()

    connection = opt.open_readonly(tmp_path / "perm.db")
    try:
        again = batch.summarize_runs(connection, NAME, run_ids)
        assert again.rows() == rows
        steps = opt.list_optimization_steps(connection, run_ids["B->C->A"])
        assert [s["pass_name"] for s in steps] == [
            None, "Optimize1qGatesDecomposition",
            "CommutativeInverseCancellation", "CommutativeCancellation",
        ]
        circuit = opt.load_step_circuit(connection, run_ids["B->C->A"], 1)
        assert circuit.size() == steps[1]["gate_count"] == 16
    finally:
        connection.close()


class FailingPass(TransformationPass):
    def run(self, dag):
        raise RuntimeError("injected pass failure")


def test_pass_failure_in_permutations_keeps_records(tmp_path, monkeypatch):
    registry = dict(opt.PASS_REGISTRY)
    registry["FailingPass"] = opt._RegistryEntry(
        normalize=lambda params: dict(params),
        build=lambda params: FailingPass(),
    )
    monkeypatch.setattr(opt, "PASS_REGISTRY", registry)

    db = make_db(tmp_path)
    preview, result = perm.run_permutation_batch(
        db, NAME, {"A": A, "F": {"name": "FailingPass"}}
    )
    assert preview.labels == ["A->F", "F->A"]
    a_f, f_a = result.results
    assert a_f.status == f_a.status == "PASS_FAILED"
    assert (a_f.failed_step_index, f_a.failed_step_index) == (2, 1)
    assert a_f.final_metrics is None and f_a.final_metrics is None
    assert result.best_by_metric() == {m: [] for m in batch.COMPARED_METRICS}

    steps = opt.list_optimization_steps(db, a_f.run_id)
    assert [s["status"] for s in steps] == ["SUCCEEDED", "SUCCEEDED", "PASS_FAILED"]
    assert opt.load_step_circuit(db, a_f.run_id, 1).size() == 7
    db.close()


def test_explicit_api_still_supports_repeats(tmp_path):
    db = make_db(tmp_path)
    result = batch.run_sequence_batch(db, NAME, {"A->B->A": [A, B, A]})
    assert len(result.results[0].pass_names) == 3
    assert final_metrics(result.results[0]) == (11, 2, 8)
    db.close()


# Example script ----------------------------------------------------------------


def _env():
    env = dict(os.environ)
    src = str(Path(qch.__file__).resolve().parents[1])
    env["PYTHONPATH"] = src + os.pathsep + env.get("PYTHONPATH", "")
    return env


def test_example_preview_and_demo(tmp_path):
    script = str(EXAMPLES / "permutation_demo.py")
    preview = subprocess.run(
        [sys.executable, script, "preview", "--ids", "A", "B", "C"],
        capture_output=True, text=True, env=_env(), check=False,
    )
    assert preview.returncode == 0, preview.stderr
    assert "Permutations: 6 (limit 24)" in preview.stdout
    assert "6. C->B->A" in preview.stdout

    db_path = tmp_path / "demo.db"
    demo = subprocess.run(
        [sys.executable, script, "demo", "--db", str(db_path), "--ids", "A", "B"],
        capture_output=True, text=True, env=_env(), check=False,
    )
    assert demo.returncode == 0, demo.stderr
    assert "gate_count: B->A" in demo.stdout


def test_example_over_limit_creates_no_database(tmp_path):
    script = str(EXAMPLES / "permutation_demo.py")
    db_path = tmp_path / "over.db"
    over = subprocess.run(
        [sys.executable, script, "demo", "--db", str(db_path),
         "--ids", "A", "B", "C", "--max", "2"],
        capture_output=True, text=True, env=_env(), check=False,
    )
    assert over.returncode == 2
    assert "6 permutations" in over.stderr
    assert not db_path.exists()
