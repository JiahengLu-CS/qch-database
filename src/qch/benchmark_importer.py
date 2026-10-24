"""Import MQT Bench and other OpenQASM benchmark datasets."""

def create_import_table(connection):
    """Create persistent per-file import tracking."""

    with connection:
        connection.execute("""
            CREATE TABLE IF NOT EXISTS benchmark_imports (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                benchmark_id INTEGER NOT NULL,
                relative_path TEXT NOT NULL,
                circuit_id INTEGER,
                sha256 TEXT,
                file_size INTEGER NOT NULL,
                status TEXT NOT NULL
                    CHECK (status IN (
                        'IMPORTED',
                        'SKIPPED_EMPTY',
                        'FAILED'
                    )),
                error_message TEXT,
                imported_at TEXT DEFAULT CURRENT_TIMESTAMP,

                UNIQUE (benchmark_id, relative_path),

                FOREIGN KEY (benchmark_id)
                    REFERENCES benchmarks(id),

                FOREIGN KEY (circuit_id)
                    REFERENCES circuits(id)
            )
        """)

import hashlib


def import_benchmark(db, benchmark_name, dataset_dir, limit=None):
    """Import OpenQASM files with persistent status tracking."""
    from pathlib import Path

    root = Path(dataset_dir).resolve()
    if not root.is_dir():
        raise ValueError(f"Dataset directory not found: {root}")

    create_import_table(db.connection)

    row = db.connection.execute(
        "SELECT id FROM benchmarks WHERE name = ?",
        (benchmark_name,)
    ).fetchone()

    if row is None:
        benchmark_id = db.add_benchmark(benchmark_name)
    else:
        benchmark_id = row[0]

    files = sorted(root.rglob("*.qasm"))
    if limit is not None:
        files = files[:limit]

    counts = {"IMPORTED": 0, "SKIPPED_EMPTY": 0, "FAILED": 0,
              "UNCHANGED": 0}

    for path in files:
        relative_path = path.relative_to(root).as_posix()
        circuit_name = f"{benchmark_name}:{relative_path}"

        try:
            data = path.read_bytes()
            digest = hashlib.sha256(data).hexdigest()

            previous = db.connection.execute(
                """
                SELECT sha256, status
                FROM benchmark_imports
                WHERE benchmark_id = ? AND relative_path = ?
                """,
                (benchmark_id, relative_path)
            ).fetchone()

            if previous and previous[0] == digest and previous[1] in (
                "IMPORTED", "SKIPPED_EMPTY"
            ):
                counts["UNCHANGED"] += 1
                continue

            if not data:
                status = "SKIPPED_EMPTY"
                circuit_id = None
                error = None
            else:
                existing = db.connection.execute(
                    "SELECT id FROM circuits WHERE name = ?",
                    (circuit_name,)
                ).fetchone()

                if existing:
                    circuit_id = existing[0]
                else:
                    circuit_id = db.add_circuit(circuit_name, path)

                db.connection.execute(
                    """
                    INSERT OR IGNORE INTO benchmark_circuits
                        (benchmark_id, circuit_id)
                    VALUES (?, ?)
                    """,
                    (benchmark_id, circuit_id)
                )

                status = "IMPORTED"
                error = None

        except Exception as exc:
            status = "FAILED"
            circuit_id = None
            error = str(exc)[:1000]
            digest = None

        with db.connection:
            db.connection.execute(
                """
                INSERT INTO benchmark_imports
                    (benchmark_id, relative_path, circuit_id,
                     sha256, file_size, status, error_message)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(benchmark_id, relative_path)
                DO UPDATE SET
                    circuit_id = excluded.circuit_id,
                    sha256 = excluded.sha256,
                    file_size = excluded.file_size,
                    status = excluded.status,
                    error_message = excluded.error_message,
                    imported_at = CURRENT_TIMESTAMP
                """,
                (
                    benchmark_id,
                    relative_path,
                    circuit_id,
                    digest,
                    path.stat().st_size,
                    status,
                    error
                )
            )

        counts[status] += 1

    return counts
