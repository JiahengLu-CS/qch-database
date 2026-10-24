# Optimization Pass Sequences (version 1)

`qch.optimization` runs an ordered sequence of whitelisted Qiskit
transformation passes on a circuit stored in a QCH database and records
step 0 (the input) and the result of every sequence position. The goal is
to compare pass orders, including repeated steps such as A -> B -> A, and
to inspect intermediate circuits, resource metrics and per-step timings.

Verified environment (2026-10-24): CPython 3.11.17 on Linux x86_64,
Qiskit 2.5.2, pytest 9.1.1. Other Qiskit versions are not verified.

## Python API

```python
from qch import QCHDatabase
from qch import optimization as opt

db = QCHDatabase("demo.db")
result = opt.run_pass_sequence(
    db,
    "my_circuit",
    [
        {"name": "InverseCancellation"},
        {"name": "Optimize1qGatesDecomposition",
         "params": {"basis": ["rz", "sx", "x", "cx"]}},
        {"name": "InverseCancellation"},
    ],
    label="IC -> O1Q -> IC",
)
db.close()

connection = opt.open_readonly("demo.db")      # SQLite read-only mode
run = opt.get_optimization_run(connection, result.run_id)
for step in run["steps"]:
    print(step["step_index"], step["pass_name"], step["status"],
          step["gate_count"], step["two_qubit_gate_count"],
          step["pass_time_s"])
circuit = opt.load_step_circuit(connection, result.run_id, 2)
connection.close()
```

| Function | Writes? | Notes |
|---|---|---|
| `run_pass_sequence(db, circuit_name, passes, *, label=None, verify_snapshots=True)` | yes | Creates the tables on first use, commits each step. Returns `RunResult`. |
| `ensure_optimization_schema(db)` | yes (only if absent) | Explicit schema creation. |
| `prepare_pass_sequence(passes)` | no | Validation only. |
| `open_readonly(path)` | no | Opens an existing file with `mode=ro`; never creates a file. |
| `get_optimization_schema_state(conn)` | no | `"absent"` or `"current"`; raises `SchemaCompatibilityError` otherwise. |
| `list_optimization_runs(conn, circuit_name=None)` | no | `[]` if the tables are absent. |
| `get_optimization_run(conn, run_id)` | no | Run plus steps, or `None` (also when tables are absent). |
| `list_optimization_steps(conn, run_id)` | no | `[]` if absent. |
| `load_step_circuit(conn, run_id, step_index)` | no | Restores from QPY; checks SHA-256 and fingerprint. `LookupError` for steps without a snapshot. |
| `compute_step_metrics(circuit)`, `representation_fingerprint(circuit)` | no | Pure functions. |

Note that `QCHDatabase(...)` itself runs DDL when it opens a database
(existing behavior). Use `open_readonly` for read-only inspection.

## Supported passes and parameters

Parameters must be JSON. Gates are constructed through explicit name
mappings; there is no `eval` and no dynamic import.

| Pass | Parameters |
|---|---|
| `InverseCancellation` | `gates_to_cancel`: `null` (Qiskit default list) or a list of self-inverse gate names (`x y z h cx cy cz ecr swap ch ccx ccz rccx cswap c3x`) and two-name inverse pairs (`[t, tdg]`, `[s, sdg]`, `[sx, sxdg]`, `[cs, csdg]`, either order). `run_default`: boolean, default `false`. |
| `Optimize1qGatesDecomposition` | `basis` (required): non-empty list of standard gate names that contains at least one complete one-qubit Euler basis (for example `rz, sx, x` or `u`). The basis is never inferred from the circuit. An explicit basis does NOT mean the whole output circuit was checked against a target gate set. |
| `RemoveIdentityEquivalent` | `approximation_degree`: number in (0, 1], default `1.0`. Target-based approximation (`null`) is not supported. |

None of the three passes uses a random seed; `optimization_runs.seed` is
always `NULL` in version 1. All three declare no required analysis passes
(checked when each instance is built; passes with `requires` are rejected).

## Execution semantics

* One new `PassManager` per run; each sequence position gets its own pass
  instance, so A -> B -> A records three steps. The pass manager context
  (property set) is shared within the run. This is not assumed to be
  equivalent to running each pass in its own `PassManager`.
