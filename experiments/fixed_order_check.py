"""Stage 2D: can one fixed pass order replace the per-circuit search?

Reads the output directory of ``experiments/mqt_pass_order.py`` (after
``select`` and ``run``) and compares one fixed order, by default
``B->A->C``, with the best valid result among all six orders of each
circuit. The experiment database is opened read-only and every compared
run is checked against it.

For each metric (two-qubit gates, depth, gates) the report gives:

  * the number of circuits on which the fixed order reaches the best value
    (ties count as reached),
  * the excess of the fixed order over the best value where it does not,
  * the change of the fixed order and of the best order against the
    rz/sx/x/cx baseline (final minus baseline),

overall and per input layer. A counterexample is a circuit on which the
fixed order is valid but misses the best value of at least one metric.
Circuits whose fixed-order run failed or was not verified are listed
separately and are not counted. Pass times are not compared.

Example (PowerShell, from the repository root):

  $Py  = ".\\.venv\\Scripts\\python.exe"
  $Src = "C:\\Users\\jilu\\Project\\QCDB\\Data\\mqt_bench_1000_test.db"
  $Prev = ".\\demo\\stage2c_20261114164214\\selection.json"
  $Out = ".\\demo\\stage2d_$(Get-Date -Format yyyyMMddHHmmss)"
  & $Py experiments\\mqt_pass_order.py select --source-db $Src `
      --out-dir $Out --count 12 --allow-fewer --exclude-selection $Prev
  & $Py experiments\\mqt_pass_order.py run --out-dir $Out
  & $Py experiments\\fixed_order_check.py analyze --out-dir $Out
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from qch import optimization as opt

METRICS = ("two_qubit_gates", "depth", "gates")
METRIC_LABELS = {"two_qubit_gates": "2q", "depth": "depth", "gates": "gates"}
DEFAULT_FIXED = "B->A->C"
OUTPUT_JSON = "fixed_order.json"
OUTPUT_MD = "fixed_order.md"


def check_records(db_path, results):
    """Every run in results.json must exist with the same status."""
    connection = opt.open_readonly(db_path)
    try:
        for circuit in results["circuits"]:
            for sequence in circuit["sequences"]:
                run = opt.get_optimization_run(connection, sequence["run_id"])
                if run["status"] != sequence["status"]:
                    raise RuntimeError(
                        f"run {sequence['run_id']}: stored status "
                        f"{run['status']} differs from results.json"
                    )
                if run["source_circuit_name"] != circuit["baseline_name"]:
                    raise RuntimeError(
                        f"run {sequence['run_id']} belongs to "
                        f"{run['source_circuit_name']}"
                    )
    finally:
        connection.close()


def compare_circuit(circuit, layer, fixed):
    sequences = {s["label"]: s for s in circuit["sequences"]}
    if fixed not in sequences:
        raise ValueError(f"order {fixed} not found for {circuit['name']}")
    valid = [s for s in circuit["sequences"] if s["valid"]]
    entry = {
        "source_circuit_id": circuit["source_circuit_id"],
        "name": circuit["name"].split(":", 1)[-1],
        "family": circuit["family"],
        "layer": layer,
        "baseline": {k: circuit["baseline_metrics"][k] for k in METRICS},
        "valid_orders": len(valid),
        "fixed_run_id": sequences[fixed]["run_id"],
        "fixed_valid": sequences[fixed]["valid"],
    }
    if not entry["fixed_valid"] or not valid:
        entry["excluded_reason"] = (
            "fixed order failed or was not verified"
            if not entry["fixed_valid"] else "no valid order"
        )
        return entry

    fixed_metrics = sequences[fixed]["final_metrics"]
    per_metric = {}
    for key in METRICS:
        best = min(s["final_metrics"][key] for s in valid)
        value = fixed_metrics[key]
        per_metric[key] = {
            "fixed": value,
            "best": best,
            "best_orders": sorted(
                s["label"] for s in valid if s["final_metrics"][key] == best
            ),
            "reached": value == best,
            "excess": value - best,
            "fixed_delta_vs_baseline": value - circuit["baseline_metrics"][key],
            "best_delta_vs_baseline": best - circuit["baseline_metrics"][key],
        }
    entry["metrics"] = per_metric
    entry["counterexample"] = not all(m["reached"] for m in per_metric.values())
    entry["order_dependent"] = circuit["order_dependent"]
    return entry


def summarize(entries):
    counted = [e for e in entries if "metrics" in e]
    summary = {
        "circuits": len(entries),
        "counted": len(counted),
        "excluded": len(entries) - len(counted),
        "order_dependent": sum(1 for e in counted if e["order_dependent"]),
        "counterexamples": [e["source_circuit_id"] for e in counted
                            if e["counterexample"]],
        "metrics": {},
    }
    for key in METRICS:
        values = [e["metrics"][key] for e in counted]
        misses = [v["excess"] for v in values if not v["reached"]]
        summary["metrics"][key] = {
            "reached": sum(1 for v in values if v["reached"]),
            "missed": len(misses),
            "excess_total": sum(misses),
            "excess_max": max(misses) if misses else 0,
            "fixed_delta_total": sum(v["fixed_delta_vs_baseline"] for v in values),
            "best_delta_total": sum(v["best_delta_vs_baseline"] for v in values),
            "baseline_total": sum(e["baseline"][key] for e in counted),
            "fixed_improves": sum(1 for v in values
                                  if v["fixed_delta_vs_baseline"] < 0),
        }
    return summary


def analyze(out_dir, fixed=DEFAULT_FIXED, check_db=True):
    out_dir = Path(out_dir)
    selection = json.loads((out_dir / "selection.json").read_text(encoding="utf-8"))
    results = json.loads((out_dir / "results.json").read_text(encoding="utf-8"))
    if fixed not in results["orders"]:
        raise ValueError(f"{fixed} is not one of {results['orders']}")
    if check_db:
        check_records(out_dir / "experiment.db", results)
    layers = {item["source_circuit_id"]: item["layer"]
              for item in selection["accepted"]}
    entries = [compare_circuit(c, layers[c["source_circuit_id"]], fixed)
               for c in results["circuits"]]
    by_layer = {
        layer: summarize([e for e in entries if e["layer"] == layer])
        for layer in sorted({e["layer"] for e in entries})
    }
    return {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "fixed_order": fixed,
        "run_environment": results["environment"],
        "selection": {
            "excluded": selection.get("excluded", []),
            "rejected": selection.get("rejected", []),
            "rule": selection["selection_rule"],
        },
        "circuits": entries,
        "overall": summarize(entries),
        "by_layer": by_layer,
    }


def _summary_rows(name, summary):
    rows = []
    for key in METRICS:
        m = summary["metrics"][key]
        rows.append(
            f"| {name} | {METRIC_LABELS[key]} | {summary['counted']} | "
            f"{m['reached']} | {m['missed']} | {m['excess_total']} | "
            f"{m['excess_max']} | {m['baseline_total']} | "
            f"{m['fixed_delta_total']:+d} | {m['best_delta_total']:+d} | "
            f"{m['fixed_improves']} |"
        )
    return rows


def format_report(analysis):
    fixed = analysis["fixed_order"]
    lines = [
        f"# Stage 2D: fixed order {fixed} vs best of six orders",
        "",
        f"Environment of the runs: {analysis['run_environment']}",
        "Gates exclude measure and barrier; depth without final "
        "measurements; deltas are final minus the rz/sx/x/cx baseline.",
        "",
        "## Per circuit (fixed / best of six, excess in brackets)",
        "",
        "| src | name | layer | baseline 2q/depth/gates | 2q | depth "
        "| gates | counterexample |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for e in analysis["circuits"]:
        b = e["baseline"]
        head = (f"| {e['source_circuit_id']} | {e['name']} | {e['layer']} | "
                f"{b['two_qubit_gates']}/{b['depth']}/{b['gates']} |")
        if "metrics" not in e:
            lines.append(f"{head} - | - | - | excluded: "
                         f"{e['excluded_reason']} |")
            continue
        cells = []
        for key in METRICS:
            m = e["metrics"][key]
            mark = "" if m["reached"] else f" (+{m['excess']})"
            cells.append(f"{m['fixed']}/{m['best']}{mark}")
        lines.append(f"{head} {' | '.join(cells)} | "
                     f"{'YES' if e['counterexample'] else 'no'} |")

    lines += [
        "",
        "## Summary (overall and per input layer)",
        "",
        "| scope | metric | counted | reached best | missed | excess total "
        "| excess max | baseline total | fixed delta | best delta "
        "| fixed improves |",
        "|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    lines += _summary_rows("all", analysis["overall"])
    for layer, summary in analysis["by_layer"].items():
        lines += _summary_rows(layer, summary)

    overall = analysis["overall"]
    lines += [
        "",
        f"Counted circuits: {overall['counted']} of {overall['circuits']} "
        f"(excluded: {overall['excluded']}); order-dependent: "
        f"{overall['order_dependent']}.",
        "Counterexamples (fixed order misses the best value of at least one "
        "metric): " + (", ".join(f"src {i}" for i in overall["counterexamples"])
                       or "none found"),
    ]
    for e in analysis["circuits"]:
        if e.get("counterexample"):
            missed = [
                f"{METRIC_LABELS[k]} +{e['metrics'][k]['excess']} "
                f"(best: {', '.join(e['metrics'][k]['best_orders'])})"
                for k in METRICS if not e["metrics"][k]["reached"]
            ]
            lines.append(f"  - src {e['source_circuit_id']}: "
                         + "; ".join(missed))
    lines += ["", "Pass times are not compared."]
    return "\n".join(lines) + "\n"


def cmd_analyze(args):
    out_dir = Path(args.out_dir)
    analysis = analyze(out_dir, args.fixed, check_db=not args.no_db_check)
    report = format_report(analysis)
    if not args.no_write:
        (out_dir / OUTPUT_JSON).write_text(json.dumps(analysis, indent=2),
                                           encoding="utf-8")
        (out_dir / OUTPUT_MD).write_text(report, encoding="utf-8")
    print(report)
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Compare one fixed pass order with the best of all orders."
    )
    sub = parser.add_subparsers(dest="command", required=True)
    analyze_cmd = sub.add_parser("analyze", help="Analyze an experiment directory.")
    analyze_cmd.add_argument("--out-dir", required=True)
    analyze_cmd.add_argument("--fixed", default=DEFAULT_FIXED)
    analyze_cmd.add_argument("--no-db-check", action="store_true",
                             help="Skip the read-only check of the run records.")
    analyze_cmd.add_argument("--no-write", action="store_true",
                             help="Print only; do not write fixed_order.*.")
    analyze_cmd.set_defaults(func=cmd_analyze)
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
