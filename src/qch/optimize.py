"""Optimize one circuit with registered passes and return the best result.

Stage 4A. One call takes a circuit, runs registered Qiskit passes in many
orders, verifies the results and returns the best circuit for a
lexicographic objective, together with the ties, the Pareto front and
every candidate::

    from qch.optimize import optimize_circuit

    result = optimize_circuit("input.qasm")
    print(result.summary())
    best_qasm = result.best.qasm

Steps:

1. The input (``QuantumCircuit``, OpenQASM 2 text or a ``.qasm`` path) is
   checked: no parameters, classical variables or control flow, and
   measurements only at the end of each qubit.
2. It is translated to ``basis_gates`` with ``transpile(...,
   optimization_level=0)``; this baseline is the common input of every
   order and is itself a candidate (the order with no pass), so the best
   result is never worse than the baseline.
3. Search:
   * ``permutations`` runs every order of the pass set. Orders that start
     with the same passes share these steps (prefix cache), so 120 orders
     of five passes need 325 pass applications instead of 600.
   * ``greedy`` applies, step by step, the pass that gives the best result
     for the objective, until no pass improves it.
   * ``fixed`` runs one given order, optionally repeated until the result
     no longer changes.
4. Every distinct result is verified against the baseline (``exact``:
   unitary equivalence up to global phase on copies without final
   measurements, plus the measurement mapping; ``sampled``: random input
   states; ``none``). Failed results are excluded; unverified results are
   marked.
5. Candidates are ranked by the objective, compared metric by metric in
   the given priority (default two-qubit gates, then depth, then total
   gates). There is no weighted score.

Metrics: ``gates`` counts single-, two- and multi-qubit gates (measure,
reset and barrier are not gates); ``two_qubit_gates`` counts gates on
exactly two qubits; ``depth`` is measured after removing final
measurements and barriers.

Nothing is stored unless ``db`` (a :class:`qch.QCHDatabase`) is given. Then
the baseline is stored as a circuit and every candidate order is recorded
with :func:`qch.optimization.run_pass_sequence`, so each candidate has a
run ID and every intermediate step can be restored.

With ``TwoQubitBlockResynthesis`` (G), gates and depth can differ slightly
between platforms; two-qubit counts are reproducible (docs/PROGRESS.md,
stages 2G and 2H). The result records the platform.

Command line::

    python -m qch.optimize input.qasm --out best.qasm --report report.json
"""

from __future__ import annotations

import argparse
import hashlib
import itertools
import json
import math
import platform
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import qiskit
from qiskit import QuantumCircuit, qasm2, transpile
from qiskit.circuit import ControlFlowOp
from qiskit.quantum_info import Operator, Statevector, random_statevector
from qiskit.transpiler import PassManager

from . import objectives
from . import optimization as opt

METRICS = ("gates", "two_qubit_gates", "depth")
DEFAULT_OBJECTIVE = ("two_qubit_gates", "depth", "gates")
DEFAULT_BASIS = ("rz", "sx", "x", "cx")
SEARCHES = ("permutations", "greedy", "fixed")
VERIFY_MODES = ("auto", "exact", "sampled", "none")
EXACT_MAX_QUBITS = 10
SAMPLED_MAX_QUBITS = 20
SAMPLED_STATES = 8
SAMPLED_SEED = 20261115
SAMPLED_TOLERANCE = 1e-8
TRANSPILE_SEED = 20261114
DEFAULT_MAX_RUNS = 120
DEFAULT_TIME_LIMIT_S = 60.0
GREEDY_MAX_STEPS = 20
FIXED_MAX_ROUNDS = 10
NON_GATES = ("measure", "barrier", "reset")

VERIFIED_EXACT = "exact"
VERIFIED_SAMPLED = "sampled"
UNVERIFIED = "unverified"
FAILED = "failed"

