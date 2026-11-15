"""Tests for experiments/export_step_metrics.py (synthetic circuits only)."""

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "experiments" / "export_step_metrics.py"


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


helpers = _load("test_mqt_pass_order_helpers_3a",
                ROOT / "tests" / "test_mqt_pass_order.py")
source_db = helpers.source_db  # pytest fixture


def export(*args):
    return subprocess.run([sys.executable, str(SCRIPT), *args],
                          capture_output=True, text=True, env=helpers.env(),
                          check=False)


def test_export_of_saved_steps(tmp_path, source_db):
    out_dir = tmp_path / "exp"
    assert helpers.call("select", "--source-db", str(source_db),
                        "--out-dir", str(out_dir)).returncode == 0
    assert helpers.call("run", "--out-dir", str(out_dir)).returncode == 0
    results = json.loads((out_dir / "results.json").read_text())
    circuit = results["circuits"][0]
    src = circuit["source_circuit_id"]
    run_ids = [s["run_id"] for s in circuit["sequences"]]
    before = {p.name: p.read_bytes() for p in out_dir.iterdir()}

    target = tmp_path / "public" / "steps.json"
    done = export("--out-dir", str(out_dir), "--src", str(src), "--runs",
                  f"{run_ids[0]}-{run_ids[-1]}", "--output", str(target))
    assert done.returncode == 0, done.stderr
    assert {p.name: p.read_bytes() for p in out_dir.iterdir()} == before
    assert "unchanged: True" in done.stdout

    data = json.loads(target.read_text())
    assert data["schema_version"] == 1 and data["source_id"] == src
    assert data["metric_profile"] == "experiment"
    assert len(data["input_sha256"]) == 64
    assert str(tmp_path) not in target.read_text()
    baseline = [circuit["baseline_metrics"][m]
                for m in ("gates", "two_qubit_gates", "depth")]
    for sequence in circuit["sequences"]:
        steps = data["runs"][str(sequence["run_id"])]
        assert [s["step"] for s in steps] == [0, 1, 2, 3]
        assert steps[0]["pass"] == "Input"
        assert [s["pass"] for s in steps[1:]] == sequence["pass_names"]
        assert [steps[0][m] for m in ("gates", "two_qubit_gates", "depth")] == baseline
        assert {m: steps[-1][m] for m in ("gates", "two_qubit_gates", "depth")} == {
            m: sequence["final_metrics"][m]
            for m in ("gates", "two_qubit_gates", "depth")}

    # Refuses to overwrite, unknown circuits and runs of another circuit.
    assert export("--out-dir", str(out_dir), "--src", str(src), "--runs",
                  str(run_ids[0]), "--output", str(target)).returncode == 2
    assert export("--out-dir", str(out_dir), "--src", "99999", "--runs", "1",
                  "--output", str(tmp_path / "x.json")).returncode == 2
    other = results["circuits"][1]["sequences"][0]["run_id"]
    bad = export("--out-dir", str(out_dir), "--src", str(src), "--runs",
                 str(other), "--output", str(tmp_path / "y.json"))
    assert bad.returncode == 2 and "do not belong" in bad.stderr
    assert not (tmp_path / "y.json").exists()
