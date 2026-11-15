# QCH Database — Development Progress

## 1. Project Overview

Project: QCH Database — Quantum Circuit Database System

GitHub: https://github.com/JiahengLu-CS/qch-database

Goal:
Develop an open-source database system for managing, querying,
comparing, and optimizing quantum circuits.

Long-term research directions:
- Quantum circuit storage and management
- Circuit feature extraction
- Circuit search and query processing
- Logical circuits and version evolution
- Multi-objective and Pareto optimization
- QCH-SQL query language
- Structural similarity and circuit comparison
- Large-scale benchmark ingestion

## 2. Development Environment

- Operating system: Windows 11
- Python: 3.11.8
- Database engine: SQLite
- Quantum framework: Qiskit 2.3.0
- Testing: pytest 9.1.1
- Package name: qch
- Current local package version: 0.1.0

Repository structure:

    qch-database/
      pyproject.toml
      README.md
      src/qch/
        __init__.py
        database.py
        circuit.py
      tests/
        test_database.py
      docs/
        PROGRESS.md

## 3. Completed Milestones

### Milestone 1: Project Initialization

- Created the Python package structure.
- Configured pyproject.toml.
- Initialized Git and GitHub repository.
- Established the QCHDatabase Python class.

### Milestone 2: Circuit Storage

Implemented:

- add_circuit(name, qasm_path)
- get_circuit(name)

Capabilities:
- Import OpenQASM 2.0 circuits.
- Store circuit source in SQLite.
- Retrieve stored circuit source.

### Milestone 3: Circuit Features and Gates

Implemented:

- get_features(name)
- get_gates(name)

Stored features:
- Number of qubits
- Gate count
- Circuit depth

Stored gate information:
- Gate index
- Gate type
- Qubit indices

Architecture improvement:
- Parse circuits during import.
- Store gates and features in SQLite.
- Read stored results without reparsing during queries.
- Use transactions for circuit insertion.

### Milestone 4: Database Persistence

- Verified data remains available after closing the database.
- Verified reopening the database preserves gates and features.

### Milestone 5: Circuit Listing and Filtering

Implemented:

- list_circuits()
- find_circuits()

Supported filters:
- min_qubits
- max_qubits
- min_depth
- max_depth
- max_gates

### Milestone 6: Query Ordering

Added sorting to find_circuits().

Supported order_by values:
- name
- num_qubits
- gate_count
- depth

Supported descending=True/False.

Invalid sorting fields raise ValueError.

## 4. Current Stable Checkpoint

Date: 2026-10-17

Git commit: ed783df

Commit message:
Add circuit ordering and sorting tests

GitHub branch: main

Automated tests: 9 passed

Test command:

    python -m pytest tests/test_database.py -v

The latest stable checkpoint has been pushed to GitHub.

## 5. Known Limitations

- The database currently uses a simple circuit-name model.
- Logical circuits and circuit versions are not yet represented.
- Existing databases created before import-time parsing may
  have missing stored features or gates.
- QCH-SQL is not implemented in this new package.
- Pareto queries are not implemented.
- Large-scale benchmark ingestion is not yet implemented.
- The current tests use small example circuits.

## 6. Next Development Tasks

Priority 1: Circuit Version Management

- Inspect the existing SQLite schema.
- Design logical_circuit and circuit_version entities.
- Preserve compatibility with existing stored circuits.
- Add APIs for listing and retrieving versions.
- Add automated migration and persistence tests.

Priority 2: Multi-objective Queries

- Define comparable circuit version groups.
- Implement Pareto frontier queries.
- Support gate count and depth as initial metrics.
- Add correctness tests.

Priority 3: QCH-SQL

- Define the initial query syntax.
- Map QCH-SQL queries to database operations.
- Add filtering, ordering, and metric queries.

Priority 4: Broader Circuit Support

- Import benchmark collections.
- Extend circuit metadata and structural features.
- Support circuit transformations and provenance.

## 7. Development Workflow

For each development step:

1. Implement one small feature.
2. Add automated tests.
3. Run pytest.
4. Review changes.
5. Commit changes to Git.
6. Push to GitHub.
7. Update this progress document when a milestone is reached.

## 8. Next Session Starting Point

Start by inspecting the current SQLite schema:

- circuits
- gates
- features

Then design a backward-compatible version management model.

Do not delete existing circuit data during migration.

## 9. Important Links

GitHub:
https://github.com/JiahengLu-CS/qch-database

Earlier QCH demonstration:
https://quantum-circuit-demo01-appd9ztiavchu2gtvrfu9dp.streamlit.app/

Note:
The QCH Demo and the new QCH Database package are related
but currently separate development efforts.

## 2026-10-18: Circuit Version Management Milestone

### Completed
- Added logical_circuits and circuit_versions tables.
- Implemented add_logical_circuit().
- Implemented add_version() with automatic version numbering.
- Implemented list_versions() with circuit features.
- Supported sharing one stored circuit across multiple logical circuits.
- Added tests for invalid references and version uniqueness.
- Preserved existing circuit storage and query APIs.

### Verification
- 14 pytest tests passed.
- Version listing tested with an in-memory SQLite database.
- Previous stable GitHub commit: ca07138.

### Next Steps
- Implement get_version().
- Add persistence tests for version management.
- Design safe schema migration for existing databases.
- Improve version numbering for concurrent writers.
- Explore circuit evolution relationships and Pareto queries.

## Circuit Version Comparison Milestone

### Completed
- Implemented get_version(logical_name, version_number).
- Implemented compare_versions(logical_name, version_a, version_b).
- Added comparison of num_qubits, gate_count, and depth.
- Defined delta as version_b minus version_a.
- Handled missing metrics using None.
- Added error handling for missing versions.
- Verified 16 automated pytest tests passed.

### Example
- GHZ-3 version 1: 3 gates, depth 3.
- GHZ-3 version 2: 5 gates, depth 4.
- Gate count delta: +2.
- Depth delta: +1.

### Next Steps
- Design circuit evolution graph.
- Store transformation relationships between versions.
- Support circuit evolution queries.
- Extend metrics for multi-objective optimization.
- Develop Pareto frontier queries.

## Circuit Evolution Graph - Phase 1

### Completed
- Added transformation_edges table.
- Implemented add_transformation().
- Implemented list_transformations().
- Supported branching circuit evolution.
- Recorded transformation types and descriptions.
- Prevented self-transformation and duplicate edges of the same type.
- Validated source and target versions within the same logical circuit.
- Added tests for transformation storage and listing.
- Verified 18 pytest tests passed.

### Current Limitations
- Cycle detection is not yet implemented.
- Transformation semantics are not automatically verified.
- Graph traversal and ancestry queries are not yet supported.
- Existing database schema migration is not yet implemented.

### Next Steps
- Implement cycle detection.
- Implement evolution path queries.
- Extend circuit metrics and transformation provenance.
- Develop Pareto frontier queries.

## Circuit Evolution Graph - Cycle Detection

### Completed
- Implemented cycle detection in add_transformation().
- Used SQLite recursive CTE (WITH RECURSIVE).
- Rejected transformations that create directed cycles.
- Preserved valid branching evolution relationships.
- Verified failed cycle insertions do not modify the graph.
- Added test_transformation_cycle_detection().
- Verified 19 automated pytest tests passed.

### Current Design
- Circuit versions are nodes.
- Transformation records are directed edges.
- New edges are checked for cycles before insertion.
- Evolution graphs support branching and multiple incoming edges.

### Limitations
- Concurrent writers are not yet fully supported.
- Existing databases are not yet checked for historical cycles.
- Ancestor and descendant queries are not yet implemented.
- Transformation semantics are not automatically verified.

### Next Steps
- Implement get_ancestors().
- Implement get_descendants().
- Support evolution path queries.
- Extend transformation provenance and metrics.

## Circuit Provenance - Phase 1

### Completed
- Implemented get_ancestors(logical_name, version_number).
- Implemented get_descendants(logical_name, version_number).
- Used SQLite recursive CTEs for graph traversal.
- Supported branching and merging evolution histories.
- Removed duplicate results when multiple paths reach the same version.
- Returned version numbers in ascending order.
- Added automated tests for ancestor and descendant queries.
- Verified 21 pytest tests passed.

### Current Capabilities
- Store quantum circuits and structural features.
- Manage logical circuits and multiple versions.
- Compare structural metrics between versions.
- Record typed transformation edges.
- Prevent cycles when adding transformation edges.
- Query all ancestors and descendants of a version.

### Limitations
- Evolution path queries are not yet implemented.
- Transformation semantics are not automatically verified.
- Database migrations are not yet implemented.
- Concurrent graph updates require stronger transaction handling.

### Next Steps
- Implement find_evolution_paths().
- Support transformation history queries.
- Add optimization metrics and provenance metadata.
- Develop graph-based quantum circuit optimization analysis.

