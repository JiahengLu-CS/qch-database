"""Tests for the second group of whitelisted passes in qch.optimization."""

import pytest
from qiskit import QuantumCircuit
from qiskit.quantum_info import DensityMatrix, Operator
from qiskit.transpiler import PassManager

from qch import QCHDatabase
from qch import optimization as opt


HEADER = 'OPENQASM 2.0;\ninclude "qelib1.inc";\n'
IBM_BASIS = ["rz", "sx", "x", "cx"]

NEW_PASSES = [
    "CommutativeCancellation",
    "CommutativeInverseCancellation",
    "Optimize1qGatesSimpleCommutation",
    "Optimize1qGates",
    "RemoveDiagonalGatesBeforeMeasure",
    "RemoveResetInZeroState",
]

DEFAULT_PARAMS = {
    "CommutativeCancellation": {"basis_gates": IBM_BASIS},
    "CommutativeInverseCancellation": {},
    "Optimize1qGatesSimpleCommutation": {"basis": IBM_BASIS},
    "Optimize1qGates": {"basis": ["u", "cx"]},
    "RemoveDiagonalGatesBeforeMeasure": {},
    "RemoveResetInZeroState": {},
    "InverseCancellation": {},
    "Optimize1qGatesDecomposition": {"basis": IBM_BASIS},
    "RemoveIdentityEquivalent": {},
    "TwoQubitBlockResynthesis": {"basis_gates": IBM_BASIS},
}


def store(tmp_path, body, name="demo"):
    db = QCHDatabase(tmp_path / "passes.db")
    path = tmp_path / f"{name}.qasm"
    path.write_text(HEADER + body, encoding="utf-8")
    db.add_circuit(name, path)
    return db


def run_single(db, pass_name, params=None, name="demo"):
    spec = {"name": pass_name, "params": params or {}}
    result = opt.run_pass_sequence(db, name, [spec])
    assert result.status == "SUCCEEDED"
    steps = opt.list_optimization_steps(db, result.run_id)
    return (
        steps,
        opt.load_step_circuit(db, result.run_id, 0),
        opt.load_step_circuit(db, result.run_id, 1),
    )


def same_unitary(before, after):
    return Operator(after).equiv(Operator(before))


def outcome_distribution(circuit):
    """Exact distribution of final computational-basis measurements."""
    mapping = {}
    body = QuantumCircuit(*circuit.qregs)
    for instruction in circuit.data:
        name = instruction.operation.name
        if name == "measure":
            qubit = circuit.find_bit(instruction.qubits[0]).index
            clbit = circuit.find_bit(instruction.clbits[0]).index
            mapping[clbit] = qubit
        elif name != "barrier":
            body.append(
                instruction.operation,
                [body.qubits[circuit.find_bit(q).index]
                 for q in instruction.qubits],
            )
    probabilities = DensityMatrix(body).probabilities_dict(
        qargs=[mapping[clbit] for clbit in sorted(mapping)]
    )
    return {k: round(v, 10) for k, v in probabilities.items() if v > 1e-10}


# Registry --------------------------------------------------------------------


def test_registry_contains_all_passes():
    assert set(opt.PASS_REGISTRY) == set(DEFAULT_PARAMS)


def test_pass_equivalence_labels():
    assert opt.pass_equivalence("RemoveDiagonalGatesBeforeMeasure") == (
        opt.EQUIVALENCE_MEASUREMENT
    )
    for name in set(DEFAULT_PARAMS) - {"RemoveDiagonalGatesBeforeMeasure"}:
        assert opt.pass_equivalence(name) == opt.EQUIVALENCE_EXACT
    with pytest.raises(opt.PassConfigError):
        opt.pass_equivalence("TemplateOptimization")


def test_new_passes_declare_no_analysis_dependencies():
    for name in NEW_PASSES:
        prepared = opt.prepare_pass_sequence(
            [{"name": name, "params": DEFAULT_PARAMS[name]}]
        )
        instance = prepared[0].build()
        assert list(instance.requires) == []


# Effects of each pass ---------------------------------------------------------


def test_commutative_cancellation_cancels_through_commuting_gates(tmp_path):
    db = store(tmp_path, (
        "qreg q[2];\n"
        "cx q[0],q[1];\nrz(0.3) q[0];\ncx q[0],q[1];\n"
        "rz(0.4) q[0];\nz q[0];\n"
    ))
    steps, before, after = run_single(
        db, "CommutativeCancellation", {"basis_gates": IBM_BASIS}
    )
    assert before.count_ops()["cx"] == 2
    assert "cx" not in after.count_ops()
    assert dict(after.count_ops()) == {"rz": 1}
    assert steps[1]["changed"] == 1
    assert steps[1]["two_qubit_gate_count"] == 0
    assert same_unitary(before, after)
    db.close()


