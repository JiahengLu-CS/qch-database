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

## Project Status

Current development version: 0.1.0

Latest verified test result: 9 tests passed.

## Development Progress

For completed milestones, architecture changes, and upcoming work:

**[Development Progress and Roadmap](docs/PROGRESS.md)**

## Quick Start

Install the project locally:

    python -m pip install -e .

Example:

    from qch import QCHDatabase

    db = QCHDatabase("circuits.db")
    db.add_circuit("ghz3", "ghz3.qasm")

    print(db.get_features("ghz3"))
    print(db.get_gates("ghz3"))

    db.close()

## Run Tests

    python -m pytest tests/test_database.py -v

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
