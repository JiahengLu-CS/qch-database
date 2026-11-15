"""Tests for experiments/mqt_pass_order.py with SYNTHETIC MQT-style circuits.

The circuits below only imitate the MQT Bench naming and layout (final
barrier plus measurements, several registers). They are not MQT Bench
data and are never used as experiment results.
"""

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from qiskit import QuantumCircuit

import qch
from qch import QCHDatabase
from qch import optimization as opt

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "experiments" / "mqt_pass_order.py"


def load_script():
    spec = importlib.util.spec_from_file_location("mqt_pass_order", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


exp = load_script()

HEADER = 'OPENQASM 2.0;\ninclude "qelib1.inc";\n'

SYNTHETIC = {
    "ghz_indep_qiskit_3.qasm": HEADER + """qreg q[3];
creg meas[3];
h q[0];
cx q[0],q[1];
cx q[1],q[2];
barrier q[0],q[1],q[2];
measure q[0] -> meas[0];
measure q[1] -> meas[1];
measure q[2] -> meas[2];
""",
    "ghz_indep_qiskit_4.qasm": HEADER + """qreg q[4];
creg meas[4];
h q[3];
cx q[3],q[2];
cx q[2],q[1];
cx q[1],q[0];
barrier q[0],q[1],q[2],q[3];
measure q[0] -> meas[0];
measure q[1] -> meas[1];
measure q[2] -> meas[2];
measure q[3] -> meas[3];
""",
    "qft_indep_qiskit_3.qasm": HEADER + """qreg q[3];
creg c[3];
h q[2];
cp(pi/2) q[2],q[1];
h q[1];
cp(pi/4) q[2],q[0];
cp(pi/2) q[1],q[0];
h q[0];
swap q[0],q[2];
barrier q[0],q[1],q[2];
measure q[0] -> c[0];
measure q[1] -> c[1];
measure q[2] -> c[2];
""",
    "qft_indep_qiskit_2.qasm": HEADER + """qreg q[2];
creg c[2];
h q[1];
cp(pi/2) q[1],q[0];
h q[0];
swap q[0],q[1];
barrier q[0],q[1];
measure q[0] -> c[0];
measure q[1] -> c[1];
""",
    # Measures only a subset of qubits, with a separate flag register.
    "dj_indep_qiskit_3.qasm": HEADER + """qreg q[2];
qreg flag[1];
creg c[2];
u2(0,0) q[0];
u2(0,0) q[1];
h flag[0];
z flag[0];
cx q[0],flag[0];
cx q[1],flag[0];
u2(0,pi) q[0];
u2(0,pi) q[1];
barrier q[0],q[1],flag[0];
measure q[0] -> c[0];
measure q[1] -> c[1];
""",
    "dj_indep_qiskit_4.qasm": HEADER + """qreg q[3];
qreg flag[1];
creg c[3];
h q[0];
h q[1];
h q[2];
x flag[0];
h flag[0];
cx q[0],flag[0];
cx q[2],flag[0];
h q[0];
h q[1];
h q[2];
barrier q[0],q[1],q[2],flag[0];
measure q[0] -> c[0];
measure q[1] -> c[1];
measure q[2] -> c[2];
""",
    # Rejected: an operation follows a measurement.
    "aaa_indep_qiskit_2.qasm": HEADER + """qreg q[2];
creg c[2];
h q[0];
measure q[0] -> c[0];
x q[0];
cx q[0],q[1];
measure q[1] -> c[1];
""",
    # Rejected: reset.
    "bbb_indep_qiskit_2.qasm": HEADER + """qreg q[2];
creg c[2];
reset q[0];
h q[0];
cx q[0],q[1];
measure q[0] -> c[0];
measure q[1] -> c[1];
""",
    # Not the preferred layer; only used if indep is exhausted.
    "ghz_nativegates_ibm_qiskit_opt0_3.qasm": HEADER + """qreg q[3];
creg meas[3];
rz(pi/2) q[0];
sx q[0];
rz(pi/2) q[0];
cx q[0],q[1];
cx q[1],q[2];
measure q[0] -> meas[0];
measure q[1] -> meas[1];
measure q[2] -> meas[2];
""",
    # Out of the qubit range.
    "ghz_indep_qiskit_7.qasm": HEADER + """qreg q[7];
h q[0];
cx q[0],q[1];
cx q[1],q[2];
cx q[2],q[3];
cx q[3],q[4];
cx q[4],q[5];
cx q[5],q[6];
""",
}


@pytest.fixture
def source_db(tmp_path):
    path = tmp_path / "source.db"
    db = QCHDatabase(path)
    folder = tmp_path / "qasm"
    folder.mkdir()
    for name, text in SYNTHETIC.items():
        qasm_path = folder / name
        qasm_path.write_text(text, encoding="utf-8")
        db.add_circuit(name, qasm_path)
    db.close()
    return path


def env():
    variables = dict(os.environ)
    src = str(Path(qch.__file__).resolve().parents[1])
    variables["PYTHONPATH"] = src + os.pathsep + variables.get("PYTHONPATH", "")
    return variables


def call(*args):
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        capture_output=True, text=True, env=env(), check=False,
    )


