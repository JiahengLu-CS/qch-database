"""Tests for experiments/fixed_order_check.py and the stage 2D selection
options of experiments/mqt_pass_order.py. Synthetic circuits only."""

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


fixed = load("fixed_order_check", ROOT / "experiments" / "fixed_order_check.py")
helpers = load("test_mqt_pass_order_helpers",
               ROOT / "tests" / "test_mqt_pass_order.py")
source_db = helpers.source_db  # pytest fixture

FIXED_SCRIPT = ROOT / "experiments" / "fixed_order_check.py"


def run_fixed(*args):
    return subprocess.run(
        [sys.executable, str(FIXED_SCRIPT), *args],
        capture_output=True, text=True, env=helpers.env(), check=False,
    )


def sequence(label, gates, two, depth, valid=True):
    return {
        "label": label, "run_id": hash(label) % 1000, "status": "SUCCEEDED",
        "valid": valid,
        "final_metrics": {"gates": gates, "two_qubit_gates": two,
                          "depth": depth},
    }


def circuit(sequences):
    return {
        "source_circuit_id": 7, "name": "src:x_indep_qiskit_3.qasm",
        "family": "x", "baseline_name": "s1:baseline",
        "baseline_metrics": {"gates": 20, "two_qubit_gates": 6, "depth": 12},
        "sequences": sequences, "order_dependent": True,
    }


def test_compare_counts_ties_and_excess():
    entry = fixed.compare_circuit(circuit([
        sequence("B->A->C", 10, 4, 8),
        sequence("A->B->C", 9, 4, 9),
        sequence("C->A->B", 12, 5, 7, valid=False),
    ]), "indep", "B->A->C")
    m = entry["metrics"]
    assert m["two_qubit_gates"]["reached"] and m["two_qubit_gates"]["best_orders"] == [
        "A->B->C", "B->A->C"]
    assert m["depth"]["reached"]          # the invalid depth 7 is ignored
    assert not m["gates"]["reached"] and m["gates"]["excess"] == 1
    assert m["gates"]["fixed_delta_vs_baseline"] == -10
    assert m["gates"]["best_delta_vs_baseline"] == -11
    assert entry["counterexample"]

    summary = fixed.summarize([entry])
    assert summary["counterexamples"] == [7]
    assert summary["metrics"]["gates"] == {
        "reached": 0, "missed": 1, "excess_total": 1, "excess_max": 1,
        "fixed_delta_total": -10, "best_delta_total": -11,
        "baseline_total": 20, "fixed_improves": 1,
    }


def test_invalid_fixed_order_is_excluded():
    entry = fixed.compare_circuit(circuit([
        sequence("B->A->C", 1, 0, 1, valid=False),
        sequence("A->B->C", 9, 4, 9),
    ]), "mapped", "B->A->C")
    assert "metrics" not in entry
    assert "fixed order" in entry["excluded_reason"]
    summary = fixed.summarize([entry])
    assert summary["counted"] == 0 and summary["excluded"] == 1


def test_unknown_order_is_rejected():
    with pytest.raises(ValueError):
        fixed.compare_circuit(circuit([sequence("A->B->C", 9, 4, 9)]),
                              "indep", "B->A->C")


def test_exclusion_and_fixed_order_flow(tmp_path, source_db):
    before = source_db.read_bytes()
    first = tmp_path / "first"
    result = helpers.call("select", "--source-db", str(source_db),
                          "--out-dir", str(first), "--count", "3")
    assert result.returncode == 0, result.stderr
    first_ids = {i["source_circuit_id"] for i in json.loads(
        (first / "selection.json").read_text())["accepted"]}

    second = tmp_path / "second"
    result = helpers.call(
        "select", "--source-db", str(source_db), "--out-dir", str(second),
        "--count", "12", "--allow-fewer",
        "--exclude-selection", str(first / "selection.json"),
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert source_db.read_bytes() == before
    selection = json.loads((second / "selection.json").read_text())
    ids = {i["source_circuit_id"] for i in selection["accepted"]}
    assert ids and not ids & first_ids
    excluded = {e["source_circuit_id"]: e["reason"]
                for e in selection["excluded"]}
    assert set(excluded) == first_ids
    assert all("previous selection" in r for r in excluded.values())
    # The pool is smaller than 12: fewer circuits are accepted.
    assert len(ids) < 12
    assert selection["selection_rule"]["allow_fewer"] is True

    assert helpers.call("run", "--out-dir", str(second)).returncode == 0
    db_bytes = (second / "experiment.db").read_bytes()
    analysis = run_fixed("analyze", "--out-dir", str(second))
    assert analysis.returncode == 0, analysis.stderr
    assert "Counterexamples" in analysis.stdout
    assert (second / "experiment.db").read_bytes() == db_bytes
    data = json.loads((second / "fixed_order.json").read_text())
    assert data["fixed_order"] == "B->A->C"
    assert data["overall"]["counted"] == len(ids)
    for entry in data["circuits"]:
        for key in fixed.METRICS:
            m = entry["metrics"][key]
            assert m["excess"] >= 0
            assert m["reached"] == (m["excess"] == 0)


def test_select_without_allow_fewer_still_requires_count(tmp_path, source_db):
    result = helpers.call("select", "--source-db", str(source_db),
                          "--out-dir", str(tmp_path / "out"), "--count", "12")
    assert result.returncode == 3
    assert not (tmp_path / "out").exists()
