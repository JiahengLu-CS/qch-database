"""Acceptance check: the same circuit optimized in two pass orders.

Runs A -> B and B -> A on one small 3-qubit circuit without measurements,
stores every step with qch.optimization, and verifies the records from a
separate process through a read-only connection.

  A = CommutativeCancellation(basis_gates=["rz", "sx", "x", "cx"])
  B = Optimize1qGatesDecomposition(basis=["rz", "sx", "x", "cx"])

Usage (PowerShell, from the repository root):

  $Py = ".\\.venv\\Scripts\\python.exe"
  $Db = "$env:TEMP\\qch_pass_order_$(Get-Date -Format yyyyMMddHHmmss).db"
  & $Py examples\\pass_order_acceptance.py run --db $Db
  & $Py examples\\pass_order_acceptance.py verify --db $Db

``run`` refuses to overwrite an existing file. ``verify`` opens the
database read-only, restores every stored step circuit from QPY,
recomputes its metrics and compares them with the stored values, and
checks that both final circuits have the same unitary as the input (up to
global phase). It exits with status 1 if any check fails.
"""

from __future__ import annotations

import argparse
import sys
import tempfile
from pathlib import Path

from qiskit import QuantumCircuit
from qiskit.quantum_info import Operator

from qch import QCHDatabase
from qch import optimization as opt

CIRCUIT_NAME = "pass_order_demo_3q"

CIRCUIT_QASM = """OPENQASM 2.0;
include "qelib1.inc";
qreg q[3];
h q[0];
cx q[1],q[0];
h q[0];
h q[1];
cx q[1],q[0];
sdg q[1];
t q[2];
cx q[2],q[0];
z q[2];
cx q[2],q[0];
"""

BASIS = ["rz", "sx", "x", "cx"]

PASS_A = {"name": "CommutativeCancellation", "params": {"basis_gates": BASIS}}
PASS_B = {"name": "Optimize1qGatesDecomposition", "params": {"basis": BASIS}}

SEQUENCES = {
    "A->B": [PASS_A, PASS_B],
    "B->A": [PASS_B, PASS_A],
}

METRIC_COLUMNS = [
    "num_qubits", "num_clbits", "gate_count", "depth",
    "single_qubit_gate_count", "two_qubit_gate_count",
    "multi_qubit_gate_count", "measure_count", "reset_count",
    "barrier_count", "opaque_gate_count",
]


def _ms(value):
    return "-" if value is None else f"{value * 1000:.3f}"


def print_steps(label, run):
    print(f"Run {run['run_id']} ({label}): status={run['status']}, "
          f"qiskit {run['compiler_version']}, Python {run['python_version']}, "
          f"{run['platform']}")
    print(f"  {'step':>4}  {'pass':<29} {'chg':>3} {'snap':>4} {'gates':>5} "
          f"{'2q':>3} {'depth':>5} {'pass_ms':>8}")
    for step in run["steps"]:
        changed = "-" if step["changed"] is None else step["changed"]
        print(f"  {step['step_index']:>4}  "
              f"{(step['pass_name'] or '(input)'):<29} {changed:>3} "
              f"{step['snapshot_id']:>4} {step['gate_count']:>5} "
              f"{step['two_qubit_gate_count']:>3} {step['depth']:>5} "
              f"{_ms(step['pass_time_s']):>8}")


def cmd_run(args):
    db_path = Path(args.db)
    if db_path.exists():
        print(f"Error: database already exists: {db_path}", file=sys.stderr)
        return 2
    db_path.parent.mkdir(parents=True, exist_ok=True)

    db = QCHDatabase(db_path)
    try:
        with tempfile.TemporaryDirectory() as tmp:
            qasm_path = Path(tmp) / "input.qasm"
            qasm_path.write_text(CIRCUIT_QASM, encoding="utf-8")
            db.add_circuit(CIRCUIT_NAME, qasm_path)
        for label, sequence in SEQUENCES.items():
            result = opt.run_pass_sequence(
                db, CIRCUIT_NAME, sequence, label=label
            )
            print(f"Run {result.run_id} ({label}) finished with status "
                  f"{result.status}")
    finally:
        db.close()
    print(f"Database written: {db_path}")
    return 0


def cmd_verify(args):
    connection = opt.open_readonly(args.db)
    failures = []
    try:
        runs = {
            run["label"]: run
            for run in opt.list_optimization_runs(connection, CIRCUIT_NAME)
        }
        missing = [label for label in SEQUENCES if label not in runs]
        if missing:
            print(f"Error: runs not found: {missing}", file=sys.stderr)
            return 1

        reference = Operator(QuantumCircuit.from_qasm_str(CIRCUIT_QASM))
        finals = {}
        for label, sequence in SEQUENCES.items():
            run = opt.get_optimization_run(connection, runs[label]["run_id"])
            print_steps(label, run)

            expected_names = [None] + [item["name"] for item in sequence]
            if [s["pass_name"] for s in run["steps"]] != expected_names:
                failures.append(f"{label}: unexpected step sequence")
            if run["status"] != "SUCCEEDED":
                failures.append(f"{label}: run status {run['status']}")

            for step in run["steps"]:
                circuit = opt.load_step_circuit(
                    connection, run["run_id"], step["step_index"]
                )
                recomputed = opt.compute_step_metrics(circuit)
                for column in METRIC_COLUMNS:
                    if recomputed[column] != step[column]:
                        failures.append(
                            f"{label} step {step['step_index']}: {column} "
                            f"stored {step[column]}, restored "
                            f"{recomputed[column]}"
                        )
                if step["step_index"] > 0 and step["pass_time_s"] is None:
                    failures.append(
                        f"{label} step {step['step_index']}: no pass time"
                    )

            final = opt.load_step_circuit(
                connection, run["run_id"], len(sequence)
            )
            finals[label] = final
            equivalent = Operator(final).equiv(reference)
            print(f"  final circuit equals the input unitary "
                  f"(up to global phase): {equivalent}")
            if not equivalent:
                failures.append(f"{label}: final circuit is not equivalent")
            print()

        same = (
            opt.representation_fingerprint(finals["A->B"])
            == opt.representation_fingerprint(finals["B->A"])
        )
        print("Final circuits of A->B and B->A are identical:", same)
    finally:
        connection.close()

    if failures:
        print("FAILED checks:")
        for failure in failures:
            print("  -", failure)
        return 1
    print("All checks passed: restored metrics match stored metrics, every "
          "pass step has a pass time, both final circuits equal the input.")
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Run and verify A->B and B->A on one small circuit."
    )
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run", help="Create a NEW database and run both orders.")
    run.add_argument("--db", required=True)
    run.set_defaults(func=cmd_run)
    verify = sub.add_parser(
        "verify", help="Verify the stored runs through a read-only connection."
    )
    verify.add_argument("--db", required=True)
    verify.set_defaults(func=cmd_verify)
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