def test_commutative_cancellation_basis_selects_rotation_gate(tmp_path):
    db = store(tmp_path, "qreg q[1];\nt q[0];\ns q[0];\nz q[0];\n")
    _, _, with_rz = run_single(
        db, "CommutativeCancellation", {"basis_gates": ["rz"]}
    )
    _, _, with_p = run_single(
        db, "CommutativeCancellation", {"basis_gates": ["p"]}
    )
    assert dict(with_rz.count_ops()) == {"rz": 1}
    assert dict(with_p.count_ops()) == {"p": 1}
    assert same_unitary(with_rz, with_p)
    db.close()


def test_commutative_inverse_cancellation(tmp_path):
    db = store(tmp_path, (
        "qreg q[2];\n"
        "x q[1];\ncx q[0],q[1];\nx q[1];\n"
        "h q[0];\ncz q[0],q[1];\nh q[0];\n"
    ))
    steps, before, after = run_single(db, "CommutativeInverseCancellation")
    assert before.count_ops()["x"] == 2
    assert "x" not in after.count_ops()
    # H does not commute with CZ, so the H pair is kept.
    assert after.count_ops()["h"] == 2
    assert steps[1]["gate_count"] == steps[0]["gate_count"] - 2
    assert same_unitary(before, after)
    db.close()


def test_commutative_inverse_cancellation_matrix_based(tmp_path):
    db = store(tmp_path, "qreg q[1];\nrz(0.3) q[0];\nrz(-0.3) q[0];\n")
    _, before, after = run_single(
        db, "CommutativeInverseCancellation",
        {"matrix_based": True, "max_qubits": 2},
    )
    assert after.size() == 0
    assert same_unitary(before, after)
    db.close()


def test_optimize_1q_simple_commutation_merges_across_cx(tmp_path):
    db = store(tmp_path, (
        "qreg q[2];\n"
        "rz(0.2) q[0];\ncx q[0],q[1];\nrz(0.5) q[0];\nsx q[0];\n"
    ))
    steps, before, after = run_single(
        db, "Optimize1qGatesSimpleCommutation",
        {"basis": IBM_BASIS, "run_to_completion": True},
    )
    assert before.count_ops()["rz"] == 2
    assert after.count_ops()["rz"] == 1
    assert steps[1]["single_qubit_gate_count"] == 2
    assert steps[1]["pass_params"] == {
        "basis": IBM_BASIS, "run_to_completion": True,
    }
    assert same_unitary(before, after)
    db.close()


def test_optimize_1q_gates_merges_u_family_only(tmp_path):
    db = store(tmp_path, (
        "qreg q[1];\n"
        "u3(0.1,0.2,0.3) q[0];\nu2(0.4,0.5) q[0];\nu1(0.6) q[0];\n"
        "h q[0];\n"
    ))
    steps, before, after = run_single(
        db, "Optimize1qGates", {"basis": ["u", "cx"]}
    )
    assert before.size() == 4
    # The u-family run becomes one u gate; h is not a u-family gate.
    assert dict(after.count_ops()) == {"u": 1, "h": 1}
    assert steps[1]["changed"] == 1
    assert same_unitary(before, after)
    db.close()


def test_optimize_1q_gates_leaves_ibm_native_gates_unchanged(tmp_path):
    db = store(tmp_path, (
        "qreg q[1];\nrz(0.1) q[0];\nsx q[0];\nrz(0.2) q[0];\nrz(0.3) q[0];\n"
    ))
    steps, _, _ = run_single(db, "Optimize1qGates", {"basis": ["u3", "cx"]})
    assert steps[1]["changed"] == 0
    assert steps[1]["snapshot_id"] == steps[0]["snapshot_id"]
    db.close()


def test_remove_diagonal_gates_before_measure(tmp_path):
    db = store(tmp_path, (
        "qreg q[2];\ncreg c[2];\n"
        "h q[0];\ncx q[0],q[1];\nrz(0.7) q[0];\nz q[1];\n"
        "measure q[0] -> c[0];\nmeasure q[1] -> c[1];\n"
    ))
    steps, before, after = run_single(db, "RemoveDiagonalGatesBeforeMeasure")
    assert "rz" not in after.count_ops()
    assert "z" not in after.count_ops()
    assert steps[1]["measure_count"] == 2
    assert steps[1]["gate_count"] == steps[0]["gate_count"] - 2
    # Only the measurement outcomes are preserved, not the unitary.
    assert outcome_distribution(after) == outcome_distribution(before)
    db.close()


def test_remove_reset_in_zero_state(tmp_path):
    db = store(tmp_path, (
        "qreg q[2];\ncreg c[1];\n"
        "reset q[0];\nreset q[1];\nh q[0];\nreset q[0];\n"
        "measure q[0] -> c[0];\n"
    ))
    steps, before, after = run_single(db, "RemoveResetInZeroState")
    assert steps[0]["reset_count"] == 3
    # Only the two leading resets are removed; the later one is kept.
    assert steps[1]["reset_count"] == 1
    assert after.count_ops()["reset"] == 1
    assert outcome_distribution(after) == outcome_distribution(before)
    db.close()


# Parameter validation ---------------------------------------------------------


