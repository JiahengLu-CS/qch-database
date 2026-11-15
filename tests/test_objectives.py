"""Tests for the objective-aware view (qch.objectives, stage 2E).

Unit tests use hand-made records; the integration tests use the small
3-qubit acceptance circuit and synthetic MQT-style circuits. None of these
numbers are MQT Bench results.
"""

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest
from qiskit.transpiler.basepasses import TransformationPass

from qch import QCHDatabase
from qch import batch
from qch import objectives as obj
from qch import optimization as opt

ROOT = Path(__file__).resolve().parents[1]
OBJ = ("gates", "two_qubit_gates", "depth")


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


order = _load("pass_order_acceptance_2e",
              ROOT / "examples" / "pass_order_acceptance.py")
helpers = _load("test_mqt_pass_order_helpers_2e",
                ROOT / "tests" / "test_mqt_pass_order.py")
source_db = helpers.source_db  # pytest fixture
VIEW_SCRIPT = ROOT / "experiments" / "objective_view.py"


def rec(label, run_id, gates, two, depth, status="SUCCEEDED", input_id="x"):
    return {
        "label": label, "run_id": run_id, "status": status,
        "input_id": input_id,
        "metrics": None if gates is None else
        {"gates": gates, "two_qubit_gates": two, "depth": depth},
    }


# Unit tests ----------------------------------------------------------------


def test_ties_are_kept_per_objective():
    view = obj.build_view([
        rec("p", 1, 10, 2, 5), rec("q", 2, 10, 3, 5), rec("r", 3, 12, 2, 6),
    ], OBJ)
    assert view.best_labels("gates") == ["p", "q"]
    assert view.best_labels("two_qubit_gates") == ["p", "r"]
    assert view.best_labels("depth") == ["p", "q"]
    assert [c.label for c in view.common_optimum()] == ["p"]
    assert not view.has_conflict()


def test_dominance_and_pareto_front():
    view = obj.build_view([
        rec("best", 1, 10, 2, 5),
        rec("worse_in_one", 2, 10, 2, 6),   # dominated by best
        rec("worse_in_all", 3, 11, 3, 7),   # dominated by both above
        rec("trade_off", 4, 9, 4, 8),       # best gates, not dominated
    ], OBJ)
    assert [c.label for c in view.pareto_front] == ["best", "trade_off"]
    assert view.dominated_by["worse_in_one"] == ["best"]
    assert view.dominated_by["worse_in_all"] == ["best", "worse_in_one"]
    assert view.dominated_by["trade_off"] == []
    # Equal in every objective does not dominate.
    a = obj.Candidate("a", 1, {"gates": 1, "two_qubit_gates": 1, "depth": 1},
                      obj.UNVERIFIED)
    b = obj.Candidate("b", 2, dict(a.metrics), obj.UNVERIFIED)
    assert not obj.dominates(a, b, OBJ) and not obj.dominates(b, a, OBJ)


def test_gate_depth_conflict_without_common_optimum():
    view = obj.build_view([
        rec("few_gates", 1, 233, 90, 80),
        rec("low_depth", 2, 238, 90, 78),
    ], OBJ)
    assert view.best_labels("gates") == ["few_gates"]
    assert view.best_labels("depth") == ["low_depth"]
    assert view.common_optimum() == []
    assert view.has_conflict()
    assert view.conflicts() == [("gates", "depth")]
    assert [c.label for c in view.pareto_front] == ["few_gates", "low_depth"]
    text = view.format()
    assert "Common optimum: none" in text
    assert "no candidate is best for both gates and depth" in text


