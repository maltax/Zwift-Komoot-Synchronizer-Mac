from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from .config import Settings
from .db import PHOTOS_DONE, PHOTOS_NONE, PHOTOS_PENDING, SyncDatabase
from .fit_scanner import FitActivity, scan_activities
from .komoot_client import KomootClient, KomootError, sport_from_fit, tour_url
from .photos import MatchedPhoto, assign_photos_exclusively, stage_photos
from .util import format_distance_km, format_duration, normalize_title
from .zwift_log import ZwiftMeta, parse_zwift_logs


@dataclass
class PreparedActivity:
    fit: FitActivity
    title: str
    zwift_activity_id: str | None
    photos: list[MatchedPhoto]
    already_synced: bool
    previous_tour_id: int | None = None


@dataclass
class SyncItemResult:
    filename: str
    title: str
    status: str
    tour_id: int | None = None
    photos_matched: int = 0
    photos_staged: int = 0
    photos_dir: str | None = None
    tour_url: str | None = None
    message: str = ""

    @property
    def photos_uploaded(self) -> int:
        # Compat with older CLI column naming
        return self.photos_staged


@dataclass
class SyncReport:
    prepared: list[PreparedActivity] = field(default_factory=list)
    results: list[SyncItemResult] = field(default_factory=list)

    @property
    def synced(self) -> int:
        return sum(1 for r in self.results if r.status == "synced")

    @property
    def skipped(self) -> int:
        return sum(1 for r in self.results if r.status == "skipped")

    @property
    def failed(self) -> int:
        return sum(1 for r in self.results if r.status == "failed")

    @property
    def dry_run(self) -> int:
        return sum(1 for r in self.results if r.status == "dry-run")


def fallback_title(fit: FitActivity) -> str:
    sport = (fit.sport or "Ride").capitalize()
    bits = [
        sport,
        format_distance_km(fit.distance_m),
        format_duration(fit.duration_s),
    ]
    if fit.start_time:
        bits.append(fit.start_time.astimezone().strftime("%Y-%m-%d %H:%M"))
    return " · ".join(bits)


def prepare_activities(settings: Settings, db: SyncDatabase) -> list[PreparedActivity]:
    metas = parse_zwift_logs(settings.logs_dir)
    fits = scan_activities(settings.activities_dir, min_bytes=settings.min_fit_bytes)
    photos_by_fit = assign_photos_exclusively(fits, settings.photos_dir)
    prepared: list[PreparedActivity] = []

    for fit in fits:
        meta: ZwiftMeta | None = metas.get(fit.filename)
        raw_name = meta.name if meta and meta.name else fallback_title(fit)
        title = normalize_title(raw_name, prefix=settings.title_prefix)
        record = db.get(fit.filename)
        prepared.append(
            PreparedActivity(
                fit=fit,
                title=title,
                zwift_activity_id=meta.activity_id if meta else None,
                photos=photos_by_fit.get(fit.filename, []),
                already_synced=record is not None and record.status == "synced",
                previous_tour_id=record.komoot_tour_id if record else None,
            )
        )
    return prepared


def sync_activities(
    settings: Settings,
    *,
    dry_run: bool = False,
    force: bool = False,
    limit: int | None = None,
) -> SyncReport:
    db = SyncDatabase(settings.db_path)
    report = SyncReport()
    prepared = prepare_activities(settings, db)
    report.prepared = prepared

    pending = [item for item in prepared if force or not item.already_synced]
    if limit is not None:
        pending = pending[:limit]

    if dry_run:
        for item in pending:
            report.results.append(
                SyncItemResult(
                    filename=item.fit.filename,
                    title=item.title,
                    status="dry-run",
                    photos_matched=len(item.photos),
                    message="Dry-run — nothing uploaded",
                )
            )
        db.close()
        return report

    if not pending:
        db.close()
        return report

    try:
        client = KomootClient(
            settings.komoot_email,
            settings.komoot_password,
            privacy=settings.privacy,
        )
        client.login()
    except KomootError as exc:
        for item in pending:
            report.results.append(
                SyncItemResult(
                    filename=item.fit.filename,
                    title=item.title,
                    status="failed",
                    photos_matched=len(item.photos),
                    message=str(exc),
                )
            )
        db.close()
        return report

    try:
        for item in pending:
            result = _sync_one(client, db, settings, item)
            report.results.append(result)
    finally:
        client.close()
        db.close()

    return report


