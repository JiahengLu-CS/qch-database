# QCH Database

**Quantum Circuit Database System**

QCH Database is an open-source research project for storing,
managing, querying, comparing, and optimizing quantum circuits.

## Current Features

- OpenQASM circuit import
- SQLite-based persistent storage
- Quantum gate and circuit feature extraction
- Circuit listing and multi-condition filtering
- Sorting by qubit count, gate count, and depth
- Automated testing with pytest
- Ordered Qiskit optimization pass sequences with recorded step
  snapshots, metrics, and timings (`qch.optimization`)
- Explicit `gates(circuit_id)` index migration for existing databases
  (`python -m qch.migrations`)

## Project Status

Current development version: 0.1.0

Latest recorded verification (2026-10-24; Linux x86_64, CPython
3.11.17, Qiskit 2.5.2, pytest 9.1.1; command `python -m pytest tests -v`):

- 3 test files containing 80 test functions
- 80 test cases collected by pytest
- 80 passed, 0 failed, 0 skipped

These numbers describe one recorded run, not a permanent guarantee.
The suite has not yet been re-run in the Windows project environment.

## Development Progress

For completed milestones, architecture changes, and upcoming work:

**[Development Progress and Roadmap](docs/PROGRESS.md)**

## Quick Start

Install the project locally (only Qiskit 2.5.2 is verified):

    python -m pip install -e .

Install with the test dependencies:

    python -m pip install -e ".[test]"

On Windows, a separate project environment avoids changing other
environments (PowerShell, from the repository root):

    $Py311 = "<path to a Python 3.11 python.exe>"
    & $Py311 -m venv .venv
    .\.venv\Scripts\python.exe -m pip install -e ".[test]"
    .\.venv\Scripts\python.exe -m pytest tests -v

Example:

    from qch import QCHDatabase

    db = QCHDatabase("circuits.db")
    db.add_circuit("ghz3", "ghz3.qasm")

    print(db.get_features("ghz3"))
    print(db.get_gates("ghz3"))

    db.close()

## Run Tests

    python -m pytest tests -v

All tests use temporary databases.

## Optimization Pass Sequences

Run an ordered list of whitelisted Qiskit passes (for example
InverseCancellation -> Optimize1qGatesDecomposition ->
InverseCancellation) on a stored circuit, and query every recorded step
later through a read-only connection:

    from qch import QCHDatabase
    from qch import optimization as opt

    db = QCHDatabase("demo.db")
    result = opt.run_pass_sequence(db, "ghz3", [
        {"name": "InverseCancellation"},
        {"name": "Optimize1qGatesDecomposition",
         "params": {"basis": ["rz", "sx", "x", "cx"]}},
        {"name": "InverseCancellation"},
    ])
    db.close()

    connection = opt.open_readonly("demo.db")
    run = opt.get_optimization_run(connection, result.run_id)
    circuit = opt.load_step_circuit(connection, result.run_id, 2)

Details, metric definitions, timing boundaries, limitations, and a
Windows PowerShell walkthrough:
**[Optimization Pass Sequences](docs/OPTIMIZATION_RUNS.md)**

## Index Migration for Existing Databases

New databases get an index on `gates(circuit_id)` automatically.
Existing databases are never indexed implicitly; migrate them
explicitly (PowerShell). The backup is written with the SQLite backup
API, must be a new file, and is checked before the migration starts:

    $Target = "<path to an existing QCH database>"
    $Backup = "$Target.backup-$(Get-Date -Format yyyyMMdd-HHmmss).db"
    python -m qch.migrations gates-circuit-id-index --db $Target --no-backup --dry-run
    python -m qch.migrations gates-circuit-id-index --db $Target --backup $Backup

## Research Roadmap

- Logical circuit and version management
- Circuit evolution and provenance
- Multi-objective Pareto queries
- QCH-SQL query language
- Structural similarity search
- Large-scale quantum circuit benchmarks

## Related Demonstration

https://quantum-circuit-demo01-appd9ztiavchu2gtvrfu9dp.streamlit.app/

The demonstration and this database package are related
but currently separate development efforts.

## Repository

https://github.com/JiahengLu-CS/qch-database
