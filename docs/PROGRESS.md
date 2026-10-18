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