def test_failed_incomplete_and_unverified_results():
    records = [
        rec("ok", 1, 10, 2, 5),
        rec("failed", 2, None, None, None, status="PASS_FAILED"),
        rec("partial", 3, 9, None, 4),
        rec("bad_eq", 4, 1, 1, 1),
        rec("no_record", 5, 11, 2, 5),
    ]
    view = obj.build_view(records, OBJ,
                          verification={"ok": True, "bad_eq": False,
                                        "failed": False})
    assert [c.label for c in view.candidates] == ["ok", "no_record"]
    assert {c.label: c.verification for c in view.candidates} == {
        "ok": "verified", "no_record": "unverified"}
    reasons = {e["label"]: e["reason"] for e in view.excluded}
    assert reasons == {"failed": "run status PASS_FAILED",
                       "partial": "incomplete metrics",
                       "bad_eq": "verification failed"}
    # The excluded result with the smallest numbers is not "best".
    assert view.best_labels("gates") == ["ok"]

    plain = obj.build_view(records[:1], OBJ)
    assert not plain.verification_available
    assert plain.candidates[0].verification == "unverified"
    assert "all candidates are unverified" in plain.format()


def test_identical_metrics_keep_separate_runs():
    view = obj.build_view([
        rec("A->B->C", 25, 238, 90, 78), rec("A->C->B", 26, 238, 90, 78),
        rec("B->A->C", 27, 233, 90, 80),
    ], OBJ)
    groups = view.identical_metric_groups()
    assert [[(c.label, c.run_id) for c in g] for g in groups] == [
        [("A->B->C", 25), ("A->C->B", 26)]]
    assert [(c.label, c.run_id) for c in view.best["depth"]] == [
        ("A->B->C", 25), ("A->C->B", 26)]
    assert len(view.pareto_front) == 3
    assert "Same metrics, separate runs: A->B->C (run 25), A->C->B (run 26)" in (
        view.format())


def test_invalid_inputs():
    with pytest.raises(obj.ObjectiveInputError, match="different inputs"):
        obj.build_view([rec("a", 1, 1, 1, 1, input_id="x"),
                        rec("b", 2, 1, 1, 1, input_id="y")], OBJ)
    with pytest.raises(obj.ObjectiveInputError, match="unique"):
        obj.build_view([rec("a", 1, 1, 1, 1), rec("a", 2, 1, 1, 1)], OBJ)
    with pytest.raises(obj.ObjectiveInputError, match="unknown labels"):
        obj.build_view([rec("a", 1, 1, 1, 1)], OBJ, verification={"z": True})
    with pytest.raises(obj.ObjectiveInputError, match="distinct"):
        obj.build_view([rec("a", 1, 1, 1, 1)], ("gates", "gates"))
    empty = obj.build_view([rec("f", 1, None, None, None, status="PASS_FAILED")],
                           OBJ)
    assert empty.candidates == [] and empty.pareto_front == []
    assert not empty.has_conflict()


# Integration with qch.batch --------------------------------------------------


class FailingPass(TransformationPass):
    def run(self, dag):
        raise RuntimeError("injected pass failure")


def test_batch_result_objective_view(tmp_path, monkeypatch):
    registry = dict(opt.PASS_REGISTRY)
    registry["FailingPass"] = opt._RegistryEntry(
        normalize=lambda params: dict(params),
        build=lambda params: FailingPass(),
    )
    monkeypatch.setattr(opt, "PASS_REGISTRY", registry)

    db = QCHDatabase(tmp_path / "obj.db")
    path = tmp_path / "in.qasm"
    path.write_text(order.CIRCUIT_QASM, encoding="utf-8")
    db.add_circuit(order.CIRCUIT_NAME, path)
    A, B = order.PASS_A, order.PASS_B
    result = batch.run_sequence_batch(db, order.CIRCUIT_NAME, [
        ("A->B", [A, B]), ("B->A", [B, A]), ("B->A again", [B, A]),
        ("broken", [A, {"name": "FailingPass"}]),
    ])
    db.close()

    view = result.objective_view()
    assert view.objectives == obj.QCH_OBJECTIVES
    assert [e["label"] for e in view.excluded] == ["broken"]
    # B->A (11 gates, depth 8) dominates A->B (13, 9); 2q is 2 for both.
    assert view.dominated_by["A->B"] == ["B->A", "B->A again"]
    assert [c.label for c in view.pareto_front] == ["B->A", "B->A again"]
    run_ids = {c.label: c.run_id for c in view.candidates}
    assert run_ids["B->A"] != run_ids["B->A again"]
    assert [[c.label for c in g] for g in view.identical_metric_groups()] == [
        ["B->A", "B->A again"]]
    assert view.common_optimum() and not view.has_conflict()
    assert not view.verification_available

    # Rebuilt read-only from run IDs: same view, verification applied.
    connection = opt.open_readonly(tmp_path / "obj.db")
    try:
        stored = batch.summarize_runs(
            connection, order.CIRCUIT_NAME,
            {r.label: r.run_id for r in result.results},
        )
    finally:
        connection.close()
    checked = stored.objective_view({"B->A": True, "B->A again": False})
    assert [c.label for c in checked.pareto_front] == ["B->A"]
    assert checked.dominated_by["A->B"] == ["B->A"]
    assert {e["label"]: e["reason"] for e in checked.excluded} == {
        "broken": "run status PASS_FAILED",
        "B->A again": "verification failed"}


