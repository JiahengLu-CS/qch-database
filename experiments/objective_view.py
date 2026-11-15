"""Stage 2E: objective-aware view of one circuit of a pass-order experiment.

Reads an experiment directory written by ``experiments/mqt_pass_order.py``
(``selection.json``, ``results.json``, ``experiment.db``) through a
read-only connection and shows, for one source circuit:

  * the sequences with the fewest gates, the fewest two-qubit gates and
    the lowest depth (ties kept),
  * the Pareto front over these three objectives (no combined score),
  * whether one sequence is best in all three, and which objectives
    conflict.

Metrics:
  --metrics experiment (default)  the experiment's metrics from
      results.json: gates without measure/barrier, depth without final
      measurements. Verification comes from the experiment checks.
  --metrics qch  the QCH stored step metrics read from experiment.db
      (gate_count includes measurements). The verification records of the
      experiment are applied by label.

In both modes the run records are rebuilt from the database, which checks
that all runs share the same input representation and belong to the
baseline circuit. Nothing is written.

Example (PowerShell, from the repository root):

  $Py = ".\\.venv\\Scripts\\python.exe"
  & $Py experiments\\objective_view.py show `
      --out-dir .\\demo\\stage2d_20261114165225 --src 291
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from qch import batch
from qch import objectives
from qch import optimization as opt

EXPERIMENT_OBJECTIVES = ("gates", "two_qubit_gates", "depth")
SHORT = {"gates": "gates", "two_qubit_gates": "2q", "depth": "depth",
         "gate_count": "gate_count (incl. measure)",
         "two_qubit_gate_count": "2q", }


def load_circuit(out_dir, src):
    out_dir = Path(out_dir)
    results = json.loads((out_dir / "results.json").read_text(encoding="utf-8"))
    for circuit in results["circuits"]:
        if circuit["source_circuit_id"] == src:
            return results, circuit
    known = [c["source_circuit_id"] for c in results["circuits"]]
    raise LookupError(f"source circuit {src} not in this experiment {known}")


def build(out_dir, src, metrics="experiment"):
    out_dir = Path(out_dir)
    results, circuit = load_circuit(out_dir, src)
    labeled = [(s["label"], s["run_id"]) for s in circuit["sequences"]]
    verification = {s["label"]: bool(s["valid"]) for s in circuit["sequences"]}

    connection = opt.open_readonly(out_dir / "experiment.db")
    try:
        stored = batch.summarize_runs(connection, circuit["baseline_name"],
                                      labeled)
    finally:
        connection.close()
    stored_by_label = {r.label: r for r in stored.results}

    if metrics == "qch":
        view = stored.objective_view(verification)
    elif metrics == "experiment":
        records = []
        for sequence in circuit["sequences"]:
            row = stored_by_label[sequence["label"]]
            if row.status != sequence["status"] or row.run_id != sequence["run_id"]:
                raise RuntimeError(
                    f"results.json disagrees with run {sequence['run_id']}"
                )
            records.append({
                "label": sequence["label"],
                "run_id": sequence["run_id"],
                "status": sequence["status"],
                "metrics": sequence["final_metrics"],
                "input_id": row.input_representation_sha256,
            })
        view = objectives.build_view(records, EXPERIMENT_OBJECTIVES,
                                     verification)
    else:
        raise ValueError(f"unknown metrics mode {metrics!r}")
    return results, circuit, view


def format_report(results, circuit, view, metrics):
    baseline = circuit["baseline_metrics"]
    lines = [
        f"# Objective view: src {circuit['source_circuit_id']} "
        f"({circuit['name'].split(':', 1)[-1]})",
        "",
        f"Runs recorded on: {results['environment']}",
        f"Metrics: {metrics}; smaller is better; no combined score.",
        f"Input representation (sha256): {view.input_id}",
        f"Baseline: gates={baseline['gates']}, "
        f"2q={baseline['two_qubit_gates']}, depth={baseline['depth']}",
        "",
        view.format(SHORT),
    ]
    return "\n".join(lines)


def cmd_show(args):
    results, circuit, view = build(args.out_dir, args.src, args.metrics)
    if args.json:
        print(json.dumps(view.as_dict(), indent=2))
    else:
        print(format_report(results, circuit, view, args.metrics))
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Per-objective best sequences and Pareto front (read-only)."
    )
    sub = parser.add_subparsers(dest="command", required=True)
    show = sub.add_parser("show", help="Show one source circuit.")
    show.add_argument("--out-dir", required=True)
    show.add_argument("--src", type=int, required=True,
                      help="Source circuit ID from selection.json.")
    show.add_argument("--metrics", choices=("experiment", "qch"),
                      default="experiment")
    show.add_argument("--json", action="store_true")
    show.set_defaults(func=cmd_show)
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except (LookupError, ValueError, opt.OptimizationError) as exc:
        print(f"Error: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
