"""Export per-step metrics of saved runs as compact JSON (stage 3A).

Reads an experiment directory written by ``experiments/mqt_pass_order.py``
through a read-only SQLite connection, restores every step circuit of the
requested runs from its stored QPY snapshot, and recomputes the three
resource metrics with the experiment metric profile
(``mqt_pass_order.experiment_metrics``: gates without measure/barrier,
two-qubit gates, depth without final measurements and barriers). No pass is
run and nothing in the experiment directory is written; the SHA-256 of
``experiment.db`` is compared before and after.

Checks before anything is written:

  * every run belongs to the requested source circuit, succeeded, and is
    marked valid (verified) in ``results.json``;
  * all runs share one input representation;
  * step 0 equals the recorded baseline metrics and the last step equals
    the recorded final metrics of the run.

The output contains no absolute paths. Example (PowerShell, from the
qch-database repository root):

  $Py  = ".\\.venv\\Scripts\\python.exe"
  $Exp = ".\\demo\\stage2d_20261114165225"
  $Out = "..\\Demo01\\quantum-circuit-hub\\data\\optimization\\stage2d_src291_steps.json"
  & $Py experiments\\export_step_metrics.py --out-dir $Exp --src 291 `
      --runs 25-30 --output $Out
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

from qch import batch
from qch import optimization as opt

sys.path.insert(0, str(Path(__file__).resolve().parent))
import mqt_pass_order as experiment  # noqa: E402

SCHEMA_VERSION = 1
METRIC_PROFILE = "experiment"
METRICS = ("gates", "two_qubit_gates", "depth")


class ExportError(Exception):
    """The export would not match the recorded experiment."""


def sha256_file(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def parse_runs(text):
    run_ids = []
    for part in text.split(","):
        part = part.strip()
        if "-" in part:
            first, last = (int(x) for x in part.split("-", 1))
            if last < first:
                raise ValueError(f"empty run range {part!r}")
            run_ids.extend(range(first, last + 1))
        elif part:
            run_ids.append(int(part))
    if not run_ids or len(set(run_ids)) != len(run_ids):
        raise ValueError("run IDs must be non-empty and unique")
    return run_ids


def build_export(out_dir, src, run_ids):
    out_dir = Path(out_dir)
    results = json.loads((out_dir / "results.json").read_text(encoding="utf-8"))
    circuit = next((c for c in results["circuits"]
                    if c["source_circuit_id"] == src), None)
    if circuit is None:
        raise ExportError(f"source circuit {src} not in {out_dir.name}")
    sequences = {s["run_id"]: s for s in circuit["sequences"]}
    missing = [r for r in run_ids if r not in sequences]
    if missing:
        raise ExportError(f"runs {missing} do not belong to source {src}")
    baseline = tuple(circuit["baseline_metrics"][m] for m in METRICS)

    db_path = out_dir / "experiment.db"
    before = sha256_file(db_path)
    connection = opt.open_readonly(db_path)
    runs = {}
    try:
        stored = batch.summarize_runs(
            connection, circuit["baseline_name"],
            [(sequences[r]["label"], r) for r in run_ids],
        )
        inputs = {row.input_representation_sha256 for row in stored.results}
        if len(inputs) != 1:
            raise ExportError("runs do not share one input representation")
        input_sha256 = inputs.pop()
        for run_id in run_ids:
            sequence = sequences[run_id]
            if sequence["status"] != "SUCCEEDED" or not sequence["valid"]:
                raise ExportError(f"run {run_id} is not a verified success")
            run = opt.get_optimization_run(connection, run_id)
            pass_names = ["Input"] + [p["name"] for p in run["pass_sequence"]]
            steps = []
            for step in run["steps"]:
                index = step["step_index"]
                circuit_at_step = opt.load_step_circuit(connection, run_id, index)
                metrics = experiment.experiment_metrics(circuit_at_step)
                steps.append({
                    "step": index,
                    "pass": pass_names[index],
                    **{m: int(metrics[m]) for m in METRICS},
                })
            if [s["step"] for s in steps] != list(range(len(pass_names))):
                raise ExportError(f"run {run_id} has missing steps")
            if tuple(steps[0][m] for m in METRICS) != baseline:
                raise ExportError(f"run {run_id}: step 0 differs from baseline")
            final = tuple(sequence["final_metrics"][m] for m in METRICS)
            if tuple(steps[-1][m] for m in METRICS) != final:
                raise ExportError(f"run {run_id}: last step differs from "
                                  "the recorded final metrics")
            runs[str(run_id)] = steps
    finally:
        connection.close()
    after = sha256_file(db_path)
    if before != after:
        raise ExportError("experiment.db changed while reading")

    export = {
        "schema_version": SCHEMA_VERSION,
        "source_id": src,
        "input_sha256": input_sha256,
        "metric_profile": METRIC_PROFILE,
        "runs": runs,
    }
    report = {
        "experiment": out_dir.name,
        "experiment_db_sha256": before,
        "experiment_db_unchanged": True,
        "runs_recorded_on": results["environment"],
        "baseline": baseline,
    }
    return export, report


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Export per-step experiment metrics of saved runs."
    )
    parser.add_argument("--out-dir", required=True,
                        help="Experiment directory (read-only).")
    parser.add_argument("--src", type=int, required=True)
    parser.add_argument("--runs", required=True, help="For example 25-30.")
    parser.add_argument("--output", required=True,
                        help="JSON file to write; must not exist.")
    args = parser.parse_args(argv)

    output = Path(args.output)
    if output.exists():
        print(f"Error: output exists: {output}", file=sys.stderr)
        return 2
    try:
        export, report = build_export(args.out_dir, args.src,
                                      parse_runs(args.runs))
    except (ExportError, ValueError, LookupError, opt.OptimizationError) as exc:
        print(f"Error: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(export, indent=2) + "\n", encoding="utf-8")

    print(f"Experiment: {report['experiment']} (runs recorded on "
          f"{report['runs_recorded_on']})")
    print(f"experiment.db sha256 {report['experiment_db_sha256']} "
          "unchanged: True")
    print(f"Input representation sha256: {export['input_sha256']}")
    print(f"Metric profile: {METRIC_PROFILE}; baseline "
          f"{'/'.join(map(str, report['baseline']))}")
    for run_id, steps in export["runs"].items():
        cells = "  ".join(
            f"{s['step']}:{s['pass']}={s['gates']}/{s['two_qubit_gates']}/"
            f"{s['depth']}" for s in steps)
        print(f"  run {run_id}: {cells}")
    print(f"Written: {output.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
