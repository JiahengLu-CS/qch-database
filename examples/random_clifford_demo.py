"""Run recorded pass sequences on a random, seeded Clifford circuit.

The script creates a NEW database, stores one random Clifford circuit,
runs three pass sequences with qch.optimization, checks that every final
circuit is the same Clifford operator as the input, and prints the
recorded steps through a read-only connection.

Example (PowerShell, from the repository root):

  .\\.venv\\Scripts\\python.exe examples\\random_clifford_demo.py `
      --db demo\\random_clifford.db

Options: --seed, --qubits, --gates. The same seed always produces the
same circuit.
"""

from __future__ import annotations

import argparse
import random
import sys
import tempfile
from pathlib import Path

from qiskit import QuantumCircuit, qasm2
from qiskit.quantum_info import Clifford

from qch import QCHDatabase
from qch import optimization as opt
from pass_sequence_demo import print_run

ONE_QUBIT_GATES = ["h", "s", "sdg", "x", "y", "z", "sx"]
TWO_QUBIT_GATES = ["cx", "cz", "swap"]

O1Q = {
    "name": "Optimize1qGatesDecomposition",
    "params": {"basis": ["rz", "sx", "x", "cx", "cz", "swap"]},
}
IC = {"name": "InverseCancellation"}
IC_PAIRS = {
    "name": "InverseCancellation",
    "params": {
        "gates_to_cancel": [["s", "sdg"], ["sx", "sxdg"]],
        "run_default": True,
    },
}
RIE = {"name": "RemoveIdentityEquivalent"}

SEQUENCES = {
    "IC -> RIE -> IC": [IC, RIE, IC],
    "IC(pairs) -> O1Q -> IC": [IC_PAIRS, O1Q, IC],
    "O1Q -> IC -> O1Q": [O1Q, IC, O1Q],
}


def random_clifford_circuit(num_qubits, num_gates, seed):
    """Build a seeded random circuit of Clifford gates."""
    if num_qubits < 2:
        raise ValueError("At least two qubits are required")
    rng = random.Random(seed)
    circuit = QuantumCircuit(num_qubits, name="random_clifford")
    for _ in range(num_gates):
        if rng.random() < 0.65:
            gate = rng.choice(ONE_QUBIT_GATES)
            getattr(circuit, gate)(rng.randrange(num_qubits))
        else:
            first, second = rng.sample(range(num_qubits), 2)
            gate = rng.choice(TWO_QUBIT_GATES)
            getattr(circuit, gate)(first, second)
    return circuit


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Run recorded pass sequences on a random Clifford circuit."
    )
    parser.add_argument("--db", required=True,
                        help="Path of a NEW database to create.")
    parser.add_argument("--seed", type=int, default=20261024)
    parser.add_argument("--qubits", type=int, default=4)
    parser.add_argument("--gates", type=int, default=40)
    args = parser.parse_args(argv)

    db_path = Path(args.db)
    if db_path.exists():
        print(f"Error: database already exists: {db_path}", file=sys.stderr)
        return 2
    db_path.parent.mkdir(parents=True, exist_ok=True)

    circuit = random_clifford_circuit(args.qubits, args.gates, args.seed)
    name = f"random_clifford_q{args.qubits}_g{args.gates}_s{args.seed}"
    reference = Clifford(circuit)

    db = QCHDatabase(db_path)
    try:
        with tempfile.TemporaryDirectory() as tmp:
            qasm_path = Path(tmp) / "random_clifford.qasm"
            qasm_path.write_text(qasm2.dumps(circuit), encoding="utf-8")
            db.add_circuit(name, qasm_path)
        print(f"Stored circuit {name!r}: {db.get_features(name)}")

        results = []
        for label, sequence in SEQUENCES.items():
            result = opt.run_pass_sequence(db, name, sequence, label=label)
            results.append((label, result))
    finally:
        db.close()

    connection = opt.open_readonly(db_path)
    try:
        all_equal = True
        for label, result in results:
            final = opt.load_step_circuit(
                connection, result.run_id, len(SEQUENCES[label])
            )
            equal = Clifford(final) == reference
            all_equal = all_equal and equal
            print()
            print_run(opt.get_optimization_run(connection, result.run_id))
            print(f"  Final circuit is the same Clifford as the input: {equal}")
            print(f"  Final gate names: {sorted(final.count_ops())}")
    finally:
        connection.close()

    print()
    print("All final circuits equal the input Clifford:", all_equal)
    return 0 if all_equal else 1


if __name__ == "__main__":
    sys.exit(main())
