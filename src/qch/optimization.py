"""Run ordered Qiskit optimization pass sequences and persist every step.

This module adds a first, deliberately narrow, experiment facility to QCH:

* read an existing circuit from the ``circuits`` table by name,
* execute a user-specified ordered list of whitelisted Qiskit
  transformation passes inside ONE ``PassManager`` (so the pass manager
  context is shared within the sequence),
* persist step 0 (the input) and the result of every sequence position,
  including circuit snapshots (QPY), resource metrics, parameters,
  status, errors and separately measured timings.

Scope of version 1 (see ``docs/OPTIMIZATION_RUNS.md``):

* Supported passes: ``InverseCancellation``,
  ``Optimize1qGatesDecomposition`` and ``RemoveIdentityEquivalent``.
* No implicit ``transpile`` and no target gate set conversion between or
  after steps.
* Supported circuit content: Qiskit standard-library gates, opaque gates
  without a definition (for example ``opaque ecr`` from OpenQASM 2),
  ``measure``, ``reset`` and ``barrier``. Everything else is rejected
  before any run record is created.

Write behavior:

* :func:`run_pass_sequence` writes to the database. It lazily creates the
  optimization tables (explicit schema version) and commits each step as
  soon as it has been recorded. It never commits or rolls back a
  transaction it did not open; it refuses to start if the connection
  already has an open transaction.
* The query functions (:func:`list_optimization_runs`,
  :func:`get_optimization_run`, :func:`list_optimization_steps`,
  :func:`load_step_circuit`, :func:`get_optimization_schema_state`) only
  execute ``SELECT`` and ``PRAGMA`` reads. Use :func:`open_readonly` to
  obtain a connection that SQLite itself opens in read-only mode.
"""

from __future__ import annotations

import hashlib
import io
import json
import math
import platform
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from time import perf_counter
from typing import Any, Callable, Mapping

import qiskit
from qiskit import QuantumCircuit, qasm2, qpy
from qiskit.circuit import Barrier, Gate, Measure, Reset
from qiskit.circuit.library import (
    C3XGate,
    CCXGate,
    CCZGate,
    CHGate,
    CSdgGate,
    CSGate,
    CSwapGate,
    CXGate,
    CYGate,
    CZGate,
    ECRGate,
    HGate,
    RCCXGate,
    SdgGate,
    SGate,
    SwapGate,
    SXdgGate,
    SXGate,
    TdgGate,
    TGate,
    XGate,
    YGate,
    ZGate,
    get_standard_gate_name_mapping,
)
from qiskit.converters import circuit_to_dag, dag_to_circuit
from qiskit.synthesis.one_qubit.one_qubit_decompose import (
    ONE_QUBIT_EULER_BASIS_GATES,
)
from qiskit.transpiler import PassManager
from qiskit.transpiler.passes import (
    InverseCancellation,
    Optimize1qGatesDecomposition,
    RemoveIdentityEquivalent,
)


# ---------------------------------------------------------------------------
# Versions and constants
# ---------------------------------------------------------------------------

#: Name of this feature in ``qch_schema_components``.
SCHEMA_COMPONENT = "optimization"

#: Version of the optimization tables created by this module.
SCHEMA_VERSION = 1

#: Definition version of the per-step metrics (see :func:`compute_step_metrics`).
METRICS_VERSION = "qch-step-metrics/1"

#: Definition version of the representation fingerprint.
REPRESENTATION_VERSION = "qch-representation/1"

#: How a run executes its passes.
EXECUTION_MODE = "single_pass_manager"

COMPILER_NAME = "qiskit"

RUN_STATUSES = (
    "RUNNING",
    "SUCCEEDED",
    "PASS_FAILED",
    "RECORDING_FAILED",
    "ABORTED",
)

STEP_STATUSES = (
    "SUCCEEDED",
    "PASS_FAILED",
    "RECORDING_FAILED",
    "NOT_RUN",
)

_STANDARD_GATES = get_standard_gate_name_mapping()

# Standard-library names that are not unitary gates or that version 1
# rejects on purpose (zero-qubit global phase gate, timing delays).
_REJECTED_STANDARD_NAMES = {"delay", "global_phase"}

_MAX_ERROR_MESSAGE_LENGTH = 4000


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class OptimizationError(Exception):
    """Base class for errors raised by this module."""


class PassConfigError(OptimizationError, ValueError):
    """A pass configuration is invalid or not supported.

    Raised before any run record is created.
    """


class UnsupportedCircuitError(OptimizationError, ValueError):
    """The circuit contains content outside the supported version 1 scope."""


class CircuitNotFoundError(OptimizationError, LookupError):
    """No circuit with the requested name exists."""


class SchemaCompatibilityError(OptimizationError):
    """Existing optimization tables do not match the expected schema."""


class TransactionConflictError(OptimizationError):
    """The caller's connection already has an open transaction."""


class SnapshotIntegrityError(OptimizationError):
    """A stored snapshot failed its integrity or round-trip check."""


class RecordingError(OptimizationError):
    """Recording infrastructure failed (metrics, serialization or database).

    This is never used for an optimization pass failure. Attributes:

    * ``run_id``: the run id if a run row was created, otherwise ``None``.
    * ``step_index``: the step whose record could not be written.
    * ``phase``: ``"metrics"``, ``"serialize"``, ``"database"`` or
      ``"finalize"``.
    * ``failure_persisted``: whether the failure state itself could be
      written to the database.
    """

    def __init__(
        self,
        message,
        *,
        run_id=None,
        step_index=None,
        phase=None,
        failure_persisted=False,
    ):
        super().__init__(message)
        self.run_id = run_id
        self.step_index = step_index
        self.phase = phase
        self.failure_persisted = failure_persisted


class ExecutionAbortedError(OptimizationError):
    """Execution stopped for a reason that is not attributable to one pass."""

    def __init__(self, message, *, run_id=None, failure_persisted=False):
        super().__init__(message)
        self.run_id = run_id
        self.failure_persisted = failure_persisted


# ---------------------------------------------------------------------------
# Pass specifications and the verified registry
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PassSpec:
    """One position in a pass sequence.

    ``params`` must be JSON-serializable. Each sequence position gets its
    own pass instance, so ``[A, B, A]`` produces three independent steps.
    """

    name: str
    params: Mapping[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data):
        if not isinstance(data, Mapping):
            raise PassConfigError(
                "Each pass entry must be an object with 'name' and "
                "optional 'params'"
            )
        unknown = set(data) - {"name", "params"}
        if unknown:
            raise PassConfigError(
                f"Unknown keys in pass entry: {sorted(unknown)}"
            )
        return cls(name=data.get("name"), params=data.get("params") or {})


@dataclass(frozen=True)
class PreparedPass:
    """A validated pass configuration with normalized parameters."""

    position: int
    name: str
    params: dict
    factory: Callable[[dict], Any]

    def build(self):
        """Create a fresh pass instance for one run."""
        instance = self.factory(self.params)
        if list(getattr(instance, "requires", []) or []):
            raise PassConfigError(
                f"Pass {self.name} declares required analysis passes; "
                "automatic analysis dependencies are not supported in "
                "version 1"
            )
        return instance


_SELF_INVERSE_GATES = {
    "x": XGate,
    "y": YGate,
    "z": ZGate,
    "h": HGate,
    "cx": CXGate,
    "cy": CYGate,
    "cz": CZGate,
    "ecr": ECRGate,
    "swap": SwapGate,
    "ch": CHGate,
    "ccx": CCXGate,
    "ccz": CCZGate,
    "rccx": RCCXGate,
    "cswap": CSwapGate,
    "c3x": C3XGate,
}