@pytest.mark.parametrize("spec", [
    {"name": "CommutativeCancellation"},
    {"name": "CommutativeCancellation", "params": {"basis_gates": []}},
    {"name": "CommutativeCancellation", "params": {"basis_gates": ["bogus"]}},
    {"name": "CommutativeCancellation",
     "params": {"basis_gates": ["rz", "rz"]}},
    {"name": "CommutativeInverseCancellation",
     "params": {"matrix_based": "yes"}},
    {"name": "CommutativeInverseCancellation", "params": {"max_qubits": 0}},
    {"name": "CommutativeInverseCancellation", "params": {"max_qubits": 99}},
    {"name": "CommutativeInverseCancellation", "params": {"max_qubits": 2.0}},
    {"name": "Optimize1qGatesSimpleCommutation"},
    {"name": "Optimize1qGatesSimpleCommutation",
     "params": {"basis": ["cx"]}},
    {"name": "Optimize1qGatesSimpleCommutation",
     "params": {"basis": IBM_BASIS, "run_to_completion": 1}},
    {"name": "Optimize1qGates"},
    {"name": "Optimize1qGates", "params": {"basis": ["rz", "sx", "x"]}},
    {"name": "RemoveDiagonalGatesBeforeMeasure", "params": {"x": 1}},
    {"name": "RemoveResetInZeroState", "params": {"seed": 1}},
])
def test_invalid_parameters_are_rejected_before_any_write(tmp_path, spec):
    db = store(tmp_path, "qreg q[1];\nh q[0];\n")
    with pytest.raises(opt.PassConfigError):
        opt.run_pass_sequence(db, "demo", [spec])
    assert opt.get_optimization_schema_state(db) == "absent"
    db.close()


def test_defaults_are_recorded_explicitly():
    prepared = opt.prepare_pass_sequence([
        {"name": "CommutativeInverseCancellation"},
        {"name": "Optimize1qGatesSimpleCommutation",
         "params": {"basis": IBM_BASIS}},
        {"name": "RemoveResetInZeroState"},
    ])
    assert prepared[0].params == {"matrix_based": False, "max_qubits": 4}
    assert prepared[1].params == {
        "basis": IBM_BASIS, "run_to_completion": False,
    }
    assert prepared[2].params == {}


# All registered passes together ------------------------------------------------------

MIXED_QASM = (
    "qreg q[3];\ncreg c[3];\n"
    "reset q[0];\n"
    "h q[0];\nh q[0];\nx q[1];\ncx q[0],q[1];\nx q[1];\n"
    "rz(0.2) q[0];\ncx q[0],q[2];\nrz(0.5) q[0];\nsx q[0];\n"
    "u3(0.1,0.2,0.3) q[2];\nu1(0.4) q[2];\n"
    "rz(0.0) q[1];\ncz q[1],q[2];\nt q[2];\n"
    "measure q[0] -> c[0];\nmeasure q[1] -> c[1];\nmeasure q[2] -> c[2];\n"
)


@pytest.mark.parametrize("pass_name", sorted(DEFAULT_PARAMS))
def test_each_pass_matches_direct_pass_manager(tmp_path, pass_name):
    db = store(tmp_path, MIXED_QASM)
    params = DEFAULT_PARAMS[pass_name]
    steps, before, after = run_single(db, pass_name, params)

    instance = opt.prepare_pass_sequence(
        [{"name": pass_name, "params": params}]
    )[0].build()
    direct = PassManager([instance]).run(
        QuantumCircuit.from_qasm_str(db.get_circuit("demo"))
    )
    assert opt.representation_fingerprint(after) == (
        opt.representation_fingerprint(direct)
    )
    assert outcome_distribution(after) == outcome_distribution(before)
    assert steps[1]["status"] == "SUCCEEDED"
    db.close()


def test_long_sequence_with_repeats(tmp_path):
    db = store(tmp_path, MIXED_QASM)
    sequence = [
        {"name": "RemoveResetInZeroState"},
        {"name": "CommutativeInverseCancellation"},
        {"name": "Optimize1qGates", "params": {"basis": ["u", "cx"]}},
        {"name": "Optimize1qGatesSimpleCommutation",
         "params": {"basis": IBM_BASIS + ["cz"], "run_to_completion": True}},
        {"name": "CommutativeCancellation",
         "params": {"basis_gates": IBM_BASIS + ["cz"]}},
        {"name": "RemoveIdentityEquivalent"},
        {"name": "CommutativeInverseCancellation"},
        {"name": "RemoveDiagonalGatesBeforeMeasure"},
    ]
    result = opt.run_pass_sequence(db, "demo", sequence)
    assert result.status == "SUCCEEDED"
    steps = opt.list_optimization_steps(db, result.run_id)
    assert [s["step_index"] for s in steps] == list(range(9))
    assert all(s["status"] == "SUCCEEDED" for s in steps)
    assert steps[-1]["gate_count"] < steps[0]["gate_count"]

    before = opt.load_step_circuit(db, result.run_id, 0)
    after = opt.load_step_circuit(db, result.run_id, 8)
    assert outcome_distribution(after) == outcome_distribution(before)
    db.close()