def _sync_one(
    client: KomootClient,
    db: SyncDatabase,
    settings: Settings,
    item: PreparedActivity,
) -> SyncItemResult:
    try:
        upload = client.upload_fit(
            item.fit.path,
            name=item.title,
            sport=sport_from_fit(item.fit.sport),
        )
        try:
            client.rename_tour(upload.tour_id, item.title)
        except KomootError:
            pass

        url = tour_url(upload.tour_id)
        staged_count = 0
        staged_dir: Path | None = None
        previous = db.get(item.fit.filename)
        already_done = previous is not None and previous.photos_status == PHOTOS_DONE
        photos_status = PHOTOS_NONE

        # Stage photos only for this ride; never rebuild tours already cleaned.
        if item.photos and not already_done:
            staged_dir = stage_photos(
                item.photos,
                tour_id=upload.tour_id,
                staging_root=settings.photos_staging_dir,
                tour_url=url,
                title=item.title,
                recreate=True,
            )
            staged_count = len(item.photos)
            photos_status = PHOTOS_PENDING
        elif already_done:
            photos_status = PHOTOS_DONE

        msg_parts = []
        if upload.created:
            msg_parts.append("tour created")
        else:
            msg_parts.append("tour already on Komoot (202)")
        if staged_count:
            msg_parts.append(
                f"{staged_count} photo(s) staged — add them manually on Komoot"
            )
        elif item.photos and already_done:
            msg_parts.append("photos already cleaned — skipped staging")
        if item.zwift_activity_id:
            msg_parts.append(f"zwift:{item.zwift_activity_id}")
        msg_parts.append(url)

        db.upsert(
            fit_filename=item.fit.filename,
            fit_sha256=item.fit.sha256,
            title=item.title,
            komoot_tour_id=upload.tour_id,
            photos_uploaded=(
                staged_count
                if staged_count
                else (previous.photos_uploaded if previous else 0)
            ),
            status="synced",
            photos_status=photos_status,
        )
        return SyncItemResult(
            filename=item.fit.filename,
            title=item.title,
            status="synced",
            tour_id=upload.tour_id,
            photos_matched=len(item.photos),
            photos_staged=staged_count,
            photos_dir=str(staged_dir) if staged_dir else None,
            tour_url=url,
            message=" · ".join(msg_parts),
        )
    except Exception as exc:
        db.upsert(
            fit_filename=item.fit.filename,
            fit_sha256=item.fit.sha256,
            title=item.title,
            komoot_tour_id=None,
            photos_uploaded=0,
            status="failed",
            error=str(exc),
        )
        return SyncItemResult(
            filename=item.fit.filename,
            title=item.title,
            status="failed",
            photos_matched=len(item.photos),
            message=str(exc),
        )


def _repair_photos_status_from_folders(db: SyncDatabase, settings: Settings) -> int:
    """If a staging folder still exists, the ride cannot be done (clean removes it)."""
    repaired = 0
    for record in db.list_all():
        if not record.komoot_tour_id or record.photos_uploaded <= 0:
            continue
        if record.photos_status != PHOTOS_DONE:
            continue
        folder = settings.photos_staging_dir / str(record.komoot_tour_id)
        if not folder.is_dir():
            continue
        db.upsert(
            fit_filename=record.fit_filename,
            fit_sha256=record.fit_sha256,
            title=record.title,
            komoot_tour_id=record.komoot_tour_id,
            photos_uploaded=record.photos_uploaded,
            status=record.status,
            photos_status=PHOTOS_PENDING,
        )
        repaired += 1
    return repaired


def list_pending_photo_folders(settings: Settings) -> list[SyncItemResult]:
    """Return synced rides with photos_status=pending and an existing staging folder."""
    db = SyncDatabase(settings.db_path)
    _repair_photos_status_from_folders(db, settings)
    results: list[SyncItemResult] = []
    for record in db.list_all():
        if record.photos_status != PHOTOS_PENDING or not record.komoot_tour_id:
            continue
        dest = settings.photos_staging_dir / str(record.komoot_tour_id)
        if not dest.is_dir():
            continue
        results.append(
            SyncItemResult(
                filename=record.fit_filename,
                title=record.title,
                status="pending",
                tour_id=record.komoot_tour_id,
                photos_matched=record.photos_uploaded,
                photos_staged=record.photos_uploaded,
                photos_dir=str(dest),
                tour_url=tour_url(record.komoot_tour_id),
            )
        )
    db.close()
    return results


def stage_photos_for_synced(
    settings: Settings,
    *,
    force: bool = False,
) -> list[SyncItemResult]:
    """
    Create missing staging folders for rides marked photos_status=pending.

    Skips rides already marked done (clean-photos). Use force=True to restage
    done or historical rides.
    """
    db = SyncDatabase(settings.db_path)
    prepared = prepare_activities(settings, db)
    results: list[SyncItemResult] = []
    for item in prepared:
        if not item.already_synced or not item.previous_tour_id:
            continue
        record = db.get(item.fit.filename)
        photos_status = record.photos_status if record else PHOTOS_NONE

        if not item.photos:
            continue

        if photos_status == PHOTOS_DONE and not force:
            continue

        dest_path = settings.photos_staging_dir / str(item.previous_tour_id)
        if dest_path.exists() and not force:
            continue

        if photos_status == PHOTOS_NONE and not force:
            continue

        url = tour_url(item.previous_tour_id)
        dest = stage_photos(
            item.photos,
            tour_id=item.previous_tour_id,
            staging_root=settings.photos_staging_dir,
            tour_url=url,
            title=item.title,
            recreate=True,
        )
        db.upsert(
            fit_filename=item.fit.filename,
            fit_sha256=item.fit.sha256,
            title=item.title,
            komoot_tour_id=item.previous_tour_id,
            photos_uploaded=len(item.photos),
            status="synced",
            photos_status=PHOTOS_PENDING,
        )
        results.append(
            SyncItemResult(
                filename=item.fit.filename,
                title=item.title,
                status="staged",
                tour_id=item.previous_tour_id,
                photos_matched=len(item.photos),
                photos_staged=len(item.photos),
                photos_dir=str(dest) if dest else None,
                tour_url=url,
                message=f"{len(item.photos)} photo(s) → {dest}",
            )
        )
    db.close()
    return results


def build_status(settings: Settings) -> dict:
    db = SyncDatabase(settings.db_path)
    prepared = prepare_activities(settings, db)
    pending = [p for p in prepared if not p.already_synced]
    synced_stats = db.stats()
    db.close()
    return {
        "activities_found": len(prepared),
        "pending": len(pending),
        "already_synced": len(prepared) - len(pending),
        "db_stats": synced_stats,
        "photos_pending": sum(len(p.photos) for p in pending),
        "activities_dir": str(settings.activities_dir),
        "photos_dir": str(settings.photos_dir),
        "logs_dir": str(settings.logs_dir),
    }