def test_parse_name():
    assert exp.parse_name("ghz_indep_qiskit_3.qasm") == ("ghz", "indep")
    assert exp.parse_name("grover-noancilla_indep_qiskit_4.qasm") == (
        "grover-noancilla", "indep"
    )
    assert exp.parse_name("MQTBench:qft_nativegates_ibm_3.qasm") == (
        "qft", "nativegates"
    )
    assert exp.parse_name("random_circuit") == (None, None)


def test_measurement_map_rules():
    good = QuantumCircuit.from_qasm_str(SYNTHETIC["dj_indep_qiskit_3.qasm"])
    assert exp.final_measurement_map(good) == {0: 0, 1: 1}
    with pytest.raises(exp.UnverifiableCircuit, match="after the measurement"):
        exp.final_measurement_map(
            QuantumCircuit.from_qasm_str(SYNTHETIC["aaa_indep_qiskit_2.qasm"])
        )
    with pytest.raises(exp.UnverifiableCircuit, match="reset"):
        exp.final_measurement_map(
            QuantumCircuit.from_qasm_str(SYNTHETIC["bbb_indep_qiskit_2.qasm"])
        )


def test_metrics_exclude_measure_and_barrier():
    circuit = QuantumCircuit.from_qasm_str(SYNTHETIC["ghz_indep_qiskit_3.qasm"])
    metrics = exp.experiment_metrics(circuit)
    assert metrics["gates"] == 3
    assert metrics["two_qubit_gates"] == 2
    assert metrics["depth"] == 3
    assert metrics["measure_count"] == 3
    assert metrics["barrier_count"] == 1
    # QCH's own gate_count includes the measurements.
    assert metrics["qch_gate_count"] == 6


def test_compare_detects_problems():
    reference = QuantumCircuit.from_qasm_str(SYNTHETIC["ghz_indep_qiskit_3.qasm"])
    _, baseline = exp.prepare_baseline(reference)
    assert exp.compare_to_reference(reference, baseline)["verified"]

    wrong_unitary = QuantumCircuit(*baseline.qregs, *baseline.cregs)
    wrong_unitary.x(0)
    wrong_unitary.measure([0, 1, 2], [0, 1, 2])
    checks = exp.compare_to_reference(reference, wrong_unitary)
    assert not checks["verified"]
    assert "unitary not equivalent" in checks["reason"]

    swapped = QuantumCircuit(*baseline.qregs, *baseline.cregs)
    for instruction in baseline.data:
        if instruction.operation.name != "measure":
            swapped.append(instruction)
    swapped.measure([0, 1, 2], [1, 0, 2])
    checks = exp.compare_to_reference(reference, swapped)
    assert not checks["measurement_map_equal"]
    assert "measurement mapping differs" in checks["reason"]

    extra = reference.copy()
    checks = exp.compare_to_reference(reference, extra)
    assert checks["gate_set_violations"] == ["h"]
    assert not checks["verified"]