_INVERSE_GATE_PAIRS = {
    frozenset({"t", "tdg"}): {"t": TGate, "tdg": TdgGate},
    frozenset({"s", "sdg"}): {"s": SGate, "sdg": SdgGate},
    frozenset({"sx", "sxdg"}): {"sx": SXGate, "sxdg": SXdgGate},
    frozenset({"cs", "csdg"}): {"cs": CSGate, "csdg": CSdgGate},
}


def _check_keys(pass_name, params, allowed, required=()):
    unknown = set(params) - set(allowed)
    if unknown:
        raise PassConfigError(
            f"{pass_name}: unsupported parameters {sorted(unknown)}; "
            f"allowed parameters are {sorted(allowed)}"
        )
    missing = [key for key in required if key not in params]
    if missing:
        raise PassConfigError(
            f"{pass_name}: missing required parameters {missing}"
        )


def _normalize_inverse_cancellation(params):
    _check_keys(
        "InverseCancellation", params, {"gates_to_cancel", "run_default"}
    )
    gates = params.get("gates_to_cancel")
    run_default = params.get("run_default", False)

    if not isinstance(run_default, bool):
        raise PassConfigError(
            "InverseCancellation: run_default must be true or false"
        )

    if gates is None:
        return {"gates_to_cancel": None, "run_default": run_default}

    if not isinstance(gates, list) or not gates:
        raise PassConfigError(
            "InverseCancellation: gates_to_cancel must be null or a "
            "non-empty list"
        )

    normalized = []
    for entry in gates:
        if isinstance(entry, str):
            if entry not in _SELF_INVERSE_GATES:
                raise PassConfigError(
                    f"InverseCancellation: '{entry}' is not a supported "
                    f"self-inverse gate; supported: "
                    f"{sorted(_SELF_INVERSE_GATES)}"
                )
            normalized.append(entry)
        elif (
            isinstance(entry, list)
            and len(entry) == 2
            and all(isinstance(item, str) for item in entry)
        ):
            if frozenset(entry) not in _INVERSE_GATE_PAIRS or (
                entry[0] == entry[1]
            ):
                supported = sorted(
                    sorted(pair) for pair in _INVERSE_GATE_PAIRS
                )
                raise PassConfigError(
                    f"InverseCancellation: {entry} is not a supported "
                    f"inverse gate pair; supported pairs: {supported}"
                )
            normalized.append([entry[0], entry[1]])
        else:
            raise PassConfigError(
                "InverseCancellation: each gates_to_cancel entry must be a "
                "gate name or a list of two gate names"
            )

    return {"gates_to_cancel": normalized, "run_default": run_default}


def _build_inverse_cancellation(params):
    gates = params["gates_to_cancel"]
    if gates is None:
        return InverseCancellation(
            gates_to_cancel=None, run_default=params["run_default"]
        )

    constructed = []
    for entry in gates:
        if isinstance(entry, str):
            constructed.append(_SELF_INVERSE_GATES[entry]())
        else:
            classes = _INVERSE_GATE_PAIRS[frozenset(entry)]
            constructed.append(
                (classes[entry[0]](), classes[entry[1]]())
            )
    return InverseCancellation(
        gates_to_cancel=constructed, run_default=params["run_default"]
    )


def _normalize_optimize_1q(params):
    _check_keys(
        "Optimize1qGatesDecomposition",
        params,
        {"basis"},
        required=("basis",),
    )
    basis = params["basis"]
    if (
        not isinstance(basis, list)
        or not basis
        or not all(isinstance(name, str) and name for name in basis)
    ):
        raise PassConfigError(
            "Optimize1qGatesDecomposition: basis must be a non-empty list "
            "of gate names"
        )
    if len(set(basis)) != len(basis):
        raise PassConfigError(
            "Optimize1qGatesDecomposition: basis contains duplicate names"
        )
    unknown = [
        name
        for name in basis
        if name not in _STANDARD_GATES
        or name in _REJECTED_STANDARD_NAMES
        or not isinstance(_STANDARD_GATES[name], Gate)
    ]
    if unknown:
        raise PassConfigError(
            f"Optimize1qGatesDecomposition: unknown or non-gate basis "
            f"names {unknown}"
        )
    basis_set = set(basis)
    usable = [
        euler
        for euler, gates in ONE_QUBIT_EULER_BASIS_GATES.items()
        if set(gates) <= basis_set
    ]
    if not usable:
        raise PassConfigError(
            "Optimize1qGatesDecomposition: the basis does not contain any "
            "complete one-qubit Euler basis; known Euler bases are "
            f"{ONE_QUBIT_EULER_BASIS_GATES}"
        )
    return {"basis": list(basis)}


def _build_optimize_1q(params):
    return Optimize1qGatesDecomposition(basis=list(params["basis"]))


def _normalize_remove_identity(params):
    _check_keys(
        "RemoveIdentityEquivalent", params, {"approximation_degree"}
    )
    degree = params.get("approximation_degree", 1.0)
    if (
        isinstance(degree, bool)
        or not isinstance(degree, (int, float))
        or not math.isfinite(degree)
        or not 0.0 < float(degree) <= 1.0
    ):
        raise PassConfigError(
            "RemoveIdentityEquivalent: approximation_degree must be a "
            "number in (0, 1]; target-based approximation (null) is not "
            "supported in version 1"
        )
    return {"approximation_degree": float(degree)}


def _build_remove_identity(params):
    return RemoveIdentityEquivalent(
        approximation_degree=params["approximation_degree"]
    )


@dataclass(frozen=True)
class _RegistryEntry:
    normalize: Callable[[dict], dict]
    build: Callable[[dict], Any]


#: The verified pass whitelist. Only these passes can be executed.
PASS_REGISTRY = {
    "InverseCancellation": _RegistryEntry(
        _normalize_inverse_cancellation, _build_inverse_cancellation
    ),
    "Optimize1qGatesDecomposition": _RegistryEntry(
        _normalize_optimize_1q, _build_optimize_1q
    ),
    "RemoveIdentityEquivalent": _RegistryEntry(
        _normalize_remove_identity, _build_remove_identity
    ),
}


def _json_roundtrip(value, what):
    try:
        text = json.dumps(value, allow_nan=False, sort_keys=True)
    except (TypeError, ValueError) as exc:
        raise PassConfigError(f"{what} must be JSON-serializable: {exc}")
    return json.loads(text)


def prepare_pass_sequence(passes):
    """Validate an ordered pass sequence and return prepared passes.

    ``passes`` is a sequence of :class:`PassSpec` objects or of mappings
    ``{"name": ..., "params": {...}}``. Every pass is constructed once
    during validation, so constructor errors surface here. Nothing is
    written to any database.
    """
    if isinstance(passes, (str, bytes, Mapping)):
        raise PassConfigError("passes must be a list of pass entries")
    try:
        entries = list(passes)
    except TypeError:
        raise PassConfigError("passes must be a list of pass entries") from None
    if not entries:
        raise PassConfigError("The pass sequence must not be empty")

    prepared = []
    for position, entry in enumerate(entries, start=1):
        spec = entry if isinstance(entry, PassSpec) else PassSpec.from_dict(entry)
        if not isinstance(spec.name, str) or spec.name not in PASS_REGISTRY:
            raise PassConfigError(
                f"Step {position}: unsupported pass {spec.name!r}; "
                f"supported passes: {sorted(PASS_REGISTRY)}"
            )
        if not isinstance(spec.params, Mapping):
            raise PassConfigError(
                f"Step {position}: params must be an object"
            )
        raw = _json_roundtrip(dict(spec.params), f"Step {position} params")
        registry_entry = PASS_REGISTRY[spec.name]
        try:
            normalized = registry_entry.normalize(raw)
        except PassConfigError as exc:
            raise PassConfigError(f"Step {position}: {exc}") from None
        item = PreparedPass(
            position=position,
            name=spec.name,
            params=normalized,
            factory=registry_entry.build,
        )
        try:
            item.build()
        except PassConfigError:
            raise
        except Exception as exc:
            raise PassConfigError(
                f"Step {position}: {spec.name} rejected its parameters: "
                f"{type(exc).__name__}: {exc}"
            ) from exc
        prepared.append(item)
    return prepared


