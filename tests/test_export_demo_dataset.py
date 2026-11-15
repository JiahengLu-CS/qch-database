"""Tests for experiments/export_demo_dataset.py (synthetic circuits only)."""

import importlib.util
import itertools
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "experiments" / "export_demo_dataset.py"


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


helpers = _load("test_mqt_pass_order_helpers_3b",
                ROOT / "tests" / "test_mqt_pass_order.py")
source_db = helpers.source_db  # pytest fixture


def export(*args):
    return subprocess.run([sys.executable, str(SCRIPT), *args],
                          capture_output=True, text=True, env=helpers.env(),
                          check=False)


def test_dataset_matches_the_experiment(tmp_path, source_db):
    out_dir = tmp_path / "exp"
    assert helpers.call("select", "--source-db", str(source_db), "--out-dir",
                        str(out_dir), "--count", "3").returncode == 0
    assert helpers.call("run", "--out-dir", str(out_dir)).returncode == 0
    before = {p.name: p.read_bytes() for p in out_dir.iterdir()}
    target = tmp_path / "public" / "dataset.json"
    done = export("--out-dir", str(out_dir), "--output", str(target))
    assert done.returncode == 0, done.stderr
    assert {p.name: p.read_bytes() for p in out_dir.iterdir()} == before
    assert str(tmp_path) not in target.read_text()

    data = json.loads(target.read_text())
    results = json.loads((out_dir / "results.json").read_text())
    assert data["schema_version"] == 2 and data["metric_profile"] == "experiment"
    assert list(data["passes"]) == ["A", "B", "C"]
    assert data["environment"] == results["environment"]
    orders = {"->".join(p) for p in itertools.permutations("ABC")}
    for circuit, recorded in zip(data["circuits"], results["circuits"]):
        assert circuit["source_id"] == recorded["source_circuit_id"]
        baseline = [recorded["baseline_metrics"][m]
                    for m in ("gates", "two_qubit_gates", "depth")]
        assert circuit["baseline"] == baseline
        assert {r["order"] for r in circuit["runs"]} == orders
        for run, sequence in zip(circuit["runs"], recorded["sequences"]):
            assert run["run_id"] == sequence["run_id"]
            assert run["verified"] is True and run["status"] == "SUCCEEDED"
            assert len(run["steps"]) == 4 and run["steps"][0] == baseline
            assert run["steps"][-1] == run["final"] == [
                sequence["final_metrics"][m]
                for m in ("gates", "two_qubit_gates", "depth")]

    assert export("--out-dir", str(out_dir), "--output",
                  str(target)).returncode == 2
    assert export("--out-dir", str(tmp_path / "missing"), "--output",
                  str(tmp_path / "x.json")).returncode == 2
    assert not (tmp_path / "x.json").exists()
