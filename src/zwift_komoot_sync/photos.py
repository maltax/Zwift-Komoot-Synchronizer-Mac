from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
import shutil

from .fit_scanner import FitActivity
from .util import local_window, parse_photo_timestamp


@dataclass
class MatchedPhoto:
    path: Path
    taken_at: datetime


def _iter_non_clean_photos(photos_dir: Path) -> list[MatchedPhoto]:
    photos: list[MatchedPhoto] = []
    if not photos_dir.exists():
        return photos
    for path in photos_dir.glob("*.jpg"):
        if path.stem.endswith("_clean"):
            continue
        taken_at = parse_photo_timestamp(path.stem)
        if taken_at is None:
            continue
        photos.append(MatchedPhoto(path=path, taken_at=taken_at))
    return photos


def match_photos(activity: FitActivity, photos_dir: Path) -> list[MatchedPhoto]:
    """Match non-_clean screenshots whose timestamp falls inside the ride window."""
    if not activity.start_time or not activity.end_time:
        return []
    start_local, end_local = local_window(activity.start_time, activity.end_time)
    matched = [
        photo
        for photo in _iter_non_clean_photos(photos_dir)
        if start_local <= photo.taken_at <= end_local
    ]
    return sorted(matched, key=lambda p: p.taken_at)


def assign_photos_exclusively(
    activities: list[FitActivity],
    photos_dir: Path,
) -> dict[str, list[MatchedPhoto]]:
    """
    Assign each photo to at most one ride so neighbouring activities
    don't share the same screenshot.
    """
    windows: list[tuple[FitActivity, datetime, datetime]] = []
    for activity in activities:
        if not activity.start_time or not activity.end_time:
            continue
        start_local, end_local = local_window(activity.start_time, activity.end_time)
        windows.append((activity, start_local, end_local))

    assigned: dict[str, list[MatchedPhoto]] = {a.filename: [] for a in activities}
    for photo in _iter_non_clean_photos(photos_dir):
        candidates: list[tuple[FitActivity, float]] = []
        for activity, start_local, end_local in windows:
            if start_local <= photo.taken_at <= end_local:
                mid = start_local + (end_local - start_local) / 2
                distance = abs((photo.taken_at - mid).total_seconds())
                candidates.append((activity, distance))
        if not candidates:
            continue
        best = min(candidates, key=lambda item: item[1])[0]
        assigned[best.filename].append(photo)

    for filename in assigned:
        assigned[filename] = sorted(assigned[filename], key=lambda p: p.taken_at)
    return assigned


def stage_photos(
    photos: list[MatchedPhoto],
    *,
    tour_id: int,
    staging_root: Path,
    tour_url: str,
    title: str,
) -> Path:
    """Copy matched photos into data/pending_photos/<tour_id>/ for manual Komoot upload."""
    dest = staging_root / str(tour_id)
    if dest.exists():
        shutil.rmtree(dest)
    dest.mkdir(parents=True, exist_ok=True)
    for photo in photos:
        shutil.copy2(photo.path, dest / photo.path.name)
    readme = dest / "README.txt"
    readme.write_text(
        "\n".join(
            [
                f"Title: {title}",
                f"Komoot tour: {tour_url}",
                "",
                "Komoot's API does not allow automated photo uploads.",
                "Add these images manually:",
                "1. Open the tour link above",
                "2. Edit tour → Add photos",
                "3. Select the files in this folder",
                "",
                "Files:",
                *[f"- {photo.path.name}" for photo in photos],
                "",
            ]
        ),
        encoding="utf-8",
    )
    return dest
