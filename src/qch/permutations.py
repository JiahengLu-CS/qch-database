"""Bounded permutation generation for optimization pass sequences (stage 2B).

The input is an ordered collection of pass configurations, each with a
unique identifier. Every generated sequence uses every configuration
exactly once. Two configurations of the same pass with different
parameters are allowed as long as their identifiers differ.

Generation is deterministic: permutations follow
``itertools.permutations`` over the input order, so the same input always
yields the same labeled sequences in the same order. Labels join the
identifiers with ``->``, for example ``A->B->C``.

Size control: the number of permutations (``n!``) is computed from the
number of configurations and checked against ``max_permutations``
(default 24 = 4!) before any configuration is validated further, any
sequence is generated, or anything is executed or written.

Execution reuses :func:`qch.batch.run_sequence_batch`; result tables, run
IDs, failure handling and per-step records are those of stage 2A. No
database schema change.

Repeated steps such as A -> B -> A are not permutations; use the explicit
sequence API in :mod:`qch.batch` for those.
"""

from __future__ import annotations

import itertools
import math
from collections.abc import Mapping
from dataclasses import dataclass

from . import batch
from . import optimization as opt

#: Default upper bound for the number of generated sequences (4!).
DEFAULT_MAX_PERMUTATIONS = 24

#: Separator used to build sequence labels from configuration identifiers.
LABEL_SEPARATOR = "->"


class PermutationLimitError(batch.BatchConfigError):
    """The requested permutations exceed the configured limit.

    Raised before any sequence is generated or written. Attributes:
    ``count`` (expected number of permutations) and ``limit``.
    """

    def __init__(self, count, limit, num_items):
        super().__init__(
            f"{num_items} pass configurations give {count} permutations, "
            f"which exceeds the limit of {limit}. Reduce the number of "
            "passes, raise max_permutations explicitly, or list the orders "
            "you want to compare with qch.batch.run_sequence_batch (stage 2A)."
        )
        self.count = count
        self.limit = limit
        self.num_items = num_items


@dataclass(frozen=True)
class PermutationPreview:
    """Candidate sequences; computed without touching any database."""

    item_ids: tuple
    count: int
    limit: int
    sequences: tuple  # ((label, (pass_config, ...)), ...)

    @property
    def labels(self):
        return [label for label, _ in self.sequences]

    def as_batch_sequences(self):
        """``{label: [pass_config, ...]}`` for :func:`run_sequence_batch`."""
        return {label: list(passes) for label, passes in self.sequences}

    def format(self):
        lines = [
            f"Pass configurations ({len(self.item_ids)}): "
            + ", ".join(self.item_ids),
            f"Permutations: {self.count} (limit {self.limit})",
        ]
        for number, label in enumerate(self.labels, start=1):
            lines.append(f"  {number:>3}. {label}")
        return "\n".join(lines)


def _normalize_items(items):
    """Return ``[(item_id, pass_config)]`` in input order."""
    if isinstance(items, Mapping):
        pairs = list(items.items())
    elif isinstance(items, (str, bytes)):
        raise batch.BatchConfigError(
            "Pass configurations must be a mapping or a list"
        )
    else:
        try:
            raw = list(items)
        except TypeError:
            raise batch.BatchConfigError(
                "Pass configurations must be a mapping or a list"
            ) from None
        pairs = []
        for entry in raw:
            if isinstance(entry, Mapping):
                if set(entry) != {"id", "pass"}:
                    raise batch.BatchConfigError(
                        "Each list entry must have exactly the keys 'id' "
                        "and 'pass'"
                    )
                pairs.append((entry["id"], entry["pass"]))
            elif isinstance(entry, (list, tuple)) and len(entry) == 2:
                pairs.append((entry[0], entry[1]))
            else:
                raise batch.BatchConfigError(
                    "Each list entry must be {'id', 'pass'} or an "
                    "(id, pass) pair"
                )
    if not pairs:
        raise batch.BatchConfigError(
            "At least one pass configuration is required"
        )

    seen = set()
    for item_id, _ in pairs:
        if not isinstance(item_id, str) or not item_id.strip():
            raise batch.BatchConfigError(
                "Pass configuration identifiers must be non-empty strings"
            )
        if item_id != item_id.strip():
            raise batch.BatchConfigError(
                f"Identifier {item_id!r} has leading or trailing spaces"
            )
        if LABEL_SEPARATOR in item_id:
            raise batch.BatchConfigError(
                f"Identifier {item_id!r} must not contain {LABEL_SEPARATOR!r}"
            )
        if item_id in seen:
            raise batch.BatchConfigError(f"Duplicate identifier {item_id!r}")
        seen.add(item_id)
    return pairs


def _check_limit(max_permutations):
    if (
        isinstance(max_permutations, bool)
        or not isinstance(max_permutations, int)
        or max_permutations < 1
    ):
        raise batch.BatchConfigError(
            "max_permutations must be a positive integer"
        )


def count_permutations(items):
    """Number of sequences that :func:`preview_permutations` would create."""
    return math.factorial(len(_normalize_items(items)))


def _generate(pairs):
    for order in itertools.permutations(range(len(pairs))):
        label = LABEL_SEPARATOR.join(pairs[index][0] for index in order)
        yield label, tuple(pairs[index][1] for index in order)


def preview_permutations(items, *, max_permutations=DEFAULT_MAX_PERMUTATIONS):
    """Validate the input and list the candidate sequences.

    Does not open, create or write any database. Order of checks:
    identifiers and structure, permutation count against
    ``max_permutations`` (:class:`PermutationLimitError`), each pass
    configuration (:class:`qch.batch.BatchConfigError` naming the
    identifier), then generation.
    """
    _check_limit(max_permutations)
    pairs = _normalize_items(items)
    count = math.factorial(len(pairs))
    if count > max_permutations:
        raise PermutationLimitError(count, max_permutations, len(pairs))

    for item_id, config in pairs:
        try:
            opt.prepare_pass_sequence([config])
        except opt.PassConfigError as exc:
            raise batch.BatchConfigError(
                f"Pass configuration {item_id!r}: {exc}"
            ) from exc

    sequences = tuple(_generate(pairs))
    if len(sequences) != count:
        raise AssertionError("Generated permutation count mismatch")
    return PermutationPreview(
        item_ids=tuple(item_id for item_id, _ in pairs),
        count=count,
        limit=max_permutations,
        sequences=sequences,
    )


def run_permutation_batch(
    db, circuit_name, items, *, max_permutations=DEFAULT_MAX_PERMUTATIONS,
    verify_snapshots=True,
):
    """Generate all permutations and run them with the stage 2A batch API.

    Returns ``(preview, batch_result)``. All checks of
    :func:`preview_permutations` and of :func:`qch.batch.run_sequence_batch`
    (labels, sequences, input circuit, open transaction) happen before the
    first run is written. Failure semantics are those of
    :func:`qch.batch.run_sequence_batch`.
    """
    preview = preview_permutations(items, max_permutations=max_permutations)
    result = batch.run_sequence_batch(
        db, circuit_name, preview.as_batch_sequences(),
        verify_snapshots=verify_snapshots,
    )
    return preview, result


__all__ = [
    "DEFAULT_MAX_PERMUTATIONS",
    "LABEL_SEPARATOR",
    "PermutationLimitError",
    "PermutationPreview",
    "count_permutations",
    "preview_permutations",
    "run_permutation_batch",
]