#: Letter -> pass name; parameters that need the basis are filled in.
PASS_LETTERS = {
    "A": "CommutativeCancellation",
    "B": "Optimize1qGatesDecomposition",
    "C": "CommutativeInverseCancellation",
    "F": "Optimize1qGatesSimpleCommutation",
    "G": "TwoQubitBlockResynthesis",
}
PASS_SETS = {
    "default": ("A", "B", "C", "F", "G"),
    "light": ("A", "B", "C"),
    "single-qubit": ("B", "F"),
}


class OptimizeError(opt.OptimizationError, ValueError):
    """The request cannot be carried out (input, options or limits)."""


# ---------------------------------------------------------------------------
# Results
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Candidate:
    """One result: the circuit after an order of passes."""

    order: tuple
    circuit: QuantumCircuit = field(repr=False, compare=False)
    metrics: dict
    verification: str
    reason: str | None = None
    run_id: int | None = None

    @property
    def label(self):
        return "->".join(self.order) if self.order else "(no pass)"

    @property
    def qasm(self):
        return qasm2.dumps(self.circuit)

    def as_dict(self, include_qasm=False):
        data = {
            "order": list(self.order),
            "label": self.label,
            "metrics": dict(self.metrics),
            "verification": self.verification,
            "reason": self.reason,
            "run_id": self.run_id,
        }
        if include_qasm:
            data["qasm"] = self.qasm
        return data


@dataclass
class OptimizationResult:
    best: Candidate
    ties: list
    pareto_front: list
    candidates: list
    excluded: list
    baseline: Candidate
    input_metrics: dict
    objective: tuple
    passes: dict
    search: str
    verify: str
    complete: bool
    pass_applications: int
    elapsed_s: float
    environment: dict
    circuit_name: str | None = None
    basis_gates: tuple = DEFAULT_BASIS
    best_steps: list = field(default_factory=list)
    notes: list = field(default_factory=list)

    def improvement(self):
        """Best minus baseline for every metric (negative is better)."""
        return {m: self.best.metrics[m] - self.baseline.metrics[m]
                for m in METRICS}

    def to_dict(self):
        return {
            "objective": list(self.objective),
            "passes": self.passes,
            "search": self.search,
            "verify": self.verify,
            "complete": self.complete,
            "pass_applications": self.pass_applications,
            "elapsed_s": round(self.elapsed_s, 3),
            "environment": self.environment,
            "circuit_name": self.circuit_name,
            "basis_gates": list(self.basis_gates),
            "input_metrics": self.input_metrics,
            "baseline": self.baseline.as_dict(),
            "best": self.best.as_dict(include_qasm=True),
            "improvement": self.improvement(),
            "best_steps": [dict(step) for step in self.best_steps],
            "ties": [c.as_dict() for c in self.ties],
            "pareto_front": [c.as_dict() for c in self.pareto_front],
            "candidates": [c.as_dict() for c in self.candidates],
            "excluded": [c.as_dict() for c in self.excluded],
            "notes": list(self.notes),
        }

    def summary(self):
        def triple(metrics):
            return "/".join(str(metrics[m]) for m in METRICS)

        lines = [
            f"Objective: {' > '.join(self.objective)} (gates/2q/depth below)",
            "Passes: " + ", ".join(f"{k}={v['name']}" for k, v in self.passes.items()),
            f"Search: {self.search}; {len(self.candidates)} candidates, "
            f"{self.pass_applications} pass applications, "
            f"{self.elapsed_s:.2f} s{'' if self.complete else ' (stopped early)'}",
            f"Input (own gate set): {triple(self.input_metrics)}; baseline in "
            f"{','.join(self.basis_gates)}: "
            f"{triple(self.baseline.metrics)}",
            f"Best: {self.best.label}: {triple(self.best.metrics)} "
            f"({self.best.verification})",
        ]
        if self.ties:
            lines.append("Ties: " + ", ".join(c.label for c in self.ties))
        lines.append(f"Pareto front: {len(self.pareto_front)} candidates")
        if self.excluded:
            lines.append("Excluded: " + "; ".join(
                f"{c.label} ({c.reason})" for c in self.excluded))
        lines.extend(self.notes)
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Circuit helpers
# ---------------------------------------------------------------------------


