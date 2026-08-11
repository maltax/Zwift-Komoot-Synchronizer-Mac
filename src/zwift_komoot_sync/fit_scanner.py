from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from fitparse import FitFile


@dataclass
class FitActivity:
    path: Path
    filename: str
    sha256: str
    size_bytes: int
    start_time: datetime | None
    end_time: datetime | None
    duration_s: float | None
    distance_m: float | None
    ascent_m: float | None
    sport: str | None
    avg_power: float | None

    @property
    def is_complete(self) -> bool:
        return bool(self.start_time and self.duration_s and self.duration_s >= 60)


def _as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def parse_fit(path: Path) -> FitActivity:
    fit = FitFile(str(path))
    session: dict = {}
    for message in fit.get_messages("session"):
        session = {field.name: field.value for field in message if field.value is not None}
        break

    start = _as_utc(session.get("start_time"))
    duration = session.get("total_timer_time") or session.get("total_elapsed_time")
    end = None
    if start is not None and duration is not None:
        end = datetime.fromtimestamp(start.timestamp() + float(duration), tz=timezone.utc)
    timestamp = _as_utc(session.get("timestamp"))
    if end is None and timestamp is not None:
        end = timestamp

    return FitActivity(
        path=path,
        filename=path.name,
        sha256=file_sha256(path),
        size_bytes=path.stat().st_size,
        start_time=start,
        end_time=end,
        duration_s=float(duration) if duration is not None else None,
        distance_m=float(session["total_distance"])
        if session.get("total_distance") is not None
        else None,
        ascent_m=float(session["total_ascent"])
        if session.get("total_ascent") is not None
        else None,
        sport=session.get("sport"),
        avg_power=float(session["avg_power"]) if session.get("avg_power") is not None else None,
    )


_IGNORED_FIT_NAMES = {
    "inprogressactivity.fit",
}


def scan_activities(directory: Path, *, min_bytes: int) -> list[FitActivity]:
    if not directory.exists():
        return []

    activities: list[FitActivity] = []
    for path in sorted(directory.glob("*.fit")):
        # Never sync the in-progress FIT Zwift is still writing
        if path.name.lower() in _IGNORED_FIT_NAMES:
            continue
        if path.stat().st_size < min_bytes:
            continue
        try:
            activity = parse_fit(path)
        except Exception:
            continue
        if not activity.is_complete:
            continue
        activities.append(activity)
    return activities
