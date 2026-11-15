"""Export one pass-order experiment as a compact JSON dataset (stage 3B).

The Streamlit demo (``quantum-circuit-hub``, page "Optimization Results")
reads this file instead of a database. For every circuit of an experiment
directory written by ``experiments/mqt_pass_order.py`` the dataset holds
the baseline, every order with its run ID, status, verification and final
metrics, and the metrics after every step. Step metrics are recomputed
from the stored QPY snapshots with the experiment metric profile
(``mqt_pass_order.experiment_metrics``); no pass is run.

Read-only: ``experiment.db`` is opened with SQLite ``mode=ro`` and its
SHA-256 is compared before and after. Before writing, the script checks
for every run that step 0 equals the baseline, that the last step equals
the final metrics in ``results.json``, that all runs of a circuit share one
input representation, and that every label is an order of the pass set.
The output contains no local paths and the target must not exist.

Format (``schema_version`` 2; metric triples are [gates, 2q, depth]):

  {"schema_version": 2, "metric_profile": "experiment",
   "experiment": "<directory name>", "title": ..., "pass_set": ...,
   "environment": {...}, "experiment_db_sha256": ...,
   "passes": {"A": {"name": ..., "params": {...}}, ...},
   "circuits": [{"source_id", "name", "family", "layer", "num_qubits",
                 "input_sha256", "baseline": [g, t, d],
                 "runs": [{"order": "A->B->C", "run_id", "status",
                           "verified", "final": [g, t, d] | null,
                           "steps": [[g, t, d], ...]}]}]}

Example (PowerShell, from the qch-database repository root):

  $Py = ".\\.venv\\Scripts\\python.exe"
  $Data = "..\\Demo01\\quantum-circuit-hub\\data\\optimization"
  & $Py experiments\\export_demo_dataset.py --out-dir .\\demo\\stage2d_20261114165225 `
      --output $Data\\stage2d_abc.json
  & $Py experiments\\export_demo_dataset.py --out-dir .\\demo\\stage2g_20261115052449 `
      --output $Data\\stage2g_abcfg.json
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

SCHEMA_VERSION = 2
METRICS = ("gates", "two_qubit_gates", "depth")


class ExportError(Exception):
    """The dataset would not match the recorded experiment."""


def sha256_file(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def triple(metrics):
    return None if metrics is None else [int(metrics[m]) for m in METRICS]


def export_circuit(connection, item, circuit, pass_ids):
    baseline = triple(circuit["baseline_metrics"])
    labeled = [(s["label"], s["run_id"]) for s in circuit["sequences"]]
    stored = batch.summarize_runs(connection, circuit["baseline_name"], labeled)
    inputs = {row.input_representation_sha256 for row in stored.results}
    if len(inputs) != 1:
        raise ExportError(f"src {item['source_circuit_id']}: runs do not share "
                          "one input representation")
    runs = []
    for sequence, row in zip(circuit["sequences"], stored.results):
        order = sequence["label"].split("->")
        if sorted(order) != sorted(pass_ids) or row.run_id != sequence["run_id"]:
            raise ExportError(f"run {sequence['run_id']}: unexpected order")
        if row.status != sequence["status"]:
            raise ExportError(f"run {sequence['run_id']}: status differs")
        run = opt.get_optimization_run(connection, sequence["run_id"])
        steps = []
        for step in run["steps"]:
            if step["status"] != "SUCCEEDED":
                break
            restored = opt.load_step_circuit(connection, sequence["run_id"],
                                             step["step_index"])
            steps.append(triple(experiment.experiment_metrics(restored)))
        if steps[0] != baseline:
            raise ExportError(f"run {sequence['run_id']}: step 0 differs from "
                              "the baseline")
        final = triple(sequence["final_metrics"])
        if sequence["status"] == "SUCCEEDED":
            if len(steps) != len(order) + 1 or steps[-1] != final:
                raise ExportError(f"run {sequence['run_id']}: last step differs "
                                  "from the recorded final metrics")
        runs.append({
            "order": sequence["label"],
            "run_id": sequence["run_id"],
            "status": sequence["status"],
            "verified": bool(sequence["valid"]),
            "final": final,
            "steps": steps,
        })
    return {
        "source_id": item["source_circuit_id"],
        "name": item["name"].split(":", 1)[-1],
        "family": item["family"],
        "layer": item["layer"],
        "num_qubits": item["num_qubits"],
        "input_sha256": inputs.pop(),
        "baseline": baseline,
        "runs": runs,
    }


def build_dataset(out_dir):
    out_dir = Path(out_dir)
    selection = json.loads((out_dir / "selection.json").read_text(encoding="utf-8"))
    results = json.loads((out_dir / "results.json").read_text(encoding="utf-8"))
    pass_ids = list(results["pass_items"])
    items = {i["source_circuit_id"]: i for i in selection["accepted"]}
    db_path = out_dir / "experiment.db"
    before = sha256_file(db_path)
    connection = opt.open_readonly(db_path)
    try:
        circuits = [export_circuit(connection, items[c["source_circuit_id"]],
                                   c, pass_ids)
                    for c in results["circuits"]]
    finally:
        connection.close()
    if sha256_file(db_path) != before:
        raise ExportError("experiment.db changed while reading")
    return {
        "schema_version": SCHEMA_VERSION,
        "metric_profile": "experiment",
        "experiment": out_dir.name,
        "title": results.get("title"),
        "pass_set": results.get("pass_set", "abc"),
        "environment": results["environment"],
        "experiment_db_sha256": before,
        "passes": results["pass_items"],
        "circuits": circuits,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Export an experiment as a compact JSON dataset.")
    parser.add_argument("--out-dir", required=True,
                        help="Experiment directory (read-only).")
    parser.add_argument("--output", required=True,
                        help="JSON file to write; must not exist.")
    args = parser.parse_args(argv)
    output = Path(args.output)
    if output.exists():
        print(f"Error: output exists: {output}", file=sys.stderr)
        return 2
    try:
        dataset = build_dataset(args.out_dir)
    except (ExportError, LookupError, OSError, ValueError,
            opt.OptimizationError) as exc:
        print(f"Error: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2
    output.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(dataset, separators=(",", ":"))
    output.write_text(text + "\n", encoding="utf-8")
    runs = sum(len(c["runs"]) for c in dataset["circuits"])
    print(f"Experiment {dataset['experiment']} ({dataset['environment']}): "
          f"{len(dataset['circuits'])} circuits, {runs} runs, pass set "
          f"{', '.join(dataset['passes'])}.")
    print(f"experiment.db sha256 {dataset['experiment_db_sha256']} unchanged.")
    print(f"Written {output.name}: {len(text) + 1} bytes.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
