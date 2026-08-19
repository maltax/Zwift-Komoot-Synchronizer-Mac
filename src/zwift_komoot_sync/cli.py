from __future__ import annotations

from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.panel import Panel
from rich.progress import BarColumn, Progress, SpinnerColumn, TaskProgressColumn, TextColumn
from rich.table import Table

from .config import Settings
from .db import SyncDatabase
from .sync import build_status, prepare_activities
from .util import format_distance_km, format_duration

app = typer.Typer(
    add_completion=False,
    no_args_is_help=True,
    help="Sync local Zwift rides to Komoot (macOS).",
)
console = Console()


def _load_settings(*, require_komoot: bool = True) -> Settings:
    try:
        return Settings.load(require_komoot=require_komoot)
    except ValueError as exc:
        console.print(f"[red]Bad config:[/red] {exc}")
        raise typer.Exit(code=1) from exc


@app.command("list")
def list_activities(
    all_items: bool = typer.Option(False, "--all", help="Include rides already synced"),
) -> None:
    """List detected FIT rides and their sync status."""
    settings = _load_settings(require_komoot=False)
    db = SyncDatabase(settings.db_path)
    prepared = prepare_activities(settings, db)
    db.close()

    table = Table(title="Zwift rides", show_lines=False)
    table.add_column("FIT file")
    table.add_column("Komoot title")
    table.add_column("Distance", justify="right")
    table.add_column("Duration", justify="right")
    table.add_column("Photos", justify="right")
    table.add_column("Status")
    table.add_column("Zwift ID")

    shown = 0
    for item in prepared:
        if item.already_synced and not all_items:
            continue
        status = "[green]synced[/green]" if item.already_synced else "[yellow]pending[/yellow]"
        table.add_row(
            item.fit.filename,
            item.title,
            format_distance_km(item.fit.distance_m),
            format_duration(item.fit.duration_s),
            str(len(item.photos)),
            status,
            item.zwift_activity_id or "—",
        )
        shown += 1

    if shown == 0:
        console.print("[dim]Nothing to show.[/dim]")
    else:
        console.print(table)
        console.print(f"[dim]{shown} shown / {len(prepared)} total[/dim]")


@app.command("status")
def status_cmd() -> None:
    """Show a short sync summary."""
    settings = _load_settings(require_komoot=False)
    info = build_status(settings)
    table = Table(title="Sync status", show_header=False)
    table.add_column("Key")
    table.add_column("Value")
    table.add_row("FITs found", str(info["activities_found"]))
    table.add_row("Already synced", str(info["already_synced"]))
    table.add_row("Pending", str(info["pending"]))
    table.add_row("Pending photos", str(info["photos_pending"]))
    table.add_row("Activities dir", info["activities_dir"])
    table.add_row("Photos dir", info["photos_dir"])
    table.add_row("Logs dir", info["logs_dir"])
    table.add_row("DB stats", str(info["db_stats"]))
    console.print(table)


