"""Batch comparison of explicit pass sequences on one stored circuit.

Subcommands:

  demo  Create a NEW database with the 3-qubit circuit of
        examples/pass_order_acceptance.py and compare A->B, B->A and
        A->B->A (A = CommutativeCancellation, B = Optimize1qGatesDecomposition,
        both with basis rz, sx, x, cx).
  run   Compare the sequences of a JSON file on a circuit that is already
        stored in an existing database. Writes optimization runs.
  show  Rebuild the comparison from stored run IDs through a read-only
        connection, and optionally restore one step circuit.

Sequence file format (order is kept):

  {"A->B": [{"name": "...", "params": {...}}, ...], "B->A": [...]}

Examples (PowerShell, from the repository root):

  $Py = ".\\.venv\\Scripts\\python.exe"
  $Db = "$env:TEMP\\qch_batch_$(Get-Date -Format yyyyMMddHHmmss).db"
  & $Py examples\\batch_sequence_demo.py demo --db $Db
  & $Py examples\\batch_sequence_demo.py show --db $Db `
      --circuit pass_order_demo_3q --run "A->B=1" --run "B->A=2" `
      --restore "B->A" 1
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path

from qch import QCHDatabase
from qch import batch
from qch import optimization as opt

import pass_order_acceptance as order_demo


def demo_sequences():
    a, b = order_demo.PASS_A, order_demo.PASS_B
    return {"A->B": [a, b], "B->A": [b, a], "A->B->A": [a, b, a]}


def print_result(result):
    print(result.format_table())
    print()
    print("Run IDs (use with 'show'): " + " ".join(
        f'--run "{item.label}={item.run_id}"' for item in result.results
    ))


def cmd_demo(args):
    db_path = Path(args.db)
    if db_path.exists():
        print(f"Error: database already exists: {db_path}", file=sys.stderr)
        return 2
    db_path.parent.mkdir(parents=True, exist_ok=True)
    db = QCHDatabase(db_path)
    try:
        with tempfile.TemporaryDirectory() as tmp:
            qasm_path = Path(tmp) / "input.qasm"
            qasm_path.write_text(order_demo.CIRCUIT_QASM, encoding="utf-8")
            db.add_circuit(order_demo.CIRCUIT_NAME, qasm_path)
        result = batch.run_sequence_batch(
            db, order_demo.CIRCUIT_NAME, demo_sequences()
        )
    finally:
        db.close()
    print_result(result)
    return 0


def cmd_run(args):
    db_path = Path(args.db)
    if not db_path.is_file():
        print(f"Error: database not found: {db_path}", file=sys.stderr)
        return 2
    sequences = json.loads(Path(args.sequences_file).read_text(encoding="utf-8"))
    db = QCHDatabase(db_path)
    try:
        result = batch.run_sequence_batch(db, args.circuit, sequences)
    except batch.BatchExecutionError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        print(f"Completed before the stop: "
              f"{[item.label for item in exc.completed]}", file=sys.stderr)
        return 3
    finally:
        db.close()
    print_result(result)
    return 0


def parse_run(value):
    label, sep, run_id = value.rpartition("=")
    if not sep or not label or not run_id.isdigit():
        raise argparse.ArgumentTypeError(
            f"expected LABEL=RUN_ID, got {value!r}"
        )
    return label, int(run_id)


def cmd_show(args):
    connection = opt.open_readonly(args.db)
    try:
        result = batch.summarize_runs(connection, args.circuit, args.run)
        print(result.format_table())
        if args.restore:
            label, step_text = args.restore
            run_ids = dict(args.run)
            if label not in run_ids:
                print(f"Error: unknown label {label!r}", file=sys.stderr)
                return 2
            step_index = int(step_text)
            circuit = opt.load_step_circuit(
                connection, run_ids[label], step_index
            )
            stored = opt.list_optimization_steps(connection, run_ids[label])
            stored_step = next(
                s for s in stored if s["step_index"] == step_index
            )
            print()
            print(f"Restored {label} (run {run_ids[label]}) step "
                  f"{step_index} from QPY: ops="
                  f"{dict(sorted(circuit.count_ops().items()))}")
            print(f"  stored:     gates={stored_step['gate_count']}, "
                  f"2q={stored_step['two_qubit_gate_count']}, "
                  f"depth={stored_step['depth']}")
            recomputed = opt.compute_step_metrics(circuit)
            print(f"  recomputed: gates={recomputed['gate_count']}, "
                  f"2q={recomputed['two_qubit_gate_count']}, "
                  f"depth={recomputed['depth']}")
            print(circuit.draw(output="text", fold=args.fold))
    finally:
        connection.close()
    return 0


def use_utf8_output():
    """Write UTF-8 when the output is redirected.

    Circuit drawings contain characters such as box lines and the Greek
    letter pi. On Windows a redirected stdout defaults to the ANSI code
    page (for example cp1252), which cannot encode them. An interactive
    console is left unchanged.
    """
    stream = sys.stdout
    if hasattr(stream, "reconfigure") and not stream.isatty():
        try:
            "\u03c0\u2500".encode(stream.encoding or "ascii")
        except (UnicodeEncodeError, LookupError):
            stream.reconfigure(encoding="utf-8")


def main(argv=None):
    use_utf8_output()
    parser = argparse.ArgumentParser(
        description="Compare explicit optimization pass sequences."
    )
    sub = parser.add_subparsers(dest="command", required=True)

    demo = sub.add_parser("demo", help="Run the built-in demo on a NEW database.")
    demo.add_argument("--db", required=True)
    demo.set_defaults(func=cmd_demo)

    run = sub.add_parser("run", help="Compare sequences from a JSON file.")
    run.add_argument("--db", required=True)
    run.add_argument("--circuit", required=True)
    run.add_argument("--sequences-file", required=True)
    run.set_defaults(func=cmd_run)

    show = sub.add_parser("show", help="Rebuild the comparison (read-only).")
    show.add_argument("--db", required=True)
    show.add_argument("--circuit", required=True)
    show.add_argument("--run", action="append", type=parse_run, required=True,
                      help="LABEL=RUN_ID; repeat for every sequence.")
    show.add_argument("--restore", nargs=2, metavar=("LABEL", "STEP"),
                      help="Restore and print one step circuit.")
    show.add_argument("--fold", type=int, default=120)
    show.set_defaults(func=cmd_show)

    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except (opt.OptimizationError, LookupError, ValueError) as exc:
        print(f"Error: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