# Experiment script ------------------------------------------------------------


def test_objective_view_script_on_synthetic_experiment(tmp_path, source_db):
    out_dir = tmp_path / "exp"
    assert helpers.call("select", "--source-db", str(source_db),
                        "--out-dir", str(out_dir)).returncode == 0
    run = helpers.call("run", "--out-dir", str(out_dir),
                       "--title", "Synthetic check")
    assert run.returncode == 0, run.stderr
    assert (out_dir / "report.md").read_text().startswith("# Synthetic check")
    results = json.loads((out_dir / "results.json").read_text())
    assert results["title"] == "Synthetic check"
    src = results["circuits"][0]["source_circuit_id"]

    before = {p.name: p.read_bytes() for p in out_dir.iterdir()}

    def call(*args):
        return subprocess.run([sys.executable, str(VIEW_SCRIPT), *args],
                              capture_output=True, text=True,
                              env=helpers.env(), check=False)

    shown = call("show", "--out-dir", str(out_dir), "--src", str(src))
    assert shown.returncode == 0, shown.stderr
    assert "Pareto front:" in shown.stdout and "Common optimum:" in shown.stdout
    data = json.loads(call("show", "--out-dir", str(out_dir), "--src",
                           str(src), "--json").stdout)
    assert data["verification_available"] is True
    assert len(data["candidates"]) == 6
    assert all(c["verification"] == "verified" for c in data["candidates"])
    assert len({c["run_id"] for c in data["candidates"]}) == 6
    qch_mode = call("show", "--out-dir", str(out_dir), "--src", str(src),
                    "--metrics", "qch")
    assert qch_mode.returncode == 0, qch_mode.stderr
    assert "gate_count (incl. measure)" in qch_mode.stdout
    assert call("show", "--out-dir", str(out_dir), "--src",
                "99999").returncode == 2

    # Reports are rebuilt with a given title; nothing is written.
    report = helpers.call("report", "--out-dir", str(out_dir),
                          "--title", "Stage 2D replay")
    assert report.stdout.startswith("# Stage 2D replay")
    assert {p.name: p.read_bytes() for p in out_dir.iterdir()} == before


def test_report_of_older_results_uses_generic_title(tmp_path, source_db):
    out_dir = tmp_path / "old"
    assert helpers.call("select", "--source-db", str(source_db),
                        "--out-dir", str(out_dir)).returncode == 0
    assert helpers.call("run", "--out-dir", str(out_dir)).returncode == 0
    results = json.loads((out_dir / "results.json").read_text())
    del results["title"]                     # as written before stage 2E
    (out_dir / "results.json").write_text(json.dumps(results))
    report = helpers.call("report", "--out-dir", str(out_dir))
    assert report.returncode == 0, report.stderr
    assert report.stdout.startswith("# MQT Bench pass-order experiment")