## Evolution Path Query - Phase 1

### Completed
- Implemented find_evolution_paths().
- Used depth-first search (DFS) to enumerate directed paths.
- Supported branching and merging evolution graphs.
- Prevented repeated node visits during path traversal.
- Supported max_paths to limit returned results.
- Returned deterministic paths ordered by version number.
- Added automated tests for path enumeration.
- Verified 22 pytest tests passed.

### Current Capabilities
- Store quantum circuits and structural features.
- Manage logical circuits and circuit versions.
- Compare circuit metrics between versions.
- Record transformation relationships.
- Detect cycles when inserting transformation edges.
- Query ancestors and descendants.
- Find directed evolution paths between versions.

### Limitations
- Paths currently contain version numbers only, not edge metadata.
- Multiple transformation types between the same versions are collapsed.
- Path enumeration can be expensive for large evolution graphs.
- max_paths limits output size but does not guarantee optimal paths.
- Transformation correctness is not yet verified automatically.

### Next Steps
- Support transformation-aware evolution paths.
- Implement shortest-path queries.
- Develop cost-based circuit evolution analysis.
- Extend provenance and optimization metrics.

## Best Circuit Query - Phase 1

### Completed
- Implemented find_best_version().
- Supported minimum gate_count, depth and num_qubits.
- Used SQL ORDER BY to select the best version.
- Added deterministic tie-breaking by version number.
- Added test_find_best_version().
- Verified 23 automated pytest tests passed.

### Limitations
- Optimization currently uses one structural metric at a time.
- Semantic equivalence between circuit versions is not verified.
- Hardware-specific costs are not yet considered.

### Next Steps
- Implement Pareto Frontier Query.
- Support multi-objective circuit optimization.
- Extend circuit features with two-qubit gate count and T-count.
- Integrate circuit equivalence verification.

## Pareto Frontier Query - Phase 1

### Completed
- Implemented find_pareto_versions().
- Supported multi-objective minimization of gate_count, depth and num_qubits.
- Implemented Pareto dominance checking.
- Supported single-metric and multi-metric queries.
- Preserved multiple non-dominated circuit versions.
- Added input validation for unsupported and duplicate metrics.
- Added test_find_pareto_versions().
- Verified 24 automated pytest tests passed.

### Current Limitations
- Pareto comparison uses an O(N^2 * K) algorithm.
- Only structural circuit metrics are supported.
- Circuit semantic equivalence is not verified.
- Hardware-specific metrics are not yet available.

### Next Steps
- Add two-qubit gate count and T-count.
- Extend Pareto queries with hardware-aware metrics.
- Integrate semantic equivalence verification.
- Explore efficient Skyline query processing.



## Quantum Resource Metrics and Optimization (October 2026)

### Implemented
- Extended circuit features with `two_qubit_gate_count` and `t_count`.
- Added automatic SQLite schema migration for existing databases.
- Added `backfill_resource_metrics()` to recover resource metrics from stored gate records.
- Extended `get_features()` to return five circuit metrics.
- Extended `find_best_version()` to support quantum resource metrics.
- Extended `find_pareto_versions()` to support multi-objective quantum resource optimization.

### Testing
- 30 automated tests passed using Python 3.11.8 and pytest 9.1.1.
- Verified schema migration, persistence, resource metric computation, best-version queries, and Pareto frontier queries.

### Known Limitations
- T-count currently counts explicit `t` gates, excluding `tdg` and decomposed equivalents.
- Two-qubit gate count is based on parsed circuit instructions, not hardware-native decompositions.
- Resource comparisons do not automatically establish semantic equivalence.

### Next Steps
- Review and commit implementation and tests to GitHub.
- Improve resource metric semantics and query behavior for incomplete historical data.
- Extend QCH benchmark ingestion and execution provenance.


## Optimization Pass Sequences and Maintenance Fixes (2026-10-24)

### Implemented
- Added `qch.optimization`: run an ordered sequence of whitelisted Qiskit
  passes (InverseCancellation, Optimize1qGatesDecomposition,
  RemoveIdentityEquivalent) on a stored circuit in one PassManager, with
  one pass instance per sequence position (A -> B -> A supported).
- Recorded step 0 and every step: QPY snapshot (authoritative, verified by
  round trip), optional QASM 2 text, representation fingerprint, `changed`
  flag, snapshot reuse for unchanged steps, metrics, parameters, status,
  errors, and separately measured timings.
- Added new tables `qch_schema_components`, `circuit_snapshots`,
  `optimization_runs`, `optimization_steps` (schema version 1, created
  lazily, incompatible structures rejected).
- Separated pass failures (`PASS_FAILED`, later steps `NOT_RUN`) from
  recording failures (`RECORDING_FAILED`, `RecordingError`).
- Added read-only query functions and `open_readonly()`.
- Added `examples/pass_sequence_demo.py` and example sequences.
- Added `docs/OPTIMIZATION_RUNS.md`.

### Fixed
- `list_versions()` now returns `two_qubit_gate_count` and `t_count`,
  which it already queried; missing values stay `None`.
- Added `idx_gates_circuit_id`: created automatically together with a new
  `gates` table; existing databases are migrated only explicitly with
  `python -m qch.migrations gates-circuit-id-index` (backup via the SQLite
  backup API, no overwrite, existing-file check, dry run).
- Updated the outdated test count in the README.

### Verification
- Command: `python -m pytest tests -v`.
- Result: 3 test files, 80 test functions, 80 collected.
- Windows AMD64, CPython 3.11.8, Qiskit 2.5.2, pytest 9.1.1 (project
  `.venv`): 80 passed, 0 failed, 0 skipped.
- Linux x86_64, CPython 3.11.17, Qiskit 2.5.2, pytest 9.1.1:
  80 passed, 0 failed, 0 skipped.
- The Windows demo (`examples/pass_sequence_demo.py`, QFT 7-qubit opt0
  circuit) reproduced the Linux step metrics exactly (gate count
  142 -> 130, depth 55 -> 53 after Optimize1qGatesDecomposition), and
  the step 2 circuit was restored from QPY through a read-only connection.
- The original `mqt_bench_1000_test.db` was only read (one circuit was
  copied into a separate demo database); it was not migrated or indexed.

### Known Limitations
- Only three passes; no permutations, batch runs, web UI, other compilers,
  or target gate set conversion.
- MQT Bench `opaque ecr` is parsed as a generic opaque Gate, so
  InverseCancellation does not cancel these gates.
- `changed` is representation-based, not semantic equivalence.
- No cross-run snapshot deduplication.

### Next Steps
- Decide whether to migrate a copy of the 1000-circuit database.
- Add more passes and batch experiments over pass orders.


## Six Additional Optimization Passes (2026-11-05)

### Implemented
- Added six passes to the `qch.optimization` whitelist (nine in total):
  `CommutativeCancellation`, `CommutativeInverseCancellation`,
  `Optimize1qGatesSimpleCommutation`, `Optimize1qGates`,
  `RemoveDiagonalGatesBeforeMeasure`, and `RemoveResetInZeroState`.
- These six are part of the 16 passes selected in the pass-order study
  used as a reference; they need no change to the supported circuit scope.
- Every pass has validated JSON parameters; defaults are written into the
  stored step parameters. Gate-set parameters that change the output
  (`basis`, `basis_gates`) are required and never inferred.
- Added `pass_equivalence()`: `RemoveDiagonalGatesBeforeMeasure` only
  preserves measurement outcomes; all other passes return an equivalent
  circuit up to a global phase. No database schema change.
- Added `examples/sequences/commutation_ibm.json`; widened the pass column
  of the demo output.

### Verification
- Command: `python -m pytest tests -v`.
- 4 test files, 96 test functions, 118 collected.
- Windows AMD64, CPython 3.11.8, Qiskit 2.5.2, pytest 9.1.1 (project
  `.venv`, 2026-11-14): 118 passed, 0 failed, 0 skipped.
- Linux x86_64, CPython 3.11.17, Qiskit 2.5.2, pytest 9.1.1: 118 passed.
- Windows manual acceptance (2026-11-14): `commutation_ibm.json` on the
  7-qubit QFT circuit in a new demo database reproduced the Linux step
  metrics exactly (142 -> 130 -> 115 gates, depth 55 -> 53 -> 48), and the
  step 3 circuit was restored from QPY through a read-only connection.
- Demo on the 7-qubit QFT circuit (IBM native gates): the new sequence
  `commutation_ibm.json` reduced the gate count from 142 to 115 and the
  depth from 55 to 48 (the earlier `ic_o1q_ic_ibm.json` reached 130 and 53);
  the final circuit was checked to have the same unitary as the input.

### Observations
- `Optimize1qGates` only rewrites `u1`, `u2`, `u3`, `u` and `p` gates, so it
  leaves IBM-native MQT Bench circuits unchanged.