def load_circuit(source):
    """``QuantumCircuit``, OpenQASM 2 text or a path to a ``.qasm`` file."""
    if isinstance(source, QuantumCircuit):
        return source.copy()
    if isinstance(source, Path) or (
        isinstance(source, str) and "OPENQASM" not in source
        and "\n" not in source
    ):
        path = Path(source)
        if not path.is_file():
            raise OptimizeError(f"QASM file not found: {path}")
        text = path.read_text(encoding="utf-8")
    elif isinstance(source, str):
        text = source
    else:
        raise OptimizeError("expected a QuantumCircuit, QASM text or a path")
    try:
        return QuantumCircuit.from_qasm_str(text)
    except Exception as exc:  # qiskit raises several parser error types
        raise OptimizeError(f"cannot parse OpenQASM 2: {exc}") from None


def final_measurement_map(circuit):
    """``{qubit: clbit}`` of the measurements, which must be final."""
    measured_at = {}
    mapping = {}
    for position, instruction in enumerate(circuit.data):
        operation = instruction.operation
        if isinstance(operation, ControlFlowOp):
            raise OptimizeError("classical control flow is not supported")
        if operation.name == "reset":
            raise OptimizeError("reset is not supported")
        qubits = [circuit.find_bit(q).index for q in instruction.qubits]
        if operation.name == "measure":
            qubit = qubits[0]
            clbit = circuit.find_bit(instruction.clbits[0]).index
            if qubit in mapping or clbit in mapping.values():
                raise OptimizeError("a qubit or classical bit is measured twice")
            mapping[qubit] = clbit
            measured_at[qubit] = position
        elif operation.name != "barrier":
            for qubit in qubits:
                if qubit in measured_at:
                    raise OptimizeError(
                        f"operation {operation.name} after the measurement of "
                        f"qubit {qubit}; only final measurements are supported")
    return mapping


def without_final_operations(circuit):
    """Copy without measurements and barriers (after the checks above)."""
    stripped = QuantumCircuit(*circuit.qregs, global_phase=circuit.global_phase)
    for instruction in circuit.data:
        if instruction.operation.name in ("measure", "barrier"):
            continue
        stripped.append(instruction.operation,
                        [stripped.qubits[circuit.find_bit(q).index]
                         for q in instruction.qubits])
    return stripped


def circuit_metrics(circuit):
    step = opt.compute_step_metrics(circuit)
    return {
        "gates": step["single_qubit_gate_count"] + step["two_qubit_gate_count"]
        + step["multi_qubit_gate_count"],
        "two_qubit_gates": step["two_qubit_gate_count"],
        "depth": without_final_operations(circuit).depth(),
    }


def objective_key(metrics, objective):
    return tuple(metrics[m] for m in objective)


# ---------------------------------------------------------------------------
# Verification
# ---------------------------------------------------------------------------


