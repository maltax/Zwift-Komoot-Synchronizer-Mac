from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

# none = no photos / never staged
# pending = staged, waiting for manual Komoot upload + clean-photos
# done = user cleaned the staging folder (don't recreate)
PHOTOS_NONE = "none"
PHOTOS_PENDING = "pending"
PHOTOS_DONE = "done"


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
    photos_status: str = PHOTOS_NONE


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
                error TEXT,
                photos_status TEXT NOT NULL DEFAULT 'none'
            )
            """
        )
        cols = {
            row["name"]
            for row in self._conn.execute("PRAGMA table_info(synced_activities)")
        }
        if "photos_status" not in cols:
            self._conn.execute(
                "ALTER TABLE synced_activities ADD COLUMN photos_status TEXT NOT NULL DEFAULT 'none'"
            )
        self._conn.commit()

    def get(self, fit_filename: str) -> SyncRecord | None:
        row = self._conn.execute(
            "SELECT * FROM synced_activities WHERE fit_filename = ?",
            (fit_filename,),
        ).fetchone()
        if not row:
            return None
        data = dict(row)
        data.setdefault("photos_status", PHOTOS_NONE)
        return SyncRecord(**data)

    def get_by_tour_id(self, tour_id: int) -> SyncRecord | None:
        row = self._conn.execute(
            "SELECT * FROM synced_activities WHERE komoot_tour_id = ?",
            (tour_id,),
        ).fetchone()
        if not row:
            return None
        data = dict(row)
        data.setdefault("photos_status", PHOTOS_NONE)
        return SyncRecord(**data)

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
        photos_status: str | None = None,
    ) -> None:
        now = datetime.now(timezone.utc).isoformat()
        existing = self.get(fit_filename)
        resolved_photos_status = (
            photos_status
            if photos_status is not None
            else (existing.photos_status if existing else PHOTOS_NONE)
        )
        self._conn.execute(
            """
            INSERT INTO synced_activities (
                fit_filename, fit_sha256, title, komoot_tour_id,
                photos_uploaded, status, synced_at, error, photos_status
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(fit_filename) DO UPDATE SET
                fit_sha256 = excluded.fit_sha256,
                title = excluded.title,
                komoot_tour_id = excluded.komoot_tour_id,
                photos_uploaded = excluded.photos_uploaded,
                status = excluded.status,
                synced_at = excluded.synced_at,
                error = excluded.error,
                photos_status = excluded.photos_status
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
                resolved_photos_status,
            ),
        )
        self._conn.commit()

    def mark_photos_done(self, tour_ids: list[int]) -> int:
        if not tour_ids:
            return 0
        placeholders = ",".join("?" * len(tour_ids))
        cur = self._conn.execute(
            f"""
            UPDATE synced_activities
            SET photos_status = ?
            WHERE komoot_tour_id IN ({placeholders})
            """,
            [PHOTOS_DONE, *tour_ids],
        )
        self._conn.commit()
        return cur.rowcount

    def list_all(self) -> list[SyncRecord]:
        rows = self._conn.execute(
            "SELECT * FROM synced_activities ORDER BY synced_at DESC"
        ).fetchall()
        records = []
        for row in rows:
            data = dict(row)
            data.setdefault("photos_status", PHOTOS_NONE)
            records.append(SyncRecord(**data))
        return records

    def stats(self) -> dict[str, int]:
        rows = self._conn.execute(
            "SELECT status, COUNT(*) AS n FROM synced_activities GROUP BY status"
        ).fetchall()
        return {row["status"]: row["n"] for row in rows}

    def close(self) -> None:
        self._conn.close()