- `CommutativeCancellation` merges Z rotations across CX controls and was
  the most effective pass on the QFT example.

### Next Steps
- Add a batch runner for pass-order experiments over a set of circuits.


## Pass Order Acceptance: A -> B versus B -> A (2026-11-14)

### Purpose
- Accept the first-stage multi-pass optimization feature with two pass
  orders applied to the same stored input. The existing executor
  (`qch.optimization.run_pass_sequence`) was reused; no core code changed.

### Setup
- Circuit `pass_order_demo_3q`: 3 qubits, no measurements, 10 gates
  (4 `cx`, 3 `h`, `sdg`, `t`, `z`), depth 7; stored once in a new
  temporary SQLite database.
- A = `CommutativeCancellation` with `basis_gates = [rz, sx, x, cx]`.
- B = `Optimize1qGatesDecomposition` with `basis = [rz, sx, x, cx]`.
- New script `examples/pass_order_acceptance.py`: `run` writes a new
  database and runs both orders; `verify` runs in a separate process with
  a read-only connection, restores every step from QPY, recomputes its
  metrics, and checks equivalence with the input unitary.

### Results (Linux x86_64, CPython 3.11.17, Qiskit 2.5.2)

| Order | Step | Pass | Gates | 2q | Depth | Pass time (ms) |
|---|---|---|---|---|---|---|
| A -> B | 0 | input | 10 | 4 | 7 | - |
| A -> B | 1 | CommutativeCancellation | 7 | 2 | 5 | 2.043 |
| A -> B | 2 | Optimize1qGatesDecomposition | 13 | 2 | 9 | 0.444 |
| B -> A | 0 | input | 10 | 4 | 7 | - |
| B -> A | 1 | Optimize1qGatesDecomposition | 16 | 4 | 11 | 0.188 |
| B -> A | 2 | CommutativeCancellation | 11 | 2 | 8 | 0.208 |

- The two orders produce different final circuits; B -> A ends with fewer
  gates (11 vs 13) and lower depth (8 vs 9), with the same two-qubit gate
  count (2). Both final circuits have the same unitary as the input up to
  global phase.
- Cause: CommutativeCancellation merges Z rotations across CX controls.
  When B runs first, H gates become `rz-sx-rz`, so their outer `rz` gates
  can be merged with neighboring Z rotations by A; when A runs first, H is
  not a Z rotation and these merges are not possible.
- Timings are single measurements and include first-call effects; they
  are not a performance comparison.

### Verification
- `verify` in a separate process: restored metrics equal stored metrics
  for all six steps, every pass step has a pass time, both finals are
  equivalent to the input, the database file is unchanged by `verify`.
- New tests `tests/test_pass_order_acceptance.py` (6 tests): both orders
  from the same input, run and verify in separate processes, restored
  intermediate metrics, A -> B -> A records both A steps separately, and
  earlier steps remain queryable after an injected failure.
- Command `python -m pytest tests -v`: 5 test files, 102 test functions,
  124 collected, 124 passed, 0 failed, 0 skipped (Linux x86_64, CPython
  3.11.17, Qiskit 2.5.2, pytest 9.1.1).
- Windows AMD64, CPython 3.11.8, Qiskit 2.5.2, pytest 9.1.1 (project
  `.venv`, 2026-11-14): `python -m pytest tests -q` gave 124 passed;
  `examples/pass_order_acceptance.py run` and `verify` in separate
  processes reported "All checks passed" and that the two final circuits
  are not identical, matching the Linux results.

### Open Items
- Batch runs over several sequences and permutation generation are not
  implemented (out of scope for the first stage).


## Stage 2A: Batch Comparison of Explicit Pass Sequences (2026-11-14)

### Implemented
- New module `src/qch/batch.py` with `run_sequence_batch()`,
  `prepare_batch()`, `summarize_run()`, `summarize_runs()`,
  `SequenceResult`, `BatchResult`, `BatchConfigError` and
  `BatchExecutionError`.
- Reuses `qch.optimization.run_pass_sequence()` for every sequence; no
  schema change, no stored batch object, no copied snapshots. Each
  sequence is an ordinary run whose label is the sequence label.
- Each sequence starts from the stored input (the executor parses the
  stored QASM again per run). Sequence order, pass parameters and repeated
  passes are kept.
- Validation of labels, sequences and the input circuit happens before the
  first run; an invalid batch writes nothing.
- `PASS_FAILED` runs are kept and the batch continues; they have no final
  metrics and are excluded from comparisons. Recording or database
  failures stop the batch with `BatchExecutionError`.
- Per-sequence report: label, pass order, run ID, status, final gate count,
  two-qubit gate count and depth, deltas (final minus input), and the sum
  of recorded pass times with an explicit completeness flag.
- Per-metric comparison with ties kept; no combined score.
- New example `examples/batch_sequence_demo.py` (`demo`, `run`, `show`) and
  a section in `docs/OPTIMIZATION_RUNS.md`.

### Acceptance (Linux x86_64, CPython 3.11.17, Qiskit 2.5.2, pytest 9.1.1)
- Same 3-qubit circuit and pass parameters as the stage 1 acceptance
  (A = CommutativeCancellation, B = Optimize1qGatesDecomposition, basis
  rz, sx, x, cx), new temporary SQLite database.
- `demo` (writing process) and `show` (separate read-only process):

| Sequence | Run | Status | Gates | Delta | 2q | Delta | Depth | Delta | Pass time (ms) |
|---|---|---|---|---|---|---|---|---|---|
| A->B | 1 | SUCCEEDED | 13 | +3 | 2 | -2 | 9 | +2 | 1.809 |
| B->A | 2 | SUCCEEDED | 11 | +1 | 2 | -2 | 8 | +1 | 0.179 |
| A->B->A | 3 | SUCCEEDED | 11 | +1 | 2 | -2 | 8 | +1 | 0.255 |

- Input: 10 gates, 4 two-qubit gates, depth 7; the step 0 records of all
  runs have the same representation fingerprint.
- Best per metric: gate count B->A and A->B->A; two-qubit gate count all
  three (tie); depth B->A and A->B->A.
- `show` rebuilt the same table from the run IDs, restored B->A step 1 from
  QPY (16 gates, 4 two-qubit gates, depth 11, equal to the stored values)
  and left the database file unchanged.
- Pass times are single measurements and include first-call effects.

### Tests
- New `tests/test_batch.py`: 14 test functions, 25 collected (parametrized
  validation cases), all passed. Covers the acceptance numbers, reconnect
  and restore by run ID, isolation between sequences and batch order,
  repeated passes, pass failure with continuation, an all-failing batch,
  recording failure stopping the batch, validation without writes, open
  transactions, mixed circuits, and the example script in separate
  processes.
- Full regression `python -m pytest tests -v`: 6 test files, 116 test
  functions, 149 collected, 149 passed, 0 failed, 0 skipped.
- Not yet re-run on Windows.

### Open Items
- Re-run the tests and `examples/batch_sequence_demo.py` on Windows.
- Automatic permutation generation, parallel execution, a web interface
  and full MQT Bench experiments are not part of stage 2A.


## Stage 2B: Bounded Permutation Generation (2026-11-14)

### Implemented
- New module `src/qch/permutations.py`: `preview_permutations()`,
  `run_permutation_batch()`, `count_permutations()`, `PermutationPreview`,
  `PermutationLimitError`, `DEFAULT_MAX_PERMUTATIONS = 24`.
- Input: pass configurations with unique identifiers; each permutation uses
  each configuration exactly once; the same pass with different parameters
  is supported through different identifiers.
- Deterministic generation (`itertools.permutations` over the input order);
  labels join identifiers with `->`.
- The permutation count is computed and checked against the limit before
  configuration validation, generation, execution or any write. The error
  shows the expected count and suggests fewer passes, an explicit higher
  limit, or the stage 2A explicit sequence API.
- Preview entry point that does not open any database.
- Execution delegates to `qch.batch.run_sequence_batch` (stage 2A); the
  comparison table, run IDs, failure handling and per-step records are
  reused. No schema change.
- New example `examples/permutation_demo.py` (`preview`, `demo`, `run`)
  and a section in `docs/OPTIMIZATION_RUNS.md`.

### Acceptance (Linux x86_64, CPython 3.11.17, Qiskit 2.5.2, pytest 9.1.1)
- 3-qubit circuit `pass_order_demo_3q` (10 gates, 4 two-qubit gates,
  depth 7) in new temporary SQLite databases. A = CommutativeCancellation,
  B = Optimize1qGatesDecomposition (basis rz, sx, x, cx),
  C = CommutativeInverseCancellation.
- Two configurations: `A->B` and `B->A`; final gates 13 and 11, two-qubit
  gates 2 and 2, depth 9 and 8.
