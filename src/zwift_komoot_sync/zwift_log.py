from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path


@dataclass
class ZwiftMeta:
    fit_filename: str
    activity_id: str | None
    name: str | None


_SAVE_RE = re.compile(
    r"save_activity with \{name: (?P<name>.+?), uploadTo3P: (?P<upload>\w+), "
    r".*?fitFileNameShort: (?P<fit>[^,}]+)"
)
_ID_RE = re.compile(r"OnNewActivityId with \{activityId: (?P<id>\d+)")
_END_RE = re.compile(
    r"EndCurrentActivity with \{activityName: (?P<name>.+?), activityDescription:"
)


def parse_zwift_logs(logs_dir: Path) -> dict[str, ZwiftMeta]:
    """
    Pull final title and activityId per FIT file from Zwift logs.

    Notes:
    - OnNewActivityId usually follows the first save of the in-progress FIT
    - The last save_activity (especially uploadTo3P=True) holds the final title
    """
    if not logs_dir.exists():
        return {}

    by_fit: dict[str, ZwiftMeta] = {}
    pending_fit: str | None = None
    pending_name: str | None = None

    log_files = sorted(logs_dir.glob("Log*.txt"), key=lambda p: p.stat().st_mtime)
    for log_path in log_files:
        try:
            text = log_path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue

        for line in text.splitlines():
            save = _SAVE_RE.search(line)
            if save:
                fit = save.group("fit").strip()
                name = save.group("name").strip()
                pending_fit = fit
                pending_name = name
                meta = by_fit.get(fit) or ZwiftMeta(fit_filename=fit, activity_id=None, name=None)
                # Prefer the final name (uploadTo3P) or a longer/more descriptive one
                if save.group("upload") == "True" or not meta.name or len(name) >= len(meta.name or ""):
                    meta.name = name
                by_fit[fit] = meta
                continue

            end = _END_RE.search(line)
            if end and pending_fit:
                meta = by_fit.get(pending_fit) or ZwiftMeta(
                    fit_filename=pending_fit, activity_id=None, name=None
                )
                meta.name = end.group("name").strip()
                by_fit[pending_fit] = meta
                continue

            id_match = _ID_RE.search(line)
            if id_match and pending_fit:
                meta = by_fit.get(pending_fit) or ZwiftMeta(
                    fit_filename=pending_fit,
                    activity_id=None,
                    name=pending_name,
                )
                meta.activity_id = id_match.group("id")
                if pending_name and not meta.name:
                    meta.name = pending_name
                by_fit[pending_fit] = meta

    return by_fit
