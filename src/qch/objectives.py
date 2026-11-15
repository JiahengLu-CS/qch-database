"""Objective-aware view of compared pass sequences (stage 2E).

Given the results of several pass sequences on ONE input circuit, this
module answers, without any combined score:

* which sequences minimize each objective on its own (ties kept), and
* which sequences are on the Pareto front: a sequence is removed only if
  another sequence is no worse in every objective and strictly better in
  at least one.

Rules for what is compared:

* Only results of the same input are compared. Mixing inputs raises
  :class:`ObjectiveInputError`.
* A result is a candidate only if its run succeeded and all objective
  values are present. Other results are listed in ``excluded`` with a
  reason.
* Verification is optional. ``True`` keeps the result as ``verified``,
  ``False`` excludes it, and a missing record keeps it marked
  ``unverified``. Without any verification records every candidate is
  ``unverified``.
* Candidates with identical objective values keep their own labels and
  run IDs. Equal metrics do not mean equal circuits; restore a circuit
  through its run ID.

Smaller is better for every objective. Nothing is written to a database.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field

#: Objectives of :class:`qch.batch.BatchResult` (QCH stored metrics).
QCH_OBJECTIVES = ("gate_count", "two_qubit_gate_count", "depth")

VERIFIED = "verified"
UNVERIFIED = "unverified"


class ObjectiveInputError(ValueError):
    """The records cannot be compared (mixed inputs, bad objectives)."""


@dataclass(frozen=True)
class Candidate:
    label: str
    run_id: int | None
    metrics: dict
    verification: str

    def values(self, objectives):
        return tuple(self.metrics[name] for name in objectives)


@dataclass
class ObjectiveView:
    objectives: tuple
    input_id: str | None
    candidates: list
    excluded: list
    best: dict
    pareto_front: list
    dominated_by: dict
    verification_available: bool
    notes: list = field(default_factory=list)

    def best_labels(self, objective):
        return [c.label for c in self.best[objective]]

    def common_optimum(self):
        """Candidates that are best in every objective (may be empty)."""
        sets = [set(self.best_labels(name)) for name in self.objectives]
        common = set.intersection(*sets) if sets else set()
        return [c for c in self.candidates if c.label in common]

    def has_conflict(self):
        """True if no candidate is best in all objectives at once."""
        return bool(self.candidates) and not self.common_optimum()

    def conflicts(self):
        """Pairs of objectives whose sets of best candidates are disjoint."""
        pairs = []
        for i, first in enumerate(self.objectives):
            for second in self.objectives[i + 1:]:
                if not (set(self.best_labels(first))
                        & set(self.best_labels(second))):
                    pairs.append((first, second))
        return pairs

    def identical_metric_groups(self):
        """Groups (size > 1) of candidates with the same objective values."""
        groups = {}
        for candidate in self.candidates:
            groups.setdefault(candidate.values(self.objectives), []).append(
                candidate
            )
        return [group for group in groups.values() if len(group) > 1]

    def as_dict(self):
        def cand(c):
            return {"label": c.label, "run_id": c.run_id,
                    "metrics": dict(c.metrics),
                    "verification": c.verification}
        return {
            "objectives": list(self.objectives),
            "input_id": self.input_id,
            "verification_available": self.verification_available,
            "candidates": [cand(c) for c in self.candidates],
            "excluded": list(self.excluded),
            "best": {k: [cand(c) for c in v] for k, v in self.best.items()},
            "pareto_front": [cand(c) for c in self.pareto_front],
            "dominated_by": {k: list(v) for k, v in self.dominated_by.items()},
            "common_optimum": [c.label for c in self.common_optimum()],
            "conflicts": [list(pair) for pair in self.conflicts()],
        }

    def format(self, short_names=None):
        names = dict(short_names or {})
        head = [names.get(name, name) for name in self.objectives]
        lines = [
            "| label | run | " + " | ".join(head)
            + " | verification | best in | Pareto |",
            "|---|---|" + "---|" * len(head) + "---|---|---|",
        ]
        front = {c.label for c in self.pareto_front}
        for c in self.candidates:
            best_in = [names.get(n, n) for n in self.objectives
                       if c.label in self.best_labels(n)]
            pareto = ("yes" if c.label in front else
                      "no (dominated by "
                      + ", ".join(self.dominated_by[c.label]) + ")")
            lines.append(
                f"| {c.label} | {c.run_id} | "
                + " | ".join(str(c.metrics[n]) for n in self.objectives)
                + f" | {c.verification} | {', '.join(best_in) or '-'} | "
                f"{pareto} |"
            )
        lines.append("")
        for name in self.objectives:
            members = self.best[name]
            value = members[0].metrics[name] if members else None
            lines.append(
                f"Best {names.get(name, name)} ({value}): "
                + (", ".join(f"{c.label} (run {c.run_id})" for c in members)
                   or "none")
            )
        lines.append("Pareto front: " + (", ".join(
            f"{c.label} (run {c.run_id})" for c in self.pareto_front
        ) or "none"))
        common = self.common_optimum()
        lines.append("Common optimum: " + (", ".join(
            c.label for c in common) or "none"))
        for first, second in self.conflicts():
            lines.append(
                f"Conflict: no candidate is best for both "
                f"{names.get(first, first)} and {names.get(second, second)}."
            )
        for group in self.identical_metric_groups():
            lines.append(
                "Same metrics, separate runs: " + ", ".join(
                    f"{c.label} (run {c.run_id})" for c in group)
            )
        if not self.verification_available:
            lines.append("No verification records: all candidates are "
                         "unverified.")
        for item in self.excluded:
            lines.append(f"Excluded: {item['label']} (run {item['run_id']}): "
                         f"{item['reason']}")
        lines.extend(self.notes)
        return "\n".join(lines) + "\n"


def dominates(first, second, objectives):
    """True if ``first`` is no worse in all objectives and better in one."""
    a = first.values(objectives)
    b = second.values(objectives)
    return all(x <= y for x, y in zip(a, b)) and any(
        x < y for x, y in zip(a, b)
    )


def build_view(records, objectives=QCH_OBJECTIVES, verification=None):
    """Build an :class:`ObjectiveView` from plain records.

    Each record is a mapping with ``label``, ``run_id``, ``status``,
    ``metrics`` (mapping or ``None``) and ``input_id``. ``verification`` is
    ``None`` (no records) or a mapping ``label -> True | False``; labels not
    in the mapping are ``unverified``.
    """
    objectives = tuple(objectives)
    if not objectives or len(set(objectives)) != len(objectives):
        raise ObjectiveInputError("objectives must be distinct and non-empty")
    records = list(records)
    labels = [r["label"] for r in records]
    if len(set(labels)) != len(labels):
        raise ObjectiveInputError("labels must be unique")
    inputs = {r.get("input_id") for r in records}
    if len(inputs) > 1:
        raise ObjectiveInputError(
            f"records come from different inputs: {sorted(map(str, inputs))}"
        )
    if verification is not None and not isinstance(verification, Mapping):
        raise ObjectiveInputError("verification must be a mapping or None")
    unknown = set(verification or {}) - set(labels)
    if unknown:
        raise ObjectiveInputError(
            f"verification for unknown labels: {sorted(unknown)}"
        )

    candidates, excluded = [], []
    for record in records:
        reason = None
        metrics = record.get("metrics")
        if record.get("status") != "SUCCEEDED":
            reason = f"run status {record.get('status')}"
        elif metrics is None or any(
            metrics.get(name) is None for name in objectives
        ):
            reason = "incomplete metrics"
        elif verification is not None and verification.get(
            record["label"]
        ) is False:
            reason = "verification failed"
        if reason:
            excluded.append({"label": record["label"],
                             "run_id": record.get("run_id"),
                             "reason": reason})
            continue
        state = (VERIFIED if verification is not None
                 and verification.get(record["label"]) is True
                 else UNVERIFIED)
        candidates.append(Candidate(
            label=record["label"], run_id=record.get("run_id"),
            metrics={name: metrics[name] for name in objectives},
            verification=state,
        ))

    best = {}
    for name in objectives:
        if candidates:
            value = min(c.metrics[name] for c in candidates)
            best[name] = [c for c in candidates if c.metrics[name] == value]
        else:
            best[name] = []
    dominated_by = {
        c.label: [o.label for o in candidates if dominates(o, c, objectives)]
        for c in candidates
    }
    front = [c for c in candidates if not dominated_by[c.label]]
    return ObjectiveView(
        objectives=objectives,
        input_id=next(iter(inputs)) if inputs else None,
        candidates=candidates,
        excluded=excluded,
        best=best,
        pareto_front=front,
        dominated_by=dominated_by,
        verification_available=verification is not None,
    )


def view_batch_result(batch_result, verification=None,
                      objectives=QCH_OBJECTIVES):
    """Objective view of a :class:`qch.batch.BatchResult`.

    Uses the QCH stored metrics (``gate_count`` includes measurements).
    The input is identified by ``input_representation_sha256``.
    """
    records = [
        {
            "label": r.label,
            "run_id": r.run_id,
            "status": r.status,
            "metrics": r.final_metrics,
            "input_id": r.input_representation_sha256,
        }
        for r in batch_result.results
    ]
    return build_view(records, objectives, verification)