# ---------------------------------------------------------------------------
# Circuit support checks, metrics and representation fingerprint
# ---------------------------------------------------------------------------


def _classify(operation):
    """Return ``(kind, is_opaque)`` or raise UnsupportedCircuitError.

    ``kind`` is one of ``"gate"``, ``"measure"``, ``"reset"``,
    ``"barrier"``.
    """
    name = operation.name
    base_class = getattr(operation, "base_class", type(operation))

    if base_class is Barrier:
        return "barrier", False
    if base_class is Measure:
        return "measure", False
    if base_class is Reset:
        return "reset", False

    if name in _REJECTED_STANDARD_NAMES:
        raise UnsupportedCircuitError(
            f"Operation '{name}' is not supported in version 1"
        )

    if isinstance(operation, Gate):
        if getattr(operation, "is_parameterized", lambda: False)():
            raise UnsupportedCircuitError(
                f"Gate '{name}' has unbound parameters; parameterized "
                "circuits are not supported in version 1"
            )
        standard = _STANDARD_GATES.get(name)
        if standard is not None and base_class is getattr(
            standard, "base_class", type(standard)
        ):
            if operation.num_qubits < 1:
                raise UnsupportedCircuitError(
                    f"Zero-qubit gate '{name}' is not supported"
                )
            return "gate", False
        if type(operation) is Gate and operation.definition is None:
            if operation.num_qubits < 1:
                raise UnsupportedCircuitError(
                    f"Zero-qubit opaque gate '{name}' is not supported"
                )
            return "gate", True
        raise UnsupportedCircuitError(
            f"Gate '{name}' ({type(operation).__name__}) is a custom or "
            "composite gate with a definition; version 1 supports Qiskit "
            "standard-library gates and opaque gates only"
        )

    raise UnsupportedCircuitError(
        f"Operation '{name}' ({type(operation).__name__}) is not supported "
        "in version 1; supported operations are standard-library gates, "
        "opaque gates, measure, reset and barrier"
    )


def validate_supported_circuit(circuit):
    """Raise :class:`UnsupportedCircuitError` for unsupported content."""
    if not isinstance(circuit, QuantumCircuit):
        raise UnsupportedCircuitError("Expected a qiskit QuantumCircuit")
    if circuit.parameters:
        raise UnsupportedCircuitError(
            "Parameterized circuits are not supported in version 1"
        )
    for attribute in ("num_vars", "num_stretches"):
        if getattr(circuit, attribute, 0):
            raise UnsupportedCircuitError(
                "Circuits with classical variables or stretches are not "
                "supported in version 1"
            )
    if _is_parameterized(circuit.global_phase):
        raise UnsupportedCircuitError(
            "A parameterized global phase is not supported"
        )
    for instruction in circuit.data:
        _classify(instruction.operation)


def _is_parameterized(value):
    return hasattr(value, "parameters") and bool(value.parameters)


def compute_step_metrics(circuit):
    """Compute the version 1 step metrics of a supported circuit.

    Definitions (``METRICS_VERSION = "qch-step-metrics/1"``):

    * ``num_qubits``: total size of all quantum registers
      (``QuantumCircuit.num_qubits``), including idle qubits.
    * ``gate_count``: ``QuantumCircuit.size()``. Same definition as the
      existing ``features.gate_count``: barriers are excluded, measure and
      reset are included.
    * ``depth``: ``QuantumCircuit.depth()``. Same definition as the existing
      ``features.depth``: barriers are excluded, measure and reset count.
    * ``single_qubit_gate_count`` / ``two_qubit_gate_count`` /
      ``multi_qubit_gate_count``: gates acting on 1 / 2 / >= 3 qubits.
      Only standard-library gates and opaque gates are counted; measure,
      reset and barrier are never included. Gates are counted in their
      current representation; nothing is decomposed.
      NOTE: this ``two_qubit_gate_count`` differs from the legacy
      ``features.two_qubit_gate_count``, which also counts two-qubit
      barriers.
    * ``measure_count``, ``reset_count``, ``barrier_count``: number of
      instructions of each kind.
    * ``opaque_gate_count``: gates without a definition that are not
      Qiskit standard-library classes (for example ``opaque ecr``).

    Within the supported scope the invariant
    ``single + two + multi + measure + reset == gate_count`` holds and is
    checked.
    """
    validate_supported_circuit(circuit)
    counts = {
        "single_qubit_gate_count": 0,
        "two_qubit_gate_count": 0,
        "multi_qubit_gate_count": 0,
        "measure_count": 0,
        "reset_count": 0,
        "barrier_count": 0,
        "opaque_gate_count": 0,
    }
    for instruction in circuit.data:
        kind, opaque = _classify(instruction.operation)
        if kind == "gate":
            arity = len(instruction.qubits)
            if arity == 1:
                counts["single_qubit_gate_count"] += 1
            elif arity == 2:
                counts["two_qubit_gate_count"] += 1
            else:
                counts["multi_qubit_gate_count"] += 1
            if opaque:
                counts["opaque_gate_count"] += 1
        else:
            counts[f"{kind}_count"] += 1

    metrics = {
        "num_qubits": circuit.num_qubits,
        "num_clbits": circuit.num_clbits,
        "gate_count": circuit.size(),
        "depth": circuit.depth(),
        **counts,
    }

    accounted = (
        metrics["single_qubit_gate_count"]
        + metrics["two_qubit_gate_count"]
        + metrics["multi_qubit_gate_count"]
        + metrics["measure_count"]
        + metrics["reset_count"]
    )
    if accounted != metrics["gate_count"]:
        raise UnsupportedCircuitError(
            "Metric invariant violated: classified instructions "
            f"({accounted}) differ from QuantumCircuit.size() "
            f"({metrics['gate_count']})"
        )
    return metrics


def _exact_number(value):
    if isinstance(value, bool):
        raise UnsupportedCircuitError("Boolean parameters are not supported")
    if isinstance(value, int):
        return ["int", str(value)]
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise UnsupportedCircuitError(
            f"Unsupported parameter value {value!r} ({type(value).__name__})"
        ) from None
    if isinstance(value, complex):
        raise UnsupportedCircuitError("Complex parameters are not supported")
    return ["float", number.hex()]


def representation_fingerprint(circuit):
    """Return a SHA-256 hex digest of the exact circuit representation.

    The fingerprint covers registers, the exact global phase (bit-exact
    float), and the ordered instruction list with exact parameter values,
    qubit and clbit indices, and whether a gate is a standard-library or
    an opaque gate. It does NOT decide semantic equivalence: two
    equivalent circuits with different representations get different
    fingerprints, and ``changed`` is defined on this representation.
    """
    validate_supported_circuit(circuit)
    instructions = []
    for instruction in circuit.data:
        operation = instruction.operation
        kind, opaque = _classify(operation)
        instructions.append([
            kind,
            "opaque" if opaque else "standard",
            operation.name,
            [circuit.find_bit(bit).index for bit in instruction.qubits],
            [circuit.find_bit(bit).index for bit in instruction.clbits],
            [_exact_number(param) for param in operation.params],
        ])
    payload = {
        "version": REPRESENTATION_VERSION,
        "num_qubits": circuit.num_qubits,
        "num_clbits": circuit.num_clbits,
        "qregs": [[reg.name, reg.size] for reg in circuit.qregs],
        "cregs": [[reg.name, reg.size] for reg in circuit.cregs],
        "global_phase": _exact_number(circuit.global_phase),
        "instructions": instructions,
    }
    text = json.dumps(payload, separators=(",", ":"))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


