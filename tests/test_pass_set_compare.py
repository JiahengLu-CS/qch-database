"""Tests for stage 2F: ``reuse``, ``run --pass-set abcef`` and
experiments/pass_set_compare.py. Synthetic MQT-style circuits only."""

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


helpers = _load("test_mqt_pass_order_helpers_2f",
                ROOT / "tests" / "test_mqt_pass_order.py")
source_db = helpers.source_db  # pytest fixture
compare = _load("pass_set_compare", ROOT / "experiments" / "pass_set_compare.py")
experiment = _load("mqt_pass_order_2f", ROOT / "experiments" / "mqt_pass_order.py")
COMPARE = ROOT / "experiments" / "pass_set_compare.py"


def run_compare(*args):
    return subprocess.run([sys.executable, str(COMPARE), *args],
                          capture_output=True, text=True, env=helpers.env(),
                          check=False)


def snapshot(directory):
    return {p.name: p.read_bytes() for p in Path(directory).iterdir()}


# Unit tests -------------------------------------------------------------------


def circuit(src, rows):
    return {
        "source_circuit_id": src, "name": f"x:c{src}", "family": "x",
        "baseline_name": f"s:{src}",
        "baseline_metrics": {"gates": 50, "two_qubit_gates": 5, "depth": 20},
        "distinct_valid_outcomes": len({r[1:] for r in rows}),
        "order_dependent": len({r[1:] for r in rows}) > 1,
        "sequences": [
            {"label": label, "run_id": i, "status": "SUCCEEDED", "valid": True,
             "final_metrics": {"gates": g, "two_qubit_gates": t, "depth": d}}
            for i, (label, g, t, d) in enumerate(rows)
        ],
    }


def test_order_classes_and_position_insensitive_passes():
    labels = ["A->E->B", "E->A->B", "A->B->E", "B->A->E", "B->E->A", "E->B->A"]
    # Only the relative order of A and B matters; E can move freely.
    circuits = [
        circuit(1, [(l, 10 if l.index("A") < l.index("B") else 12, 2, 5)
                    for l in labels]),
        circuit(2, [(l, 7, 1, 4 if l.index("A") < l.index("B") else 3)
                    for l in labels]),
    ]
    classes, vectors = compare.order_classes(circuits, labels)
    assert sorted(map(sorted, classes)) == [
        ["A->B->E", "A->E->B", "E->A->B"], ["B->A->E", "B->E->A", "E->B->A"]]
    assert compare.position_insensitive(vectors, ["A", "B", "E"]) == ["E"]


def test_rank_orders_counts_counterexamples():
    labels = ["A->B", "B->A"]
    results = {"circuits": [
        circuit(1, [("A->B", 10, 2, 5), ("B->A", 12, 2, 5)]),
        circuit(2, [("A->B", 7, 1, 4), ("B->A", 7, 1, 3)]),
        circuit(3, [("A->B", 9, 1, 4), ("B->A", 9, 1, 4)]),
    ]}
    ranking = {r["order"]: r for r in compare.rank_orders(results, labels)}
    assert ranking["A->B"]["reached_all_three"] == 2
    assert ranking["A->B"]["counterexamples"] == [2]
    assert ranking["B->A"]["counterexamples"] == [1]
    assert ranking["B->A"]["excess_total"]["gates"] == 2


def test_pass_sets():
    assert list(experiment.PASS_SETS["abc"]) == ["A", "B", "C"]
    assert list(experiment.PASS_SETS["abcef"]) == ["A", "B", "C", "E", "F"]
    assert experiment.PASS_SETS["abcef"]["F"]["name"] == (
        "Optimize1qGatesSimpleCommutation")
    assert experiment.PASS_SETS["abc"] is experiment.PASS_ITEMS
    assert list(experiment.PASS_SETS["abcfg"]) == ["A", "B", "C", "F", "G"]
    assert experiment.PASS_SETS["abcfg"]["G"] == {
        "name": "TwoQubitBlockResynthesis",
        "params": {"basis_gates": ["rz", "sx", "x", "cx"]}}


# Integration ------------------------------------------------------------------