class _Verifier:
    def __init__(self, reference, mode, basis):
        self.mode = mode
        self.basis = set(basis)
        self.mapping = final_measurement_map(reference)
        self.num_clbits = reference.num_clbits
        unitary = without_final_operations(reference)
        n = reference.num_qubits
        if mode == "auto":
            mode = ("exact" if n <= EXACT_MAX_QUBITS else
                    "sampled" if n <= SAMPLED_MAX_QUBITS else "none")
        if mode == "exact" and n > EXACT_MAX_QUBITS:
            raise OptimizeError(f"exact verification supports at most "
                                f"{EXACT_MAX_QUBITS} qubits")
        if mode == "sampled" and n > SAMPLED_MAX_QUBITS:
            raise OptimizeError(f"sampled verification supports at most "
                                f"{SAMPLED_MAX_QUBITS} qubits")
        self.effective = mode
        if mode == "exact":
            self.reference = Operator(unitary)
        elif mode == "sampled":
            rng = np.random.default_rng(SAMPLED_SEED)
            self.states = [random_statevector(2 ** n, seed=int(rng.integers(2**31)))
                           for _ in range(SAMPLED_STATES)]
            self.expected = [state.evolve(unitary) for state in self.states]

    def check(self, circuit):
        """Return (status, reason)."""
        outside = sorted({i.operation.name for i in circuit.data}
                         - self.basis - {"measure", "barrier"})
        if outside:
            return FAILED, f"gates outside the basis: {outside}"
        try:
            mapping = final_measurement_map(circuit)
        except OptimizeError as exc:
            return FAILED, str(exc)
        if mapping != self.mapping or circuit.num_clbits != self.num_clbits:
            return FAILED, "measurement mapping changed"
        if self.effective == "none":
            return UNVERIFIED, None
        unitary = without_final_operations(circuit)
        if self.effective == "exact":
            if Operator(unitary).equiv(self.reference):
                return VERIFIED_EXACT, None
            return FAILED, "not unitarily equivalent"
        phases = []
        for state, expected in zip(self.states, self.expected):
            overlap = complex(np.vdot(expected.data, state.evolve(unitary).data))
            if abs(abs(overlap) - 1) > SAMPLED_TOLERANCE:
                return FAILED, "output differs on a random input state"
            phases.append(overlap / abs(overlap))
        if max(abs(p - phases[0]) for p in phases) > 1e-6:
            return FAILED, "output phase differs between input states"
        return VERIFIED_SAMPLED, None


# ---------------------------------------------------------------------------
# Passes and search
# ---------------------------------------------------------------------------


def resolve_passes(pass_set, basis):
    """``{letter: {"name", "params"}}`` for a named set, letters or specs."""
    basis = list(basis)
    params = {
        "CommutativeCancellation": {"basis_gates": basis},
        "Optimize1qGatesDecomposition": {"basis": basis},
        "Optimize1qGatesSimpleCommutation": {"basis": basis},
        "TwoQubitBlockResynthesis": {"basis_gates": basis},
    }
    if isinstance(pass_set, str) and pass_set not in PASS_SETS and \
            pass_set.isalpha() and pass_set.isupper():
        pass_set = tuple(pass_set)  # letters, for example "ABF"
    if isinstance(pass_set, str):
        if pass_set not in PASS_SETS:
            raise OptimizeError(f"unknown pass set {pass_set!r}; choose one of "
                                f"{sorted(PASS_SETS)}")
        pass_set = PASS_SETS[pass_set]
    items = {}
    if isinstance(pass_set, dict):
        for key, spec in pass_set.items():
            items[str(key)] = {"name": spec["name"],
                               "params": dict(spec.get("params", {}))}
    else:
        for letter in pass_set:
            if letter not in PASS_LETTERS:
                raise OptimizeError(f"unknown pass letter {letter!r}; known: "
                                    f"{sorted(PASS_LETTERS)}")
            name = PASS_LETTERS[letter]
            items[letter] = {"name": name, "params": dict(params.get(name, {}))}
    if not items:
        raise OptimizeError("the pass set is empty")
    if any("->" in key or not key.strip() for key in items):
        raise OptimizeError("pass identifiers must be non-empty without '->'")
    prepared = opt.prepare_pass_sequence(list(items.values()))
    return items, {key: p for key, p in zip(items, prepared)}


class _Runner:
    """Applies passes with a prefix cache, a run budget and a time limit."""

    def __init__(self, baseline, prepared, deadline):
        self.prepared = prepared
        self.deadline = deadline
        self.cache = {(): baseline}
        self.applications = 0
        self.timed_out = False

    def run(self, order):
        order = tuple(order)
        if order in self.cache:
            return self.cache[order]
        parent = self.run(order[:-1])
        if parent is None:
            return None
        if time.monotonic() > self.deadline:
            self.timed_out = True
            return None
        circuit = PassManager([self.prepared[order[-1]].build()]).run(parent)
        self.applications += 1
        self.cache[order] = circuit
        return circuit