@dataclass
class SerializedSnapshot:
    representation_sha256: str
    qpy_bytes: bytes
    qpy_sha256: str
    qpy_format_version: int
    qasm2_text: str | None
    qasm2_error: str | None
    num_qubits: int
    num_clbits: int


def serialize_snapshot(circuit, representation_sha256, *, verify=True):
    """Serialize a circuit to QPY (authoritative) and, if possible, QASM 2.

    With ``verify=True`` the QPY bytes are loaded back and the
    representation fingerprint of the loaded circuit must equal
    ``representation_sha256``.
    """
    buffer = io.BytesIO()
    qpy.dump(circuit, buffer, version=qpy.QPY_VERSION)
    data = buffer.getvalue()

    if verify:
        restored = _load_qpy(data)
        if representation_fingerprint(restored) != representation_sha256:
            raise SnapshotIntegrityError(
                "QPY round trip changed the circuit representation"
            )

    try:
        qasm_text = qasm2.dumps(circuit)
        qasm_error = None
    except Exception as exc:  # QASM is optional; QPY remains authoritative.
        qasm_text = None
        qasm_error = f"{type(exc).__name__}: {exc}"[:_MAX_ERROR_MESSAGE_LENGTH]

    return SerializedSnapshot(
        representation_sha256=representation_sha256,
        qpy_bytes=data,
        qpy_sha256=hashlib.sha256(data).hexdigest(),
        qpy_format_version=qpy.QPY_VERSION,
        qasm2_text=qasm_text,
        qasm2_error=qasm_error,
        num_qubits=circuit.num_qubits,
        num_clbits=circuit.num_clbits,
    )


def _load_qpy(data):
    circuits = qpy.load(io.BytesIO(data))
    if len(circuits) != 1:
        raise SnapshotIntegrityError(
            f"Expected one circuit in QPY payload, found {len(circuits)}"
        )
    return circuits[0]


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

_SCHEMA_STATEMENTS = (
    """
    CREATE TABLE IF NOT EXISTS qch_schema_components (
        component TEXT PRIMARY KEY,
        version INTEGER NOT NULL CHECK (version >= 1),
        installed_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE circuit_snapshots (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        representation_sha256 TEXT NOT NULL,
        representation_version TEXT NOT NULL,
        qpy BLOB NOT NULL,
        qpy_sha256 TEXT NOT NULL,
        qpy_format_version INTEGER NOT NULL,
        qiskit_version TEXT NOT NULL,
        qasm2 TEXT,
        qasm2_error TEXT,
        num_qubits INTEGER NOT NULL,
        num_clbits INTEGER NOT NULL,
        created_at TEXT NOT NULL,
        CHECK (qasm2 IS NULL OR qasm2_error IS NULL)
    )
    """,
    """
    CREATE TABLE optimization_runs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        source_circuit_id INTEGER NOT NULL REFERENCES circuits(id),
        source_qasm_sha256 TEXT NOT NULL,
        label TEXT,
        pass_sequence_json TEXT NOT NULL,
        num_steps INTEGER NOT NULL CHECK (num_steps >= 1),
        compiler_name TEXT NOT NULL,
        compiler_version TEXT NOT NULL,
        python_version TEXT NOT NULL,
        platform TEXT NOT NULL,
        metrics_version TEXT NOT NULL,
        representation_version TEXT NOT NULL,
        execution_mode TEXT NOT NULL,
        seed INTEGER CHECK (seed IS NULL),
        status TEXT NOT NULL CHECK (status IN (
            'RUNNING', 'SUCCEEDED', 'PASS_FAILED',
            'RECORDING_FAILED', 'ABORTED'
        )),
        failed_step_index INTEGER,
        error_type TEXT,
        error_message TEXT,
        started_at TEXT NOT NULL,
        finished_at TEXT,
        pass_manager_wall_time_s REAL,
        CHECK ((status = 'RUNNING') = (finished_at IS NULL)),
        CHECK (
            status NOT IN ('RUNNING', 'SUCCEEDED')
            OR (error_type IS NULL AND failed_step_index IS NULL)
        ),
        CHECK (
            status NOT IN ('PASS_FAILED', 'RECORDING_FAILED')
            OR (error_type IS NOT NULL AND failed_step_index IS NOT NULL)
        ),
        CHECK (status != 'PASS_FAILED' OR failed_step_index >= 1),
        CHECK (status != 'ABORTED' OR error_type IS NOT NULL)
    )
    """,
    """
    CREATE INDEX idx_optimization_runs_source_circuit
        ON optimization_runs(source_circuit_id)
    """,
    """
    CREATE TABLE optimization_steps (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        run_id INTEGER NOT NULL REFERENCES optimization_runs(id),
        step_index INTEGER NOT NULL CHECK (step_index >= 0),
        pass_name TEXT,
        pass_params_json TEXT,
        status TEXT NOT NULL CHECK (status IN (
            'SUCCEEDED', 'PASS_FAILED', 'RECORDING_FAILED', 'NOT_RUN'
        )),
        error_type TEXT,
        error_message TEXT,
        snapshot_id INTEGER REFERENCES circuit_snapshots(id),
        changed INTEGER CHECK (changed IN (0, 1)),
        num_qubits INTEGER,
        num_clbits INTEGER,
        gate_count INTEGER,
        depth INTEGER,
        single_qubit_gate_count INTEGER,
        two_qubit_gate_count INTEGER,
        multi_qubit_gate_count INTEGER,
        measure_count INTEGER,
        reset_count INTEGER,
        barrier_count INTEGER,
        opaque_gate_count INTEGER,
        pass_time_s REAL,
        qiskit_reported_time_s REAL,
        snapshot_copy_time_s REAL,
        metrics_time_s REAL,
        serialize_time_s REAL,
        db_write_time_s REAL,
        UNIQUE (run_id, step_index),
        CHECK ((step_index = 0) = (pass_name IS NULL)),
        CHECK ((step_index = 0) = (pass_params_json IS NULL)),
        CHECK (step_index > 0 OR status IN ('SUCCEEDED', 'RECORDING_FAILED')),
        CHECK (status != 'SUCCEEDED' OR snapshot_id IS NOT NULL),
        CHECK (status = 'SUCCEEDED' OR snapshot_id IS NULL),
        CHECK (status = 'SUCCEEDED' OR changed IS NULL),
        CHECK (step_index > 0 OR changed IS NULL),
        CHECK (
            step_index = 0 OR status != 'SUCCEEDED' OR changed IS NOT NULL
        ),
        CHECK (
            status NOT IN ('PASS_FAILED', 'RECORDING_FAILED')
            OR error_type IS NOT NULL
        ),
        CHECK (
            status != 'NOT_RUN'
            OR (error_type IS NULL AND pass_time_s IS NULL)
        )
    )
    """,
    """
    CREATE INDEX idx_optimization_steps_snapshot
        ON optimization_steps(snapshot_id)
    """,
)

_EXPECTED_COLUMNS = {
    "circuit_snapshots": [
        "id", "representation_sha256", "representation_version", "qpy",
        "qpy_sha256", "qpy_format_version", "qiskit_version", "qasm2",
        "qasm2_error", "num_qubits", "num_clbits", "created_at",
    ],
    "optimization_runs": [
        "id", "source_circuit_id", "source_qasm_sha256", "label",
        "pass_sequence_json", "num_steps", "compiler_name",
        "compiler_version", "python_version", "platform",
        "metrics_version", "representation_version", "execution_mode",
        "seed", "status", "failed_step_index", "error_type",
        "error_message", "started_at", "finished_at",
        "pass_manager_wall_time_s",
    ],
    "optimization_steps": [
        "id", "run_id", "step_index", "pass_name", "pass_params_json",
        "status", "error_type", "error_message", "snapshot_id", "changed",
        "num_qubits", "num_clbits", "gate_count", "depth",
        "single_qubit_gate_count", "two_qubit_gate_count",
        "multi_qubit_gate_count", "measure_count", "reset_count",
        "barrier_count", "opaque_gate_count", "pass_time_s",
        "qiskit_reported_time_s", "snapshot_copy_time_s",
        "metrics_time_s", "serialize_time_s", "db_write_time_s",
    ],
}