def test_selection_order_round_robin():
    candidates = [
        {"family": f, "layer": "indep", "num_qubits": n,
         "source_gate_count": g, "source_circuit_id": i}
        for i, (f, n, g) in enumerate(
            [("qft", 3, 9), ("ghz", 4, 5), ("ghz", 3, 4), ("dj", 3, 8),
             ("qft", 2, 5)], start=1)
    ] + [{"family": "ghz", "layer": "nativegates", "num_qubits": 2,
          "source_gate_count": 1, "source_circuit_id": 99}]
    order = exp.selection_order(candidates, ["indep", "nativegates"])
    assert [(c["family"], c["num_qubits"]) for c in order] == [
        ("dj", 3), ("ghz", 3), ("qft", 2), ("ghz", 4), ("qft", 3), ("ghz", 2),
    ]


def test_full_experiment_on_synthetic_source(tmp_path, source_db):
    before = source_db.read_bytes()
    out_dir = tmp_path / "exp"

    select = call("select", "--source-db", str(source_db),
                  "--out-dir", str(out_dir))
    assert select.returncode == 0, select.stdout + select.stderr
    assert source_db.read_bytes() == before

    selection = json.loads((out_dir / "selection.json").read_text())
    assert selection["source_database"]["unchanged"] is True
    accepted = selection["accepted"]
    assert len(accepted) == 6
    assert {item["family"] for item in accepted} == {"dj", "ghz", "qft"}
    assert all(item["layer"] == "indep" for item in accepted)
    assert all(2 <= item["num_qubits"] <= 6 for item in accepted)
    rejected = {item["name"]: item["reason"] for item in selection["rejected"]}
    assert "after the measurement" in rejected["aaa_indep_qiskit_2.qasm"]
    assert "reset" in rejected["bbb_indep_qiskit_2.qasm"]
    for item in accepted:
        assert set(item["baseline_metrics"]["ops"]) <= {
            "rz", "sx", "x", "cx", "measure", "barrier"
        }
        assert item["baseline_checks"]["verified"]
        assert len(item["source_qasm_sha256"]) == 64
    assert selection["baseline_preparation"]["optimization_level"] == 0

    # The experiment database holds the copies; the source is untouched.
    connection = opt.open_readonly(out_dir / "experiment.db")
    try:
        names = [r[0] for r in connection.execute("SELECT name FROM circuits")]
    finally:
        connection.close()
    assert len(names) == 12

    run = call("run", "--out-dir", str(out_dir))
    assert run.returncode == 0, run.stdout + run.stderr
    results = json.loads((out_dir / "results.json").read_text())
    assert results["total_runs"] == 36
    assert len(results["orders"]) == 6
    run_ids = set()
    for circuit in results["circuits"]:
        assert len(circuit["sequences"]) == 6
        for sequence in circuit["sequences"]:
            run_ids.add(sequence["run_id"])
            assert sequence["status"] == "SUCCEEDED"
            assert sequence["valid"], sequence
            assert sequence["step0_matches_baseline"]
            assert sequence["checks"]["unitary_equivalent"]
            assert sequence["checks"]["measurement_map_equal"]
            assert sequence["checks"]["gate_set_violations"] == []
        for metric in ("gates", "two_qubit_gates", "depth"):
            assert circuit["best"][metric]["labels"]
    assert len(run_ids) == 36
    assert (out_dir / "report.md").read_text().startswith(
        "# MQT Bench pass-order experiment")

    before_exp = (out_dir / "experiment.db").read_bytes()
    report = call("report", "--out-dir", str(out_dir))
    assert report.returncode == 0, report.stderr
    assert "Results per circuit" in report.stdout
    assert "## Summary" in report.stdout
    assert (out_dir / "experiment.db").read_bytes() == before_exp

    again = call("run", "--out-dir", str(out_dir))
    assert again.returncode == 2


def test_select_refuses_when_not_enough_families(tmp_path):
    path = tmp_path / "small.db"
    db = QCHDatabase(path)
    qasm = tmp_path / "ghz_indep_qiskit_3.qasm"
    qasm.write_text(SYNTHETIC["ghz_indep_qiskit_3.qasm"], encoding="utf-8")
    db.add_circuit("ghz_indep_qiskit_3.qasm", qasm)
    db.close()
    result = call("select", "--source-db", str(path),
                  "--out-dir", str(tmp_path / "out"))
    assert result.returncode == 3
    assert not (tmp_path / "out").exists()
