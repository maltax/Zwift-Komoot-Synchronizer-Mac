from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


@dataclass
class SyncRecord:
    fit_filename: str
    fit_sha256: str
    title: str
    komoot_tour_id: int | None
    photos_uploaded: int
    status: str
    synced_at: str
    error: str | None = None


class SyncDatabase:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.path)
        self._conn.row_factory = sqlite3.Row
        self._migrate()

    def _migrate(self) -> None:
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS synced_activities (
                fit_filename TEXT PRIMARY KEY,
                fit_sha256 TEXT NOT NULL,
                title TEXT NOT NULL,
                komoot_tour_id INTEGER,
                photos_uploaded INTEGER NOT NULL DEFAULT 0,
                status TEXT NOT NULL,
                synced_at TEXT NOT NULL,
                error TEXT
            )
            """
        )
        self._conn.commit()

    def get(self, fit_filename: str) -> SyncRecord | None:
        row = self._conn.execute(
            "SELECT * FROM synced_activities WHERE fit_filename = ?",
            (fit_filename,),
        ).fetchone()
        if not row:
            return None
        return SyncRecord(**dict(row))

    def is_synced(self, fit_filename: str) -> bool:
        record = self.get(fit_filename)
        return record is not None and record.status == "synced"

    def upsert(
        self,
        *,
        fit_filename: str,
        fit_sha256: str,
        title: str,
        komoot_tour_id: int | None,
        photos_uploaded: int,
        status: str,
        error: str | None = None,
    ) -> None:
        now = datetime.now(timezone.utc).isoformat()
        self._conn.execute(
            """
            INSERT INTO synced_activities (
                fit_filename, fit_sha256, title, komoot_tour_id,
                photos_uploaded, status, synced_at, error
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(fit_filename) DO UPDATE SET
                fit_sha256 = excluded.fit_sha256,
                title = excluded.title,
                komoot_tour_id = excluded.komoot_tour_id,
                photos_uploaded = excluded.photos_uploaded,
                status = excluded.status,
                synced_at = excluded.synced_at,
                error = excluded.error
            """,
            (
                fit_filename,
                fit_sha256,
                title,
                komoot_tour_id,
                photos_uploaded,
                status,
                now,
                error,
            ),
        )
        self._conn.commit()

    def list_all(self) -> list[SyncRecord]:
        rows = self._conn.execute(
            "SELECT * FROM synced_activities ORDER BY synced_at DESC"
        ).fetchall()
        return [SyncRecord(**dict(row)) for row in rows]

    def stats(self) -> dict[str, int]:
        rows = self._conn.execute(
            "SELECT status, COUNT(*) AS n FROM synced_activities GROUP BY status"
        ).fetchall()
        return {row["status"]: row["n"] for row in rows}

    def close(self) -> None:
        self._conn.close()