- Three configurations: six orders, reproducible across two runs:

| Order | Gates | 2q | Depth |
|---|---|---|---|
| A->B->C | 11 | 2 | 8 |
| A->C->B | 13 | 2 | 9 |
| B->A->C | 11 | 2 | 8 |
| B->C->A | 11 | 2 | 8 |
| C->A->B | 13 | 2 | 9 |
| C->B->A | 11 | 2 | 8 |

- Observation: on this circuit the result depends on whether B runs last;
  every order that ends with Optimize1qGatesDecomposition stays at 13 gates.
- Preview, invalid input and over-limit requests write no runs; the
  example's over-limit request (6 > 5) and the default limit for five
  configurations (120 > 24) fail before a database file is created.

### Tests
- New `tests/test_permutations.py`: 23 test functions, 40 collected, all
  passed. Covers generation order and reproducibility, input formats,
  same pass with different parameters, the limit check before generation
  and before configuration validation, invalid limits and inputs without
  writes, preview without database access, the 2A acceptance numbers,
  reconnect and restore by run ID, pass failures, the explicit repeat API,
  and the example script in separate processes.
- Full regression `python -m pytest tests -v`: 7 test files, 139 test
  functions, 189 collected, 189 passed, 0 failed, 0 skipped.
- Not yet re-run on Windows.

### Open Items
- Re-run the tests and `examples/permutation_demo.py` on Windows.
- Parallel execution, a web interface and full MQT Bench experiments are
  not part of stage 2B.

## Stage 2C: MQT Bench pass-order experiment (setup)

### Scope
- New `experiments/mqt_pass_order.py` with three subcommands: `select`,
  `run` and `report`. It reuses `run_permutation_batch` (stage 2B) without
  changes to the executor or the database schema.
- `select` opens the source database read-only (SQLite `mode=ro`,
  `query_only`), records SHA-256, size and modification time of the source
  file before and after reading, and refuses to write any output if the
  file changed. It never migrates, indexes or writes the source database.
- Selection rule: circuits joined with `features`, 2 to 6 qubits, at most
  400 source gates, MQT name with a known layer. The `indep` layer is
  tried first. Within a layer the order is round-robin over families
  (alphabetical), and within a family by (qubits, gate count, circuit id).
  The first 6 accepted circuits are used; at least 3 families are
  required. Rejected candidates are recorded with their reason.
- Baseline: `qiskit.transpile(basis_gates=[rz, sx, x, cx],
  optimization_level=0, seed_transpiler=20261114)`, written as QASM 2 and
  parsed again. Acceptance requires gates only from rz, sx, x, cx plus
  measure and barrier, final measurements without classical control or
  reset, the same measurement mapping and the same unitary as the original
  (final measurements and barriers removed on copies).
- Original and baseline circuits are copied into a new `experiment.db` in
  the output directory; `selection.json` lists source circuit IDs, names
  and QASM hashes.
- `run` executes the six orders of A, B, C on every baseline (36 runs),
  then checks each final circuit: gate set, measurement mapping, number of
  classical bits, unitary equivalence with the baseline and that step 0
  equals the baseline. Failed or unverified runs are listed separately and
  excluded from the best orders; ties are kept. Results go to
  `results.json` and `report.md`.
- Metrics of the experiment: gates exclude measure and barrier, depth is
  measured without final measurements and barriers, deltas are final minus
  baseline. Pass times are recorded only, not ranked.

### Tests (Linux x86_64, CPython 3.11.17, Qiskit 2.5.2, pytest 9.1.1)
- New `tests/test_mqt_pass_order.py`: 7 tests with synthetic MQT-style
  circuits only (not MQT Bench data). Covers name parsing, measurement
  rules, metric definitions, detection of wrong unitaries, measurement
  mappings and gate sets, the selection order, the full
  select/run/report flow with an unchanged source file, and refusal when
  fewer than 3 families pass.
- Full regression: 8 test files, 196 passed, 0 failed.

### Status
- The MQT Bench database (`mqt_bench_1000_test.db`, about 624 MB) could
  not be read from the cloud environment, so no MQT results are recorded
  yet. The experiment has to be run on the Windows machine; results will
  be added in a follow-up entry.
- Not yet re-run on Windows.

## Stage 2C: MQT Bench pass-order experiment (results)

### Run
- Platform: Windows AMD64, CPython 3.11.8, Qiskit 2.5.2. Output directory
  `demo/stage2c_20261114164214` (experiment.db, selection.json,
  results.json, report.md).
- Commands (PowerShell, repository root):
  `experiments\mqt_pass_order.py select --source-db <mqt_bench_1000_test.db>
  --out-dir <dir>`, then `run --out-dir <dir>` and `report --out-dir <dir>`.
- Source database unchanged (SHA-256 before and after reading:
  `083c02c48ca0888992e42e87cfa4e69938781b0ccfbc39e98e6bbd400f54999b`).
- 38 candidates matched the filter (2 to 6 qubits, at most 400 gates).
  Only 3 of them are in the `indep` layer, so the remaining 3 circuits
  came from the `nativegates` fallback layer. One candidate was rejected
  (src 178, `ghz_nativegates_oqc_qiskit_opt3_5`: opaque `ecr` cannot be
  translated).
- Independent check of the stored records on Linux (read-only connection,
  file hash unchanged): all 36 final circuits use only rz, sx, x, cx plus
  measure and barrier, keep the measurement mapping, are unitarily
  equivalent to the ORIGINAL source circuit, and their recomputed metrics
  match results.json.

### Selected circuits (gates exclude measure and barrier)

| src | Name | Layer | Qubits | Baseline gates/2q/depth |
|---|---|---|---|---|
| 293 | pricingcall_indep_tket_5 | indep | 5 | 240/52/176 |
| 586 | qpeinexact_indep_qiskit_3 | indep | 3 | 31/9/23 |
| 939 | wstate_indep_qiskit_4 | indep | 4 | 55/6/36 |
| 32 | ae_nativegates_ionq_qiskit_opt1_4 | nativegates | 4 | 332/24/183 |
| 121 | dj_nativegates_quantinuum_tket_3 | nativegates | 3 | 96/4/50 |
| 254 | graphstate_nativegates_rigetti_qiskit_opt0_4 | nativegates | 4 | 56/4/22 |

### Results (36 of 36 runs succeeded and verified; none excluded)

| src | Distinct outcomes | Best gates | Best 2q | Best depth | Best orders (gates and depth) |
|---|---|---|---|---|---|
| 293 | 4 | 157 (-83) | 48 (-4) | 114 (-62) | B->A->C, B->C->A |
| 586 | 1 | 29 (-2) | 9 (0) | 21 (-2) | all six |
| 939 | 2 | 22 (-33) | 6 (0) | 13 (-23) | B->A->C, B->C->A, C->B->A |
| 32 | 3 | 94 (-238) | 24 (0) | 77 (-106) | B->A->C, B->C->A, C->B->A |
| 121 | 1 | 20 (-76) | 4 (0) | 12 (-38) | all six |
| 254 | 1 | 16 (-40) | 4 (0) | 15 (-7) | all six |

### Observations
- Metrics depend on the order for src 293, 939 and 32. src 121 has two
  different final circuits with identical metrics.
- On every circuit, the orders that are best for depth are a subset of the
  orders that are best for two-qubit gates; there is no conflict between
  the two metrics in this sample.
- Orders in which Optimize1qGatesDecomposition (B) runs before
  CommutativeCancellation (A) are always among the best. Orders that end
  with B (A->C->B, C->A->B) are always among the worst, the same pattern
  as the 3-qubit demo circuit.
- The only two-qubit reduction (src 293, 52 to 48) needs B before a
  cancellation pass (A or C); A->C->B and C->A->B keep 52.
- All 36 runs reduce gates and depth against the baseline. Most of this
  gain removes overhead introduced by the level-0 translation into
  rz, sx, x, cx; it is not a gain over the source circuit in its own gate
  set.

### Limitations
- Six small circuits, three passes, one baseline configuration; no
  statistical claim. Three circuits come from the `nativegates` layer
  because the sample has only three `indep` circuits within the bounds.
- Pass times were recorded but are not compared.
- The Windows test-suite result for stage 2C was not reported in this
  session.

## Stage 2D: fixed order B->A->C vs per-circuit search (setup)

### Scope
- Question: can the fixed order B->A->C replace the search over all six
  orders of A, B, C on MQT circuits that were not used in stage 2C?
- `experiments/mqt_pass_order.py select` has two new options:
  `--exclude-selection SELECTION_JSON` (repeatable) skips every circuit
  accepted in an earlier selection, and `--allow-fewer` treats `--count`
  as a maximum when the candidate pool is exhausted. Excluded candidates
  (earlier selection, or a layer outside indep/nativegates/mapped) are
  recorded in `selection.json` with their reason and layer. Without the
  new options the stage 2C behaviour is unchanged.
