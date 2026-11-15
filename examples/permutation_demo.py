"""Bounded permutation generation for pass sequences (stage 2B).

Subcommands:

  preview  List the candidate orders without opening any database.
  demo     Create a NEW database with the 3-qubit circuit of
           examples/pass_order_acceptance.py and run every permutation of
           the chosen built-in configurations through the stage 2A batch API.
  run      Run every permutation of the configurations in a JSON file on a
           circuit that is already stored in an existing database.

Built-in configurations (all with basis rz, sx, x, cx where applicable):

  A = CommutativeCancellation
  B = Optimize1qGatesDecomposition
  C = CommutativeInverseCancellation

Items file format (order defines the generation order):

  {"A": {"name": "...", "params": {...}}, "B": {...}}

Examples (PowerShell, from the repository root):

  $Py = ".\\.venv\\Scripts\\python.exe"
  & $Py examples\\permutation_demo.py preview --ids A B C
  $Db = "$env:TEMP\\qch_perm_$(Get-Date -Format yyyyMMddHHmmss).db"
  & $Py examples\\permutation_demo.py demo --db $Db --ids A B C
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
from qch import permutations

import pass_order_acceptance as order_demo

BUILT_IN = {
    "A": order_demo.PASS_A,
    "B": order_demo.PASS_B,
    "C": {"name": "CommutativeInverseCancellation", "params": {}},
}


def built_in_items(ids):
    unknown = [item_id for item_id in ids if item_id not in BUILT_IN]
    if unknown:
        raise batch.BatchConfigError(
            f"Unknown built-in configuration(s) {unknown}; "
            f"choose from {sorted(BUILT_IN)}"
        )
    return {item_id: BUILT_IN[item_id] for item_id in ids}


def load_items(args):
    if args.items_file:
        return json.loads(Path(args.items_file).read_text(encoding="utf-8"))
    return built_in_items(args.ids)


def print_batch(result):
    print(result.format_table())
    print()
    print("Run IDs: " + ", ".join(
        f"{item.label}={item.run_id}" for item in result.results
    ))


def cmd_preview(args):
    preview = permutations.preview_permutations(
        load_items(args), max_permutations=args.max
    )
    print(preview.format())
    print("Preview only: no database was opened or written.")
    return 0


def cmd_demo(args):
    db_path = Path(args.db)
    if db_path.exists():
        print(f"Error: database already exists: {db_path}", file=sys.stderr)
        return 2
    items = built_in_items(args.ids)
    # Validate and bound the request before the database file is created.
    permutations.preview_permutations(items, max_permutations=args.max)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    db = QCHDatabase(db_path)
    try:
        with tempfile.TemporaryDirectory() as tmp:
            qasm_path = Path(tmp) / "input.qasm"
            qasm_path.write_text(order_demo.CIRCUIT_QASM, encoding="utf-8")
            db.add_circuit(order_demo.CIRCUIT_NAME, qasm_path)
        preview, result = permutations.run_permutation_batch(
            db, order_demo.CIRCUIT_NAME, items, max_permutations=args.max
        )
    finally:
        db.close()
    print(preview.format())
    print()
    print_batch(result)
    return 0


def cmd_run(args):
    db_path = Path(args.db)
    if not db_path.is_file():
        print(f"Error: database not found: {db_path}", file=sys.stderr)
        return 2
    items = load_items(args)
    db = QCHDatabase(db_path)
    try:
        preview, result = permutations.run_permutation_batch(
            db, args.circuit, items, max_permutations=args.max
        )
    except batch.BatchExecutionError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        print(f"Completed before the stop: "
              f"{[item.label for item in exc.completed]}", file=sys.stderr)
        return 3
    finally:
        db.close()
    print(preview.format())
    print()
    print_batch(result)
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Generate and run bounded permutations of pass sequences."
    )
    sub = parser.add_subparsers(dest="command", required=True)

    def add_common(sub_parser, allow_file):
        sub_parser.add_argument(
            "--ids", nargs="+", default=["A", "B"],
            help="Built-in configuration identifiers, in generation order.",
        )
        if allow_file:
            sub_parser.add_argument(
                "--items-file", help="JSON object {id: pass configuration}."
            )
        sub_parser.add_argument(
            "--max", type=int, default=permutations.DEFAULT_MAX_PERMUTATIONS,
            help="Maximum number of permutations (default 24).",
        )

    preview = sub.add_parser("preview", help="List candidates; no database.")
    add_common(preview, allow_file=True)
    preview.set_defaults(func=cmd_preview)

    demo = sub.add_parser("demo", help="Run the built-in demo on a NEW database.")
    demo.add_argument("--db", required=True)
    add_common(demo, allow_file=False)
    demo.set_defaults(func=cmd_demo)

    run = sub.add_parser("run", help="Run permutations on a stored circuit.")
    run.add_argument("--db", required=True)
    run.add_argument("--circuit", required=True)
    add_common(run, allow_file=True)
    run.set_defaults(func=cmd_run)

    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except (opt.OptimizationError, ValueError) as exc:
        print(f"Error: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