def _search_permutations(runner, keys, max_runs):
    count = math.factorial(len(keys))
    if count > max_runs:
        raise OptimizeError(
            f"{len(keys)} passes have {count} orders, more than max_runs="
            f"{max_runs}; raise max_runs or use search='greedy'")
    orders = []
    for order in itertools.permutations(keys):
        if runner.run(order) is None:
            break
        orders.append(order)
    return orders


def _search_greedy(runner, keys, objective, max_steps):
    order = ()
    current = objective_key(circuit_metrics(runner.run(order)), objective)
    for _ in range(max_steps):
        trials = []
        for key in keys:
            circuit = runner.run(order + (key,))
            if circuit is None:
                return [order]
            trials.append((objective_key(circuit_metrics(circuit), objective),
                           keys.index(key), key))
        best_key, _, best_pass = min(trials)
        if best_key >= current:
            break
        order, current = order + (best_pass,), best_key
    return [order]


def _search_fixed(runner, order, repeat):
    rounds = FIXED_MAX_ROUNDS if repeat else 1
    current = ()
    previous = opt.representation_fingerprint(runner.run(current))
    for _ in range(rounds):
        candidate = current + tuple(order)
        circuit = runner.run(candidate)
        if circuit is None:
            break
        fingerprint = opt.representation_fingerprint(circuit)
        if current and fingerprint == previous:
            break  # the last round changed nothing; keep the shorter order
        current, previous = candidate, fingerprint
    return [current]


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------