- Selection range, target gate set, baseline preparation, A/B/C
  configurations, verification and the read-only access to the source
  database are those of stage 2C.
- New `experiments/fixed_order_check.py analyze --out-dir DIR [--fixed
  B->A->C]` compares the fixed order with the best valid result of the six
  orders for two-qubit gates, depth and gates: circuits that reach the best
  value (ties count), excess where they do not, and change against the
  baseline, overall and per input layer. A counterexample is a circuit on
  which the valid fixed order misses the best value of at least one
  metric. Circuits whose fixed-order run is not valid are excluded and
  listed. Each run is checked against the experiment database through a
  read-only connection. Writes `fixed_order.json` and `fixed_order.md`.
- Applied to the stage 2C output as a reference: B->A->C reaches the best
  value on all 6 circuits for all three metrics (no counterexample).

### Tests (Linux x86_64, CPython 3.11.17, Qiskit 2.5.2, pytest 9.1.1)
- New `tests/test_fixed_order_check.py`: 5 tests with synthetic circuits
  (ties, excess, exclusion of an invalid fixed order, unknown order,
  exclusion of earlier selections with `--allow-fewer`, read-only
  analysis, and `--count` still enforced without `--allow-fewer`).
- Full regression on Linux: 9 test files, 201 passed, 0 failed.

### Windows
- The full test suite was not re-run on Windows in this session: the
  cloud environment cannot execute commands on the Windows machine.
- The MQT experiment itself has to be run on Windows (the source database
  is not readable from the cloud); results will follow in a separate
  entry.

## Stage 2D: fixed order B->A->C vs per-circuit search (results)

### Run
- Platform: Windows AMD64, CPython 3.11.8, Qiskit 2.5.2. Output directory
  `demo/stage2d_20261114165225`.
- Commands (PowerShell, repository root):
  `experiments\mqt_pass_order.py select --source-db <mqt_bench_1000_test.db>
  --out-dir <dir> --count 12 --allow-fewer --exclude-selection
  demo\stage2c_20261114164214\selection.json`, then
  `experiments\mqt_pass_order.py run --out-dir <dir>` and
  `experiments\fixed_order_check.py analyze --out-dir <dir>`.
- Source database unchanged (SHA-256
  `083c02c48ca0888992e42e87cfa4e69938781b0ccfbc39e98e6bbd400f54999b`
  before and after).
- Selection: 38 candidates in range; 6 excluded as stage 2C circuits
  (src 32, 121, 254, 293, 586, 939); 6 rejected (src 178, 562, 628, 840,
  904: opaque `ecr` cannot be translated; src 278: a qubit is measured
  twice); 12 accepted from 12 families; 14 candidates not reached. All 12
  accepted circuits are in the `nativegates` layer: the three `indep`
  circuits in range were already used in stage 2C.
- 72 runs, all succeeded and verified. Independent check on Linux through a
  read-only connection (file hash unchanged): every final circuit uses only
  rz, sx, x, cx plus measure and barrier, keeps the measurement mapping, is
  unitarily equivalent to the original source circuit, and matches the
  metrics in results.json; `fixed_order.json` was reproduced exactly.
- The `run` report header still reads "Stage 2C" because the same script
  is reused; the content is the stage 2D run.

### Per circuit (B->A->C / best of six orders; excess in brackets)

| src | Circuit | Baseline 2q/depth/gates | 2q | Depth | Gates |
|---|---|---|---|---|---|
| 62 | ae_nativegates_rigetti_qiskit_opt2_6 | 30/166/243 | 30/30 | 56/56 | 95/95 |
| 94 | dj_nativegates_ibm_qiskit_opt1_5 | 4/10/31 | 4/4 | 9/9 | 27/27 |
| 234 | graphstate_nativegates_ionq_qiskit_opt1_6 | 12/52/182 | 12/12 | 14/14 | 42/42 |
| 274 | grover-v-chain_nativegates_ionq_tket_3 | 18/281/479 | 18/18 | 58/57 (+1) | 73/73 |
| 291 | portfoliovqe_nativegates_ionq_qiskit_opt3_6 | 90/218/838 | 90/90 | 80/78 (+2) | 233/233 |
| 325 | qaoa_nativegates_rigetti_qiskit_opt2_6 | 24/110/326 | 24/24 | 29/29 | 110/110 |
| 443 | qftentangled_nativegates_ionq_tket_6 | 70/573/1784 | 70/70 | 120/110 (+10) | 270/266 (+4) |
| 725 | random_nativegates_rigetti_qiskit_opt3_2 | 2/31/49 | 2/2 | 14/12 (+2) | 19/19 |
| 779 | realamprandom_nativegates_rigetti_qiskit_opt1_2 | 3/49/77 | 3/3 | 18/18 | 31/31 |
| 861 | tsp_nativegates_rigetti_qiskit_opt0_4 | 15/89/273 | 15/15 | 49/47 (+2) | 125/124 (+1) |
| 936 | vqe_nativegates_ionq_qiskit_opt2_3 | 8/72/138 | 8/8 | 28/28 | 50/50 |
| 168 | ghz_nativegates_ionq_qiskit_opt3_6 | 10/100/170 | 10/10 | 24/24 | 38/38 |

### Summary (all 12 circuits; only the `nativegates` layer is present)

| Metric | Reached best | Missed | Excess total / max | B->A->C vs baseline | Best of six vs baseline |
|---|---|---|---|---|---|
| Two-qubit gates | 12 | 0 | 0 / 0 | +0 of 286 | +0 |
| Depth | 7 | 5 | 17 / 10 | -1252 of 1751 | -1269 |
| Gates | 10 | 2 | 5 / 4 | -3477 of 4590 | -3482 |

### Conclusions
- Counterexamples found: 5 of 12 circuits (src 274, 291, 443, 725, 861).
  B->A->C cannot replace the per-circuit search when depth or gates must
  be optimal; it recovers 98.7% of the best depth reduction and 99.9% of
  the best gate reduction against the baseline.
- No order changes the two-qubit gate count on these 12 circuits, so the
  two-qubit comparison is trivially satisfied.
- 11 of 12 circuits are order-dependent. On src 291 the orders that are
  best for gates (B first, or C->B->A) and those best for depth
  (A->B->C, A->C->B, C->A->B) are disjoint: no single order is optimal for
  both metrics there.
- Exploratory, not a pre-registered test: every one of the six orders has
  counterexamples on this sample (between 4 and 8 circuits); C->B->A has
  the fewest (4). This does not justify replacing B->A->C without a new,
  independent sample.

### Limitations
- 12 circuits, all from the `nativegates` layer; no `indep` or `mapped`
  circuit could be tested within the stage 2C range.
- Gains against the baseline mostly remove the overhead of the level-0
  translation to rz, sx, x, cx.

### Windows tests
- The full test suite was not re-run on Windows in this session (no
  pytest output was provided); the last Linux result is 201 passed.

## Windows test run after stage 2D

- Windows AMD64, CPython 3.11.8, Qiskit 2.5.2: `python -m pytest tests -q`
  gave 1 failed, 200 passed.
- Failure: `tests/test_batch.py::test_demo_script_in_separate_processes`.
  `examples/batch_sequence_demo.py show --restore` prints a circuit
  drawing; with stdout redirected to a pipe, Windows uses the ANSI code
  page cp1252, which cannot encode the Greek letter pi, so the script
  stopped with `UnicodeEncodeError`. This was a defect of the example
  script, not of the optimization code or the experiments.
- Fix: when stdout is redirected and its encoding cannot represent the
  drawing characters, the script switches stdout to UTF-8; an interactive
  console is unchanged. The existing test now decodes the output as UTF-8,
  and a new test forces `PYTHONIOENCODING=cp1252`; it reproduced the
  Windows error on Linux before the fix and passes after it.
- Linux after the fix: 202 passed (also `tests/test_batch.py` with
  `PYTHONIOENCODING=cp1252`: 26 passed). Windows re-run after the fix:
  pending.

## Stage 2E: objective-aware comparison of pass sequences

### Scope
- New `src/qch/objectives.py` and `BatchResult.objective_view()`: for the
  results of one input, the sequences with the fewest gates, the fewest
  two-qubit gates and the lowest depth (ties kept), and the Pareto front
  over these objectives. No combined score. A sequence leaves the front
  only if another one is no worse in every objective and better in at
  least one.
- Comparison rules: one input only (mixed inputs raise
  `ObjectiveInputError`); only successful runs with all objective values;
  results whose verification failed are excluded with a reason; without a
  verification record a candidate is marked `unverified`. Sequences with
  identical metrics keep their own labels and run IDs.