@app.command("sync")
def sync_cmd(
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Simulate without uploading to Komoot"
    ),
    force: bool = typer.Option(
        False, "--force", help="Also re-sync rides already marked as synced"
    ),
    limit: Optional[int] = typer.Option(
        None, "--limit", min=1, help="Max number of rides to process"
    ),
    open_photos: bool = typer.Option(
        False, "--open", help="Open staged photo folders in Finder after sync (macOS)"
    ),
) -> None:
    """Upload new Zwift rides to Komoot."""
    settings = _load_settings(require_komoot=not dry_run)

    if not settings.activities_dir.exists():
        console.print(f"[red]Activities folder not found:[/red] {settings.activities_dir}")
        raise typer.Exit(code=1)

    mode = "DRY-RUN" if dry_run else "SYNC"
    console.print(
        Panel.fit(
            f"[bold]{mode}[/bold] · privacy=[cyan]{settings.privacy}[/cyan]\n"
            f"Activities: {settings.activities_dir}\n"
            f"Photos: {settings.photos_dir}",
            title="Zwift → Komoot",
        )
    )

    db = SyncDatabase(settings.db_path)
    prepared = prepare_activities(settings, db)
    db.close()
    pending = [p for p in prepared if force or not p.already_synced]
    if limit is not None:
        pending = pending[:limit]

    if not pending:
        console.print("[green]Nothing to sync.[/green] You're up to date.")
        return

    preview = Table(title=f"{len(pending)} ride(s) to process")
    preview.add_column("#", justify="right")
    preview.add_column("FIT")
    preview.add_column("Title")
    preview.add_column("Photos", justify="right")
    for idx, item in enumerate(pending, start=1):
        preview.add_row(str(idx), item.fit.filename, item.title, str(len(item.photos)))
    console.print(preview)

    from .komoot_client import KomootClient, KomootError
    from .sync import SyncItemResult, _sync_one

    results: list[SyncItemResult] = []
    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TaskProgressColumn(),
        console=console,
    ) as progress:
        task = progress.add_task("Syncing…", total=len(pending))
        if dry_run:
            for item in pending:
                progress.update(task, description=f"Dry-run {item.fit.filename}")
                results.append(
                    SyncItemResult(
                        filename=item.fit.filename,
                        title=item.title,
                        status="dry-run",
                        photos_matched=len(item.photos),
                        message="Dry-run — nothing uploaded",
                    )
                )
                progress.advance(task)
        else:
            db = SyncDatabase(settings.db_path)
            try:
                client = KomootClient(
                    settings.komoot_email,
                    settings.komoot_password,
                    privacy=settings.privacy,
                )
                client.login()
            except KomootError as exc:
                console.print(f"[red]Komoot:[/red] {exc}")
                db.close()
                raise typer.Exit(code=1) from exc

            try:
                for item in pending:
                    progress.update(task, description=f"Upload {item.fit.filename}")
                    results.append(_sync_one(client, db, settings, item))
                    progress.advance(task)
            finally:
                client.close()
                db.close()

    result_table = Table(title="Results")
    result_table.add_column("FIT")
    result_table.add_column("Status")
    result_table.add_column("Komoot tour")
    result_table.add_column("Photos staged")
    result_table.add_column("Detail")
    for item in results:
        color = {
            "synced": "green",
            "dry-run": "cyan",
            "failed": "red",
            "skipped": "yellow",
            "staged": "cyan",
        }.get(item.status, "white")
        result_table.add_row(
            item.filename,
            f"[{color}]{item.status}[/{color}]",
            str(item.tour_id or "—"),
            f"{item.photos_staged}/{item.photos_matched}",
            item.message,
        )
    console.print(result_table)

    for item in results:
        if item.photos_dir and item.tour_url:
            console.print(
                Panel.fit(
                    f"[bold]{item.title}[/bold]\n"
                    f"Tour: {item.tour_url}\n"
                    f"Add these photos manually: {item.photos_dir}",
                    title="Photos → Komoot (manual)",
                    border_style="yellow",
                )
            )

    console.print(
        Panel.fit(
            f"[green]OK[/green] {sum(1 for r in results if r.status in {'synced', 'dry-run'})} · "
            f"[red]Failed[/red] {sum(1 for r in results if r.status == 'failed')} · "
            f"Photos staged {sum(r.photos_staged for r in results)}/"
            f"{sum(r.photos_matched for r in results)}",
            title="Summary",
        )
    )

    if any(r.status == "failed" for r in results):
        raise typer.Exit(code=2)

    if open_photos and not dry_run:
        import subprocess

        from .sync import list_pending_photo_folders

        to_open = list_pending_photo_folders(settings)
        if not to_open:
            console.print("[dim]No pending photo folders to open.[/dim]")
        else:
            for item in to_open:
                console.print(f"[cyan]Opening[/cyan] {item.photos_dir}")
                subprocess.run(["open", item.photos_dir], check=False)