* No `transpile` is run between or after steps and no target gate set
  conversion happens. Version 1 compares steps of the same run, starting
  from the stored circuit in its stored gate set.
* Step 0 is the input as the pass manager sees it:
  `dag_to_circuit(circuit_to_dag(input, copy_operations=True))`. All
  later snapshots are taken with the same conversion inside the Qiskit
  callback, immediately, with `copy_operations=True`.
* The stored `circuits.qasm` and the parsed input object are never
  modified; every run parses its own copy.
* In Qiskit 2.5.2 the callback is also invoked for a pass that raised.
  The failing position is identified from the per-instance run wrapper
  (exception object identity), not from the last callback.

### Statuses

Runs: `RUNNING`, `SUCCEEDED`, `PASS_FAILED`, `RECORDING_FAILED`,
`ABORTED`. Steps: `SUCCEEDED`, `PASS_FAILED`, `RECORDING_FAILED`,
`NOT_RUN`.

* Invalid configuration, missing circuit, unparsable QASM or unsupported
  circuit content: an exception is raised and NO rows are written.
* A pass exception: earlier steps stay `SUCCEEDED`, the failing step is
  `PASS_FAILED` (error type, message, measured pass time), later steps are
  `NOT_RUN`, and the run is `PASS_FAILED`. `run_pass_sequence` returns
  normally.
* Metrics, serialization or database failure: the run becomes
  `RECORDING_FAILED` and `RecordingError` is raised. It is never reported
  as a pass failure. If even the failure state cannot be written,
  `RecordingError.failure_persisted` is `False` and `run_id` is still set.
* Anything else: `ABORTED` and `ExecutionAbortedError`.

### Transactions

`run_pass_sequence` refuses to start when the connection has an open
transaction (`TransactionConflictError`); it never commits or rolls back
a transaction it did not open. Every write is its own short
`BEGIN IMMEDIATE ... COMMIT`, so completed steps survive a later failure.
`PRAGMA foreign_keys` is switched on for the connection if it is off.

## Snapshots

* `circuit_snapshots.qpy` (QPY, version recorded in `qpy_format_version`,
  plus `qiskit_version`) is the authoritative format. Each new snapshot
  is loaded back before it is stored and must reproduce the same
  representation fingerprint.
* `qasm2` is optional display/export text. If QASM 2 export fails,
  `qasm2` is `NULL`, `qasm2_error` holds the reason and the QPY snapshot
  is still stored.
* `representation_sha256` is a SHA-256 over registers, the bit-exact
  global phase and the ordered instruction list with exact parameter
  values (`float.hex`) and qubit/clbit indices. `qpy_sha256` is only an
  integrity check of the stored bytes, not a circuit identity.
* `changed` (steps >= 1) compares the representation fingerprint with the
  previous step. `0` means bit-identical representation, and the previous
  snapshot is reused. `1` means the representation differs; it does NOT
  claim or deny semantic equivalence. `Operator.equiv` is never used.
* Version 1 does not deduplicate snapshots across runs.

## Metrics (`qch-step-metrics/1`)

| Column | Definition |
|---|---|
| `num_qubits`, `num_clbits` | Register sizes, idle qubits included. |
| `gate_count` | `QuantumCircuit.size()`: barriers excluded, measure and reset included. Same definition as `features.gate_count`. |
| `depth` | `QuantumCircuit.depth()`: same definition as `features.depth`. |
| `single_qubit_gate_count`, `two_qubit_gate_count`, `multi_qubit_gate_count` | Gates on 1, 2, >= 3 qubits. Only standard-library gates and opaque gates; never measure, reset or barrier. Counted as represented (nothing decomposed). |
| `measure_count`, `reset_count`, `barrier_count` | Instruction counts. |
| `opaque_gate_count` | Gates without definition that are not standard-library classes, e.g. `opaque ecr` from MQT Bench OpenQASM 2 files. |

Within the supported scope, `single + two + multi + measure + reset ==
gate_count` is checked for every snapshot.

Comparability with the legacy `features` table: `gate_count`, `depth`
and `num_qubits` use the same definitions. The legacy
`features.two_qubit_gate_count` counts every two-qubit instruction,
including two-qubit barriers, so it can differ from the step metric.
`features.t_count` is not part of the step metrics and was not changed.