- New `experiments/objective_view.py show --out-dir DIR --src ID
  [--metrics experiment|qch] [--json]`: read-only view of one circuit of an
  experiment directory. Run records are rebuilt from the database with
  `batch.summarize_runs`, which also checks the common input
  representation; verification comes from the experiment checks.
- `experiments/mqt_pass_order.py`: the report heading is no longer fixed
  to "Stage 2C". `run --title` sets it and stores it in `results.json`;
  `report` uses `--title`, else the stored title, else the generic
  "MQT Bench pass-order experiment". Existing experiment outputs were not
  rewritten.
- No schema change; the original MQT database was not accessed.

### Read-only demonstration: stage 2D, src 291
- Command: `experiments\objective_view.py show --out-dir
  demo\stage2d_20261114165225 --src 291` (run on Linux on a copy of the
  Windows output; database file hash unchanged).
- Runs recorded on Windows AMD64, CPython 3.11.8, Qiskit 2.5.2. Experiment
  metrics (gates without measure/barrier, depth without final
  measurements); all six runs verified.

| Order | Run | Gates | 2q | Depth | Best in |
|---|---|---|---|---|---|
| A->B->C | 25 | 238 | 90 | 78 | 2q, depth |
| A->C->B | 26 | 238 | 90 | 78 | 2q, depth |
| B->A->C | 27 | 233 | 90 | 80 | gates, 2q |
| B->C->A | 28 | 233 | 90 | 80 | gates, 2q |
| C->A->B | 29 | 238 | 90 | 78 | 2q, depth |
| C->B->A | 30 | 233 | 90 | 80 | gates, 2q |

- Fewest gates: runs 27, 28, 30 (233). Lowest depth: runs 25, 26, 29 (78).
  Two-qubit gates are 90 for all six.
- No common optimum: the two groups are disjoint, and each group is better
  than the other in one objective (5 fewer gates vs depth 2 lower), so
  neither dominates the other and all six runs are on the Pareto front.
- Within each group the metrics are identical; their final circuits also
  have the same representation fingerprint (runs 25, 26, 29 share one,
  runs 27, 28, 30 another), checked separately. Runs stay listed
  individually.
- With the QCH stored metrics (`--metrics qch`, gate_count includes the 6
  measurements) the result is the same: gate_count 239 vs 244, depth 81 vs
  79.

### Tests
- New `tests/test_objectives.py`: 9 tests (ties, dominance and Pareto
  front, gate/depth conflict, failed, incomplete and unverified results,
  identical metrics with different run IDs, invalid inputs, the batch
  integration with a failing pass and a read-only rebuild, the experiment
  script on synthetic circuits, and the report titles).
- Full regression on Linux x86_64, CPython 3.11.17, Qiskit 2.5.2,
  pytest 9.1.1: 10 test files, 211 passed. Also 211 passed with
  `PYTHONIOENCODING=cp1252`.
- Windows: not re-run in this session (the cloud environment cannot run
  commands on the Windows machine).

## Windows acceptance of stages 2D fix and 2E (2026-11-14)

- `python -m pytest tests -q` on Windows: 211 passed in 59.28 s. This also
  confirms the fix of the cp1252 output failure recorded after stage 2D.
- `experiments\objective_view.py show --out-dir
  demo\stage2d_20261114165225 --src 291` on Windows reproduced the
  verified trade-off: 233 gates at depth 80 versus 238 gates at depth 78,
  both with 90 two-qubit gates; all six runs on the Pareto front, no
  common optimum.

## Stage 3A: step-metric export for the demo Optimization Results page

- New `experiments/export_step_metrics.py`: exports the per-step metrics of
  saved runs as compact JSON for the Streamlit demo
  (`Demo01/quantum-circuit-hub`). It opens `experiment.db` read-only,
  restores every step circuit from its QPY snapshot and recomputes gates,
  two-qubit gates and depth with the experiment metric profile
  (`mqt_pass_order.experiment_metrics`). No pass is run, the original MQT
  database is not accessed, and the SHA-256 of `experiment.db` is compared
  before and after. It refuses to write unless every run is a verified
  success of the requested source circuit, all runs share one input, step
  0 equals the baseline and the last step equals the recorded final
  metrics. The output has no local paths and the target must not exist.
- Export of Stage 2D src 291, runs 25-30:
  `experiments\export_step_metrics.py --out-dir
  demo\stage2d_20261114165225 --src 291 --runs 25-30 --output
  ..\Demo01\quantum-circuit-hub\data\optimization\stage2d_src291_steps.json`.
  Produced on Linux x86_64 (CPython 3.11.17, Qiskit 2.5.2) from a
  byte-identical copy of the Windows experiment directory;
  `experiment.db` SHA-256
  `6238d916f7b4f59a4e3c6f4bdc6043bfbcbc3a0ed50818147d15871ede3d2282`
  unchanged; input representation
  `71314199cfb96d396ca8a86498b49ac489aad405b70d634c00f74ebe408b5144`;
  step 0 is 838/90/218 for all runs and every last step matches
  results.json. The step table is recorded in the demo repository
  (`docs/QCH_OPTIMIZATION_RESULTS.md`).
- New `tests/test_export_step_metrics.py` (synthetic circuits). Full
  regression on Linux: 11 test files, 212 passed. Windows: not re-run.

## Stage 2F: five passes on the stage 2D circuits

### Design
- Pass set `abcef`: A = CommutativeCancellation, B =
  Optimize1qGatesDecomposition, C = CommutativeInverseCancellation (as in
  stages 2C-2D), plus E = RemoveIdentityEquivalent() and F =
  Optimize1qGatesSimpleCommutation(basis = rz, sx, x, cx). All 120 orders
  per circuit; the permutation limit is raised explicitly to 5! = 120.
- InverseCancellation(gates_to_cancel = [cx, x]) was left out after a
  design screen in memory (not recorded runs): on the 12 stage 2D
  baselines it changed no circuit after any prefix of any of the 720
  orders of six passes (0 of 8640 applications). Adding it would have
  multiplied the runs by six without new information.
- Same 12 circuits as stage 2D (all `nativegates`). New
  `mqt_pass_order.py reuse --from-dir` copies original and baseline QASM
  from the stage 2D experiment database (read-only, file hash unchanged),
  checks them against the stage 2D selection.json hashes and verifies every
  baseline again. The MQT source database is not opened.
- `mqt_pass_order.py run --pass-set abc|abcef` (default `abc`, unchanged
  behaviour); `results.json` records the pass set.
- New `experiments/pass_set_compare.py --before DIR --after DIR`: both
  experiment databases read-only and every run checked; requires the same
  baseline circuits. Reports the best values before and after per circuit
  and metric, the classes of orders with identical final metrics on all
  circuits, the passes whose position never changes a final metric, and
  for every order the circuits where it reaches all three best values.

### Run (cloud)
- Linux x86_64, CPython 3.11.17, Qiskit 2.5.2, on a byte-identical copy of
  `demo/stage2d_20261114165225` (experiment.db SHA-256 `6238d916...`,
  unchanged). Commands:
  `mqt_pass_order.py reuse --from-dir <stage2d> --out-dir <stage2f>`,
  `mqt_pass_order.py run --out-dir <stage2f> --pass-set abcef --title
  "Stage 2F: five passes"`, `pass_set_compare.py --before <stage2d>
  --after <stage2f>`.
- 1440 runs (12 x 120), all succeeded and verified; 233 s; experiment
  database 92 MB. Independent check: all 1440 final circuits are
  unitarily equivalent to the original source circuits, keep the
  measurement mapping and match the recorded metrics.

### Results (gates / 2q / depth)

| src | Best with A, B, C (2D) | Best with A, B, C, E, F | Change |
|---|---|---|---|
| 62 | 95/30/56 | 89/30/51 | -6/0/-5 |
| 779 | 31/3/18 | 22/3/15 | -9/0/-3 |
| 861 | 124/15/47 | 96/15/42 | -28/0/-5 |
| other 9 | unchanged | unchanged | 0/0/0 |

- E and F lower gates and depth on 3 of 12 circuits; no order changes the
  two-qubit gate count on any circuit.
- The 120 orders form 18 classes with identical final metrics on all 12
  circuits. The position of E never changes a final metric; the outcome
  depends only on the relative order of A, B, C and F.
- src 291 keeps its gate/depth conflict (233 gates at depth 80 vs 238 at
  depth 78): no order is best in all three metrics there.
- No fixed order is best in all three metrics on every circuit. The best
  class (for example A->B->C->E->F, 10 orders) reaches all three best
  values on 7 of 12 circuits (counterexamples 62, 234, 291, 325, 936);
  classes with B before A reach 6 of 12. Orders with B first are best for
  gates more often; orders with A before B are best for depth more often.

