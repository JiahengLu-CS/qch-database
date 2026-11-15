"""Batch comparison of explicit optimization pass sequences (stage 2A).

Runs several named, user-specified pass sequences on ONE stored circuit
with :func:`qch.optimization.run_pass_sequence` and returns a comparison
table. Nothing new is stored: every sequence is an ordinary optimization
run, and its steps and snapshots are queried through its run ID.

Semantics:

* Every sequence starts from the stored input circuit. The single-sequence
  executor parses ``circuits.qasm`` again for each run, so no sequence
  sees the output of another one.
* Sequences run in the order given; their pass order, parameters and
  repeated steps (A -> B -> A) are kept as specified.
* All labels, sequences and the input circuit are validated before the
  first run. An invalid batch writes nothing.
* A recognized pass failure (run status ``PASS_FAILED``) is recorded and
  the batch continues with the next sequence. A failed sequence has no
  final metrics and is excluded from comparisons; its last successful
  step is NOT treated as its result.
* Any other error (recording, database, schema, aborted execution) stops
  the batch with :class:`BatchExecutionError`, which carries the results
  completed so far and the original exception as ``__cause__``.
* Deltas are ``final minus input``. Each metric is compared on its own,
  smaller is better, and ties are kept; there is no combined score.
* ``pass_time_recorded_s`` is the sum of the recorded per-step pass times
  of one sequence. It is ``None`` if no step was timed, and
  ``pass_time_complete`` is ``False`` if any step of the sequence has no
  recorded time. It is not the wall time of the batch.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field

from qiskit import QuantumCircuit

from . import optimization as opt

#: Metrics reported and compared for every sequence.
COMPARED_METRICS = ("gate_count", "two_qubit_gate_count", "depth")


class BatchConfigError(opt.PassConfigError):
    """The batch definition is invalid. Raised before any run is created."""


class BatchExecutionError(opt.OptimizationError):
    """The batch stopped because of a non-pass failure.

    Attributes:
        completed: :class:`SequenceResult` objects finished before the stop.
        failed_label: label of the sequence that was running.
        run_id: run ID of that sequence, if the cause reported one.
    The original exception is available as ``__cause__``.
    """

    def __init__(self, message, *, completed, failed_label, run_id=None):
        super().__init__(message)
        self.completed = completed
        self.failed_label = failed_label
        self.run_id = run_id


@dataclass
class SequenceResult:
    """Comparison row of one sequence; steps are available via ``run_id``."""

    label: str
    pass_names: list
    pass_sequence: list
    run_id: int | None
    status: str
    failed_step_index: int | None
    error_type: str | None
    error_message: str | None
    input_metrics: dict
    final_metrics: dict | None
    deltas: dict | None
    pass_time_recorded_s: float | None
    pass_time_complete: bool
    untimed_steps: list = field(default_factory=list)
    input_representation_sha256: str | None = None

    @property
    def succeeded(self):
        return self.status == "SUCCEEDED"


@dataclass
class BatchResult:
    circuit_name: str
    input_metrics: dict
    results: list

    def comparable(self):
        """Results with final metrics (successful sequences only)."""
        return [result for result in self.results if result.succeeded]

    def best_labels(self, metric):
        """Labels with the smallest final value of ``metric`` (ties kept).

        Returns ``[]`` if no sequence succeeded.
        """
        if metric not in COMPARED_METRICS:
            raise ValueError(
                f"Unsupported metric {metric!r}; use one of {COMPARED_METRICS}"
            )
        candidates = self.comparable()
        if not candidates:
            return []
        best = min(result.final_metrics[metric] for result in candidates)
        return [
            result.label for result in candidates
            if result.final_metrics[metric] == best
        ]

    def best_by_metric(self):
        return {metric: self.best_labels(metric) for metric in COMPARED_METRICS}

    def objective_view(self, verification=None):
        """Per-objective best sequences and Pareto front (stage 2E).

        See :func:`qch.objectives.view_batch_result`. ``verification`` is
        ``None`` or a mapping ``label -> bool``.
        """
        from . import objectives

        return objectives.view_batch_result(self, verification)

    def rows(self):
        """One plain dict per sequence, in batch order."""
        rows = []
        for result in self.results:
            row = {
                "label": result.label,
                "passes": " -> ".join(result.pass_names),
                "run_id": result.run_id,
                "status": result.status,
            }
            for metric in COMPARED_METRICS:
                row[f"final_{metric}"] = (
                    None if result.final_metrics is None
                    else result.final_metrics[metric]
                )
                row[f"delta_{metric}"] = (
                    None if result.deltas is None else result.deltas[metric]
                )
            row["pass_time_recorded_s"] = result.pass_time_recorded_s
            row["pass_time_complete"] = result.pass_time_complete
            rows.append(row)
        return rows

    def format_table(self):
        """Human-readable comparison table (English)."""
        inp = self.input_metrics
        lines = [
            f"Circuit: {self.circuit_name}",
            f"Input: gates={inp['gate_count']}, "
            f"2q={inp['two_qubit_gate_count']}, depth={inp['depth']}",
            "Deltas are final minus input. Pass time is the sum of recorded "
            "per-step pass times, not the batch wall time.",
            "",
            f"{'label':<12} {'run':>4} {'status':<16} {'gates':>5} "
            f"{'d_gates':>7} {'2q':>3} {'d_2q':>5} {'depth':>5} "
            f"{'d_depth':>7} {'pass_ms':>9}  passes",
        ]

        def cell(value, signed=False):
            if value is None:
                return "-"
            return f"{value:+d}" if signed else str(value)

        for row, result in zip(self.rows(), self.results):
            if row["pass_time_recorded_s"] is None:
                time_cell = "missing"
            else:
                time_cell = f"{row['pass_time_recorded_s'] * 1000:.3f}"
                if not row["pass_time_complete"]:
                    time_cell += "*"
            lines.append(
                f"{row['label']:<12} {cell(row['run_id']):>4} "
                f"{row['status']:<16} "
                f"{cell(row['final_gate_count']):>5} "
                f"{cell(row['delta_gate_count'], True):>7} "
                f"{cell(row['final_two_qubit_gate_count']):>3} "
                f"{cell(row['delta_two_qubit_gate_count'], True):>5} "
                f"{cell(row['final_depth']):>5} "
                f"{cell(row['delta_depth'], True):>7} "
                f"{time_cell:>9}  {row['passes']}"
            )
            if result.error_type:
                lines.append(
                    f"{'':<12} failed at step {result.failed_step_index}: "
                    f"{result.error_type}: {result.error_message}"
                )
        lines.append("")
        if any(not result.pass_time_complete for result in self.results):
            lines.append(
                "* incomplete pass timing: some steps of this sequence have "
                "no recorded time (for example NOT_RUN steps)"
            )
        lines.append("Best per metric (successful sequences, ties kept):")
        for metric, labels in self.best_by_metric().items():
            lines.append(f"  {metric}: {', '.join(labels) if labels else '-'}")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def _normalize_sequences(sequences):
    """Return ``[(label, passes)]`` from a mapping or a list of pairs/dicts."""
    if isinstance(sequences, Mapping):
        items = list(sequences.items())
    elif isinstance(sequences, (str, bytes)):
        raise BatchConfigError("sequences must be a mapping or a list")
    else:
        try:
            raw = list(sequences)
        except TypeError:
            raise BatchConfigError(
                "sequences must be a mapping or a list"
            ) from None
        items = []
        for entry in raw:
            if isinstance(entry, Mapping):
                unknown = set(entry) - {"label", "passes"}
                if unknown or "label" not in entry or "passes" not in entry:
                    raise BatchConfigError(
                        "Each sequence entry must have exactly the keys "
                        "'label' and 'passes'"
                    )
                items.append((entry["label"], entry["passes"]))
            elif isinstance(entry, (list, tuple)) and len(entry) == 2:
                items.append((entry[0], entry[1]))
            else:
                raise BatchConfigError(
                    "Each sequence entry must be {'label', 'passes'} or a "
                    "(label, passes) pair"
                )
    if not items:
        raise BatchConfigError("The batch must contain at least one sequence")

    seen = set()
    for label, _ in items:
        if not isinstance(label, str) or not label.strip():
            raise BatchConfigError("Sequence labels must be non-empty strings")
        if label != label.strip():
            raise BatchConfigError(
                f"Sequence label {label!r} has leading or trailing spaces"
            )
        if label in seen:
            raise BatchConfigError(f"Duplicate sequence label {label!r}")
        seen.add(label)
    return items


def prepare_batch(db, circuit_name, sequences):
    """Validate a batch without writing anything.

    Returns ``[(label, prepared_passes)]``. Raises :class:`BatchConfigError`
    (for labels or a sequence, naming the sequence),
    :class:`qch.optimization.CircuitNotFoundError` or
    :class:`qch.optimization.UnsupportedCircuitError`.
    """
    items = _normalize_sequences(sequences)
    prepared = []
    for label, passes in items:
        try:
            prepared.append((label, opt.prepare_pass_sequence(passes)))
        except opt.PassConfigError as exc:
            raise BatchConfigError(f"Sequence {label!r}: {exc}") from exc

    connection = opt._connection_of(db)
    row = connection.execute(
        "SELECT qasm FROM circuits WHERE name = ?", (circuit_name,)
    ).fetchone()
    if row is None:
        raise opt.CircuitNotFoundError(f"Circuit not found: {circuit_name}")
    try:
        circuit = QuantumCircuit.from_qasm_str(row[0])
    except Exception as exc:
        raise opt.UnsupportedCircuitError(
            f"Circuit {circuit_name!r} could not be parsed as OpenQASM 2: "
            f"{type(exc).__name__}: {exc}"
        ) from exc
    opt.validate_supported_circuit(circuit)
    return prepared


# ---------------------------------------------------------------------------
# Running and summarizing
# ---------------------------------------------------------------------------


def _metrics_of(step):
    return {metric: step[metric] for metric in COMPARED_METRICS}


def _snapshot_representation(connection, snapshot_id):
    if snapshot_id is None:
        return None
    row = connection.execute(
        "SELECT representation_sha256 FROM circuit_snapshots WHERE id = ?",
        (snapshot_id,),
    ).fetchone()
    return None if row is None else row[0]


def summarize_run(db_or_connection, run_id, label=None):
    """Build a :class:`SequenceResult` from a stored run (read-only)."""
    connection = opt._connection_of(db_or_connection)
    run = opt.get_optimization_run(connection, run_id)
    if run is None:
        raise LookupError(f"Optimization run {run_id} not found")
    steps = run["steps"]
    if not steps or steps[0]["step_index"] != 0 or (
        steps[0]["status"] != "SUCCEEDED"
    ):
        raise LookupError(f"Run {run_id} has no recorded input step")

    input_metrics = _metrics_of(steps[0])
    pass_steps = [step for step in steps if step["step_index"] > 0]
    final_metrics = None
    deltas = None
    if run["status"] == "SUCCEEDED":
        final_step = pass_steps[-1]
        final_metrics = _metrics_of(final_step)
        deltas = {
            metric: final_metrics[metric] - input_metrics[metric]
            for metric in COMPARED_METRICS
        }

    timed = [s["pass_time_s"] for s in pass_steps if s["pass_time_s"] is not None]
    untimed = [s["step_index"] for s in pass_steps if s["pass_time_s"] is None]
    return SequenceResult(
        label=label if label is not None else run["label"],
        pass_names=[item["name"] for item in run["pass_sequence"]],
        pass_sequence=run["pass_sequence"],
        run_id=run_id,
        status=run["status"],
        failed_step_index=run["failed_step_index"],
        error_type=run["error_type"],
        error_message=run["error_message"],
        input_metrics=input_metrics,
        final_metrics=final_metrics,
        deltas=deltas,
        pass_time_recorded_s=sum(timed) if timed else None,
        pass_time_complete=not untimed,
        untimed_steps=untimed,
        input_representation_sha256=_snapshot_representation(
            connection, steps[0]["snapshot_id"]
        ),
    )


def summarize_runs(db_or_connection, circuit_name, labeled_run_ids):
    """Rebuild a :class:`BatchResult` from stored runs (read-only).

    ``labeled_run_ids`` is a mapping or a list of ``(label, run_id)`` pairs,
    for example the ``run_id`` values returned by :func:`run_sequence_batch`.
    Works on a connection from :func:`qch.optimization.open_readonly`.
    """
    pairs = (
        list(labeled_run_ids.items())
        if isinstance(labeled_run_ids, Mapping)
        else list(labeled_run_ids)
    )
    results = [
        summarize_run(db_or_connection, run_id, label=label)
        for label, run_id in pairs
    ]
    connection = opt._connection_of(db_or_connection)
    names = {
        connection.execute(
            "SELECT c.name FROM optimization_runs AS r "
            "JOIN circuits AS c ON c.id = r.source_circuit_id WHERE r.id = ?",
            (result.run_id,),
        ).fetchone()[0]
        for result in results
    }
    if names != {circuit_name}:
        raise ValueError(
            f"Runs belong to circuits {sorted(names)}, not only "
            f"{circuit_name!r}"
        )
    return _build_batch(circuit_name, results)


def _build_batch(circuit_name, results):
    if not results:
        raise ValueError("A batch result needs at least one sequence")
    inputs = {result.input_representation_sha256 for result in results}
    if len(inputs) != 1:
        raise opt.SnapshotIntegrityError(
            "The sequences of this batch do not share the same input "
            "representation"
        )
    return BatchResult(
        circuit_name=circuit_name,
        input_metrics=dict(results[0].input_metrics),
        results=results,
    )


def run_sequence_batch(db, circuit_name, sequences, *, verify_snapshots=True):
    """Run several named pass sequences on one stored circuit.

    Parameters:
        db: :class:`qch.QCHDatabase` or a writable ``sqlite3.Connection``
            without an open transaction.
        circuit_name: name of a circuit in the ``circuits`` table.
        sequences: ``{label: [pass, ...]}`` (order preserved) or a list of
            ``{"label": ..., "passes": [...]}`` / ``(label, passes)``.
            Each pass is ``{"name": ..., "params": {...}}`` or a
            :class:`qch.optimization.PassSpec`.

    Returns:
        :class:`BatchResult`. Each sequence is stored as a normal
        optimization run labeled with its sequence label.

    Raises:
        :class:`BatchConfigError`, ``CircuitNotFoundError`` or
        ``UnsupportedCircuitError`` before anything is written;
        :class:`BatchExecutionError` if a non-pass failure stops the batch.
    """
    opt._require_no_open_transaction(opt._connection_of(db))
    prepared = prepare_batch(db, circuit_name, sequences)

    completed = []
    for label, passes in prepared:
        specs = [opt.PassSpec(item.name, item.params) for item in passes]
        try:
            result = opt.run_pass_sequence(
                db, circuit_name, specs, label=label,
                verify_snapshots=verify_snapshots,
            )
        except Exception as exc:
            raise BatchExecutionError(
                f"Batch stopped at sequence {label!r}: "
                f"{type(exc).__name__}: {exc}",
                completed=completed,
                failed_label=label,
                run_id=getattr(exc, "run_id", None),
            ) from exc
        completed.append(summarize_run(db, result.run_id, label=label))

    return _build_batch(circuit_name, completed)


__all__ = [
    "BatchConfigError",
    "BatchExecutionError",
    "BatchResult",
    "COMPARED_METRICS",
    "SequenceResult",
    "prepare_batch",
    "run_sequence_batch",
    "summarize_run",
    "summarize_runs",
]
