"""A small, reproducible MQT Bench pass-order experiment (stages 2C-2F).

Subcommands; all outputs go to one experiment directory.

  select  Read the source QCH database READ-ONLY (SQLite mode=ro, query_only),
          choose small circuits with a fixed rule, prepare a baseline in the
          gate set rz, sx, x, cx with a fixed transpiler configuration,
          verify it, and copy original and baseline circuits into a NEW
          experiment database. Writes ``selection.json``. The SHA-256, size
          and modification time of the source file are recorded before and
          after reading.
  reuse   Copy the circuits of an earlier experiment directory into a NEW
          one (earlier experiment.db read-only, hashes checked, baselines
          verified again). The source MQT database is not opened.
  run     Run all permutations of a pass set (``--pass-set abc``: A, B, C;
          ``abcef``: A, B, C, E, F; ``abcfg``: A, B, C, F, G) on every
          baseline, verify every final
          circuit, and write ``results.json`` and ``report.md``.
  report  Rebuild ``report.md`` from the experiment database and the JSON
          files through a read-only connection.

Passes (same configurations as examples/permutation_demo.py):

  A = CommutativeCancellation(basis_gates=[rz, sx, x, cx])
  B = Optimize1qGatesDecomposition(basis=[rz, sx, x, cx])
  C = CommutativeInverseCancellation()
  E = RemoveIdentityEquivalent()                       (pass set abcef)
  F = Optimize1qGatesSimpleCommutation(basis=[rz, sx, x, cx])  (abcef, abcfg)
  G = TwoQubitBlockResynthesis(basis_gates=[rz, sx, x, cx])    (abcfg)

Metric definitions used in this experiment:

  gates  = single + two + multi-qubit gates (measure, reset and barrier are
           NOT gates here; QCH's stored gate_count includes measure)
  2q     = two-qubit gates
  depth  = depth of the circuit with final measurements and barriers removed
  deltas = final minus baseline

Example (PowerShell, from the repository root):

  $Py  = ".\\.venv\\Scripts\\python.exe"
  $Src = "C:\\Users\\jilu\\Project\\QCDB\\Data\\mqt_bench_1000_test.db"
  $Out = ".\\demo\\stage2c_$(Get-Date -Format yyyyMMddHHmmss)"
  & $Py experiments\\mqt_pass_order.py select --source-db $Src --out-dir $Out
  & $Py experiments\\mqt_pass_order.py run --out-dir $Out
  & $Py experiments\\mqt_pass_order.py report --out-dir $Out

Stage 2F (five passes on the stage 2D circuits):

  $Out = ".\\demo\\stage2f_$(Get-Date -Format yyyyMMddHHmmss)"
  & $Py experiments\\mqt_pass_order.py reuse `
      --from-dir .\\demo\\stage2d_20261114165225 --out-dir $Out
  & $Py experiments\\mqt_pass_order.py run --out-dir $Out `
      --pass-set abcef --title "Stage 2F: five passes"

Stage 2G: the same with ``--pass-set abcfg`` (G = two-qubit block
re-synthesis) and ``experiments\\pass_set_compare.py --before <stage2f>
--after <stage2g>``.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import platform
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import qiskit
from qiskit import QuantumCircuit, qasm2, transpile
from qiskit.circuit import ControlFlowOp
from qiskit.quantum_info import Operator

from qch import QCHDatabase
from qch import batch
from qch import optimization as opt
from qch import permutations

EXPERIMENT_VERSION = "stage2c/1"

BASIS = ["rz", "sx", "x", "cx"]
NON_GATE_OPS = {"measure", "barrier"}

PASS_ITEMS = {
    "A": {"name": "CommutativeCancellation", "params": {"basis_gates": BASIS}},
    "B": {"name": "Optimize1qGatesDecomposition", "params": {"basis": BASIS}},
    "C": {"name": "CommutativeInverseCancellation", "params": {}},
}

# Stage 2F adds two registered passes. InverseCancellation was left out: in
# a design screen on the 12 stage 2D baselines it never changed a circuit
# after any prefix of any order (see docs/PROGRESS.md).
PASS_SETS = {
    "abc": PASS_ITEMS,
    "abcef": {
        **PASS_ITEMS,
        "E": {"name": "RemoveIdentityEquivalent", "params": {}},
        "F": {"name": "Optimize1qGatesSimpleCommutation",
              "params": {"basis": BASIS}},
    },
    # Stage 2G replaces E (position-insensitive in stage 2F) by the
    # two-qubit block re-synthesis step G.
    "abcfg": {
        **PASS_ITEMS,
        "F": {"name": "Optimize1qGatesSimpleCommutation",
              "params": {"basis": BASIS}},
        "G": {"name": "TwoQubitBlockResynthesis",
              "params": {"basis_gates": BASIS}},
    },
}
DEFAULT_PASS_SET = "abc"

TRANSPILE_CONFIG = {
    "function": "qiskit.transpile",
    "basis_gates": BASIS,
    "optimization_level": 0,
    "seed_transpiler": 20261114,
    "coupling_map": None,
    "layout_method": None,
    "routing_method": None,
}

SELECTION_DEFAULTS = {
    "count": 6,
    "min_qubits": 2,
    "max_qubits": 6,
    "max_source_gates": 400,
    "min_families": 3,
    "preferred_layer": "indep",
    "fallback_layers": ["nativegates", "mapped"],
}

DEFAULT_TITLE = "MQT Bench pass-order experiment"

EXPERIMENT_DB = "experiment.db"
SELECTION_FILE = "selection.json"
RESULTS_FILE = "results.json"
REPORT_FILE = "report.md"


class UnverifiableCircuit(Exception):
    """The circuit cannot be checked with the methods of this experiment."""


# ---------------------------------------------------------------------------
# Circuit helpers
# ---------------------------------------------------------------------------


def sha256_text(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def sha256_file(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def file_state(path):
    stat = os.stat(path)
    return {
        "sha256": sha256_file(path),
        "size": stat.st_size,
        "mtime_ns": stat.st_mtime_ns,
    }


def final_measurement_map(circuit):
    """Return ``{qubit index: clbit index}`` of the final measurements.

    Raises :class:`UnverifiableCircuit` if the circuit has control flow,
    resets, a measurement that is followed by a non-barrier operation on
    the same qubit, a qubit measured twice, or a clbit written twice.
    """
    for instruction in circuit.data:
        if isinstance(instruction.operation, ControlFlowOp):
            raise UnverifiableCircuit("classical control flow")
        if instruction.operation.name == "reset":
            raise UnverifiableCircuit("reset operation")

    mapping = {}
    used_clbits = set()
    measured_at = {}
    for position, instruction in enumerate(circuit.data):
        if instruction.operation.name != "measure":
            continue
        qubit = circuit.find_bit(instruction.qubits[0]).index
        clbit = circuit.find_bit(instruction.clbits[0]).index
        if qubit in mapping:
            raise UnverifiableCircuit(f"qubit {qubit} measured twice")
        if clbit in used_clbits:
            raise UnverifiableCircuit(f"clbit {clbit} written twice")
        mapping[qubit] = clbit
        used_clbits.add(clbit)
        measured_at[qubit] = position

    for position, instruction in enumerate(circuit.data):
        name = instruction.operation.name
        if name in NON_GATE_OPS:
            continue
        qubits = [circuit.find_bit(q).index for q in instruction.qubits]
        for qubit in qubits:
            if qubit in measured_at and position > measured_at[qubit]:
                raise UnverifiableCircuit(
                    f"operation {name} after the measurement of qubit {qubit}"
                )
    return mapping


def strip_final_measurements(circuit):
    """Copy without measurements and barriers (call after the map check)."""
    stripped = QuantumCircuit(*circuit.qregs, global_phase=circuit.global_phase)
    for instruction in circuit.data:
        if instruction.operation.name in NON_GATE_OPS:
            continue
        stripped.append(
            instruction.operation,
            [stripped.qubits[circuit.find_bit(q).index]
             for q in instruction.qubits],
        )
    return stripped


def experiment_metrics(circuit):
    """Gates exclude measure/barrier; depth is measured without them."""
    step = opt.compute_step_metrics(circuit)
    unitary = strip_final_measurements(circuit)
    return {
        "gates": step["single_qubit_gate_count"]
        + step["two_qubit_gate_count"]
        + step["multi_qubit_gate_count"],
        "two_qubit_gates": step["two_qubit_gate_count"],
        "depth": unitary.depth(),
        "num_qubits": step["num_qubits"],
        "measure_count": step["measure_count"],
        "barrier_count": step["barrier_count"],
        "reset_count": step["reset_count"],
        "qch_gate_count": step["gate_count"],
        "qch_depth": step["depth"],
        "ops": dict(sorted(circuit.count_ops().items())),
    }


def gate_set_violations(circuit):
    allowed = set(BASIS) | NON_GATE_OPS
    return sorted({
        instruction.operation.name for instruction in circuit.data
        if instruction.operation.name not in allowed
    })


def compare_to_reference(reference, candidate):
    """Check gate set, measurement mapping and unitary equivalence."""
    checks = {"gate_set_violations": gate_set_violations(candidate)}
    try:
        reference_map = final_measurement_map(reference)
        candidate_map = final_measurement_map(candidate)
    except UnverifiableCircuit as exc:
        checks.update(verified=False, reason=f"unverifiable: {exc}")
        return checks
    checks["measurement_map"] = {str(k): v for k, v in sorted(candidate_map.items())}
    checks["measurement_map_equal"] = reference_map == candidate_map
    checks["clbits_equal"] = reference.num_clbits == candidate.num_clbits
    checks["unitary_equivalent"] = bool(
        Operator(strip_final_measurements(candidate)).equiv(
            Operator(strip_final_measurements(reference))
        )
    )
    problems = []
    if checks["gate_set_violations"]:
        problems.append(f"gates outside basis {checks['gate_set_violations']}")
    if not checks["measurement_map_equal"]:
        problems.append("measurement mapping differs")
    if not checks["clbits_equal"]:
        problems.append("number of classical bits differs")
    if not checks["unitary_equivalent"]:
        problems.append("unitary not equivalent")
    checks["verified"] = not problems
    checks["reason"] = "; ".join(problems) if problems else None
    return checks


def prepare_baseline(original):
    baseline = transpile(
        original,
        basis_gates=TRANSPILE_CONFIG["basis_gates"],
        optimization_level=TRANSPILE_CONFIG["optimization_level"],
        seed_transpiler=TRANSPILE_CONFIG["seed_transpiler"],
    )
    if baseline.num_qubits != original.num_qubits:
        raise UnverifiableCircuit("baseline changed the number of qubits")
    # Store and re-read through QASM 2, exactly as the runs will see it.
    text = qasm2.dumps(baseline)
    return text, QuantumCircuit.from_qasm_str(text)


# ---------------------------------------------------------------------------
# select
# ---------------------------------------------------------------------------


def parse_name(name):
    """``MQTBench_...:family_layer_compiler_rest.qasm`` -> (family, layer)."""
    base = name.split(":", 1)[-1]
    for layer in ("indep", "nativegates", "mapped", "alg"):
        marker = f"_{layer}_"
        if marker in base:
            return base.split(marker, 1)[0], layer
    return None, None


def load_candidates(connection, params):
    rows = connection.execute(
        """
        SELECT c.id, c.name, c.qasm, f.num_qubits, f.gate_count
        FROM circuits AS c JOIN features AS f ON f.circuit_id = c.id
        WHERE f.num_qubits BETWEEN ? AND ?
        ORDER BY c.id
        """,
        (params["min_qubits"], params["max_qubits"]),
    ).fetchall()
    candidates = []
    for circuit_id, name, qasm, num_qubits, gate_count in rows:
        family, layer = parse_name(name)
        if family is None:
            continue
        if gate_count is not None and gate_count > params["max_source_gates"]:
            continue
        candidates.append({
            "source_circuit_id": circuit_id,
            "name": name,
            "family": family,
            "layer": layer,
            "num_qubits": num_qubits,
            "source_gate_count": gate_count,
            "qasm": qasm,
        })
    return candidates


def selection_order(candidates, layers):
    """Round-robin over families (alphabetical), smallest circuits first."""
    ordered = []
    for layer in layers:
        pool = [c for c in candidates if c["layer"] == layer]
        families = {}
        for item in sorted(
            pool,
            key=lambda c: (c["family"], c["num_qubits"],
                           c["source_gate_count"], c["source_circuit_id"]),
        ):
            families.setdefault(item["family"], []).append(item)
        while any(families.values()):
            for family in sorted(families):
                if families[family]:
                    ordered.append(families[family].pop(0))
    return ordered


def evaluate_candidate(candidate):
    original = QuantumCircuit.from_qasm_str(candidate["qasm"])
    opt.validate_supported_circuit(original)
    mapping = final_measurement_map(original)
    baseline_text, baseline = prepare_baseline(original)
    opt.validate_supported_circuit(baseline)
    checks = compare_to_reference(original, baseline)
    if not checks["verified"]:
        raise UnverifiableCircuit(checks["reason"])
    return {
        "original_metrics": experiment_metrics(original),
        "baseline_metrics": experiment_metrics(baseline),
        "original_measurement_map": {str(k): v for k, v in sorted(mapping.items())},
        "baseline_checks": checks,
        "baseline_qasm": baseline_text,
    }


def cmd_select(args):
    source = Path(args.source_db)
    out_dir = Path(args.out_dir)
    if not source.is_file():
        print(f"Error: source database not found: {source}", file=sys.stderr)
        return 2
    if out_dir.exists() and any(out_dir.iterdir()):
        print(f"Error: output directory is not empty: {out_dir}",
              file=sys.stderr)
        return 2

    params = dict(SELECTION_DEFAULTS)
    params["count"] = args.count
    print(f"Hashing source database (read-only): {source}")
    before = file_state(source)

    connection = opt.open_readonly(source)
    try:
        candidates = load_candidates(connection, params)
    finally:
        connection.close()

    layers = [params["preferred_layer"]] + params["fallback_layers"]
    excluded = []
    excluded_ids = {}
    for previous in args.exclude_selection or []:
        data = json.loads(Path(previous).read_text(encoding="utf-8"))
        for item in data["accepted"]:
            excluded_ids[item["source_circuit_id"]] = previous
    pool = []
    for candidate in candidates:
        if candidate["source_circuit_id"] in excluded_ids:
            excluded.append({
                "source_circuit_id": candidate["source_circuit_id"],
                "name": candidate["name"],
                "layer": candidate["layer"],
                "reason": "accepted in previous selection "
                          f"{Path(excluded_ids[candidate['source_circuit_id']]).as_posix()}",
            })
        elif candidate["layer"] not in layers:
            excluded.append({
                "source_circuit_id": candidate["source_circuit_id"],
                "name": candidate["name"],
                "layer": candidate["layer"],
                "reason": f"layer {candidate['layer']} not in {layers}",
            })
        else:
            pool.append(candidate)

    accepted, rejected = [], []
    for candidate in selection_order(pool, layers):
        if len(accepted) >= params["count"]:
            break
        try:
            evaluation = evaluate_candidate(candidate)
        except Exception as exc:  # every failure is a recorded rejection
            rejected.append({
                "source_circuit_id": candidate["source_circuit_id"],
                "name": candidate["name"],
                "layer": candidate["layer"],
                "reason": f"{type(exc).__name__}: {exc}",
            })
            continue
        accepted.append({**candidate, **evaluation})

    after = file_state(source)
    families = sorted({item["family"] for item in accepted})
    too_few = len(accepted) < params["count"] and not args.allow_fewer
    if not accepted or too_few or len(families) < params["min_families"]:
        print(f"Error: only {len(accepted)} circuits from {len(families)} "
              "families passed the selection rule; nothing was written.",
              file=sys.stderr)
        return 3
    if before != after:
        print("Error: the source database file changed during selection; "
              "nothing was written.", file=sys.stderr)
        return 3

    out_dir.mkdir(parents=True, exist_ok=True)
    db = QCHDatabase(out_dir / EXPERIMENT_DB)
    try:
        with tempfile.TemporaryDirectory() as tmp:
            for index, item in enumerate(accepted, start=1):
                stem = f"s{index}_src{item['source_circuit_id']}"
                item["experiment_original_name"] = f"{stem}:original"
                item["experiment_baseline_name"] = f"{stem}:baseline_rz_sx_x_cx"
                for kind, name, text in (
                    ("original", item["experiment_original_name"], item["qasm"]),
                    ("baseline", item["experiment_baseline_name"],
                     item["baseline_qasm"]),
                ):
                    path = Path(tmp) / f"{stem}_{kind}.qasm"
                    path.write_text(text, encoding="utf-8")
                    db.add_circuit(name, path)
                stored = db.get_circuit(item["experiment_original_name"])
                if sha256_text(stored) != sha256_text(item["qasm"]):
                    raise RuntimeError("Copied QASM does not match the source")
    finally:
        db.close()

    selection = {
        "experiment_version": EXPERIMENT_VERSION,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "environment": environment(),
        "source_database": {
            "path": str(source),
            "opened_with": "sqlite3 URI mode=ro + PRAGMA query_only=ON",
            "before": before,
            "after": after,
            "unchanged": before == after,
        },
        "selection_rule": {
            **params,
            "allow_fewer": bool(args.allow_fewer),
            "exclude_selection": [Path(p).as_posix()
                                  for p in args.exclude_selection or []],
            "candidate_filter": (
                "circuits joined with features; num_qubits within bounds; "
                "source gate_count <= max_source_gates; MQT name with a "
                "known layer"
            ),
            "order": (
                "preferred layer first, then fallback layers; within a layer "
                "round-robin over families in alphabetical order; within a "
                "family by (num_qubits, gate_count, circuit id)"
            ),
            "acceptance": (
                "parses, QCH-supported, final measurements without classical "
                "control, baseline uses only rz/sx/x/cx plus measure/barrier, "
                "baseline unitary equals the original (final measurements "
                "removed on copies) and measurement mapping is unchanged"
            ),
        },
        "baseline_preparation": TRANSPILE_CONFIG,
        "candidates_considered": len(candidates),
        "excluded": excluded,
        "not_reached": len(pool) - len(accepted) - len(rejected),
        "accepted": [
            {key: value for key, value in item.items()
             if key not in ("qasm", "baseline_qasm")}
            | {"source_qasm_sha256": sha256_text(item["qasm"]),
               "baseline_qasm_sha256": sha256_text(item["baseline_qasm"])}
            for item in accepted
        ],
        "rejected": rejected,
    }
    (out_dir / SELECTION_FILE).write_text(
        json.dumps(selection, indent=2), encoding="utf-8"
    )
    print(f"Selected {len(accepted)} circuits from {len(families)} families "
          f"({', '.join(families)}); rejected {len(rejected)}; "
          f"excluded {len(excluded)}.")
    for item in accepted:
        print(f"  src {item['source_circuit_id']:>4}  {item['layer']:<11} "
              f"{item['name']}  "
              f"qubits={item['num_qubits']}  "
              f"sha256={sha256_text(item['qasm'])[:16]}")
    print(f"Source database unchanged: {before == after} "
          f"(sha256 {before['sha256'][:16]}...)")
    print(f"Experiment directory: {out_dir}")
    return 0


def cmd_reuse(args):
    """Copy the circuits of an earlier experiment into a new one.

    The earlier experiment database is opened read-only; its file hash is
    compared before and after. Every copied QASM must match the hashes in
    the earlier selection.json, and every baseline is verified again
    against its original. The source MQT database is not opened.
    """
    from_dir = Path(args.from_dir)
    out_dir = Path(args.out_dir)
    if out_dir.exists() and any(out_dir.iterdir()):
        print(f"Error: output directory is not empty: {out_dir}",
              file=sys.stderr)
        return 2
    previous = json.loads((from_dir / SELECTION_FILE).read_text(encoding="utf-8"))
    db_from = from_dir / EXPERIMENT_DB
    before = file_state(db_from)
    connection = opt.open_readonly(db_from)
    copied = []
    try:
        for item in previous["accepted"]:
            texts = {}
            for kind in ("original", "baseline"):
                name = item[f"experiment_{kind}_name"]
                row = connection.execute(
                    "SELECT qasm FROM circuits WHERE name = ?", (name,)
                ).fetchone()
                if row is None:
                    raise RuntimeError(f"circuit {name} not in {db_from}")
                expected = item["source_qasm_sha256" if kind == "original"
                                else "baseline_qasm_sha256"]
                if sha256_text(row[0]) != expected:
                    raise RuntimeError(f"{name}: QASM hash differs from "
                                       "the earlier selection.json")
                texts[kind] = row[0]
            original = QuantumCircuit.from_qasm_str(texts["original"])
            baseline = QuantumCircuit.from_qasm_str(texts["baseline"])
            checks = compare_to_reference(original, baseline)
            metrics = experiment_metrics(baseline)
            if not checks["verified"] or metrics != item["baseline_metrics"]:
                raise RuntimeError(f"{item['experiment_baseline_name']}: "
                                   "baseline no longer verifies")
            copied.append((item, texts))
    except RuntimeError as exc:
        print(f"Error: {exc}; nothing was written.", file=sys.stderr)
        return 3
    finally:
        connection.close()
    after = file_state(db_from)
    if before != after:
        print("Error: the earlier experiment database changed while reading; "
              "nothing was written.", file=sys.stderr)
        return 3

    out_dir.mkdir(parents=True, exist_ok=True)
    db = QCHDatabase(out_dir / EXPERIMENT_DB)
    try:
        with tempfile.TemporaryDirectory() as tmp:
            for index, (item, texts) in enumerate(copied, start=1):
                for kind in ("original", "baseline"):
                    path = Path(tmp) / f"{index}_{kind}.qasm"
                    path.write_text(texts[kind], encoding="utf-8")
                    db.add_circuit(item[f"experiment_{kind}_name"], path)
    finally:
        db.close()

    selection = dict(previous)
    selection["created_at"] = datetime.now(timezone.utc).isoformat()
    selection["environment"] = environment()
    selection["reused_from"] = {
        "experiment": from_dir.name,
        "selection_sha256": sha256_file(from_dir / SELECTION_FILE),
        "experiment_db_before": before,
        "experiment_db_after": after,
        "unchanged": True,
        "checks": "QASM hashes equal the earlier selection.json; baselines "
                  "verified again against their originals",
    }
    (out_dir / SELECTION_FILE).write_text(json.dumps(selection, indent=2),
                                          encoding="utf-8")
    print(f"Reused {len(copied)} circuits from {from_dir.name}; "
          f"{EXPERIMENT_DB} unchanged (sha256 {before['sha256'][:16]}...).")
    for item, _ in copied:
        print(f"  src {item['source_circuit_id']:>4}  {item['name']}")
    print(f"Experiment directory: {out_dir}")
    return 0


def environment():
    return {
        "platform": f"{platform.system()} {platform.machine()}",
        "python": platform.python_version(),
        "qiskit": qiskit.__version__,
    }


# ---------------------------------------------------------------------------
# run
# ---------------------------------------------------------------------------


def verify_runs(connection, item, batch_result):
    baseline_name = item["experiment_baseline_name"]
    reference_qasm = connection.execute(
        "SELECT qasm FROM circuits WHERE name = ?", (baseline_name,)
    ).fetchone()[0]
    reference = QuantumCircuit.from_qasm_str(reference_qasm)
    baseline_metrics = experiment_metrics(reference)

    sequences = []
    for result in batch_result.results:
        entry = {
            "label": result.label,
            "run_id": result.run_id,
            "status": result.status,
            "pass_names": result.pass_names,
            "pass_time_recorded_s": result.pass_time_recorded_s,
            "pass_time_complete": result.pass_time_complete,
        }
        step0 = opt.load_step_circuit(connection, result.run_id, 0)
        entry["step0_matches_baseline"] = (
            experiment_metrics(step0)["ops"] == baseline_metrics["ops"]
            and Operator(strip_final_measurements(step0)).equiv(
                Operator(strip_final_measurements(reference))
            )
        )
        if result.succeeded:
            final = opt.load_step_circuit(
                connection, result.run_id, len(result.pass_names)
            )
            metrics = experiment_metrics(final)
            entry["final_metrics"] = metrics
            entry["deltas"] = {
                key: metrics[key] - baseline_metrics[key]
                for key in ("gates", "two_qubit_gates", "depth")
            }
            entry["checks"] = compare_to_reference(reference, final)
        else:
            entry["final_metrics"] = None
            entry["deltas"] = None
            entry["checks"] = {"verified": False,
                               "reason": f"run status {result.status}"}
        entry["valid"] = bool(
            entry["checks"]["verified"] and entry["step0_matches_baseline"]
        )
        sequences.append(entry)

    valid = [s for s in sequences if s["valid"]]
    best = {}
    for key in ("gates", "two_qubit_gates", "depth"):
        if valid:
            value = min(s["final_metrics"][key] for s in valid)
            best[key] = {"value": value, "labels": [
                s["label"] for s in valid if s["final_metrics"][key] == value
            ]}
        else:
            best[key] = {"value": None, "labels": []}
    outcomes = {
        tuple(s["final_metrics"][k] for k in ("gates", "two_qubit_gates", "depth"))
        for s in valid
    }
    return {
        "source_circuit_id": item["source_circuit_id"],
        "name": item["name"],
        "family": item["family"],
        "baseline_name": baseline_name,
        "baseline_metrics": baseline_metrics,
        "sequences": sequences,
        "best": best,
        "distinct_valid_outcomes": len(outcomes),
        "order_dependent": len(outcomes) > 1,
        "best_2q_and_depth_overlap": sorted(
            set(best["two_qubit_gates"]["labels"]) & set(best["depth"]["labels"])
        ),
        "invalid": [
            {"label": s["label"], "run_id": s["run_id"],
             "reason": s["checks"].get("reason")
             or ("step 0 differs from baseline"
                 if not s["step0_matches_baseline"] else None)}
            for s in sequences if not s["valid"]
        ],
    }


def cmd_run(args):
    out_dir = Path(args.out_dir)
    selection = json.loads((out_dir / SELECTION_FILE).read_text(encoding="utf-8"))
    if (out_dir / RESULTS_FILE).exists():
        print(f"Error: {RESULTS_FILE} already exists in {out_dir}",
              file=sys.stderr)
        return 2
    pass_items = PASS_SETS[args.pass_set]
    limit = math.factorial(len(pass_items))
    preview = permutations.preview_permutations(pass_items,
                                                max_permutations=limit)
    db = QCHDatabase(out_dir / EXPERIMENT_DB)
    batches = []
    try:
        for item in selection["accepted"]:
            _, result = permutations.run_permutation_batch(
                db, item["experiment_baseline_name"], pass_items,
                max_permutations=limit,
            )
            batches.append((item, result))
    finally:
        db.close()

    connection = opt.open_readonly(out_dir / EXPERIMENT_DB)
    try:
        circuits = [verify_runs(connection, item, result)
                    for item, result in batches]
    finally:
        connection.close()

    results = {
        "experiment_version": EXPERIMENT_VERSION,
        "title": args.title,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "environment": environment(),
        "pass_set": args.pass_set,
        "pass_items": pass_items,
        "orders": preview.labels,
        "total_runs": sum(len(c["sequences"]) for c in circuits),
        "metric_definitions": {
            "gates": "single + two + multi-qubit gates; measure, reset and "
                     "barrier excluded",
            "two_qubit_gates": "gates acting on exactly two qubits",
            "depth": "depth after removing final measurements and barriers",
            "deltas": "final minus baseline",
        },
        "circuits": circuits,
    }
    (out_dir / RESULTS_FILE).write_text(json.dumps(results, indent=2),
                                        encoding="utf-8")
    report = build_report(selection, results, results["title"])
    (out_dir / REPORT_FILE).write_text(report, encoding="utf-8")
    print(report)
    return 0


# ---------------------------------------------------------------------------
# report
# ---------------------------------------------------------------------------


def build_report(selection, results, title=None):
    lines = [
        f"# {title or results.get('title') or DEFAULT_TITLE}",
        "",
        f"Environment of the run: {results['environment']}",
        f"Source database unchanged: "
        f"{selection['source_database']['unchanged']} "
        f"(sha256 {selection['source_database']['before']['sha256']})",
        f"Orders: {', '.join(results['orders'])}; total runs: "
        f"{results['total_runs']}",
        "Gates exclude measure and barrier; depth without final "
        "measurements; deltas are final minus baseline.",
        "",
        "## Selected circuits",
        "",
        "| # | src id | name | family | qubits | measured | source QASM sha256 "
        "| original gates/2q/depth | baseline gates/2q/depth |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for index, item in enumerate(selection["accepted"], start=1):
        o, b = item["original_metrics"], item["baseline_metrics"]
        lines.append(
            f"| {index} | {item['source_circuit_id']} | "
            f"{item['name'].split(':', 1)[-1]} | {item['family']} | "
            f"{item['num_qubits']} | {o['measure_count']} | "
            f"{item['source_qasm_sha256'][:16]} | "
            f"{o['gates']}/{o['two_qubit_gates']}/{o['depth']} | "
            f"{b['gates']}/{b['two_qubit_gates']}/{b['depth']} |"
        )
    lines += ["", "## Results per circuit", ""]
    for circuit in results["circuits"]:
        b = circuit["baseline_metrics"]
        lines += [
            f"### src {circuit['source_circuit_id']}: "
            f"{circuit['name'].split(':', 1)[-1]}",
            "",
            f"Baseline: gates={b['gates']}, 2q={b['two_qubit_gates']}, "
            f"depth={b['depth']}, measure={b['measure_count']}, "
            f"barrier={b['barrier_count']}",
            "",
            "| order | run | status | valid | gates | d | 2q | d | depth | d "
            "| pass ms |",
            "|---|---|---|---|---|---|---|---|---|---|---|",
        ]
        for s in circuit["sequences"]:
            m, d = s["final_metrics"], s["deltas"]
            time_ms = ("-" if s["pass_time_recorded_s"] is None
                       else f"{s['pass_time_recorded_s'] * 1000:.3f}")
            if m is None:
                cells = "- | - | - | - | - | -"
            else:
                cells = (f"{m['gates']} | {d['gates']:+d} | "
                         f"{m['two_qubit_gates']} | {d['two_qubit_gates']:+d} | "
                         f"{m['depth']} | {d['depth']:+d}")
            lines.append(f"| {s['label']} | {s['run_id']} | {s['status']} | "
                         f"{'yes' if s['valid'] else 'NO'} | {cells} | "
                         f"{time_ms} |")
        best = circuit["best"]
        lines += [
            "",
            f"Order dependent: {circuit['order_dependent']} "
            f"({circuit['distinct_valid_outcomes']} distinct valid outcomes)",
            f"Best gates ({best['gates']['value']}): "
            f"{', '.join(best['gates']['labels'])}",
            f"Best 2q ({best['two_qubit_gates']['value']}): "
            f"{', '.join(best['two_qubit_gates']['labels'])}",
            f"Best depth ({best['depth']['value']}): "
            f"{', '.join(best['depth']['labels'])}",
            f"Orders best for both 2q and depth: "
            f"{', '.join(circuit['best_2q_and_depth_overlap']) or 'none'}",
        ]
        if circuit["invalid"]:
            lines.append("Excluded (failed or unverified): " + "; ".join(
                f"{x['label']} (run {x['run_id']}): {x['reason']}"
                for x in circuit["invalid"]
            ))
        lines.append("")
    lines += summary_lines(results)
    lines.append("")
    lines.append("Pass times are single measurements and are not a "
                 "performance ranking.")
    return "\n".join(lines) + "\n"


def summary_lines(results):
    """Short answers to the three questions of the experiment."""
    circuits = results["circuits"]
    short = {c["source_circuit_id"]: c["name"].split(":", 1)[-1]
             for c in circuits}
    dependent = [c for c in circuits if c["order_dependent"]]
    lines = [
        "## Summary",
        "",
        f"Valid runs: {sum(len(c['sequences']) - len(c['invalid']) for c in circuits)}"
        f" of {results['total_runs']}; excluded: "
        f"{sum(len(c['invalid']) for c in circuits)}.",
        "",
        "1. Circuits whose metrics depend on the order: "
        + (", ".join(f"src {c['source_circuit_id']} ({short[c['source_circuit_id']]})"
                     for c in dependent) or "none"),
        "",
        "2. Best order by two-qubit gates vs by depth:",
    ]
    for c in circuits:
        best_2q = set(c["best"]["two_qubit_gates"]["labels"])
        best_depth = set(c["best"]["depth"]["labels"])
        if best_2q == best_depth:
            verdict = "same set of orders"
        elif best_2q & best_depth:
            verdict = ("overlap: "
                       + ", ".join(sorted(best_2q & best_depth)))
        else:
            verdict = "no order is best for both"
        lines.append(f"   - src {c['source_circuit_id']}: {verdict}")
    lines += ["", "3. Gain against the baseline (valid runs, best per metric):"]
    for c in circuits:
        valid = [s for s in c["sequences"] if s["valid"]]
        if not valid:
            lines.append(f"   - src {c['source_circuit_id']}: no valid run")
            continue
        gains = {
            key: min(s["deltas"][key] for s in valid)
            for key in ("gates", "two_qubit_gates", "depth")
        }
        improving = sorted(
            s["label"] for s in valid
            if any(s["deltas"][key] < 0 for key in gains)
        )
        lines.append(
            f"   - src {c['source_circuit_id']}: best deltas gates "
            f"{gains['gates']:+d}, 2q {gains['two_qubit_gates']:+d}, depth "
            f"{gains['depth']:+d}; orders with any reduction: "
            f"{', '.join(improving) or 'none'}"
        )
    return lines


def cmd_report(args):
    out_dir = Path(args.out_dir)
    selection = json.loads((out_dir / SELECTION_FILE).read_text(encoding="utf-8"))
    results = json.loads((out_dir / RESULTS_FILE).read_text(encoding="utf-8"))
    connection = opt.open_readonly(out_dir / EXPERIMENT_DB)
    try:
        for circuit in results["circuits"]:
            labeled = {s["label"]: s["run_id"] for s in circuit["sequences"]}
            stored = batch.summarize_runs(
                connection, circuit["baseline_name"], labeled
            )
            for row, sequence in zip(stored.rows(), circuit["sequences"]):
                if row["status"] != sequence["status"]:
                    raise RuntimeError(
                        f"Stored status differs for run {sequence['run_id']}"
                    )
    finally:
        connection.close()
    print(build_report(selection, results, args.title))
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="MQT Bench pass-order experiment."
    )
    sub = parser.add_subparsers(dest="command", required=True)
    select = sub.add_parser("select", help="Read-only selection and baseline.")
    select.add_argument("--source-db", required=True)
    select.add_argument("--out-dir", required=True)
    select.add_argument("--count", type=int,
                        default=SELECTION_DEFAULTS["count"])
    select.add_argument(
        "--exclude-selection", action="append", metavar="SELECTION_JSON",
        help="Skip circuits accepted in an earlier selection.json "
             "(may be given several times).")
    select.add_argument(
        "--allow-fewer", action="store_true",
        help="Treat --count as a maximum when the pool is exhausted.")
    select.set_defaults(func=cmd_select)
    reuse = sub.add_parser(
        "reuse", help="Copy the circuits of an earlier experiment (read-only).")
    reuse.add_argument("--from-dir", required=True)
    reuse.add_argument("--out-dir", required=True)
    reuse.set_defaults(func=cmd_reuse)
    run = sub.add_parser("run", help="Run all permutations and verify.")
    run.add_argument("--out-dir", required=True)
    run.add_argument("--pass-set", choices=sorted(PASS_SETS),
                     default=DEFAULT_PASS_SET,
                     help="abc: A, B, C (stages 2C, 2D); abcef: adds "
                          "E and F (stage 2F); abcfg: A, B, C, F and G "
                          "(stage 2G). All orders are run.")
    run.add_argument("--title", default=DEFAULT_TITLE,
                     help="Heading of report.md, e.g. 'Stage 2D ...'.")
    run.set_defaults(func=cmd_run)
    report = sub.add_parser("report", help="Rebuild the report (read-only).")
    report.add_argument("--out-dir", required=True)
    report.add_argument("--title", default=None,
                        help="Heading; defaults to the title stored in "
                             "results.json, else the generic title.")
    report.set_defaults(func=cmd_report)
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