### Tests
- New `tests/test_pass_set_compare.py`: 6 tests (order classes and
  position-insensitive passes, ranking and counterexamples, pass sets,
  reuse read-only and refusal of a tampered selection, a 360-run five-pass
  run on synthetic circuits with the comparison, and refusal of different
  circuits).
- Full regression on Linux: 12 test files, 218 passed (also with
  `PYTHONIOENCODING=cp1252`). Windows: not yet re-run; the stage 2F
  experiment has not yet been reproduced on Windows.

### Windows reproduction of stage 2F (2026-11-15)
- Windows AMD64, CPython 3.11.8, Qiskit 2.5.2: `python -m pytest tests -q`
  gave 218 passed in 115 s.
- Output directory `demo/stage2f_20261115050102` (reuse, run with
  `--pass-set abcef`, pass_set_compare). `reuse` reported the stage 2D
  experiment.db unchanged (SHA-256 `6238d916...`).
- 1440 of 1440 runs valid. For every (circuit, order) the run ID, status,
  verification and final gates/2q/depth are identical to the Linux run;
  the comparison report is the same (gains on src 62, 779, 861; 18 order
  classes; E position-insensitive; best fixed class 7 of 12; no order best
  in all three metrics on every circuit).

## Stage 2G: two-qubit block re-synthesis

### New pass
- `TwoQubitBlockResynthesis` in `PASS_REGISTRY` (parameter
  `basis_gates`, required; it must contain `cx`, `cz` or `ecr` and a
  complete one-qubit Euler basis). A QCH composite step: `Collect2qBlocks`,
  `ConsolidateBlocks(basis_gates, approximation_degree=1.0)` and
  `UnitarySynthesis(basis_gates, approximation_degree=1.0)` run on one DAG
  with one private property set, so the PassManager callback and the QCH
  record see exactly one step. Equivalence label `exact`. No schema
  change.
- New `tests/test_two_qubit_resynthesis.py` (12 tests): fewer two-qubit
  gates on a known block, one recorded step, same result as the three
  Qiskit passes run directly, cz/ecr/u bases stay in the basis, no change
  when nothing can be gained, use inside a longer sequence, and parameter
  rejection before any write. `tests/test_optimization_passes.py` now
  covers the tenth pass (registry test renamed to
  `test_registry_contains_all_passes`). `docs/OPTIMIZATION_RUNS.md` lists
  the pass.

### Experiment
- Pass set `abcfg`: A, B, C, F as in stage 2F and G =
  TwoQubitBlockResynthesis(basis_gates = rz, sx, x, cx). E was dropped
  because its position never changed a final metric in stage 2F. 120
  orders on the 12 stage 2D circuits (`reuse` from stage 2D).
- Commands: `mqt_pass_order.py reuse --from-dir <stage2d> --out-dir
  <stage2g>`, `mqt_pass_order.py run --out-dir <stage2g> --pass-set abcfg
  --title "Stage 2G: two-qubit block re-synthesis"`,
  `pass_set_compare.py --before <stage2f> --after <stage2g>`.
- Run on Linux x86_64, CPython 3.11.17, Qiskit 2.5.2: 1440 runs, all
  succeeded and verified, 232 s, 94 MB. Independent check: all 1440 final
  circuits are unitarily equivalent to the original source circuits, keep
  the measurement mapping and match the recorded metrics.

### Results against stage 2F (best values, gates / 2q / depth)

Recorded on two platforms: the Windows run (`demo/stage2g_20261115052449`,
Windows AMD64, CPython 3.11.8, Qiskit 2.5.2, compared with
`demo/stage2f_20261115050102`) and the Linux run above. All 1440 runs are
valid on both. The two-qubit counts are identical run by run; gates and
depth differ (see "Cross-platform differences"). Values are the best
value of each metric separately; "conflict" marks circuits where no order
reaches all three at once.

| src | Stage 2F | Stage 2G, Windows | Stage 2G, Linux |
|---|---|---|---|
| 234 | 42/12/14 | 46/6/16 | 50/6/15 |
| 274 | 73/18/57 | 50/9/32, conflict | 49/9/32 |
| 291 | 233/90/78 | 206/45/63, conflict | 209/45/59 |
| 443 | 266/70/110 | 152/35/54, conflict | 155/35/55 |
| 779 | 22/3/15 | 21/3/14 | 21/3/14 |
| 936 | 50/8/28 | 50/4/27 | 51/4/27 |
| 168 | 38/10/24 | 50/5/26 | 53/5/29 |
| 62, 94, 325, 725, 861 | | unchanged | unchanged |

- Two-qubit gates fall for the first time: halved on 6 of 12 circuits
  (234, 274, 291, 443, 936, 168) on both platforms. On every circuit all
  120 orders reach the same two-qubit count, so the position of G does
  not matter for 2q.
- On 274, 291 and 443 gates and depth also fall strongly. On 234 and 168
  the halved 2q count costs more gates and depth than the best stage 2F
  result: every stage 2G order contains G, so the stage 2F optimum for
  gates and depth is not reachable inside this pass set.
- Gate/depth conflicts (no order best in all three metrics): Windows 274,
  291, 443; Linux 291 is resolved (A->G->B->C->F, A->G->C->B->F reach
  209/45/59), so conflicts are platform-dependent under G.
- Order sensitivity grows: 80 classes of identical final metrics on both
  platforms (18 in stage 2F); no pass is position-insensitive.
- No fixed order is best in all three metrics on every circuit; the best
  class reaches 7 of 12 on Windows (B->C->G->A->F) and 6 of 12 on Linux.

### Cross-platform differences
- Independent check of the Windows records on Linux: all 1440 final
  circuits are unitarily equivalent to the original source circuits and
  match the recorded metrics.
- 818 of 1440 runs end with different gates or depth on Windows and
  Linux; none differs in two-qubit gates. Same Python minor version and
  Qiskit 2.5.2 on both.
- Cause: the step circuits already differ after B or F (Optimize1qGates*)
  by at most 2.7e-14 in rotation angles (same gate structure; checked for
  all 288 orders that start with B). These last-bit differences do not
  change any metric in stage 2F, but G makes discrete decisions on them
  (block consolidation and synthesis), so the outcomes diverge. The first
  differing step is B in 430 runs, F in 444, G in 466; 100 runs are
  identical at every step.
- On Linux, G is deterministic across processes (three separate runs gave
  identical fingerprints for all 12 baselines).
- Consequence: with G, gates and depth (and therefore best orders, Pareto
  fronts and conflicts) are reproducible per platform, not across
  platforms. The 2q result is robust. The QCH representation fingerprint
  is exact and differs across platforms already for B and F.

### Tests
- Full regression on Linux: 13 test files, 231 passed (also with
  `PYTHONIOENCODING=cp1252`). Windows: 231 passed in 80 s.

## Stage 2H: platform-independent results with rounded angles

### Problem
- Stage 2G gave different gates and depth on Windows and Linux in 818 of
  1440 runs: B and F leave angles that differ by up to 2.7e-14 between
  platforms, and TwoQubitBlockResynthesis (G) turns these last-bit
  differences into different discrete decisions.

### Change
- Every pass accepts the optional parameter `angle_decimals` (6 to 15).
  The pass is then wrapped in `AngleRoundingPass`: numeric gate parameters
  and the global phase are rounded to that many decimals before and after
  the pass (`round_dag_parameters`; `-0.0` becomes `0.0`). The step keeps
  the pass name and records `angle_decimals` in its parameters. Without
  the parameter nothing changes, so earlier runs and stored parameters are
  unaffected. No schema change. Documented in `docs/OPTIMIZATION_RUNS.md`.
- `mqt_pass_order.py run --pass-set abcfg_r13`: the stage 2G passes with
  `angle_decimals = 13` on every pass.
- New `experiments/platform_compare.py --a DIR --b DIR`: compares two runs
  of the same experiment (same pass set and baselines) read-only: status,
  verification, final metrics and the fingerprint of every step, and the
  pass at which two runs first differ.

### Choice of 13 decimals (design study in memory, not recorded runs)
- 12 circuits x 20 random orders of A, B, C, F, G; every step output was
  perturbed by random offsets up to 3e-14 (the observed platform
  difference) and compared with the unperturbed run.

| Rounding | Runs with different final metrics | Sum of gates | Sum of depth |
|---|---|---|---|
| none | 158 of 480 | 24465 | 9202 |
| 13 decimals | 0 of 480 | 24450 | 9155 |
| 12 decimals | 0 of 480 | 24495 | 9141 |
| 11 decimals | 0 of 480 | 33979 | 12206 |

- Rounding only before and after G (not after every pass) still left
  17 of 720 runs different in an earlier variant of the study, so the
  rounding is applied to every pass.
- 10 or 11 decimals change angles by more than Qiskit's internal
  tolerances and destroy exact recognitions. A first stage 2H run with 10
  decimals (Linux, 1440 runs, all valid) was much worse than stage 2G on
  10 of 12 circuits (for example src 291: 288/45/95 instead of 209/45/59)
  and was discarded.

