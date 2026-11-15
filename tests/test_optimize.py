"""Tests for qch.optimize (stage 4A)."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from qiskit import QuantumCircuit
from qiskit.quantum_info import Operator
from qiskit.transpiler.basepasses import TransformationPass

import qch
from qch import QCHDatabase
from qch import optimization as opt
from qch import optimize as qo

HEADER = 'OPENQASM 2.0;\ninclude "qelib1.inc";\n'

# The 3-qubit acceptance circuit, with final measurements on two qubits.
BODY = (
    "qreg q[3];\ncreg c[2];\n"
    "h q[0];\ncx q[1],q[0];\nh q[0];\nh q[1];\ncx q[1],q[0];\nsdg q[1];\n"
    "t q[2];\ncx q[2],q[0];\nz q[2];\ncx q[2],q[0];\ncx q[0],q[1];\n"
    "cx q[0],q[1];\nrz(0.3) q[1];\ncx q[1],q[2];\nbarrier q[0],q[1],q[2];\n"
    "measure q[0] -> c[0];\nmeasure q[2] -> c[1];\n"
)
QASM = HEADER + BODY


def key(candidate, objective=qo.DEFAULT_OBJECTIVE):
    return tuple(candidate.metrics[m] for m in objective)


@pytest.fixture(scope="module")
def default_result():
    return qo.optimize_circuit(QASM)


# Inputs ---------------------------------------------------------------------


def test_accepts_circuit_text_and_path(tmp_path):
    path = tmp_path / "in.qasm"
    path.write_text(QASM, encoding="utf-8")
    results = [qo.optimize_circuit(source, pass_set="light")
               for source in (QuantumCircuit.from_qasm_str(QASM), QASM, path,
                              str(path))]
    assert len({r.best.qasm for r in results}) == 1


@pytest.mark.parametrize("source, message", [
    ("missing_file.qasm", "not found"),
    (HEADER + "qreg q[1];\nfoo q[0];\n", "cannot parse"),
    (HEADER + "qreg q[1];\ncreg c[1];\nmeasure q[0] -> c[0];\nx q[0];\n",
     "after the measurement"),
    (HEADER + "qreg q[1];\nreset q[0];\n", "reset"),
])
def test_rejects_unsupported_inputs(source, message):
    with pytest.raises(qo.OptimizeError, match=message):
        qo.optimize_circuit(source)


@pytest.mark.parametrize("kwargs, message", [
    ({"objective": "fidelity"}, "objective"),
    ({"objective": ("depth", "depth")}, "objective"),
    ({"pass_set": "unknown"}, "unknown pass set"),
    ({"pass_set": "AZ"}, "unknown pass letter"),
    ({"search": "random"}, "search"),
    ({"verify": "maybe"}, "verify"),
    ({"search": "fixed"}, "needs an order"),
    ({"search": "fixed", "order": "AX"}, "outside the pass set"),
    ({"order": "AB"}, "only used with"),
    ({"max_runs": 100}, "max_runs"),
    ({"max_runs": 0}, "max_runs"),
    ({"basis_gates": ("rz", "sx", "x")}, "two-qubit gate"),
])
def test_rejects_invalid_options(kwargs, message):
    with pytest.raises(opt.OptimizationError, match=message):
        qo.optimize_circuit(QASM, **kwargs)


# Default search -----------------------------------------------------------------


def test_default_search_covers_all_orders_with_a_prefix_cache(default_result):
    r = default_result
    assert r.search == "permutations" and r.complete
    assert list(r.passes) == ["A", "B", "C", "F", "G"]
    assert len(r.candidates) + len(r.excluded) == 121     # 120 orders + baseline
    assert r.pass_applications == 5 + 20 + 60 + 120 + 120
    assert r.verify == "exact"
    assert r.excluded == []
    assert {c.verification for c in r.candidates} == {"exact"}


def test_best_follows_the_lexicographic_objective(default_result):
    r = default_result
    keys = [key(c) for c in r.candidates]
    assert key(r.best) == min(keys)
    assert keys == sorted(keys)
    assert all(key(t) == key(r.best) for t in r.ties)
    assert key(r.best) <= key(r.baseline)
    assert r.best.metrics["two_qubit_gates"] < r.baseline.metrics["two_qubit_gates"]
    labels = {c.label for c in r.pareto_front}
    assert r.best.label in labels
    for c in r.pareto_front:
        assert not any(all(o.metrics[m] <= c.metrics[m] for m in qo.METRICS)
                       and any(o.metrics[m] < c.metrics[m] for m in qo.METRICS)
                       for o in r.candidates)


def test_best_circuit_is_equivalent_and_keeps_measurements(default_result):
    best = QuantumCircuit.from_qasm_str(default_result.best.qasm)
    source = QuantumCircuit.from_qasm_str(QASM)
    assert set(best.count_ops()) <= {"rz", "sx", "x", "cx", "measure", "barrier"}
    assert qo.final_measurement_map(best) == {0: 0, 2: 1}
    assert Operator(qo.without_final_operations(best)).equiv(
        Operator(qo.without_final_operations(source)))


def test_other_objectives_change_the_ranking():
    for objective in ("gates", "depth", ("gates", "depth")):
        r = qo.optimize_circuit(QASM, objective=objective)
        objective = (objective,) if isinstance(objective, str) else objective
        assert key(r.best, objective) == min(key(c, objective) for c in r.candidates)


def test_best_steps_follow_the_best_order(default_result):
    r = default_result
    steps = r.best_steps
    assert [s["step"] for s in steps] == list(range(len(r.best.order) + 1))
    assert [s["pass"] for s in steps] == [None, *r.best.order]
    assert {m: steps[0][m] for m in qo.METRICS} == r.baseline.metrics
    assert {m: steps[-1][m] for m in qo.METRICS} == r.best.metrics


def test_to_dict_is_json_and_contains_the_best_qasm(default_result):
    data = json.loads(json.dumps(default_result.to_dict()))
    assert data["best"]["qasm"].startswith("OPENQASM 2.0;")
    assert data["objective"] == list(qo.DEFAULT_OBJECTIVE)
    assert data["improvement"]["two_qubit_gates"] < 0
    assert data["environment"]["qiskit"]
    assert "Best:" in default_result.summary()


# Other searches -------------------------------------------------------------------


def test_light_set_and_greedy_and_fixed():
    light = qo.optimize_circuit(QASM, pass_set="light")
    assert len(light.candidates) + len(light.excluded) == 7
    assert light.pass_applications == 3 + 6 + 6

    greedy = qo.optimize_circuit(QASM, search="greedy")
    assert key(greedy.best) <= key(greedy.baseline)
    assert len(greedy.candidates) + len(greedy.excluded) == 2   # baseline + path
    assert greedy.pass_applications <= 5 * qo.GREEDY_MAX_STEPS

    fixed = qo.optimize_circuit(QASM, search="fixed", order="BAG")
    assert [c.order for c in fixed.candidates + fixed.excluded if c.order] == [
        ("B", "A", "G")]
    repeated = qo.optimize_circuit(QASM, search="fixed", order="BAG",
                                   repeat_until_stable=True)
    final = next(c for c in repeated.candidates + repeated.excluded if c.order)
    assert len(final.order) % 3 == 0 and 1 <= len(final.order) // 3 <= qo.FIXED_MAX_ROUNDS
    # One more round would not change the result.
    again = qo.optimize_circuit(final.qasm, search="fixed", order="BAG",
                                basis_gates=qo.DEFAULT_BASIS)
    assert key(next(c for c in again.candidates if c.order)) == key(final)


def test_custom_pass_mapping():
    passes = {"X": {"name": "CommutativeCancellation",
                    "params": {"basis_gates": ["rz", "sx", "x", "cx"]}},
              "Y": {"name": "RemoveIdentityEquivalent", "params": {}}}
    r = qo.optimize_circuit(QASM, pass_set=passes)
    assert {c.label for c in r.candidates} >= {"X->Y", "Y->X"}


# Verification ------------------------------------------------------------------


class DropFirstGate(TransformationPass):
    """A broken pass: removes the first gate, which changes the unitary."""

    def run(self, dag):
        for node in dag.topological_op_nodes():
            if node.op.name not in ("measure", "barrier"):
                dag.remove_op_node(node)
                break
        return dag


@pytest.fixture
def broken_registry(monkeypatch):
    registry = dict(opt.PASS_REGISTRY)
    registry["DropFirstGate"] = opt._RegistryEntry(
        normalize=lambda params: dict(params), build=lambda params: DropFirstGate())
    monkeypatch.setattr(opt, "PASS_REGISTRY", registry)


@pytest.mark.parametrize("verify", ["exact", "sampled"])
def test_wrong_results_are_excluded(broken_registry, verify):
    passes = {"B": {"name": "Optimize1qGatesDecomposition",
                    "params": {"basis": ["rz", "sx", "x", "cx"]}},
              "D": {"name": "DropFirstGate", "params": {}}}
    r = qo.optimize_circuit(QASM, pass_set=passes, verify=verify)
    assert {c.label for c in r.excluded} == {"B->D", "D->B"}
    assert all(c.verification == "failed" and c.reason for c in r.excluded)
    assert r.best.label in ("B", "(no pass)", "B->D") and r.best.label != "B->D"
    assert r.verify == verify


def test_sampled_and_exact_agree_and_none_marks_unverified():
    exact = qo.optimize_circuit(QASM, pass_set="light", verify="exact")
    sampled = qo.optimize_circuit(QASM, pass_set="light", verify="sampled")
    assert [c.label for c in exact.candidates] == [c.label for c in sampled.candidates]
    assert {c.verification for c in sampled.candidates} == {"sampled"}
    none = qo.optimize_circuit(QASM, pass_set="light", verify="none")
    assert {c.verification for c in none.candidates} == {"unverified"}
    assert any("not verified" in note for note in none.notes)


def test_time_limit_stops_early():
    r = qo.optimize_circuit(QASM, time_limit_s=1e-9)
    assert not r.complete
    assert r.best.order == ()       # only the baseline was finished
    assert any("time limit" in note for note in r.notes)


# Storage --------------------------------------------------------------------------


def test_db_records_every_candidate(tmp_path):
    db = QCHDatabase(tmp_path / "opt.db")
    r = qo.optimize_circuit(QASM, pass_set="light", db=db, name="demo")
    assert r.circuit_name == "demo"
    ordered = [c for c in r.candidates if c.order]
    assert all(c.run_id is not None for c in ordered)
    for c in ordered:
        restored = opt.load_step_circuit(db, c.run_id, len(c.order))
        assert opt.representation_fingerprint(restored) == (
            opt.representation_fingerprint(c.circuit))
    # The same name with the same circuit can be reused.
    again = qo.optimize_circuit(QASM, pass_set="light", db=db, name="demo")
    assert again.best.run_id != r.best.run_id or again.best.order == ()
    with pytest.raises(qo.OptimizeError, match="exists"):
        qo.optimize_circuit(HEADER + "qreg q[1];\nh q[0];\n", pass_set="light",
                            db=db, name="demo")
    db.close()


def test_without_db_nothing_is_written(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    qo.optimize_circuit(QASM, pass_set="light")
    assert list(tmp_path.iterdir()) == []


# Command line ---------------------------------------------------------------------


def run_cli(*args):
    env = dict(os.environ)
    src = str(Path(qch.__file__).resolve().parents[1])
    env["PYTHONPATH"] = src + os.pathsep + env.get("PYTHONPATH", "")
    return subprocess.run([sys.executable, "-m", "qch.optimize", *args],
                          capture_output=True, text=True, env=env, check=False)


def test_command_line(tmp_path):
    source = tmp_path / "in.qasm"
    source.write_text(QASM, encoding="utf-8")
    out, report = tmp_path / "best.qasm", tmp_path / "report.json"
    done = run_cli(str(source), "--pass-set", "ABF", "--out", str(out),
                   "--report", str(report), "--db", str(tmp_path / "r.db"))
    assert done.returncode == 0, done.stderr
    assert "Best:" in done.stdout
    data = json.loads(report.read_text())
    assert out.read_text() == data["best"]["qasm"]
    assert list(data["passes"]) == ["A", "B", "F"]
    assert data["best"]["run_id"] is not None or data["best"]["order"] == []

    bad = run_cli(str(tmp_path / "missing.qasm"))
    assert bad.returncode == 2 and "not found" in bad.stderr
