from __future__ import annotations

import re
from datetime import datetime


_ZWIFT_PREFIX = re.compile(r"^\s*zwift\s*[-–—:]\s*", re.IGNORECASE)


def normalize_title(raw_name: str | None, *, prefix: str = "ZWIFT - ") -> str:
    """Apply the ZWIFT - prefix without doubling an existing Zwift prefix."""
    name = (raw_name or "").strip() or "Ride"
    name = _ZWIFT_PREFIX.sub("", name).strip() or "Ride"
    if name.upper().startswith(prefix.strip().upper()):
        return name
    return f"{prefix}{name}"


def format_duration(seconds: float | int | None) -> str:
    if seconds is None:
        return "?"
    total = int(round(float(seconds)))
    hours, rem = divmod(total, 3600)
    minutes, secs = divmod(rem, 60)
    if hours:
        return f"{hours}h{minutes:02d}m"
    return f"{minutes}m{secs:02d}s"


def format_distance_km(meters: float | int | None) -> str:
    if meters is None:
        return "?"
    return f"{float(meters) / 1000:.1f} km"


def parse_photo_timestamp(stem: str) -> datetime | None:
    """Parse 2026-08-11_17-14-16_0(_clean) → naive local datetime."""
    match = re.match(
        r"^(?P<date>\d{4}-\d{2}-\d{2})_(?P<h>\d{2})-(?P<m>\d{2})-(?P<s>\d{2})",
        stem,
    )
    if not match:
        return None
    try:
        return datetime.strptime(
            f"{match.group('date')} {match.group('h')}:{match.group('m')}:{match.group('s')}",
            "%Y-%m-%d %H:%M:%S",
        )
    except ValueError:
        return None


def local_window(
    start_utc: datetime,
    end_utc: datetime,
) -> tuple[datetime, datetime]:
    """
    Convert a FIT UTC window to naive local time so it matches Zwift
    screenshot filenames (local timestamps).

    Strict [start, end] — no padding, so adjacent rides don't leak photos.
    """
    start_local = start_utc.astimezone().replace(tzinfo=None)
    end_local = end_utc.astimezone().replace(tzinfo=None)
    return start_local, end_local
