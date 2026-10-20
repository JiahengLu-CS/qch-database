import json
import sqlite3
from pathlib import Path

from qiskit import QuantumCircuit


class QCHDatabase:
    """A lightweight SQLite database for quantum circuits."""

    def __init__(self, db_path="circuits.db"):
        self.db_path = Path(db_path)
        self.connection = sqlite3.connect(self.db_path)
        self.connection.execute("PRAGMA foreign_keys = ON")
        self._create_tables()

    def _create_tables(self):
        self.connection.executescript("""
            CREATE TABLE IF NOT EXISTS circuits (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE,
                qasm TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS gates (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                circuit_id INTEGER NOT NULL,
                gate_index INTEGER NOT NULL,
                gate_type TEXT NOT NULL,
                qubits TEXT NOT NULL,
                FOREIGN KEY (circuit_id) REFERENCES circuits(id)
            );

            CREATE TABLE IF NOT EXISTS features (
                circuit_id INTEGER PRIMARY KEY,
                num_qubits INTEGER,
                gate_count INTEGER,
                depth INTEGER,
                two_qubit_gate_count INTEGER,
                t_count INTEGER,
                FOREIGN KEY (circuit_id) REFERENCES circuits(id)
        );

            CREATE TABLE IF NOT EXISTS logical_circuits (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE,
                description TEXT
            );

            CREATE TABLE IF NOT EXISTS circuit_versions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,

                logical_circuit_id INTEGER NOT NULL,

                version_number INTEGER NOT NULL
                CHECK (version_number >= 1),

                version_label TEXT,

                circuit_id INTEGER NOT NULL,

                FOREIGN KEY (logical_circuit_id)
                REFERENCES logical_circuits(id),

                FOREIGN KEY (circuit_id)
                REFERENCES circuits(id),

                UNIQUE (logical_circuit_id, version_number)
            );

           CREATE TABLE IF NOT EXISTS transformation_edges (
                id INTEGER PRIMARY KEY AUTOINCREMENT,

                source_version_id INTEGER NOT NULL,
                target_version_id INTEGER NOT NULL,

                transformation_type TEXT NOT NULL,
                description TEXT,

                FOREIGN KEY (source_version_id)
                    REFERENCES circuit_versions(id),

                FOREIGN KEY (target_version_id)
                    REFERENCES circuit_versions(id),

                CHECK (source_version_id != target_version_id),

                UNIQUE (
                    source_version_id,
                    target_version_id,
                    transformation_type
                    )
        );

        """)

      # Safely migrate existing databases.
        existing_columns = {
            row[1]
            for row in self.connection.execute(
                "PRAGMA table_info(features)"
            ).fetchall()
        }

        for column in ("two_qubit_gate_count", "t_count"):
            if column not in existing_columns:
                self.connection.execute(
                    f"ALTER TABLE features ADD COLUMN {column} INTEGER"
                )

        self.connection.commit()

    def add_logical_circuit(self, name, description=None):
        """Create a logical circuit and return its ID."""
        with self.connection:
            cursor = self.connection.execute(
                """
                INSERT INTO logical_circuits (name, description)
                VALUES (?, ?)
                """,
                (name, description)
            )
        return cursor.lastrowid

    def add_version(self, logical_name, circuit_name, label=None):
        """Add a circuit version with an automatically assigned number."""

        with self.connection:
            logical = self.connection.execute(
                "SELECT id FROM logical_circuits WHERE name = ?",
                (logical_name,)
            ).fetchone()

            if logical is None:
                raise ValueError(
                    f"Logical circuit not found: {logical_name}"
                )

            circuit = self.connection.execute(
                "SELECT id FROM circuits WHERE name = ?",
                (circuit_name,)
            ).fetchone()

            if circuit is None:
                raise ValueError(
                    f"Circuit not found: {circuit_name}"
                )

            logical_id = logical[0]
            circuit_id = circuit[0]

            row = self.connection.execute(
                """
                SELECT COALESCE(MAX(version_number), 0) + 1
                FROM circuit_versions
                WHERE logical_circuit_id = ?
                """,
                (logical_id,)
            ).fetchone()

            version_number = row[0]

            self.connection.execute(
                """
                INSERT INTO circuit_versions
                    (logical_circuit_id, version_number,
                     version_label, circuit_id)
                VALUES (?, ?, ?, ?)
                """,
                (logical_id, version_number, label, circuit_id)
            )

        return version_number


    def list_versions(self, logical_name):
        """List versions of a logical circuit in version order."""
        rows = self.connection.execute(
            """
            SELECT
                cv.version_number,
                cv.version_label,
                c.name,
                f.num_qubits,
                f.gate_count,
                f.depth,
                f.two_qubit_gate_count,
                f.t_count
            FROM circuit_versions AS cv
            JOIN logical_circuits AS lc
                ON lc.id = cv.logical_circuit_id
            JOIN circuits AS c
                ON c.id = cv.circuit_id
            LEFT JOIN features AS f
                ON f.circuit_id = c.id
            WHERE lc.name = ?
            ORDER BY cv.version_number ASC
            """,
            (logical_name,)
        ).fetchall()

        return [
            {
                "version_number": row[0],
                "version_label": row[1],
                "circuit_name": row[2],
                "num_qubits": row[3],
                "gate_count": row[4],
                "depth": row[5],
        }
            for row in rows
        ]

    def get_version(self, logical_name, version_number):
        """Return one version of a logical circuit, or None."""
        row = self.connection.execute(
            """
            SELECT
                cv.version_number,
                cv.version_label,
                c.name,
                f.num_qubits,
                f.gate_count,
                f.depth
            FROM circuit_versions AS cv
            JOIN logical_circuits AS lc
                ON lc.id = cv.logical_circuit_id
            JOIN circuits AS c
                ON c.id = cv.circuit_id
            LEFT JOIN features AS f
                ON f.circuit_id = c.id
            WHERE lc.name = ?
              AND cv.version_number = ?
            """,
            (logical_name, version_number)
        ).fetchone()

        if row is None:
            return None

        return {
            "version_number": row[0],
            "version_label": row[1],
            "circuit_name": row[2],
            "num_qubits": row[3],
            "gate_count": row[4],
            "depth": row[5]
        }

    def compare_versions(self, logical_name, version_a, version_b):
        """Compare structural metrics of two circuit versions."""

        first = self.get_version(logical_name, version_a)
        second = self.get_version(logical_name, version_b)

        if first is None:
            raise ValueError(
                f"Version not found: {logical_name} v{version_a}"
            )

        if second is None:
            raise ValueError(
                f"Version not found: {logical_name} v{version_b}"
            )

        metrics = {}

        for metric in ("num_qubits", "gate_count", "depth"):
            value_a = first[metric]
            value_b = second[metric]

            metrics[metric] = {
                "version_a": value_a,
                "version_b": value_b,
                "delta": (
                    value_b - value_a
                    if value_a is not None and value_b is not None
                    else None
                )
            }

        return {
            "logical_circuit": logical_name,
            "version_a": version_a,
            "version_b": version_b,
            "metrics": metrics
        }


    def add_transformation(
        self,
        logical_name,
        source_version,
        target_version,
        transformation_type,
        description=None
    ):
        """Record a transformation between two circuit versions."""

        if source_version == target_version:
            raise ValueError("Source and target versions must differ")

        if not transformation_type or not transformation_type.strip():
            raise ValueError("Transformation type cannot be empty")

        def find_version_id(version_number):
            row = self.connection.execute(
                """
                SELECT cv.id
                FROM circuit_versions AS cv
                JOIN logical_circuits AS lc
                    ON lc.id = cv.logical_circuit_id
                WHERE lc.name = ?
                  AND cv.version_number = ?
                """,
                (logical_name, version_number)
            ).fetchone()

            if row is None:
                raise ValueError(
                    f"Version not found: {logical_name} v{version_number}"
                )

            return row[0]

        with self.connection:
            source_id = find_version_id(source_version)
            target_id = find_version_id(target_version)

            # Reject transformations that would create a cycle.
            cycle = self.connection.execute(
                """
                WITH RECURSIVE reachable(version_id) AS (
                    SELECT ?
                    UNION
                    SELECT e.target_version_id
                    FROM transformation_edges AS e
                    JOIN reachable AS r
                        ON e.source_version_id = r.version_id
                )
                SELECT 1
                FROM reachable
                WHERE version_id = ?
                LIMIT 1
                """,
                (target_id, source_id)
            ).fetchone()

            if cycle is not None:
                raise ValueError(
                    f"Transformation would create a cycle: "
                    f"{logical_name} v{source_version} -> v{target_version}"
                )

            cursor = self.connection.execute(
                """
                INSERT INTO transformation_edges
                    (
                        source_version_id,
                        target_version_id,
                        transformation_type,
                        description
                    )
                VALUES (?, ?, ?, ?)
                """,
                (
                    source_id,
                    target_id,
                    transformation_type.strip(),
                    description
                )
            )

        return cursor.lastrowid

    def list_transformations(self, logical_name):
        """List transformation edges for a logical circuit."""

        rows = self.connection.execute(
            """
            SELECT
                e.id,
                source.version_number,
                target.version_number,
                e.transformation_type,
                e.description
            FROM transformation_edges AS e
            JOIN circuit_versions AS source
                ON source.id = e.source_version_id
            JOIN circuit_versions AS target
                ON target.id = e.target_version_id
            JOIN logical_circuits AS lc
                ON lc.id = source.logical_circuit_id
            WHERE lc.name = ?
              AND target.logical_circuit_id = lc.id
            ORDER BY e.id ASC
            """,
            (logical_name,)
        ).fetchall()

        return [
            {
                "edge_id": row[0],
                "source_version": row[1],
                "target_version": row[2],
                "transformation_type": row[3],
                "description": row[4]
            }
            for row in rows
        ]


    def get_ancestors(self, logical_name, version_number):
        """Return all ancestor version numbers, excluding the version itself."""

        row = self.connection.execute(
            """
            SELECT cv.id
            FROM circuit_versions AS cv
            JOIN logical_circuits AS lc
                ON lc.id = cv.logical_circuit_id
            WHERE lc.name = ?
              AND cv.version_number = ?
            """,
            (logical_name, version_number)
        ).fetchone()

        if row is None:
            raise ValueError(
                f"Version not found: {logical_name} v{version_number}"
            )

        version_id = row[0]

        rows = self.connection.execute(
            """
            WITH RECURSIVE ancestors(version_id) AS (
                SELECT e.source_version_id
                FROM transformation_edges AS e
                WHERE e.target_version_id = ?

                UNION

                SELECT e.source_version_id
                FROM transformation_edges AS e
                JOIN ancestors AS a
                    ON e.target_version_id = a.version_id
            )
            SELECT DISTINCT cv.version_number
            FROM ancestors AS a
            JOIN circuit_versions AS cv
                ON cv.id = a.version_id
            JOIN circuit_versions AS target
                ON target.id = ?
            WHERE cv.logical_circuit_id = target.logical_circuit_id
              AND cv.id != target.id
            ORDER BY cv.version_number
            """,
            (version_id, version_id)
        ).fetchall()

        return [row[0] for row in rows]


    def get_descendants(self, logical_name, version_number):
        """Return all descendant version numbers, excluding the version itself."""

        row = self.connection.execute(
            """
            SELECT cv.id
            FROM circuit_versions AS cv
            JOIN logical_circuits AS lc
                ON lc.id = cv.logical_circuit_id
            WHERE lc.name = ?
              AND cv.version_number = ?
            """,
            (logical_name, version_number)
        ).fetchone()

        if row is None:
            raise ValueError(
                f"Version not found: {logical_name} v{version_number}"
            )

        version_id = row[0]

        rows = self.connection.execute(
            """
            WITH RECURSIVE descendants(version_id) AS (
                SELECT e.target_version_id
                FROM transformation_edges AS e
                WHERE e.source_version_id = ?

                UNION

                SELECT e.target_version_id
                FROM transformation_edges AS e
                JOIN descendants AS d
                    ON e.source_version_id = d.version_id
            )
            SELECT DISTINCT cv.version_number
            FROM descendants AS d
            JOIN circuit_versions AS cv
                ON cv.id = d.version_id
            JOIN circuit_versions AS source
                ON source.id = ?
            WHERE cv.logical_circuit_id = source.logical_circuit_id
              AND cv.id != source.id
            ORDER BY cv.version_number
            """,
            (version_id, version_id)
        ).fetchall()

        return [row[0] for row in rows]

    def find_evolution_paths(
        self, logical_name, source_version, target_version,
        max_paths=1000
    ):
        """Find directed evolution paths between two versions."""

        if max_paths < 1:
            raise ValueError("max_paths must be positive")

        def find_version_id(version_number):
            row = self.connection.execute(
                """
                SELECT cv.id
                FROM circuit_versions AS cv
                JOIN logical_circuits AS lc
                    ON lc.id = cv.logical_circuit_id
                WHERE lc.name = ? AND cv.version_number = ?
                """,
                (logical_name, version_number)
            ).fetchone()

            if row is None:
                raise ValueError(
                    f"Version not found: {logical_name} v{version_number}"
                )

            return row[0]

        source_id = find_version_id(source_version)
        target_id = find_version_id(target_version)

        if source_id == target_id:
            return [[source_version]]

        # Load edges belonging to this logical circuit.
        rows = self.connection.execute(
            """
            SELECT e.source_version_id, e.target_version_id,
                   target.version_number
            FROM transformation_edges AS e
            JOIN circuit_versions AS source
                ON source.id = e.source_version_id
            JOIN circuit_versions AS target
                ON target.id = e.target_version_id
            WHERE source.logical_circuit_id = (
                SELECT id FROM logical_circuits WHERE name = ?
            )
              AND target.logical_circuit_id = source.logical_circuit_id
            ORDER BY target.version_number
            """,
            (logical_name,)
        ).fetchall()

        adjacency = {}

        for from_id, to_id, to_number in rows:
            adjacency.setdefault(from_id, set()).add(
                (to_id, to_number)
            )

        paths = []

        def dfs(current_id, path, visited):
            if len(paths) >= max_paths:
                return

            if current_id == target_id:
                paths.append(path.copy())
                return

            for next_id, next_number in sorted(
                adjacency.get(current_id, set()),
                key=lambda item: item[1]
            ):
                if next_id not in visited:
                    visited.add(next_id)
                    path.append(next_number)

                    dfs(next_id, path, visited)

                    path.pop()
                    visited.remove(next_id)

                    if len(paths) >= max_paths:
                        return

        dfs(source_id, [source_version], {source_id})

        return paths


    def backfill_resource_metrics(self):
        """Fill missing resource metrics using stored gate records."""

        rows = self.connection.execute(
            """
            SELECT
                f.circuit_id,
                SUM(
                    CASE
                        WHEN json_array_length(g.qubits) = 2
                        THEN 1 ELSE 0
                    END
                ),
                SUM(
                    CASE
                        WHEN g.gate_type = 't'
                        THEN 1 ELSE 0
                    END
                )
            FROM features AS f
            LEFT JOIN gates AS g
                ON g.circuit_id = f.circuit_id
            WHERE f.two_qubit_gate_count IS NULL
               OR f.t_count IS NULL
            GROUP BY f.circuit_id
            """
        ).fetchall()

        with self.connection:
            for circuit_id, two_qubit_count, t_count in rows:
                self.connection.execute(
                    """
                    UPDATE features
                    SET
                        two_qubit_gate_count =
                            COALESCE(
                                two_qubit_gate_count, ?
                            ),
                        t_count =
                            COALESCE(t_count, ?)
                    WHERE circuit_id = ?
                    """,
                    (
                        two_qubit_count or 0,
                        t_count or 0,
                        circuit_id
                    )
                )

        return len(rows)


    def add_circuit(self, name, qasm_path):
        """Parse and store a circuit, its gates, and its features."""
        qasm = Path(qasm_path).read_text(encoding="utf-8")
        qc = QuantumCircuit.from_qasm_str(qasm)

        gates = []
        two_qubit_gate_count = 0
        t_count = 0

        for index, instruction in enumerate(qc.data):
            gate_type = instruction.operation.name
            qubits = [
                qc.find_bit(q).index
                for q in instruction.qubits
            ]

            gates.append((
                index,
                gate_type,
                json.dumps(qubits)
            ))

            # Count gates acting on exactly two qubits.
            if len(qubits) == 2:
                two_qubit_gate_count += 1

            # Count T gates (excluding T-dagger).
            if gate_type == "t":
                t_count += 1

        with self.connection:
            cursor = self.connection.execute(
                "INSERT INTO circuits (name, qasm) VALUES (?, ?)",
                (name, qasm)
            )
            circuit_id = cursor.lastrowid

            self.connection.executemany(
                """
                INSERT INTO gates
                    (circuit_id, gate_index, gate_type, qubits)
                VALUES (?, ?, ?, ?)
                """,
                [
                    (circuit_id, index, gate_type, qubits)
                    for index, gate_type, qubits in gates
                ]
            )

            self.connection.execute(
                """
                INSERT INTO features
                    (
                        circuit_id,
                        num_qubits,
                        gate_count,
                        depth,
                        two_qubit_gate_count,
                        t_count
                    )
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    circuit_id,
                    qc.num_qubits,
                    qc.size(),
                    qc.depth(),
                    two_qubit_gate_count,
                    t_count
                )
            )

        return circuit_id

    def get_circuit(self, name):
        """Return stored OpenQASM content."""
        row = self.connection.execute(
            "SELECT qasm FROM circuits WHERE name = ?",
            (name,)
        ).fetchone()
        return row[0] if row else None

    def get_features(self, name):
        """Read stored features without reparsing the circuit."""
        row = self.connection.execute(
            """
            SELECT
                f.num_qubits,
                f.gate_count,
                f.depth,
                f.two_qubit_gate_count,
                f.t_count
            FROM features AS f
            JOIN circuits AS c ON c.id = f.circuit_id
            WHERE c.name = ?
            """,
            (name,)
        ).fetchone()

        if row is None:
            return None

        return {
            "num_qubits": row[0],
            "gate_count": row[1],
            "depth": row[2],
            "two_qubit_gate_count": row[3],
            "t_count": row[4],
        }

    def get_gates(self, name):
        """Read stored gates without reparsing the circuit."""
        circuit = self.connection.execute(
            "SELECT id FROM circuits WHERE name = ?",
            (name,)
        ).fetchone()

        if circuit is None:
            return None

        rows = self.connection.execute(
            """
            SELECT gate_index, gate_type, qubits
            FROM gates
            WHERE circuit_id = ?
            ORDER BY gate_index
            """,
            (circuit[0],)
        ).fetchall()

        return [
            {
                "gate_index": index,
                "gate_type": gate_type,
                "qubits": json.loads(qubits)
            }
            for index, gate_type, qubits in rows
        ]

    def close(self):
        self.connection.close()

    def list_circuits(self):
        """List all stored circuit names."""
        rows = self.connection.execute(
            "SELECT name FROM circuits ORDER BY name"
        ).fetchall()
        return [row[0] for row in rows]

    def find_circuits(
        self,
        min_qubits=None,
        max_qubits=None,
        min_depth=None,
        max_depth=None,
        max_gates=None,
        order_by="name",
        descending=False
    ):
        """Find circuits using stored features."""
        sql = """
            SELECT c.name
            FROM circuits AS c
            JOIN features AS f ON c.id = f.circuit_id
            WHERE 1 = 1
        """
        params = []

        conditions = [
            ("f.num_qubits >= ?", min_qubits),
            ("f.num_qubits <= ?", max_qubits),
            ("f.depth >= ?", min_depth),
            ("f.depth <= ?", max_depth),
            ("f.gate_count <= ?", max_gates)
        ]

        for condition, value in conditions:
            if value is not None:
                sql += " AND " + condition
                params.append(value)

        allowed_columns = {
            "name": "c.name",
            "num_qubits": "f.num_qubits",
            "gate_count": "f.gate_count",
            "depth": "f.depth"
        }

        if order_by not in allowed_columns:
            raise ValueError(f"Unsupported order_by: {order_by}")

        direction = "DESC" if descending else "ASC"
        sql += f" ORDER BY {allowed_columns[order_by]} {direction}, c.name ASC"

        rows = self.connection.execute(sql, params).fetchall()
        return [row[0] for row in rows]


    def find_best_version(self, logical_name, metric="gate_count"):
        """Find the best circuit version by a structural metric."""

        allowed_metrics = {
            "gate_count": "f.gate_count",
            "depth": "f.depth",
            "num_qubits": "f.num_qubits",
            "two_qubit_gate_count": "f.two_qubit_gate_count",
            "t_count": "f.t_count",
        }

        if metric not in allowed_metrics:
            raise ValueError(f"Unsupported metric: {metric}")

        column = allowed_metrics[metric]

        row = self.connection.execute(
            f"""
            SELECT
                cv.version_number,
                cv.version_label,
                c.name,
                f.num_qubits,
                f.gate_count,
                f.depth
            FROM circuit_versions AS cv
            JOIN logical_circuits AS lc
                ON lc.id = cv.logical_circuit_id
            JOIN circuits AS c
                ON c.id = cv.circuit_id
            JOIN features AS f
                ON f.circuit_id = c.id
            WHERE lc.name = ?
              AND {column} IS NOT NULL
            ORDER BY {column} ASC, cv.version_number ASC
            LIMIT 1
            """,
            (logical_name,)
        ).fetchone()

        if row is None:
            return None

        return {
            "version_number": row[0],
            "version_label": row[1],
            "circuit_name": row[2],
            "num_qubits": row[3],
            "gate_count": row[4],
            "depth": row[5],
            "optimized_metric": metric,
        }

    def find_pareto_versions(
        self, logical_name, metrics=("gate_count", "depth")
    ):
        """Find Pareto-optimal circuit versions (smaller is better)."""

        allowed_metrics = {
            "gate_count": "gate_count",
            "depth": "depth",
            "num_qubits": "num_qubits",
            "two_qubit_gate_count": "two_qubit_gate_count",
            "t_count": "t_count",
    }

        if isinstance(metrics, str):
            raise ValueError("metrics must be a non-empty sequence")

        try:
            metrics = tuple(metrics)
        except TypeError:
            raise ValueError("metrics must be a non-empty sequence") from None

        if not metrics or any(
            not isinstance(m, str) or m not in allowed_metrics
            for m in metrics
        ):
            raise ValueError("Unsupported metrics")

        if len(set(metrics)) != len(metrics):
            raise ValueError("Duplicate metrics are not allowed")

        rows = self.connection.execute(
            """
            SELECT
                cv.version_number,
                cv.version_label,
                c.name,
                f.num_qubits,
                f.gate_count,
                f.depth,
                f.two_qubit_gate_count,
                f.t_count
            FROM circuit_versions AS cv
            JOIN logical_circuits AS lc
                ON lc.id = cv.logical_circuit_id
            JOIN circuits AS c
                ON c.id = cv.circuit_id
            JOIN features AS f
                ON f.circuit_id = c.id
            WHERE lc.name = ?
            ORDER BY cv.version_number
            """,
            (logical_name,)
        ).fetchall()

        versions = [
            {
                "version_number": row[0],
                "version_label": row[1],
                "circuit_name": row[2],
                "num_qubits": row[3],
                "gate_count": row[4],
                "depth": row[5],
                "two_qubit_gate_count": row[6],
                "t_count": row[7],
            }
            for row in rows
        ]

        # Only compare versions with all requested metrics available.
        versions = [
            v for v in versions
            if all(v[m] is not None for m in metrics)
        ]

        def dominates(a, b):
            return (
                all(a[m] <= b[m] for m in metrics)
                and any(a[m] < b[m] for m in metrics)
            )

        frontier = []

        for candidate in versions:
            if not any(
                dominates(other, candidate)
                for other in versions
                if other is not candidate
            ):
                frontier.append(candidate)

        return frontier