Supported circuit content: Qiskit standard-library gates (matched by
class, not only by name), opaque gates without a definition, `measure`,
`reset`, `barrier`. Rejected with an English error: custom/composite gates
with a definition, `delay`, `global_phase` gates, unbound parameters,
control flow, classical variables and stretches.

Important for MQT Bench data: `opaque ecr` is parsed by Qiskit as a
generic `Gate`, not `ECRGate`, so `InverseCancellation` does not cancel
these `ecr` gates.

## Timings

All times are seconds; a value is `NULL` when it is not measured.

| Column | Interval |
|---|---|
| `pass_time_s` | `perf_counter` around the pass's own `run()` call only (wrapper installed as an instance attribute; pass hash, equality, return value and exceptions are unchanged). |
| `qiskit_reported_time_s` | Qiskit's own value passed to the callback (`time.time()` around the same call). Kept separately; it can be coarser. |
| `snapshot_copy_time_s` | `dag_to_circuit(..., copy_operations=True)` in the callback. |
| `metrics_time_s` | `compute_step_metrics`. |
| `serialize_time_s` | Fingerprint, QPY dump, QPY verification load, QASM export (fingerprint only for unchanged steps). |
| `db_write_time_s` | Snapshot and step INSERT plus COMMIT. The follow-up UPDATE that stores this value is not included. |
| `optimization_runs.pass_manager_wall_time_s` | `PassManager.run`, including the recording work done in callbacks. |

Timings are not used as test assertions.

## Schema

Tables: `qch_schema_components` (component `optimization`, version 1),
`circuit_snapshots`, `optimization_runs`, `optimization_steps`.
Snapshots are not written to `gates`, and no logical circuit versions,
transformation edges or benchmark links are created. Existing structures
that do not match version 1 (missing registration, other version,
different columns) raise `SchemaCompatibilityError`; nothing is
overwritten.

## Command-line demo

`examples/pass_sequence_demo.py` has the subcommands `prepare` (new demo
database from a read-only source database or a QASM file), `run`, `list`
and `show` (`list` and `show` use `open_readonly`). Example sequences are
in `examples/sequences/`.

Windows PowerShell walkthrough (from the repository root, with the
project environment described in the README):

```powershell
$Py   = ".\.venv\Scripts\python.exe"
$Src  = "C:\Users\jilu\Project\QCDB\Data\mqt_bench_1000_test.db"
$Name = "MQTBench_1000_sample:qft_nativegates_ibm_qiskit_opt0_7.qasm"
New-Item -ItemType Directory -Force demo | Out-Null
$Demo = ".\demo\pass_sequences_demo.db"

# 1. Copy one circuit (read-only source) into a NEW demo database.
& $Py examples\pass_sequence_demo.py prepare --source-db $Src --circuit $Name --demo-db $Demo

# 2. Run two sequences (writes to the demo database only).
& $Py examples\pass_sequence_demo.py run --db $Demo --circuit $Name --sequence-file examples\sequences\ic_o1q_ic_ibm.json --label "IC -> O1Q -> IC"
& $Py examples\pass_sequence_demo.py run --db $Demo --circuit $Name --sequence-file examples\sequences\o1q_ic_o1q_rie_ibm.json --label "O1Q -> IC -> O1Q -> RIE"

# 3. New processes, read-only connection: list, show, restore a step.
& $Py examples\pass_sequence_demo.py list --db $Demo
& $Py examples\pass_sequence_demo.py show --db $Demo --run-id 1
& $Py examples\pass_sequence_demo.py show --db $Demo --run-id 1 --restore-step 2
```

`prepare` refuses to overwrite an existing demo database; delete
`demo\pass_sequences_demo.db` yourself to start over.

## Known limitations

* Three passes only; no permutations, batches, web UI, other compilers,
  automatic ordering or target gate set conversion.
* `changed` is representation-based; a pass that only reorders
  independent instructions would also be reported as changed.
* No cross-run snapshot deduplication.
* Parameterized circuits, control flow, custom composite gates,
  `delay` and `global_phase` gates are rejected.
* `RemoveIdentityEquivalent` with a `Target` and `Optimize1qGatesDecomposition`
  with a `Target` are not supported.
