"""Command-line demo for recorded optimization pass sequences.

Subcommands:

  prepare  Create a NEW demo database containing one circuit, either
           copied from an existing QCH database (opened read-only) or
           loaded from an OpenQASM 2 file.
  run      Run an ordered pass sequence (JSON file) on a stored circuit.
           Writes to the database.
  list     List recorded runs (read-only connection).
  show     Show one run with all steps (read-only connection), and
           optionally restore and print the circuit of one step.

Example (see docs/OPTIMIZATION_RUNS.md for the full walkthrough):

  python examples/pass_sequence_demo.py prepare --source-db source.db \\
      --circuit "MQTBench_1000_sample:x.qasm" --demo-db demo.db
  python examples/pass_sequence_demo.py run --db demo.db \\
      --circuit "MQTBench_1000_sample:x.qasm" \\
      --sequence-file examples/sequences/ic_o1q_ic_ibm.json
  python examples/pass_sequence_demo.py show --db demo.db --restore-step 2
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import tempfile
from pathlib import Path

from qch import QCHDatabase
from qch import optimization as opt


def _ms(value):
    return "-" if value is None else f"{value * 1000:.3f}"


def _value(value):
    return "-" if value is None else str(value)


def print_run(run):
    print(f"Run {run['run_id']}  status={run['status']}  "
          f"label={run['label']!r}")
    print(f"  circuit:  {run['source_circuit_name']} "
          f"(id {run['source_circuit_id']})")
    print("  sequence: "
          + " -> ".join(step["name"] for step in run["pass_sequence"]))
    print(f"  compiler: {run['compiler_name']} {run['compiler_version']}, "
          f"Python {run['python_version']}, {run['platform']}")
    print(f"  metrics:  {run['metrics_version']}, "
          f"mode={run['execution_mode']}, seed={_value(run['seed'])}")
    print(f"  started:  {run['started_at']}  finished: "
          f"{_value(run['finished_at'])}")
    print(f"  PassManager wall time (ms, includes recording callbacks): "
          f"{_ms(run['pass_manager_wall_time_s'])}")
    if run["error_type"]:
        print(f"  error at step {run['failed_step_index']}: "
              f"{run['error_type']}: {run['error_message']}")
    print()
    header = (
        f"{'step':>4} {'pass':<29} {'status':<16} {'chg':>3} {'snap':>4} "
        f"{'qub':>3} {'1q':>5} {'2q':>5} {'3q+':>4} {'meas':>4} "
        f"{'bar':>4} {'gates':>6} {'depth':>5} {'pass_ms':>9} "
        f"{'qiskit_ms':>9} {'metric_ms':>9} {'serial_ms':>9} {'db_ms':>8}"
    )
    print(header)
    print("-" * len(header))
    for step in run["steps"]:
        print(
            f"{step['step_index']:>4} "
            f"{(step['pass_name'] or '(input)'):<29} "
            f"{step['status']:<16} {_value(step['changed']):>3} "
            f"{_value(step['snapshot_id']):>4} "
            f"{_value(step['num_qubits']):>3} "
            f"{_value(step['single_qubit_gate_count']):>5} "
            f"{_value(step['two_qubit_gate_count']):>5} "
            f"{_value(step['multi_qubit_gate_count']):>4} "
            f"{_value(step['measure_count']):>4} "
            f"{_value(step['barrier_count']):>4} "
            f"{_value(step['gate_count']):>6} {_value(step['depth']):>5} "
            f"{_ms(step['pass_time_s']):>9} "
            f"{_ms(step['qiskit_reported_time_s']):>9} "
            f"{_ms(step['metrics_time_s']):>9} "
            f"{_ms(step['serialize_time_s']):>9} "
            f"{_ms(step['db_write_time_s']):>8}"
        )
        if step["error_type"]:
            print(f"     error: {step['error_type']}: {step['error_message']}")


def cmd_prepare(args):
    demo_path = Path(args.demo_db)
    if demo_path.exists():
        print(f"Error: demo database already exists: {demo_path}",
              file=sys.stderr)
        return 2

    if args.source_db:
        source = opt.open_readonly(args.source_db)
        try:
            row = source.execute(
                "SELECT qasm FROM circuits WHERE name = ?", (args.circuit,)
            ).fetchone()
        finally:
            source.close()
        if row is None:
            print(f"Error: circuit not found in source: {args.circuit}",
                  file=sys.stderr)
            return 2
        qasm = row[0]
    else:
        qasm = Path(args.qasm_file).read_text(encoding="utf-8")

    with tempfile.TemporaryDirectory() as tmp:
        qasm_path = Path(tmp) / "input.qasm"
        qasm_path.write_text(qasm, encoding="utf-8")
        db = QCHDatabase(demo_path)
        try:
            circuit_id = db.add_circuit(args.circuit, qasm_path)
            features = db.get_features(args.circuit)
        finally:
            db.close()
    print(f"Created demo database: {demo_path}")
    print(f"Stored circuit {args.circuit!r} as id {circuit_id}: {features}")
    return 0


def cmd_run(args):
    sequence = json.loads(Path(args.sequence_file).read_text(encoding="utf-8"))
    db_path = Path(args.db)
    if not db_path.is_file():
        print(f"Error: database not found: {db_path}", file=sys.stderr)
        return 2
    db = QCHDatabase(db_path)
    try:
        result = opt.run_pass_sequence(
            db, args.circuit, sequence, label=args.label
        )
    except opt.RecordingError as exc:
        print(f"Recording failure (run {exc.run_id}, step {exc.step_index},"
              f" phase {exc.phase}): {exc}", file=sys.stderr)
        return 3
    finally:
        db.close()
    print(f"Run {result.run_id} finished with status {result.status}")
    if result.failed_step_index is not None:
        print(f"Failed step {result.failed_step_index}: "
              f"{result.error_type}: {result.error_message}")
    return 0


def cmd_list(args):
    connection = opt.open_readonly(args.db)
    try:
        runs = opt.list_optimization_runs(connection, args.circuit)
    finally:
        connection.close()
    if not runs:
        print("No optimization runs recorded.")
        return 0
    for run in runs:
        print(f"{run['run_id']:>4}  {run['status']:<16} "
              f"{run['started_at']}  {run['source_circuit_name']}  "
              + " -> ".join(step["name"] for step in run["pass_sequence"]))
    return 0


def cmd_show(args):
    connection = opt.open_readonly(args.db)
    try:
        run_id = args.run_id
        if run_id is None:
            runs = opt.list_optimization_runs(connection)
            if not runs:
                print("No optimization runs recorded.")
                return 0
            run_id = runs[0]["run_id"]
        run = opt.get_optimization_run(connection, run_id)
        if run is None:
            print(f"Run {run_id} not found.", file=sys.stderr)
            return 2
        print_run(run)
        if args.restore_step is not None:
            circuit = opt.load_step_circuit(
                connection, run_id, args.restore_step
            )
            print()
            print(f"Restored circuit of run {run_id} step "
                  f"{args.restore_step} from QPY "
                  f"({len(circuit.data)} instructions, "
                  f"global phase {circuit.global_phase}):")
            print(circuit.draw(output="text", fold=args.fold))
    finally:
        connection.close()
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Run and inspect recorded optimization pass sequences."
    )
    sub = parser.add_subparsers(dest="command", required=True)

    prepare = sub.add_parser("prepare", help="Create a new demo database.")
    prepare.add_argument("--demo-db", required=True,
                         help="Path of the NEW demo database.")
    prepare.add_argument("--circuit", required=True,
                         help="Circuit name to store (and to copy).")
    source = prepare.add_mutually_exclusive_group(required=True)
    source.add_argument("--source-db",
                        help="Existing QCH database, opened read-only.")
    source.add_argument("--qasm-file", help="OpenQASM 2 file to load.")
    prepare.set_defaults(func=cmd_prepare)

    run = sub.add_parser("run", help="Run a pass sequence (writes).")
    run.add_argument("--db", required=True)
    run.add_argument("--circuit", required=True)
    run.add_argument("--sequence-file", required=True,
                     help='JSON list such as [{"name": "...", '
                          '"params": {...}}].')
    run.add_argument("--label")
    run.set_defaults(func=cmd_run)

    list_parser = sub.add_parser("list", help="List runs (read-only).")
    list_parser.add_argument("--db", required=True)
    list_parser.add_argument("--circuit")
    list_parser.set_defaults(func=cmd_list)

    show = sub.add_parser("show", help="Show a run (read-only).")
    show.add_argument("--db", required=True)
    show.add_argument("--run-id", type=int,
                      help="Run id (default: the latest run).")
    show.add_argument("--restore-step", type=int,
                      help="Restore and print this step's circuit.")
    show.add_argument("--fold", type=int, default=120,
                      help="Line width for the circuit drawing.")
    show.set_defaults(func=cmd_show)

    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except (opt.OptimizationError, sqlite3.Error, FileNotFoundError) as exc:
        print(f"Error: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