_METRIC_COLUMNS = [
    "num_qubits", "num_clbits", "gate_count", "depth",
    "single_qubit_gate_count", "two_qubit_gate_count",
    "multi_qubit_gate_count", "measure_count", "reset_count",
    "barrier_count", "opaque_gate_count",
]


def _connection_of(db_or_connection):
    if isinstance(db_or_connection, sqlite3.Connection):
        return db_or_connection
    connection = getattr(db_or_connection, "connection", None)
    if isinstance(connection, sqlite3.Connection):
        return connection
    raise TypeError(
        "Expected a QCHDatabase or a sqlite3.Connection, got "
        f"{type(db_or_connection).__name__}"
    )


def _table_names(connection):
    return {
        row[0]
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        ).fetchall()
    }


def get_optimization_schema_state(db_or_connection):
    """Return ``"absent"`` or ``"current"``; raise if incompatible.

    Read-only: executes only ``SELECT`` and ``PRAGMA table_info``.
    """
    connection = _connection_of(db_or_connection)
    tables = _table_names(connection)
    feature_tables = [name for name in _EXPECTED_COLUMNS if name in tables]

    version = None
    if "qch_schema_components" in tables:
        row = connection.execute(
            "SELECT version FROM qch_schema_components WHERE component = ?",
            (SCHEMA_COMPONENT,),
        ).fetchone()
        version = row[0] if row else None

    if version is None and not feature_tables:
        return "absent"

    if version is None:
        raise SchemaCompatibilityError(
            "Optimization tables exist without a registered schema version: "
            f"{feature_tables}. Refusing to use or modify them."
        )
    if version != SCHEMA_VERSION:
        raise SchemaCompatibilityError(
            f"Optimization schema version {version} is not supported by "
            f"this code (expected {SCHEMA_VERSION})."
        )

    missing = [name for name in _EXPECTED_COLUMNS if name not in tables]
    if missing:
        raise SchemaCompatibilityError(
            f"Optimization schema version {version} is registered but "
            f"tables are missing: {missing}"
        )
    for table, expected in _EXPECTED_COLUMNS.items():
        actual = [
            row[1]
            for row in connection.execute(f"PRAGMA table_info({table})")
        ]
        if actual != expected:
            raise SchemaCompatibilityError(
                f"Table {table} does not match schema version "
                f"{SCHEMA_VERSION}: expected columns {expected}, "
                f"found {actual}"
            )
    return "current"


def _require_no_open_transaction(connection):
    if connection.in_transaction:
        raise TransactionConflictError(
            "The connection has an open transaction. Commit or roll it back "
            "before calling this function; QCH does not commit or roll back "
            "transactions it did not open."
        )


class _OwnTransaction:
    """Open, commit or roll back a transaction owned by this module."""

    def __init__(self, connection):
        self.connection = connection

    def __enter__(self):
        _require_no_open_transaction(self.connection)
        self.connection.execute("BEGIN IMMEDIATE")
        return self.connection

    def __exit__(self, exc_type, exc, tb):
        if exc_type is None:
            self.connection.commit()
        else:
            self.connection.rollback()
        return False


def ensure_optimization_schema(db_or_connection):
    """Create the version 1 optimization tables if they are absent.

    Returns ``True`` if tables were created, ``False`` if they already
    existed. Raises :class:`SchemaCompatibilityError` for incompatible
    existing structures and :class:`TransactionConflictError` if the
    connection has an open transaction. The DDL runs in one transaction.
    """
    connection = _connection_of(db_or_connection)
    _require_no_open_transaction(connection)
    if "circuits" not in _table_names(connection):
        raise SchemaCompatibilityError(
            "The database has no 'circuits' table; it is not a QCH database"
        )
    if get_optimization_schema_state(connection) == "current":
        return False
    with _OwnTransaction(connection):
        for statement in _SCHEMA_STATEMENTS:
            connection.execute(statement)
        connection.execute(
            """
            INSERT INTO qch_schema_components
                (component, version, installed_at)
            VALUES (?, ?, ?)
            """,
            (SCHEMA_COMPONENT, SCHEMA_VERSION, _utc_now()),
        )
    get_optimization_schema_state(connection)
    return True


def _utc_now():
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def _truncate(message):
    text = str(message)
    if len(text) > _MAX_ERROR_MESSAGE_LENGTH:
        return text[: _MAX_ERROR_MESSAGE_LENGTH - 3] + "..."
    return text


def _error_type(exc):
    cls = type(exc)
    return f"{cls.__module__}.{cls.__qualname__}"


# ---------------------------------------------------------------------------
# Execution
# ---------------------------------------------------------------------------


@dataclass
class RunResult:
    """Outcome of :func:`run_pass_sequence` when no exception is raised."""

    run_id: int
    status: str
    num_steps: int
    failed_step_index: int | None = None
    error_type: str | None = None
    error_message: str | None = None


@dataclass
class _StepState:
    started: bool = False
    succeeded: bool = False
    failed: bool = False
    exception: BaseException | None = None
    pass_time_s: float | None = None
    qiskit_time_s: float | None = None
    recorded: bool = False


class _RecordingFailure(Exception):
    """Internal: raised inside the pass manager callback to stop the run."""

    def __init__(self, step_index, phase, cause, step_row_written=False):
        super().__init__(f"Recording failed at step {step_index} ({phase})")
        self.step_index = step_index
        self.phase = phase
        self.cause = cause
        self.step_row_written = step_row_written


class _ForeignTaskError(Exception):
    """Internal: the pass manager executed a task that is not ours."""


