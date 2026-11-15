"""Stage 2F: compare a larger pass set with an earlier experiment.

Reads two experiment directories written by ``experiments/mqt_pass_order.py``
on the SAME circuits (for example stage 2D with A, B, C and stage 2F with A,
B, C, E, F created with ``reuse``). Both experiment databases are opened
read-only and every run in ``results.json`` is checked against its database.
Only verified runs are used.

The report answers:

  1. Gain: per circuit and metric, the best value of the larger pass set
     against the best value of the earlier one (ties kept, no combined
     score).
  2. Structure: how many distinct behaviours the orders show (orders with
     identical final metrics on every circuit form one class), and which
     passes can be moved anywhere without changing any final metric.
  3. Fixed orders: for every order of the larger set, on how many circuits
     it reaches the best value of each metric and of all three at once;
     a circuit where a valid order misses a best value is a counterexample
     for that order.

Example (PowerShell, from the repository root):

  $Py = ".\\.venv\\Scripts\\python.exe"
  & $Py experiments\\pass_set_compare.py --before .\\demo\\stage2d_20261114165225 `
      --after .\\demo\\stage2f_<timestamp>
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import fixed_order_check  # noqa: E402

from qch import objectives  # noqa: E402

METRICS = ("gates", "two_qubit_gates", "depth")
SHORT = {"gates": "gates", "two_qubit_gates": "2q", "depth": "depth"}
SEPARATOR = "->"
OUTPUT_JSON = "pass_set_compare.json"
OUTPUT_MD = "pass_set_compare.md"


class CompareError(Exception):
    """The two experiments cannot be compared."""


def load(out_dir, check_db=True):
    out_dir = Path(out_dir)
    selection = json.loads((out_dir / "selection.json").read_text(encoding="utf-8"))
    results = json.loads((out_dir / "results.json").read_text(encoding="utf-8"))
    if check_db:
        fixed_order_check.check_records(out_dir / "experiment.db", results)
    return selection, results


def valid_sequences(circuit):
    return [s for s in circuit["sequences"] if s["valid"]]


def best_values(circuit):
    valid = valid_sequences(circuit)
    if not valid:
        return None
    return {m: min(s["final_metrics"][m] for s in valid) for m in METRICS}


def order_classes(circuits, labels):
    """Group orders with identical final metrics on every circuit."""
    vectors = defaultdict(list)
    for circuit in circuits:
        by_label = {s["label"]: s for s in circuit["sequences"]}
        for label in labels:
            sequence = by_label[label]
            vectors[label].append(
                tuple(sequence["final_metrics"][m] for m in METRICS)
                if sequence["valid"] else None
            )
    classes = defaultdict(list)
    for label in labels:
        classes[tuple(vectors[label])].append(label)
    return list(classes.values()), vectors


def position_insensitive(vectors, item_ids):
    """Pass IDs whose position never changes any final metric.

    A pass qualifies if removing it from the labels leaves a function: all
    orders that agree on the remaining passes have the same final metrics
    on every circuit.
    """
    result = []
    for item in item_ids:
        groups = defaultdict(set)
        for label, vector in vectors.items():
            rest = SEPARATOR.join(p for p in label.split(SEPARATOR) if p != item)
            groups[rest].add(tuple(vector))
        if all(len(values) == 1 for values in groups.values()):
            result.append(item)
    return result


def rank_orders(after_results, labels):
    """Counterexamples and reached-best counts for every fixed order."""
    ranking = []
    for label in labels:
        entries = [fixed_order_check.compare_circuit(c, None, label)
                   for c in after_results["circuits"]]
        summary = fixed_order_check.summarize(entries)
        reached_all = sum(1 for e in entries
                          if "metrics" in e and not e["counterexample"])
        ranking.append({
            "order": label,
            "reached_all_three": reached_all,
            "counted": summary["counted"],
            "counterexamples": summary["counterexamples"],
            "reached": {m: summary["metrics"][m]["reached"] for m in METRICS},
            "excess_total": {m: summary["metrics"][m]["excess_total"]
                             for m in METRICS},
        })
    ranking.sort(key=lambda r: (-r["reached_all_three"],
                                sum(r["excess_total"].values()), r["order"]))
    return ranking


def compare(before_dir, after_dir, check_db=True):
    before_sel, before = load(before_dir, check_db)
    after_sel, after = load(after_dir, check_db)

    def key(selection):
        return {i["source_circuit_id"]: i["baseline_qasm_sha256"]
                for i in selection["accepted"]}

    if key(before_sel) != key(after_sel):
        raise CompareError("the experiments do not use the same baseline "
                           "circuits")
    before_by_src = {c["source_circuit_id"]: c for c in before["circuits"]}

    circuits = []
    for circuit in after["circuits"]:
        src = circuit["source_circuit_id"]
        old = best_values(before_by_src[src])
        new = best_values(circuit)
        valid = valid_sequences(circuit)
        view = objectives.build_view(
            [{"label": s["label"], "run_id": s["run_id"], "status": s["status"],
              "metrics": s["final_metrics"], "input_id": src}
             for s in circuit["sequences"]],
            METRICS, {s["label"]: bool(s["valid"]) for s in circuit["sequences"]},
        )
        circuits.append({
            "source_circuit_id": src,
            "name": circuit["name"].split(":", 1)[-1],
            "baseline": {m: circuit["baseline_metrics"][m] for m in METRICS},
            "valid_orders": len(valid),
            "orders": len(circuit["sequences"]),
            "distinct_outcomes": circuit["distinct_valid_outcomes"],
            "best_before": old,
            "best_after": new,
            "gain": None if old is None or new is None
            else {m: new[m] - old[m] for m in METRICS},
            "best_orders_after": {m: circuit["best"][m]["labels"]
                                  for m in METRICS},
            "pareto_front_size": len(view.pareto_front),
            "common_optimum": len(view.common_optimum()),
            "conflicts": [list(p) for p in view.conflicts()],
        })

    labels = after["orders"]
    classes, vectors = order_classes(after["circuits"], labels)
    item_ids = list(after["pass_items"])
    ranking = rank_orders(after, labels)
    return {
        "before": {"experiment": Path(before_dir).name,
                   "pass_items": before["pass_items"],
                   "orders": len(before["orders"]),
                   "environment": before["environment"]},
        "after": {"experiment": Path(after_dir).name,
                  "pass_items": after["pass_items"],
                  "orders": len(labels),
                  "environment": after["environment"]},
        "valid_runs_after": sum(c["valid_orders"] for c in circuits),
        "total_runs_after": sum(c["orders"] for c in circuits),
        "circuits": circuits,
        "order_classes": sorted(classes, key=lambda c: (-len(c), c[0])),
        "position_insensitive": position_insensitive(vectors, item_ids),
        "ranking": ranking,
    }


def format_report(data, top=10):
    def triple(values):
        return "-" if values is None else "/".join(
            str(values[m]) for m in METRICS)

    before, after = data["before"], data["after"]
    lines = [
        "# Pass-set comparison",
        "",
        f"Before: {before['experiment']} with {', '.join(before['pass_items'])} "
        f"({before['orders']} orders, runs on {before['environment']}).",
        f"After: {after['experiment']} with {', '.join(after['pass_items'])} "
        f"({after['orders']} orders, runs on {after['environment']}).",
        f"Valid runs after: {data['valid_runs_after']} of "
        f"{data['total_runs_after']}. Metrics gates/2q/depth (experiment "
        "profile); smaller is better; no combined score.",
        "",
        "## Best values per circuit",
        "",
        "| src | name | baseline | best before | best after | gain | "
        "outcomes | Pareto | common optimum |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for c in data["circuits"]:
        gain = "-" if c["gain"] is None else "/".join(
            f"{c['gain'][m]:+d}" for m in METRICS)
        lines.append(
            f"| {c['source_circuit_id']} | {c['name']} | "
            f"{triple(c['baseline'])} | {triple(c['best_before'])} | "
            f"{triple(c['best_after'])} | {gain} | {c['distinct_outcomes']} | "
            f"{c['pareto_front_size']} | "
            f"{c['common_optimum'] or 'none (' + ', '.join('/'.join(p) for p in c['conflicts']) + ')'} |"
        )
    improved = {m: [c["source_circuit_id"] for c in data["circuits"]
                    if c["gain"] and c["gain"][m] < 0] for m in METRICS}
    lines += [
        "",
        "Circuits improved by the larger pass set: " + "; ".join(
            f"{SHORT[m]}: {', '.join(map(str, v)) or 'none'}"
            for m, v in improved.items()),
        "",
        "## Order structure",
        "",
        f"{after['orders']} orders form {len(data['order_classes'])} classes "
        "with identical final metrics on every circuit.",
        "Passes whose position never changes a final metric: "
        + (", ".join(data["position_insensitive"]) or "none"),
        "",
        f"## Fixed orders by class (top {top} of "
        f"{len(data['order_classes'])}; orders in one class behave alike)",
        "",
        "| example order | orders in class | best in all three | 2q | depth "
        "| gates | excess gates/2q/depth | counterexamples |",
        "|---|---|---|---|---|---|---|---|",
    ]
    class_of = {label: index for index, members in
                enumerate(data["order_classes"]) for label in members}
    shown = set()
    for r in data["ranking"]:
        index = class_of[r["order"]]
        if index in shown:
            continue
        shown.add(index)
        if len(shown) > top:
            break
        lines.append(
            f"| {r['order']} | {len(data['order_classes'][index])} | "
            f"{r['reached_all_three']}/{r['counted']} | "
            f"{r['reached']['two_qubit_gates']} | {r['reached']['depth']} | "
            f"{r['reached']['gates']} | "
            f"{r['excess_total']['gates']}/{r['excess_total']['two_qubit_gates']}/"
            f"{r['excess_total']['depth']} | "
            f"{', '.join(map(str, r['counterexamples'])) or 'none'} |"
        )
    best = data["ranking"][0]
    universal = [r["order"] for r in data["ranking"]
                 if r["reached_all_three"] == r["counted"]]
    lines += [
        "",
        "Orders that are best in all three metrics on every circuit: "
        + (", ".join(universal) or "none"),
        f"Best fixed order(s) reach all three best values on "
        f"{best['reached_all_three']} of {best['counted']} circuits.",
        "",
        "Pass times are not compared.",
    ]
    return "\n".join(lines) + "\n"


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Compare a larger pass set with an earlier experiment."
    )
    parser.add_argument("--before", required=True)
    parser.add_argument("--after", required=True)
    parser.add_argument("--top", type=int, default=10)
    parser.add_argument("--no-write", action="store_true",
                        help="Print only; do not write pass_set_compare.* "
                             "into the --after directory.")
    args = parser.parse_args(argv)
    try:
        data = compare(args.before, args.after)
    except (CompareError, RuntimeError, LookupError, ValueError) as exc:
        print(f"Error: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2
    report = format_report(data, args.top)
    if not args.no_write:
        out = Path(args.after)
        (out / OUTPUT_JSON).write_text(json.dumps(data, indent=2),
                                       encoding="utf-8")
        (out / OUTPUT_MD).write_text(report, encoding="utf-8")
    print(report)
    return 0


if __name__ == "__main__":
    sys.exit(main())
