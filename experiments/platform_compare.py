"""Stage 2H: compare two runs of the same experiment on different platforms.

Both directories must be outputs of ``experiments/mqt_pass_order.py run``
with the same pass set on the same circuits (for example one run on Windows
and one on Linux). Both experiment databases are opened read-only. For
every (circuit, order) pair the script compares the status, the
verification result, the final metrics and the representation
fingerprint of every stored step, and reports the first step at which the
two runs differ.

Example (PowerShell, from the repository root):

  $Py = ".\\.venv\\Scripts\\python.exe"
  & $Py experiments\\platform_compare.py --a .\\demo\\stage2h_<windows> `
      --b .\\demo\\stage2h_<linux>
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

from qch import optimization as opt

METRICS = ("gates", "two_qubit_gates", "depth")


class PlatformCompareError(Exception):
    """The two directories do not hold the same experiment."""


def load(directory):
    directory = Path(directory)
    selection = json.loads((directory / "selection.json").read_text(encoding="utf-8"))
    results = json.loads((directory / "results.json").read_text(encoding="utf-8"))
    return selection, results


def _triple(metrics):
    return None if metrics is None else "/".join(
        str(metrics[m]) for m in METRICS)


def compare(dir_a, dir_b, fingerprints=True):
    sel_a, res_a = load(dir_a)
    sel_b, res_b = load(dir_b)
    if res_a.get("pass_items") != res_b.get("pass_items"):
        raise PlatformCompareError("the pass sets differ")
    hashes = [{i["source_circuit_id"]: i["baseline_qasm_sha256"]
               for i in sel["accepted"]} for sel in (sel_a, sel_b)]
    if hashes[0] != hashes[1]:
        raise PlatformCompareError("the baseline circuits differ")

    runs_b = {(c["source_circuit_id"], s["label"]): s
              for c in res_b["circuits"] for s in c["sequences"]}
    conn_a = opt.open_readonly(Path(dir_a) / "experiment.db")
    conn_b = opt.open_readonly(Path(dir_b) / "experiment.db")
    summary = Counter()
    first_step = Counter()
    examples = []
    try:
        for circuit in res_a["circuits"]:
            src = circuit["source_circuit_id"]
            for seq in circuit["sequences"]:
                other = runs_b.get((src, seq["label"]))
                if other is None:
                    raise PlatformCompareError(f"{src} {seq['label']} missing")
                summary["runs"] += 1
                same_outcome = (seq["status"] == other["status"]
                                and seq["valid"] == other["valid"])
                same_metrics = seq["final_metrics"] == other["final_metrics"]
                summary["same_status_and_validity"] += same_outcome
                summary["same_final_metrics"] += same_metrics
                if seq["final_metrics"] and other["final_metrics"]:
                    summary["same_two_qubit_gates"] += (
                        seq["final_metrics"]["two_qubit_gates"]
                        == other["final_metrics"]["two_qubit_gates"])
                if not fingerprints:
                    continue
                steps = len(seq["pass_names"]) + 1
                differing = None
                for index in range(steps):
                    a = opt.representation_fingerprint(
                        opt.load_step_circuit(conn_a, seq["run_id"], index))
                    b = opt.representation_fingerprint(
                        opt.load_step_circuit(conn_b, other["run_id"], index))
                    if a != b:
                        differing = index
                        break
                if differing is None:
                    summary["identical_at_every_step"] += 1
                else:
                    label = ("input" if differing == 0 else
                             seq["label"].split("->")[differing - 1])
                    first_step[label] += 1
                    if not same_metrics and len(examples) < 5:
                        examples.append({"source_circuit_id": src,
                                         "order": seq["label"],
                                         "first_differing_step": differing,
                                         "a": _triple(seq["final_metrics"]),
                                         "b": _triple(other["final_metrics"])})
    finally:
        conn_a.close()
        conn_b.close()
    return {
        "a": {"experiment": Path(dir_a).name, "environment": res_a["environment"]},
        "b": {"experiment": Path(dir_b).name, "environment": res_b["environment"]},
        "summary": dict(summary),
        "first_differing_pass": dict(first_step),
        "examples": examples,
    }


def format_report(data):
    s = data["summary"]
    lines = [
        "# Platform comparison",
        "",
        f"A: {data['a']['experiment']} ({data['a']['environment']})",
        f"B: {data['b']['experiment']} ({data['b']['environment']})",
        "",
        f"Runs compared: {s.get('runs', 0)}",
        f"Same status and verification: {s.get('same_status_and_validity', 0)}",
        f"Same final gates/2q/depth: {s.get('same_final_metrics', 0)}",
        f"Same final two-qubit gates: {s.get('same_two_qubit_gates', 0)}",
    ]
    if "identical_at_every_step" in s or data["first_differing_pass"]:
        lines.append("Identical representation at every step: "
                     f"{s.get('identical_at_every_step', 0)}")
        lines.append("First differing step, by pass: "
                     + (", ".join(f"{k}: {v}" for k, v in
                                  sorted(data["first_differing_pass"].items()))
                        or "none"))
    for example in data["examples"]:
        lines.append(f"  - src {example['source_circuit_id']} "
                     f"{example['order']}: step {example['first_differing_step']}; "
                     f"A {example['a']} vs B {example['b']}")
    return "\n".join(lines) + "\n"


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Compare two runs of one experiment across platforms.")
    parser.add_argument("--a", required=True)
    parser.add_argument("--b", required=True)
    parser.add_argument("--metrics-only", action="store_true",
                        help="Skip the per-step fingerprint comparison.")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    try:
        data = compare(args.a, args.b, fingerprints=not args.metrics_only)
    except (PlatformCompareError, LookupError, OSError, ValueError) as exc:
        print(f"Error: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(data, indent=2) if args.json else format_report(data))
    return 0


if __name__ == "__main__":
    sys.exit(main())
