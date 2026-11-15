"""Tests for the TwoQubitBlockResynthesis step (stage 2G)."""

import pytest
from qiskit import QuantumCircuit
from qiskit.quantum_info import Operator
from qiskit.transpiler import PassManager
from qiskit.transpiler.passes import Collect2qBlocks, ConsolidateBlocks, UnitarySynthesis

from qch import QCHDatabase
from qch import optimization as opt

HEADER = 'OPENQASM 2.0;\ninclude "qelib1.inc";\n'
IBM = ["rz", "sx", "x", "cx"]
G = {"name": "TwoQubitBlockResynthesis", "params": {"basis_gates": IBM}}

# Six CX on one pair: a two-qubit block that needs at most three CX.
BLOCK_QASM = (
    "qreg q[3];\ncreg c[3];\n"
    "h q[0];\ncx q[0],q[1];\nrz(0.3) q[1];\ncx q[0],q[1];\n"
    "cx q[1],q[0];\nsx q[0];\ncx q[0],q[1];\ncx q[1],q[0];\nrz(1.1) q[0];\n"
    "cx q[0],q[1];\ncx q[1],q[2];\nbarrier q[0],q[1],q[2];\n"
    "measure q[0] -> c[0];\nmeasure q[1] -> c[1];\nmeasure q[2] -> c[2];\n"
)


def store(tmp_path, body=BLOCK_QASM):
    db = QCHDatabase(tmp_path / "g.db")
    path = tmp_path / "in.qasm"
    path.write_text(HEADER + body, encoding="utf-8")
    db.add_circuit("demo", path)
    return db


def unitary_part(circuit):
    return circuit.remove_final_measurements(inplace=False)


def cx_count(circuit):
    return circuit.count_ops().get("cx", 0)


def test_reduces_two_qubit_gates_and_is_one_step(tmp_path):
    db = store(tmp_path)
    result = opt.run_pass_sequence(db, "demo", [G])
    assert result.status == "SUCCEEDED"
    steps = opt.list_optimization_steps(db, result.run_id)
    assert [s["pass_name"] for s in steps] == [None, "TwoQubitBlockResynthesis"]
    before = opt.load_step_circuit(db, result.run_id, 0)
    after = opt.load_step_circuit(db, result.run_id, 1)
    assert cx_count(before) == 7
    assert cx_count(after) <= 4          # at most 3 for the block, 1 for q1-q2
    assert steps[1]["two_qubit_gate_count"] < steps[0]["two_qubit_gate_count"]
    assert set(after.count_ops()) <= set(IBM) | {"measure", "barrier"}
    assert Operator(unitary_part(after)).equiv(Operator(unitary_part(before)))
    # Measurements and their mapping are untouched.
    assert after.count_ops()["measure"] == 3
    assert [(after.find_bit(i.qubits[0]).index, after.find_bit(i.clbits[0]).index)
            for i in after.data if i.operation.name == "measure"] == [(0, 0), (1, 1), (2, 2)]
    db.close()


def test_matches_the_three_qiskit_passes_run_directly():
    circuit = QuantumCircuit.from_qasm_str(HEADER + BLOCK_QASM)
    step = opt.prepare_pass_sequence([G])[0].build()
    ours = PassManager([step]).run(circuit)
    direct = PassManager([
        Collect2qBlocks(),
        ConsolidateBlocks(basis_gates=IBM, approximation_degree=1.0),
        UnitarySynthesis(basis_gates=IBM, approximation_degree=1.0),
    ]).run(circuit)
    assert opt.representation_fingerprint(ours) == opt.representation_fingerprint(direct)


@pytest.mark.parametrize("basis, two_qubit", [
    (["rz", "sx", "x", "cz"], "cz"),
    (["rz", "sx", "x", "ecr"], "ecr"),
    (["u", "cx"], "cx"),
])
def test_other_bases_stay_in_the_basis(basis, two_qubit):
    circuit = QuantumCircuit.from_qasm_str(HEADER + BLOCK_QASM)
    step = opt.prepare_pass_sequence(
        [{"name": "TwoQubitBlockResynthesis", "params": {"basis_gates": basis}}]
    )[0].build()
    out = PassManager([step]).run(circuit)
    assert set(out.count_ops()) <= set(basis) | {"measure", "barrier"}
    assert out.count_ops()[two_qubit] <= 4
    assert Operator(unitary_part(out)).equiv(Operator(unitary_part(circuit)))


def test_nothing_to_gain_keeps_the_two_qubit_count():
    circuit = QuantumCircuit.from_qasm_str(
        HEADER + "qreg q[2];\nsx q[0];\ncx q[0],q[1];\nrz(0.2) q[1];\n")
    step = opt.prepare_pass_sequence([G])[0].build()
    out = PassManager([step]).run(circuit)
    assert cx_count(out) == 1
    assert Operator(out).equiv(Operator(circuit))


def test_inside_a_sequence_with_other_passes(tmp_path):
    db = store(tmp_path)
    sequence = [
        {"name": "Optimize1qGatesDecomposition", "params": {"basis": IBM}},
        G,
        {"name": "CommutativeCancellation", "params": {"basis_gates": IBM}},
        G,
    ]
    result = opt.run_pass_sequence(db, "demo", sequence)
    assert result.status == "SUCCEEDED"
    steps = opt.list_optimization_steps(db, result.run_id)
    assert [s["step_index"] for s in steps] == [0, 1, 2, 3, 4]
    assert all(s["pass_time_s"] is not None for s in steps[1:])
    final = opt.load_step_circuit(db, result.run_id, 4)
    first = opt.load_step_circuit(db, result.run_id, 0)
    assert Operator(unitary_part(final)).equiv(Operator(unitary_part(first)))
    assert opt.pass_equivalence("TwoQubitBlockResynthesis") == opt.EQUIVALENCE_EXACT
    db.close()


@pytest.mark.parametrize("params", [
    {},
    {"basis_gates": ["rz", "sx", "x"]},          # no two-qubit gate
    {"basis_gates": ["cx"]},                     # no one-qubit Euler basis
    {"basis_gates": ["rz", "sx", "cx", "bogus"]},
    {"basis_gates": IBM, "approximation_degree": 0.9},
])
def test_invalid_parameters_are_rejected_before_any_write(tmp_path, params):
    db = store(tmp_path)
    with pytest.raises(opt.PassConfigError):
        opt.run_pass_sequence(db, "demo", [{"name": "TwoQubitBlockResynthesis",
                                            "params": params}])
    assert opt.get_optimization_schema_state(db) == "absent"
    db.close()