def optimize_circuit(
    circuit,
    *,
    objective=DEFAULT_OBJECTIVE,
    pass_set="default",
    search="permutations",
    order=None,
    repeat_until_stable=False,
    basis_gates=DEFAULT_BASIS,
    max_runs=DEFAULT_MAX_RUNS,
    time_limit_s=DEFAULT_TIME_LIMIT_S,
    verify="auto",
    db=None,
    name=None,
):
    """Run passes on ``circuit`` and return an :class:`OptimizationResult`.

    ``objective`` is a metric name or a tuple of names from ``gates``,
    ``two_qubit_gates`` and ``depth``, compared in that priority.
    ``pass_set`` is a named set (``default``: A, B, C, F, G; ``light``:
    A, B, C; ``single-qubit``: B, F), letters such as ``"ABF"``, or a mapping
    ``{id: {"name": ..., "params": {...}}}`` of registered passes.
    ``order`` (for ``search="fixed"``) is a sequence of pass ids.
    """
    started = time.monotonic()
    if isinstance(objective, str):
        objective = (objective,)
    objective = tuple(objective)
    if not objective or len(set(objective)) != len(objective) or any(
            m not in METRICS for m in objective):
        raise OptimizeError(f"objective must be distinct names from {METRICS}")
    if search not in SEARCHES:
        raise OptimizeError(f"search must be one of {SEARCHES}")
    if verify not in VERIFY_MODES:
        raise OptimizeError(f"verify must be one of {VERIFY_MODES}")
    if type(max_runs) is not int or max_runs < 1:
        raise OptimizeError("max_runs must be a positive integer")
    if time_limit_s is not None and time_limit_s <= 0:
        raise OptimizeError("time_limit_s must be positive")

    source = load_circuit(circuit)
    opt.validate_supported_circuit(source)
    final_measurement_map(source)
    items, prepared = resolve_passes(pass_set, basis_gates)
    keys = list(items)
    if search == "fixed":
        if not order:
            raise OptimizeError("search='fixed' needs an order")
        order = tuple(order)
        unknown = [k for k in order if k not in prepared]
        if unknown:
            raise OptimizeError(f"order uses passes outside the pass set: {unknown}")
    elif order:
        raise OptimizeError("order is only used with search='fixed'")

    try:
        baseline_circuit = transpile(source, basis_gates=list(basis_gates),
                                     optimization_level=0,
                                     seed_transpiler=TRANSPILE_SEED)
    except Exception as exc:
        raise OptimizeError(f"cannot translate to {list(basis_gates)}: {exc}") from None
    # Re-read through QASM 2 so that the stored and the optimized input agree.
    baseline_circuit = QuantumCircuit.from_qasm_str(qasm2.dumps(baseline_circuit))
    verifier = _Verifier(source, verify, basis_gates)
    status, reason = verifier.check(baseline_circuit)
    if status == FAILED:
        raise OptimizeError(f"the translated input does not verify: {reason}")

    deadline = started + (time_limit_s if time_limit_s else float("inf"))
    runner = _Runner(baseline_circuit, prepared, deadline)
    if search == "permutations":
        orders = _search_permutations(runner, keys, max_runs)
    elif search == "greedy":
        orders = _search_greedy(runner, keys, objective, GREEDY_MAX_STEPS)
    else:
        orders = _search_fixed(runner, order, repeat_until_stable)

    checked = {}
    candidates, excluded = [], []
    for candidate_order in [()] + [o for o in orders if o != ()]:
        circuit_out = runner.cache[candidate_order]
        fingerprint = opt.representation_fingerprint(circuit_out)
        if fingerprint not in checked:
            checked[fingerprint] = verifier.check(circuit_out)
        status, reason = checked[fingerprint]
        candidate = Candidate(candidate_order, circuit_out,
                              circuit_metrics(circuit_out), status, reason)
        (excluded if status == FAILED else candidates).append(candidate)

    circuit_name = None
    if db is not None:
        circuit_name, candidates = _record(db, name, baseline_circuit,
                                           items, candidates)

    ranked = sorted(candidates, key=lambda c: (
        objective_key(c.metrics, objective), len(c.order), c.label))
    best = ranked[0]
    ties = [c for c in ranked[1:]
            if objective_key(c.metrics, objective)
            == objective_key(best.metrics, objective)]
    view = objectives.build_view(
        [{"label": c.label, "run_id": c.run_id, "status": "SUCCEEDED",
          "metrics": c.metrics, "input_id": "baseline"} for c in candidates],
        METRICS,
    )
    front_labels = {c.label for c in view.pareto_front}
    pareto = [c for c in ranked if c.label in front_labels]

    notes = []
    if runner.timed_out:
        notes.append(f"Stopped at the time limit of {time_limit_s} s; "
                     "only finished orders were compared.")
    if verifier.effective == "none":
        notes.append("Results were not verified (verify='none' or too many "
                     "qubits); they are marked 'unverified'.")
    if "TwoQubitBlockResynthesis" in {v["name"] for v in items.values()}:
        notes.append("With TwoQubitBlockResynthesis, gates and depth can differ "
                     "slightly between platforms; two-qubit counts are "
                     "reproducible.")
    baseline_candidate = next(c for c in candidates + excluded if c.order == ())
    best_steps = [
        {"step": i, "pass": best.order[i - 1] if i else None,
         **circuit_metrics(runner.cache[best.order[:i]])}
        for i in range(len(best.order) + 1)
    ]
    return OptimizationResult(
        best=best,
        ties=ties,
        pareto_front=pareto,
        candidates=ranked,
        excluded=excluded,
        baseline=baseline_candidate,
        input_metrics=circuit_metrics(source),
        objective=objective,
        passes=items,
        search=search,
        verify=verifier.effective,
        complete=not runner.timed_out,
        pass_applications=runner.applications,
        elapsed_s=time.monotonic() - started,
        environment={"platform": f"{platform.system()} {platform.machine()}",
                     "python": platform.python_version(),
                     "qiskit": qiskit.__version__},
        circuit_name=circuit_name,
        basis_gates=tuple(basis_gates),
        best_steps=best_steps,
        notes=notes,
    )


