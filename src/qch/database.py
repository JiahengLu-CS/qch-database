import sqlite3
from pathlib import Path


class QCHDatabase:
    """A lightweight SQLite database for quantum circuits."""

    def __init__(self, db_path="circuits.db"):
        self.db_path = Path(db_path)
        self.connection = sqlite3.connect(self.db_path)
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
        self.connection.commit()

    def close(self):
        self.connection.close()
    def add_circuit(self, name, qasm_path):
        """Store an OpenQASM circuit."""
        qasm = Path(qasm_path).read_text(encoding="utf-8")

        cursor = self.connection.execute(
            "INSERT INTO circuits (name, qasm) VALUES (?, ?)",
            (name, qasm)
        )
        self.connection.commit()
        return cursor.lastrowid

    def get_circuit(self, name):
        """Retrieve the OpenQASM content of a circuit."""
        cursor = self.connection.execute(
            "SELECT qasm FROM circuits WHERE name = ?",
            (name,)
        )
        row = cursor.fetchone()
        return row[0] if row else None
    def get_features(self, name):
        """Compute, store and return basic circuit features."""
        from qiskit import QuantumCircuit

        qasm = self.get_circuit(name)
        if qasm is None:
            return None

        qc = QuantumCircuit.from_qasm_str(qasm)

        features = {
            "num_qubits": qc.num_qubits,
            "gate_count": qc.size(),
            "depth": qc.depth()
        }

        self.connection.execute(
            """
            INSERT INTO features
                (circuit_id, num_qubits, gate_count, depth)
            VALUES (
                (SELECT id FROM circuits WHERE name = ?),
                ?, ?, ?
            )
            ON CONFLICT(circuit_id) DO UPDATE SET
                num_qubits = excluded.num_qubits,
                gate_count = excluded.gate_count,
                depth = excluded.depth
            """,
            (
                name,
                features["num_qubits"],
                features["gate_count"],
                features["depth"]
            )
        )
        self.connection.commit()

        return features
    def get_gates(self, name):
        """Extract, store and return circuit gates."""
        import json
        from qiskit import QuantumCircuit

        qasm = self.get_circuit(name)
        if qasm is None:
            return None

        qc = QuantumCircuit.from_qasm_str(qasm)

        circuit_id = self.connection.execute(
            "SELECT id FROM circuits WHERE name = ?",
            (name,)
        ).fetchone()[0]

        gates = []

        with self.connection:
            self.connection.execute(
                "DELETE FROM gates WHERE circuit_id = ?",
                (circuit_id,)
            )

            for index, instruction in enumerate(qc.data):
                gate = {
                    "gate_index": index,
                    "gate_type": instruction.operation.name,
                    "qubits": [
                        qc.find_bit(q).index
                        for q in instruction.qubits
                    ]
                }

                self.connection.execute(
                    """
                    INSERT INTO gates
                        (circuit_id, gate_index, gate_type, qubits)
                    VALUES (?, ?, ?, ?)
                    """,
                    (
                        circuit_id,
                        gate["gate_index"],
                        gate["gate_type"],
                        json.dumps(gate["qubits"])
                    )
                )

                gates.append(gate)

        return gates