@app.command("stage-photos")
def stage_photos_cmd(
    open_folder: bool = typer.Option(
        False, "--open", help="Open pending photo folders in Finder (macOS)"
    ),
    force: bool = typer.Option(
        False,
        "--force",
        help="Rebuild staging folders even for rides already marked done",
    ),
) -> None:
    """Create missing staging folders, or open existing pending ones."""
    import subprocess

    settings = _load_settings(require_komoot=False)
    from .sync import list_pending_photo_folders, stage_photos_for_synced

    if open_folder:
        pending = list_pending_photo_folders(settings)
        if not pending:
            stage_photos_for_synced(settings, force=force)
            pending = list_pending_photo_folders(settings)
        if not pending:
            console.print("[dim]No pending photo folders to open.[/dim]")
            return
        for item in pending:
            console.print(f"[cyan]Opening[/cyan] {item.photos_dir}  →  {item.tour_url}")
            subprocess.run(["open", item.photos_dir], check=False)
        return

    created = stage_photos_for_synced(settings, force=force)
    if not created:
        console.print("[dim]No missing staging folders to create.[/dim]")
        return

    table = Table(title="Staged photos")
    table.add_column("FIT")
    table.add_column("Tour")
    table.add_column("Photos")
    table.add_column("Folder")
    for item in created:
        table.add_row(
            item.filename,
            str(item.tour_id or "—"),
            str(item.photos_staged),
            item.photos_dir or "—",
        )
        if item.tour_url:
            console.print(f"  → {item.tour_url}")
    console.print(table)


@app.command("clean-photos")
def clean_photos_cmd(
    tour_id: Optional[int] = typer.Option(
        None, "--tour-id", help="Only delete the folder for this tour id"
    ),
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Show what would be deleted"
    ),
) -> None:
    """Delete staged photo folders after you’ve uploaded them on Komoot."""
    import shutil

    settings = _load_settings(require_komoot=False)
    root = settings.photos_staging_dir

    if not root.exists():
        console.print(f"[dim]Nothing to clean — {root} does not exist.[/dim]")
        return

    if tour_id is not None:
        targets = [p for p in [root / str(tour_id)] if p.exists()]
        if not targets:
            console.print(f"[yellow]Folder not found:[/yellow] {root / str(tour_id)}")
            raise typer.Exit(code=1)
    else:
        targets = sorted(p for p in root.iterdir() if p.is_dir())

    if not targets:
        console.print(f"[dim]No photo folders to clean in {root}[/dim]")
        return

    table = Table(title="Clean pending_photos")
    table.add_column("Folder")
    table.add_column("Files", justify="right")
    for path in targets:
        file_count = sum(1 for f in path.rglob("*") if f.is_file())
        table.add_row(str(path), str(file_count))
    console.print(table)

    if dry_run:
        console.print("[cyan]Dry-run — nothing deleted.[/cyan]")
        return

    cleaned_ids: list[int] = []
    for path in targets:
        try:
            cleaned_ids.append(int(path.name))
        except ValueError:
            pass
        shutil.rmtree(path)
        console.print(f"[green]Deleted[/green] {path}")

    db = SyncDatabase(settings.db_path)
    marked = db.mark_photos_done(cleaned_ids)
    db.close()

    try:
        if root.exists() and not any(root.iterdir()):
            root.rmdir()
    except OSError:
        pass

    console.print(
        f"[green]Done.[/green] Removed {len(targets)} folder(s)"
        + (f", marked {marked} ride(s) as photos done." if marked else ".")
    )


@app.command("doctor")
def doctor_cmd() -> None:
    """Check local paths and .env without calling Komoot."""
    from dotenv import load_dotenv
    import os

    load_dotenv()
    home = Path.home()
    checks = {
        "Activities": Path(
            os.getenv("ZWIFT_ACTIVITIES_DIR", home / "Documents/Zwift/Activities")
        ).expanduser(),
        "Logs": Path(os.getenv("ZWIFT_LOGS_DIR", home / "Documents/Zwift/Logs")).expanduser(),
        "Photos": Path(os.getenv("ZWIFT_PHOTOS_DIR", home / "Pictures/Zwift")).expanduser(),
        ".env": Path.cwd() / ".env",
    }
    table = Table(title="Doctor")
    table.add_column("Resource")
    table.add_column("Path")
    table.add_column("OK")
    for name, path in checks.items():
        exists = path.exists()
        table.add_row(name, str(path), "[green]yes[/green]" if exists else "[red]no[/red]")
    console.print(table)

    email = os.getenv("KOMOOT_EMAIL")
    password = os.getenv("KOMOOT_PASSWORD")
    if email and password:
        console.print("[green]Komoot credentials found in .env[/green]")
    else:
        console.print("[yellow]Missing KOMOOT_EMAIL / KOMOOT_PASSWORD in .env[/yellow]")


if __name__ == "__main__":
    app()