def _record(db, name, baseline_circuit, items, candidates):
    """Store the baseline and record every candidate order as a run."""
    text = qasm2.dumps(baseline_circuit)
    if name is None:
        name = "optimize:" + hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]
    stored = db.get_circuit(name)
    if stored is None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "baseline.qasm"
            path.write_text(text, encoding="utf-8")
            db.add_circuit(name, path)
    elif stored != text:
        raise OptimizeError(f"circuit name {name!r} exists with different QASM")
    recorded = []
    for candidate in candidates:
        if not candidate.order:
            recorded.append(candidate)
            continue
        result = opt.run_pass_sequence(
            db, name, [items[k] for k in candidate.order],
            label=f"optimize {candidate.label}")
        if result.status != "SUCCEEDED":
            raise OptimizeError(f"recording {candidate.label} failed: "
                                f"{result.status} {result.error_message}")
        final = opt.load_step_circuit(db, result.run_id, len(candidate.order))
        if (opt.representation_fingerprint(final)
                != opt.representation_fingerprint(candidate.circuit)):
            raise OptimizeError(f"recorded run of {candidate.label} differs "
                                "from the search result")
        recorded.append(Candidate(candidate.order, candidate.circuit,
                                  candidate.metrics, candidate.verification,
                                  candidate.reason, result.run_id))
    return name, recorded


# ---------------------------------------------------------------------------
# Command line
# ---------------------------------------------------------------------------


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="python -m qch.optimize",
        description="Run registered passes on a circuit and write the best one.")
    parser.add_argument("input", help="OpenQASM 2 file")
    parser.add_argument("--objective", default=",".join(DEFAULT_OBJECTIVE),
                        help="comma-separated priority, e.g. "
                             "two_qubit_gates,depth,gates")
    parser.add_argument("--pass-set", default="default",
                        help=f"one of {sorted(PASS_SETS)} or letters such as ABF")
    parser.add_argument("--search", choices=SEARCHES, default="permutations")
    parser.add_argument("--order", help="letters for --search fixed, e.g. BAG")
    parser.add_argument("--repeat", action="store_true",
                        help="with --search fixed: repeat until stable")
    parser.add_argument("--basis", default=",".join(DEFAULT_BASIS))
    parser.add_argument("--max-runs", type=int, default=DEFAULT_MAX_RUNS)
    parser.add_argument("--time-limit", type=float, default=DEFAULT_TIME_LIMIT_S)
    parser.add_argument("--verify", choices=VERIFY_MODES, default="auto")
    parser.add_argument("--db", help="record every candidate in this QCH database")
    parser.add_argument("--name", help="circuit name in --db")
    parser.add_argument("--out", help="write the best circuit as OpenQASM 2")
    parser.add_argument("--report", help="write a JSON report")
    args = parser.parse_args(argv)

    pass_set = args.pass_set
    db = None
    try:
        if args.db:
            from .database import QCHDatabase
            db = QCHDatabase(args.db)
        result = optimize_circuit(
            Path(args.input),
            objective=tuple(m.strip() for m in args.objective.split(",")),
            pass_set=pass_set,
            search=args.search,
            order=tuple(args.order) if args.order else None,
            repeat_until_stable=args.repeat,
            basis_gates=tuple(g.strip() for g in args.basis.split(",")),
            max_runs=args.max_runs,
            time_limit_s=args.time_limit,
            verify=args.verify,
            db=db,
            name=args.name,
        )
    except opt.OptimizationError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2
    finally:
        if db is not None:
            db.close()
    print(result.summary())
    for path, content in ((args.out, result.best.qasm),
                          (args.report, json.dumps(result.to_dict(), indent=2))):
        if path:
            Path(path).write_text(content, encoding="utf-8")
            print(f"Written: {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
