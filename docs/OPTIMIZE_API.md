# Circuit optimization API (`qch.optimize`)

One call takes a circuit, runs registered Qiskit passes in many orders,
verifies the results and returns the best circuit.

```python
from qch.optimize import optimize_circuit

result = optimize_circuit("input.qasm")          # or QASM text, or a QuantumCircuit
print(result.summary())
open("best.qasm", "w").write(result.best.qasm)
```

```powershell
python -m qch.optimize input.qasm --out best.qasm --report report.json
```

## Parameters

| Parameter | Default | Meaning |
|---|---|---|
| `circuit` | required | `QuantumCircuit`, OpenQASM 2 text, or a path to a `.qasm` file |
| `objective` | `("two_qubit_gates", "depth", "gates")` | Metric name or priority tuple. Candidates are compared metric by metric in this order (lexicographic); there is no weighted score. |
| `pass_set` | `"default"` | `"default"` (A, B, C, F, G), `"light"` (A, B, C), `"single-qubit"` (B, F), letters such as `"ABF"`, or a mapping `{id: {"name": ..., "params": {...}}}` of registered passes |
| `search` | `"permutations"` | `"permutations"`: every order of the pass set; `"greedy"`: repeatedly apply the pass that gives the best result until none improves it; `"fixed"`: one given order |
| `order` | `None` | Pass ids for `search="fixed"`, for example `"BAG"` |
| `repeat_until_stable` | `False` | With `search="fixed"`: repeat the order (at most 10 rounds) until a round changes nothing |
| `basis_gates` | `("rz", "sx", "x", "cx")` | Target gate set; must contain `cx`, `cz` or `ecr` for G |
| `max_runs` | `120` | Upper limit for the number of orders in `permutations` |
| `time_limit_s` | `60` | Stop starting new pass applications after this time; finished orders are still compared |
| `verify` | `"auto"` | `"exact"` (up to 10 qubits), `"sampled"` (up to 20 qubits), `"none"`, or `"auto"` (exact, then sampled, then none by qubit count) |
| `db` | `None` | A `QCHDatabase`; when given, every candidate order is recorded as an optimization run |
| `name` | `None` | Circuit name in `db` (default `optimize:<hash>`) |

Pass letters: A = CommutativeCancellation, B = Optimize1qGatesDecomposition,
C = CommutativeInverseCancellation, F = Optimize1qGatesSimpleCommutation,
G = TwoQubitBlockResynthesis. Parameters that need the basis are filled in
from `basis_gates`.

## What happens

1. **Input checks**: no parameters, classical variables or control flow;
   measurements only at the end of each qubit; no reset.
2. **Baseline**: `transpile(circuit, basis_gates, optimization_level=0,
   seed_transpiler=20261114)`, read back through OpenQASM 2. The baseline
   is the common input of every order and is itself a candidate (label
   `(no pass)`), so the best result is never worse than the baseline.
3. **Search** with a prefix cache: orders that begin with the same passes
   share these steps. All 120 orders of five passes need 325 pass
   applications instead of 600.
4. **Verification** of every distinct result against the input:
   * gate set within `basis_gates` plus measure and barrier;
   * the same final measurements (qubit to classical bit);
   * `exact`: the unitaries (final measurements and barriers removed on
     copies) are equal up to global phase (`Operator.equiv`);
   * `sampled`: 8 random input states (fixed seed) give the same output
     state up to one common global phase (tolerance 1e-8). This is a
     strong test, not a proof.
   Failed results are moved to `excluded` and never ranked. With
   `verify="none"`, or more than 20 qubits under `auto`, results are
   marked `unverified`.
5. **Ranking** by the objective; ties are listed; the Pareto front over
   gates, two-qubit gates and depth is returned as well.

Metrics: `gates` counts single-, two- and multi-qubit gates (measure, reset
and barrier are not gates); `two_qubit_gates` counts gates on exactly two
qubits; `depth` is measured after removing final measurements and
barriers. These are the metrics of the MQT Bench experiments.

## Result (`OptimizationResult`)

| Field | Content |
|---|---|
| `best` | `Candidate`: `order`, `label`, `circuit`, `qasm`, `metrics`, `verification`, `run_id` |
| `ties` | Other candidates with the same objective values as `best` |
| `pareto_front` | Candidates not dominated in gates, two-qubit gates and depth |
| `candidates` | All compared candidates, sorted by the objective |
| `excluded` | Candidates that failed verification, with the reason |
| `baseline` | The translated input (`order == ()`) |
| `input_metrics` | Metrics of the input in its own gate set (not comparable with the baseline when the gate sets differ) |
| `best_steps` | Metrics after every step of the best order (`step`, `pass`, `gates`, `two_qubit_gates`, `depth`; step 0 is the baseline) |
| `improvement()` | Best minus baseline per metric |
| `complete`, `pass_applications`, `elapsed_s` | Search statistics |
| `environment` | Platform, Python and Qiskit versions |
| `notes` | Time limit, verification and platform remarks |
| `to_dict()`, `summary()` | JSON-ready report and a short text summary |

## Storage

Without `db`, nothing is written. With `db`, the baseline is stored as a
circuit and every candidate order is recorded with
`qch.optimization.run_pass_sequence`; the recorded final circuit must be
identical to the search result, and each candidate gets its `run_id`, so
every intermediate step can be restored with `load_step_circuit`. The same
`name` can be reused only with the same baseline circuit. Recording runs the
passes again for every order, so it is slower than the search itself.

## Errors

`OptimizeError` (a `ValueError` and `OptimizationError`) for unreadable
input, unsupported content, invalid options, too many orders for
`max_runs`, or a qubit count above the verification limit.
`PassConfigError` for invalid pass parameters. The command line prints the
message and exits with status 2.

## Reproducibility

With G (TwoQubitBlockResynthesis), total gates and depth can differ
slightly between platforms; two-qubit counts are reproducible
(`docs/PROGRESS.md`, stages 2G and 2H). The result records the platform.

## Check against the experiments

On the 12 MQT Bench circuits of stage 2D/2G, `optimize_circuit` with the
defaults returns, for every circuit, the same best two-qubit gates, depth
and gates (in this priority) as the recorded stage 2G experiment on the
same platform (Linux), in about 1 s per circuit (13.5 s for all 12)
without recording.
