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
                FOREIGN KEY (circuit_id) REFERENCES circuits(id)
            );
        """)

    def add_circuit(self, name, qasm_path):
        """Parse and store a circuit, its gates, and its features."""
        qasm = Path(qasm_path).read_text(encoding="utf-8")
        qc = QuantumCircuit.from_qasm_str(qasm)

        gates = []
        for index, instruction in enumerate(qc.data):
            gates.append((
                index,
                instruction.operation.name,
                json.dumps([
                    qc.find_bit(q).index
                    for q in instruction.qubits
                ])
            ))

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
                    (circuit_id, num_qubits, gate_count, depth)
                VALUES (?, ?, ?, ?)
                """,
                (
                    circuit_id,
                    qc.num_qubits,
                    qc.size(),
                    qc.depth()
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
            SELECT f.num_qubits, f.gate_count, f.depth
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
            "depth": row[2]
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
        max_gates=None
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

        sql += " ORDER BY c.name"

        rows = self.connection.execute(sql, params).fetchall()
        return [row[0] for row in rows]