@pytest.fixture
def three_circuit_experiment(tmp_path, source_db):
    out = tmp_path / "before"
    result = helpers.call("select", "--source-db", str(source_db),
                          "--out-dir", str(out), "--count", "3")
    assert result.returncode == 0, result.stderr
    assert helpers.call("run", "--out-dir", str(out)).returncode == 0
    return out


def test_reuse_copies_circuits_read_only(tmp_path, three_circuit_experiment):
    before = snapshot(three_circuit_experiment)
    target = tmp_path / "after"
    result = helpers.call("reuse", "--from-dir", str(three_circuit_experiment),
                          "--out-dir", str(target))
    assert result.returncode == 0, result.stderr
    assert snapshot(three_circuit_experiment) == before
    old = json.loads((three_circuit_experiment / "selection.json").read_text())
    new = json.loads((target / "selection.json").read_text())
    assert new["reused_from"]["experiment"] == "before"
    assert new["reused_from"]["unchanged"] is True
    assert [i["baseline_qasm_sha256"] for i in new["accepted"]] == [
        i["baseline_qasm_sha256"] for i in old["accepted"]]
    assert not (target / "results.json").exists()

    again = helpers.call("reuse", "--from-dir", str(three_circuit_experiment),
                         "--out-dir", str(target))
    assert again.returncode == 2


def test_reuse_refuses_tampered_selection(tmp_path, three_circuit_experiment):
    copy = tmp_path / "copy"
    copy.mkdir()
    for name in ("experiment.db", "selection.json"):
        (copy / name).write_bytes((three_circuit_experiment / name).read_bytes())
    selection = json.loads((copy / "selection.json").read_text())
    selection["accepted"][0]["baseline_qasm_sha256"] = "0" * 64
    (copy / "selection.json").write_text(json.dumps(selection))
    result = helpers.call("reuse", "--from-dir", str(copy),
                          "--out-dir", str(tmp_path / "never"))
    assert result.returncode == 3
    assert "hash differs" in result.stderr
    assert not (tmp_path / "never").exists()


def test_five_pass_run_and_comparison(tmp_path, three_circuit_experiment):
    after = tmp_path / "after"
    assert helpers.call("reuse", "--from-dir", str(three_circuit_experiment),
                        "--out-dir", str(after)).returncode == 0
    run = helpers.call("run", "--out-dir", str(after), "--pass-set", "abcef",
                       "--title", "Stage 2F synthetic")
    assert run.returncode == 0, run.stderr
    results = json.loads((after / "results.json").read_text())
    assert results["pass_set"] == "abcef"
    assert len(results["orders"]) == 120
    assert results["total_runs"] == 360
    assert all(s["valid"] for c in results["circuits"] for s in c["sequences"])

    before_bytes = snapshot(three_circuit_experiment)
    after_db = (after / "experiment.db").read_bytes()
    shown = run_compare("--before", str(three_circuit_experiment),
                        "--after", str(after))
    assert shown.returncode == 0, shown.stderr
    assert snapshot(three_circuit_experiment) == before_bytes
    assert (after / "experiment.db").read_bytes() == after_db
    data = json.loads((after / "pass_set_compare.json").read_text())
    assert data["before"]["orders"] == 6 and data["after"]["orders"] == 120
    assert data["valid_runs_after"] == 360
    assert sum(len(c) for c in data["order_classes"]) == 120
    assert len(data["ranking"]) == 120
    for c in data["circuits"]:
        assert set(c["gain"]) == {"gates", "two_qubit_gates", "depth"}
        for metric in c["gain"]:
            assert c["gain"][metric] == (
                c["best_after"][metric] - c["best_before"][metric])
    assert "## Fixed orders by class" in shown.stdout

    # Different circuits cannot be compared.
    other = tmp_path / "other"
    assert helpers.call("select", "--source-db", str(three_circuit_experiment.parent / "source.db"),
                        "--out-dir", str(other), "--count", "4").returncode == 0
    assert helpers.call("run", "--out-dir", str(other)).returncode == 0
    refused = run_compare("--before", str(other), "--after", str(after),
                          "--no-write")
    assert refused.returncode == 2
    assert "same baseline" in refused.stderr