class _StepRecorder:
    def __init__(self, connection, run_id, verify):
        self.connection = connection
        self.run_id = run_id
        self.verify = verify
        self.previous_fingerprint = None
        self.previous_snapshot_id = None

    def record_success(
        self, step_index, circuit, prepared, *, copy_time, pass_time,
        qiskit_time,
    ):
        phase = "metrics"
        try:
            start = perf_counter()
            metrics = compute_step_metrics(circuit)
            metrics_time = perf_counter() - start

            phase = "serialize"
            start = perf_counter()
            fingerprint = representation_fingerprint(circuit)
            if step_index > 0 and fingerprint == self.previous_fingerprint:
                changed = 0
                snapshot = None
            else:
                changed = None if step_index == 0 else 1
                snapshot = serialize_snapshot(
                    circuit, fingerprint, verify=self.verify
                )
            serialize_time = perf_counter() - start

            phase = "database"
            start = perf_counter()
            with _OwnTransaction(self.connection):
                if snapshot is None:
                    snapshot_id = self.previous_snapshot_id
                else:
                    snapshot_id = self._insert_snapshot(snapshot)
                step_row_id = self._insert_step(
                    step_index=step_index,
                    prepared=prepared,
                    status="SUCCEEDED",
                    snapshot_id=snapshot_id,
                    changed=changed,
                    metrics=metrics,
                    timings={
                        "pass_time_s": pass_time,
                        "qiskit_reported_time_s": qiskit_time,
                        "snapshot_copy_time_s": copy_time,
                        "metrics_time_s": metrics_time,
                        "serialize_time_s": serialize_time,
                    },
                )
            db_write_time = perf_counter() - start
        except _RecordingFailure:
            raise
        except Exception as exc:
            raise _RecordingFailure(step_index, phase, exc) from exc

        # The step row is committed. Storing the measured write time is a
        # second, separate update whose own duration is not measured.
        try:
            with _OwnTransaction(self.connection):
                self.connection.execute(
                    "UPDATE optimization_steps SET db_write_time_s = ? "
                    "WHERE id = ?",
                    (db_write_time, step_row_id),
                )
        except Exception as exc:
            raise _RecordingFailure(
                step_index, "database", exc, step_row_written=True
            ) from exc

        self.previous_fingerprint = fingerprint
        self.previous_snapshot_id = snapshot_id

    def _insert_snapshot(self, snapshot):
        cursor = self.connection.execute(
            """
            INSERT INTO circuit_snapshots (
                representation_sha256, representation_version, qpy,
                qpy_sha256, qpy_format_version, qiskit_version, qasm2,
                qasm2_error, num_qubits, num_clbits, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                snapshot.representation_sha256,
                REPRESENTATION_VERSION,
                sqlite3.Binary(snapshot.qpy_bytes),
                snapshot.qpy_sha256,
                snapshot.qpy_format_version,
                qiskit.__version__,
                snapshot.qasm2_text,
                snapshot.qasm2_error,
                snapshot.num_qubits,
                snapshot.num_clbits,
                _utc_now(),
            ),
        )
        return cursor.lastrowid

    def _insert_step(
        self, *, step_index, prepared, status, snapshot_id=None,
        changed=None, metrics=None, timings=None, error=None,
    ):
        metrics = metrics or {}
        timings = timings or {}
        columns = [
            "run_id", "step_index", "pass_name", "pass_params_json",
            "status", "error_type", "error_message", "snapshot_id",
            "changed", *_METRIC_COLUMNS, "pass_time_s",
            "qiskit_reported_time_s", "snapshot_copy_time_s",
            "metrics_time_s", "serialize_time_s",
        ]
        values = [
            self.run_id,
            step_index,
            None if prepared is None else prepared.name,
            None if prepared is None else json.dumps(
                prepared.params, sort_keys=True
            ),
            status,
            None if error is None else _error_type(error),
            None if error is None else _truncate(error),
            snapshot_id,
            changed,
            *[metrics.get(name) for name in _METRIC_COLUMNS],
            timings.get("pass_time_s"),
            timings.get("qiskit_reported_time_s"),
            timings.get("snapshot_copy_time_s"),
            timings.get("metrics_time_s"),
            timings.get("serialize_time_s"),
        ]
        placeholders = ", ".join("?" for _ in columns)
        cursor = self.connection.execute(
            f"INSERT INTO optimization_steps ({', '.join(columns)}) "
            f"VALUES ({placeholders})",
            values,
        )
        return cursor.lastrowid


def _wrap_run(instance, state):
    """Time ``instance.run`` with ``perf_counter`` without changing it.

    The wrapper is installed as an instance attribute. Pass identity
    (hash and equality, derived from constructor arguments) is unchanged.
    The return value is passed through and exceptions are re-raised
    unchanged after being recorded.
    """
    original_run = instance.run

    def timed_run(dag):
        state.started = True
        start = perf_counter()
        try:
            result = original_run(dag)
        except BaseException as exc:
            state.pass_time_s = perf_counter() - start
            state.failed = True
            state.exception = exc
            raise
        state.pass_time_s = perf_counter() - start
        state.succeeded = True
        return result

    timed_run.__wrapped__ = original_run
    instance.run = timed_run


def _exception_chain(exc):
    seen = []
    current = exc
    while current is not None and all(current is not item for item in seen):
        seen.append(current)
        current = current.__cause__ or current.__context__
    return seen


def run_pass_sequence(db, circuit_name, passes, *, label=None,
                      verify_snapshots=True):
    """Execute an ordered pass sequence on a stored circuit and record it.

    Parameters:
        db: a :class:`qch.QCHDatabase` (or a ``sqlite3.Connection`` to a
            QCH database). Must not have an open transaction.
        circuit_name: name in the ``circuits`` table.
        passes: ordered list of :class:`PassSpec` or
            ``{"name": ..., "params": {...}}`` mappings.
        label: optional free-text label stored with the run.
        verify_snapshots: load every new QPY snapshot back and compare
            its representation fingerprint before storing it.

    Behavior:
        * Invalid pass configurations, a missing circuit, unparsable QASM
          or unsupported circuit content raise before ANY row is written
          (no run record is created).
        * A pass exception does not raise: the run is stored with status
          ``PASS_FAILED``; earlier steps stay ``SUCCEEDED``, the failing
          step is ``PASS_FAILED`` and later steps are ``NOT_RUN``.
        * Metrics, serialization or database failures raise
          :class:`RecordingError` (never reported as a pass failure). The
          run is marked ``RECORDING_FAILED`` when that state can still be
          written; ``RecordingError.failure_persisted`` tells which.
        * Any other unexpected error marks the run ``ABORTED`` (when
          possible) and raises :class:`ExecutionAbortedError`.

    Returns:
        :class:`RunResult` for ``SUCCEEDED`` and ``PASS_FAILED`` runs.
    """
    connection = _connection_of(db)
    prepared = prepare_pass_sequence(passes)
    _require_no_open_transaction(connection)

    row = connection.execute(
        "SELECT id, qasm FROM circuits WHERE name = ?", (circuit_name,)
    ).fetchone()
    if row is None:
        raise CircuitNotFoundError(f"Circuit not found: {circuit_name}")
    circuit_id, qasm = row

    try:
        input_circuit = QuantumCircuit.from_qasm_str(qasm)
    except Exception as exc:
        raise UnsupportedCircuitError(
            f"Circuit {circuit_name!r} could not be parsed as OpenQASM 2: "
            f"{type(exc).__name__}: {exc}"
        ) from exc
    validate_supported_circuit(input_circuit)

    return _execute_sequence(
        connection,
        source_circuit_id=circuit_id,
        source_qasm_sha256=hashlib.sha256(qasm.encode("utf-8")).hexdigest(),
        input_circuit=input_circuit,
        prepared=prepared,
        label=label,
        verify_snapshots=verify_snapshots,
    )


def _execute_sequence(
    connection, *, source_circuit_id, source_qasm_sha256, input_circuit,
    prepared, label, verify_snapshots,
):
    _require_no_open_transaction(connection)
    if connection.execute("PRAGMA foreign_keys").fetchone()[0] != 1:
        connection.execute("PRAGMA foreign_keys = ON")
    ensure_optimization_schema(connection)

    instances = [item.build() for item in prepared]
    states = [_StepState() for _ in prepared]
    position_by_task = {}
    for position, (instance, state) in enumerate(
        zip(instances, states), start=1
    ):
        _wrap_run(instance, state)
        position_by_task[id(instance)] = position

    sequence_json = json.dumps(
        [{"name": item.name, "params": item.params} for item in prepared],
        sort_keys=True,
    )
    with _OwnTransaction(connection):
        cursor = connection.execute(
            """
            INSERT INTO optimization_runs (
                source_circuit_id, source_qasm_sha256, label,
                pass_sequence_json, num_steps, compiler_name,
                compiler_version, python_version, platform,
                metrics_version, representation_version, execution_mode,
                seed, status, started_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL,
                      'RUNNING', ?)
            """,
            (
                source_circuit_id,
                source_qasm_sha256,
                label,
                sequence_json,
                len(prepared),
                COMPILER_NAME,
                qiskit.__version__,
                platform.python_version(),
                f"{platform.system()} {platform.machine()}",
                METRICS_VERSION,
                REPRESENTATION_VERSION,
                EXECUTION_MODE,
                _utc_now(),
            ),
        )
        run_id = cursor.lastrowid

    recorder = _StepRecorder(connection, run_id, verify_snapshots)

    # Step 0: the input as the pass manager will see it. The pass manager
    # front end converts the circuit with circuit_to_dag(copy_operations=
    # True); snapshots use dag_to_circuit(copy_operations=True) at every
    # step, so all steps share one conversion path.
    try:
        start = perf_counter()
        step0 = dag_to_circuit(
            circuit_to_dag(input_circuit, copy_operations=True),
            copy_operations=True,
        )
        copy_time = perf_counter() - start
        recorder.record_success(
            0, step0, None, copy_time=copy_time, pass_time=None,
            qiskit_time=None,
        )
    except _RecordingFailure as failure:
        _handle_recording_failure(
            connection, recorder, prepared, states, run_id, failure
        )

    def callback(pass_, dag, time, property_set, count):  # noqa: A002
        position = position_by_task.get(id(pass_))
        if position is None:
            raise _ForeignTaskError(
                f"Unexpected task executed by the pass manager: "
                f"{type(pass_).__name__}"
            )
        state = states[position - 1]
        # ``time`` is Qiskit's own measurement (time.time() around the
        # call of ``run``); it is stored separately from pass_time_s.
        state.qiskit_time_s = time
        if state.failed:
            # Qiskit also calls the callback when a pass raised; the
            # failure is recorded after PassManager.run() re-raises.
            return
        if not state.succeeded or state.recorded:
            raise _ForeignTaskError(
                f"Inconsistent callback for step {position}"
            )
        start = perf_counter()
        snapshot = dag_to_circuit(dag, copy_operations=True)
        snapshot_copy_time = perf_counter() - start
        recorder.record_success(
            position,
            snapshot,
            prepared[position - 1],
            copy_time=snapshot_copy_time,
            pass_time=state.pass_time_s,
            qiskit_time=time,
        )
        state.recorded = True

    pass_manager = PassManager(instances)
    wall_start = perf_counter()
    try:
        output = pass_manager.run(input_circuit, callback=callback)
    except BaseException as exc:
        wall_time = perf_counter() - wall_start
        return _handle_run_exception(
            connection, recorder, prepared, states, run_id, exc, wall_time
        )
    wall_time = perf_counter() - wall_start

    missing = [
        position
        for position, state in enumerate(states, start=1)
        if not state.recorded
    ]
    if missing:
        error = ExecutionAbortedError(
            f"Pass manager finished without executing steps {missing}",
            run_id=run_id,
        )
        _finalize_aborted(connection, prepared, run_id, error, wall_time)
        raise error

    if representation_fingerprint(output) != recorder.previous_fingerprint:
        error = ExecutionAbortedError(
            "PassManager output does not match the last recorded snapshot",
            run_id=run_id,
        )
        _finalize_aborted(connection, prepared, run_id, error, wall_time)
        raise error

    try:
        with _OwnTransaction(connection):
            connection.execute(
                """
                UPDATE optimization_runs
                SET status = 'SUCCEEDED', finished_at = ?,
                    pass_manager_wall_time_s = ?
                WHERE id = ?
                """,
                (_utc_now(), wall_time, run_id),
            )
    except Exception as exc:
        raise RecordingError(
            f"Run {run_id}: all steps were recorded but the final run "
            f"status could not be written: {exc}",
            run_id=run_id,
            step_index=None,
            phase="finalize",
            failure_persisted=False,
        ) from exc

    return RunResult(run_id=run_id, status="SUCCEEDED",
                     num_steps=len(prepared))


def _handle_run_exception(
    connection, recorder, prepared, states, run_id, exc, wall_time
):
    chain = _exception_chain(exc)

    for failure in chain:
        if isinstance(failure, _RecordingFailure):
            _handle_recording_failure(
                connection, recorder, prepared, states, run_id, failure,
                wall_time=wall_time,
            )

    for position, state in enumerate(states, start=1):
        if state.failed and any(state.exception is item for item in chain):
            return _finalize_pass_failure(
                connection, recorder, prepared, states, run_id, position,
                state, wall_time,
            )

    error = ExecutionAbortedError(
        f"Run {run_id} aborted: {type(exc).__name__}: {exc}",
        run_id=run_id,
    )
    _finalize_aborted(connection, prepared, run_id, exc, wall_time,
                      public_error=error)
    if isinstance(exc, Exception):
        raise error from exc
    raise exc


def _finalize_pass_failure(
    connection, recorder, prepared, states, run_id, position, state,
    wall_time,
):
    error = state.exception
    try:
        with _OwnTransaction(connection):
            recorder._insert_step(
                step_index=position,
                prepared=prepared[position - 1],
                status="PASS_FAILED",
                timings={
                    "pass_time_s": state.pass_time_s,
                    "qiskit_reported_time_s": state.qiskit_time_s,
                },
                error=error,
            )
            _insert_not_run(recorder, prepared, position + 1)
            connection.execute(
                """
                UPDATE optimization_runs
                SET status = 'PASS_FAILED', failed_step_index = ?,
                    error_type = ?, error_message = ?, finished_at = ?,
                    pass_manager_wall_time_s = ?
                WHERE id = ?
                """,
                (
                    position,
                    _error_type(error),
                    _truncate(error),
                    _utc_now(),
                    wall_time,
                    run_id,
                ),
            )
    except Exception as db_exc:
        raise RecordingError(
            f"Run {run_id}: step {position} ({prepared[position - 1].name}) "
            f"failed with {_error_type(error)}: {_truncate(error)}; the "
            f"failure could not be recorded: {db_exc}",
            run_id=run_id,
            step_index=position,
            phase="database",
            failure_persisted=False,
        ) from db_exc

    return RunResult(
        run_id=run_id,
        status="PASS_FAILED",
        num_steps=len(prepared),
        failed_step_index=position,
        error_type=_error_type(error),
        error_message=_truncate(error),
    )


def _insert_not_run(recorder, prepared, first_position):
    for item in prepared[first_position - 1:]:
        recorder._insert_step(
            step_index=item.position, prepared=item, status="NOT_RUN"
        )


def _handle_recording_failure(
    connection, recorder, prepared, states, run_id, failure, wall_time=None,
):
    cause = failure.cause
    position = failure.step_index
    persisted = False
    try:
        if connection.in_transaction:
            # Only our own failed write can be pending here.
            connection.rollback()
        with _OwnTransaction(connection):
            if not failure.step_row_written:
                state = states[position - 1] if position > 0 else None
                recorder._insert_step(
                    step_index=position,
                    prepared=None if position == 0 else prepared[position - 1],
                    status="RECORDING_FAILED",
                    timings={
                        "pass_time_s": None if state is None
                        else state.pass_time_s,
                        "qiskit_reported_time_s": None if state is None
                        else state.qiskit_time_s,
                    },
                    error=cause,
                )
            _insert_not_run(recorder, prepared, position + 1)
            connection.execute(
                """
                UPDATE optimization_runs
                SET status = 'RECORDING_FAILED', failed_step_index = ?,
                    error_type = ?, error_message = ?, finished_at = ?,
                    pass_manager_wall_time_s = ?
                WHERE id = ?
                """,
                (
                    position,
                    _error_type(cause),
                    _truncate(f"[{failure.phase}] {cause}"),
                    _utc_now(),
                    wall_time,
                    run_id,
                ),
            )
        persisted = True
    except Exception as db_exc:
        raise RecordingError(
            f"Run {run_id}: recording failed at step {position} during "
            f"{failure.phase} ({_error_type(cause)}: {_truncate(cause)}), "
            f"and the failure state could not be written: {db_exc}",
            run_id=run_id,
            step_index=position,
            phase=failure.phase,
            failure_persisted=False,
        ) from db_exc

    raise RecordingError(
        f"Run {run_id}: recording failed at step {position} during "
        f"{failure.phase}: {_error_type(cause)}: {_truncate(cause)}. "
        "This is a recording infrastructure failure, not a pass failure.",
        run_id=run_id,
        step_index=position,
        phase=failure.phase,
        failure_persisted=persisted,
    ) from cause


def _finalize_aborted(connection, prepared, run_id, exc, wall_time,
                      public_error=None):
    try:
        if connection.in_transaction:
            connection.rollback()
        with _OwnTransaction(connection):
            recorded = {
                row[0]
                for row in connection.execute(
                    "SELECT step_index FROM optimization_steps "
                    "WHERE run_id = ?",
                    (run_id,),
                )
            }
            for item in prepared:
                if item.position not in recorded:
                    connection.execute(
                        """
                        INSERT INTO optimization_steps
                            (run_id, step_index, pass_name,
                             pass_params_json, status)
                        VALUES (?, ?, ?, ?, 'NOT_RUN')
                        """,
                        (
                            run_id,
                            item.position,
                            item.name,
                            json.dumps(item.params, sort_keys=True),
                        ),
                    )
            connection.execute(
                """
                UPDATE optimization_runs
                SET status = 'ABORTED', error_type = ?, error_message = ?,
                    finished_at = ?, pass_manager_wall_time_s = ?
                WHERE id = ?
                """,
                (_error_type(exc), _truncate(exc), _utc_now(), wall_time,
                 run_id),
            )
        if public_error is not None:
            public_error.failure_persisted = True
        elif isinstance(exc, ExecutionAbortedError):
            exc.failure_persisted = True
    except Exception as db_exc:
        raise RecordingError(
            f"Run {run_id} aborted ({_error_type(exc)}: {_truncate(exc)}) "
            f"and the aborted state could not be written: {db_exc}",
            run_id=run_id,
            phase="database",
            failure_persisted=False,
        ) from db_exc


# ---------------------------------------------------------------------------
# Read-only queries
# ---------------------------------------------------------------------------


def open_readonly(db_path):
    """Open an existing SQLite database in SQLite read-only mode.

    Never creates a file: a missing path raises ``FileNotFoundError``.
    """
    path = Path(db_path)
    if not path.is_file():
        raise FileNotFoundError(f"Database file not found: {path}")
    uri = path.resolve().as_uri() + "?mode=ro"
    connection = sqlite3.connect(uri, uri=True)
    connection.execute("PRAGMA query_only = ON")
    return connection


_RUN_COLUMNS = [
    "id", "source_circuit_id", "source_qasm_sha256", "label",
    "pass_sequence_json", "num_steps", "compiler_name", "compiler_version",
    "python_version", "platform", "metrics_version",
    "representation_version", "execution_mode", "seed", "status",
    "failed_step_index", "error_type", "error_message", "started_at",
    "finished_at", "pass_manager_wall_time_s",
]

_STEP_COLUMNS = [
    "step_index", "pass_name", "pass_params_json", "status", "error_type",
    "error_message", "snapshot_id", "changed", *_METRIC_COLUMNS,
    "pass_time_s", "qiskit_reported_time_s", "snapshot_copy_time_s",
    "metrics_time_s", "serialize_time_s", "db_write_time_s",
]


def _run_row_to_dict(row, circuit_name):
    record = dict(zip(_RUN_COLUMNS, row))
    record["run_id"] = record.pop("id")
    record["source_circuit_name"] = circuit_name
    record["pass_sequence"] = json.loads(record.pop("pass_sequence_json"))
    return record


def list_optimization_runs(db_or_connection, circuit_name=None):
    """List runs (newest first). Returns ``[]`` if the tables are absent."""
    connection = _connection_of(db_or_connection)
    if get_optimization_schema_state(connection) == "absent":
        return []
    sql = (
        "SELECT "
        + ", ".join(f"r.{column}" for column in _RUN_COLUMNS)
        + ", c.name FROM optimization_runs AS r "
        "JOIN circuits AS c ON c.id = r.source_circuit_id"
    )
    params = []
    if circuit_name is not None:
        sql += " WHERE c.name = ?"
        params.append(circuit_name)
    sql += " ORDER BY r.id DESC"
    return [
        _run_row_to_dict(row[:-1], row[-1])
        for row in connection.execute(sql, params).fetchall()
    ]


def list_optimization_steps(db_or_connection, run_id):
    """List the steps of a run in step order (``[]`` if absent)."""
    connection = _connection_of(db_or_connection)
    if get_optimization_schema_state(connection) == "absent":
        return []
    rows = connection.execute(
        "SELECT " + ", ".join(_STEP_COLUMNS) + " FROM optimization_steps "
        "WHERE run_id = ? ORDER BY step_index",
        (run_id,),
    ).fetchall()
    steps = []
    for row in rows:
        step = dict(zip(_STEP_COLUMNS, row))
        params = step.pop("pass_params_json")
        step["pass_params"] = None if params is None else json.loads(params)
        steps.append(step)
    return steps


def get_optimization_run(db_or_connection, run_id):
    """Return one run with its steps, or ``None``.

    Also returns ``None`` when the optimization tables do not exist.
    """
    connection = _connection_of(db_or_connection)
    if get_optimization_schema_state(connection) == "absent":
        return None
    row = connection.execute(
        "SELECT "
        + ", ".join(f"r.{column}" for column in _RUN_COLUMNS)
        + ", c.name FROM optimization_runs AS r "
        "JOIN circuits AS c ON c.id = r.source_circuit_id WHERE r.id = ?",
        (run_id,),
    ).fetchone()
    if row is None:
        return None
    record = _run_row_to_dict(row[:-1], row[-1])
    record["steps"] = list_optimization_steps(connection, run_id)
    return record


def load_step_circuit(db_or_connection, run_id, step_index):
    """Restore the circuit snapshot of one step from its QPY payload.

    Raises ``LookupError`` if the run, the step or its snapshot does not
    exist (for example a failed or not-run step), and
    :class:`SnapshotIntegrityError` if the stored payload does not match
    its recorded SHA-256 or representation fingerprint.
    """
    connection = _connection_of(db_or_connection)
    if get_optimization_schema_state(connection) == "absent":
        raise LookupError("This database has no optimization tables")
    row = connection.execute(
        """
        SELECT s.status, s.snapshot_id, cs.qpy, cs.qpy_sha256,
               cs.representation_sha256, cs.representation_version
        FROM optimization_steps AS s
        LEFT JOIN circuit_snapshots AS cs ON cs.id = s.snapshot_id
        WHERE s.run_id = ? AND s.step_index = ?
        """,
        (run_id, step_index),
    ).fetchone()
    if row is None:
        raise LookupError(f"Run {run_id} has no step {step_index}")
    status, snapshot_id, data, qpy_sha, rep_sha, rep_version = row
    if snapshot_id is None:
        raise LookupError(
            f"Run {run_id} step {step_index} has status {status} and no "
            "circuit snapshot"
        )
    data = bytes(data)
    if hashlib.sha256(data).hexdigest() != qpy_sha:
        raise SnapshotIntegrityError(
            f"Snapshot {snapshot_id}: QPY payload does not match its SHA-256"
        )
    circuit = _load_qpy(data)
    if rep_version == REPRESENTATION_VERSION and (
        representation_fingerprint(circuit) != rep_sha
    ):
        raise SnapshotIntegrityError(
            f"Snapshot {snapshot_id}: restored circuit does not match its "
            "representation fingerprint"
        )
    return circuit


__all__ = [
    "CircuitNotFoundError",
    "EXECUTION_MODE",
    "ExecutionAbortedError",
    "METRICS_VERSION",
    "OptimizationError",
    "PASS_REGISTRY",
    "PassConfigError",
    "PassSpec",
    "RecordingError",
    "REPRESENTATION_VERSION",
    "RunResult",
    "SCHEMA_VERSION",
    "SchemaCompatibilityError",
    "SnapshotIntegrityError",
    "TransactionConflictError",
    "UnsupportedCircuitError",
    "compute_step_metrics",
    "ensure_optimization_schema",
    "get_optimization_run",
    "get_optimization_schema_state",
    "list_optimization_runs",
    "list_optimization_steps",
    "load_step_circuit",
    "open_readonly",
    "prepare_pass_sequence",
    "representation_fingerprint",
    "run_pass_sequence",
    "serialize_snapshot",
    "validate_supported_circuit",
]