### Run (Linux)
- Linux x86_64, CPython 3.11.17, Qiskit 2.5.2: `reuse` from stage 2D,
  `run --pass-set abcfg_r13 --title "Stage 2H: rounded angles"`. 1440 runs,
  all succeeded and verified, 233 s. Independent check: all final circuits
  are unitarily equivalent to the original source circuits and match the
  recorded metrics.
- Against stage 2G on Linux (best values gates/2q/depth): two-qubit counts
  unchanged on all circuits; 234: 54/6/14 (2G 50/6/15), 274: 49/9/31
  (49/9/32), 291: 201/45/58 (209/45/59), 443: 157/35/54 (155/35/55),
  936: 50/4/27 (51/4/27); the other 7 circuits are unchanged. 81 order
  classes; no fixed order is best in all three metrics everywhere (best
  class 7 of 12).
- `platform_compare.py` on the stage 2G outputs reproduces the known
  differences (Windows vs Linux: 622 of 1440 runs with the same final
  metrics, 1440 with the same two-qubit count, first difference at B, F
  or G).

### Tests
- New `tests/test_angle_rounding.py` (10 tests) and
  `tests/test_platform_compare.py` (1 test: two identical runs of
  `abcfg_r13` are identical at every step, refusal of different pass sets
  and of a missing directory). Full regression on Linux: 15 test files,
  242 passed.

### Windows run and cross-platform result (2026-11-15)
- Windows AMD64, CPython 3.11.8, Qiskit 2.5.2: `python -m pytest tests -q`
  gave 242 passed in 122 s. Output `demo/stage2h_20261115075048`: 1440 of
  1440 runs valid; independent check on Linux: all final circuits are
  unitarily equivalent to the original source circuits and match the
  recorded metrics.
- `platform_compare.py --a <windows stage2h> --b <linux stage2h>`: same
  status and verification 1440, same final two-qubit gates 1440, same final
  gates/2q/depth only 717 of 1440 (stage 2G without rounding: 622),
  identical at every step 365. First differing step: G 708, F 337, B 30.
- The goal of stage 2H is NOT reached. Two causes:
  1. G itself is platform-dependent: with byte-identical (rounded) input,
     Windows and Linux produce different but equivalent decompositions,
     with a different gate structure in 492 runs and angle differences of
     about 0.2 rad or 2 pi in another 216. This is not last-bit noise, so
     rounding cannot remove it.
  2. Rounding to 13 decimals still splits values that lie on both sides of
     a rounding boundary: most B and F differences are exactly one
     1e-13 step (307 of 367 F parameter differences, all 30 for B); the
     rest are 2 pi offsets and other values.
- Windows stage 2H best values (gates/2q/depth): 234 56/6/16, 274 48/9/32,
  291 197/45/55, 443 157/35/55, 936 50/4/27, 168 53/5/29; two-qubit counts
  are the same as on Linux and as in stage 2G.
- Conclusion: with G, gates, depth, best orders and Pareto fronts are
  reproducible per platform only; two-qubit counts are reproducible across
  Windows and Linux. Runs already record platform, Python and Qiskit
  versions. `angle_decimals` remains optional and off by default; whether
  to keep it is open.

### Decision: `angle_decimals` removed (2026-11-15)
- Because rounding did not make stage 2G results platform-independent,
  the optional `angle_decimals` parameter (`AngleRoundingPass`,
  `round_dag_parameters`), the pass set `abcfg_r13` and
  `tests/test_angle_rounding.py` were removed. `src/qch/optimization.py`,
  `experiments/mqt_pass_order.py`, `tests/test_pass_set_compare.py` and
  `docs/OPTIMIZATION_RUNS.md` are back to their stage 2G state. The
  stage 2H records above remain as history; the output directories
  `stage2h_*` were produced with the removed parameter.
- Kept: `experiments/platform_compare.py` (now tested with two runs of
  pass set `abcfg` on one platform, which are identical at every step).
- Policy: across platforms only two-qubit counts are compared; gates,
  depth, best orders and Pareto fronts with G are compared within one
  platform, using the platform recorded with every run.
- Full regression on Linux after the removal: 14 test files, 232 passed.
  Windows (after deleting tests/test_angle_rounding.py): 232 passed in
  124 s.

## Stage 3B: demo datasets for all 12 circuits

- New `experiments/export_demo_dataset.py --out-dir DIR --output FILE`:
  exports one experiment as a compact JSON dataset (`schema_version` 2)
  for the Optimization Results page of the demo repository. For every
  circuit: baseline, input representation, and for every order the run ID,
  status, verification, final metrics and the metrics after every step,
  recomputed from the stored QPY snapshots with
  `mqt_pass_order.experiment_metrics`. Read-only (SQLite `mode=ro`,
  experiment.db SHA-256 compared before and after); refuses to write if
  step 0 differs from the baseline, the last step differs from the
  recorded final metrics, runs of a circuit have different inputs, or a
  label is not an order of the pass set. No local paths in the output.
  `experiments/export_step_metrics.py` (stage 3A) is kept.
- Exports (Linux x86_64, CPython 3.11.17, Qiskit 2.5.2, from byte-identical
  copies of the Windows experiment directories): stage 2D
  (`stage2d_20261114165225`, 72 runs, 13,788 bytes, experiment.db
  `6238d916...`) and stage 2G (`stage2g_20261115052449`, 1,440 runs,
  251,188 bytes, experiment.db `4873f3ee...`); both databases unchanged.
  Stage 2F is not shown in the demo.
- New `tests/test_export_demo_dataset.py` (synthetic circuits). Full
  regression on Linux: 15 test files, 233 passed. Windows: not yet re-run.
- Demo repository: the page now shows all 12 circuits and both pass sets;
  details in `quantum-circuit-hub/docs/QCH_OPTIMIZATION_RESULTS.md`.

## Stage 4A: circuit optimization API

- New `src/qch/optimize.py`: `optimize_circuit(circuit, objective=...,
  pass_set=..., search=..., verify=..., db=None, ...)` returns an
  `OptimizationResult` with the best circuit, ties, Pareto front, all
  candidates, excluded candidates, baseline and environment. Command line
  `python -m qch.optimize input.qasm --out best.qasm --report report.json`.
  Documented in `docs/OPTIMIZE_API.md`.
- Decisions: default objective two-qubit gates, then depth, then gates
  (lexicographic, no weighted score); default pass set A, B, C, F, G;
  nothing is stored unless `db` is given.
- Search `permutations` (with a prefix cache: 325 pass applications for
  the 120 orders of five passes), `greedy` and `fixed`
  (optionally repeated until stable). The translated baseline is a
  candidate, so the result is never worse than the baseline.
- Verification `exact` (up to 10 qubits), `sampled` (8 random states, up
  to 20 qubits), `none`, or `auto`; failed results are excluded.
- With `db`, every candidate order is recorded with
  `run_pass_sequence` and checked to be identical to the search result.
- Check: on the 12 stage 2D/2G MQT Bench circuits the defaults give the
  same best (2q, depth, gates) as the recorded Linux stage 2G experiment
  for every circuit; 13.5 s in total without recording.
- New `tests/test_optimize.py` (31 tests): input forms and rejections,
  option validation, the prefix cache count, lexicographic ranking, ties
  and Pareto front, equivalence and measurement mapping of the best
  circuit, other objectives, JSON report, light/greedy/fixed searches,
  custom pass mappings, exclusion of a deliberately wrong pass (exact and
  sampled), sampled versus exact, unverified mode, time limit, recording
  in a database, nothing written without a database, and the command
  line. Full regression on Linux: 16 test files, 264 passed. Windows:
  264 passed in 212 s; `python -m qch.optimize` on a 3-qubit circuit with
  final measurements: baseline 20/7/15 (gates/2q/depth), best
  B->G->A->C->F 13/3/9, verified exact, 10 ties, 0.16 s.

## Stage 4B: "Optimize Your Circuit" page in the demo

- `qch.optimize`: `OptimizationResult.best_steps` (metrics after every
  step of the best order, taken from the prefix cache) and `best_steps`
  in `to_dict()`; documented in `docs/OPTIMIZE_API.md`; one new test.
  Full regression on Linux: 265 passed.
- Demo repository (`quantum-circuit-hub`): new page
  `pages/Optimize_Circuit.py`. `src/qch_optimizer/` holds byte-identical
  copies of `qch/optimization.py`, `qch/objectives.py` and
  `qch/optimize.py` (SHA-256 in `SOURCE.json`; the demo has its own `qch`
  package, so the optimizer is copied rather than installed). Details in
  `quantum-circuit-hub/docs/QCH_OPTIMIZE_PAGE.md`.
