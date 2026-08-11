from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


def _expand(path: str | None, default: Path) -> Path:
    if not path:
        return default
    return Path(path).expanduser().resolve()


@dataclass(frozen=True)
class Settings:
    komoot_email: str
    komoot_password: str
    activities_dir: Path
    logs_dir: Path
    photos_dir: Path
    db_path: Path
    photos_staging_dir: Path
    privacy: str
    min_fit_kb: int
    title_prefix: str = "ZWIFT - "

    @property
    def min_fit_bytes(self) -> int:
        return self.min_fit_kb * 1024

    @classmethod
    def load(cls, env_file: Path | None = None, *, require_komoot: bool = True) -> Settings:
        if env_file:
            load_dotenv(env_file)
        else:
            load_dotenv()

        home = Path.home()
        project_root = Path(__file__).resolve().parents[2]
        email = os.getenv("KOMOOT_EMAIL", "").strip()
        password = os.getenv("KOMOOT_PASSWORD", "").strip()
        if require_komoot and (not email or not password):
            raise ValueError(
                "KOMOOT_EMAIL and KOMOOT_PASSWORD are required. "
                "Copy .env.example to .env and fill them in."
            )

        return cls(
            komoot_email=email,
            komoot_password=password,
            activities_dir=_expand(
                os.getenv("ZWIFT_ACTIVITIES_DIR"),
                home / "Documents" / "Zwift" / "Activities",
            ),
            logs_dir=_expand(
                os.getenv("ZWIFT_LOGS_DIR"),
                home / "Documents" / "Zwift" / "Logs",
            ),
            photos_dir=_expand(
                os.getenv("ZWIFT_PHOTOS_DIR"),
                home / "Pictures" / "Zwift",
            ),
            db_path=_expand(
                os.getenv("SYNC_DB_PATH"),
                project_root / "data" / "sync.db",
            ),
            photos_staging_dir=_expand(
                os.getenv("PHOTOS_STAGING_DIR"),
                project_root / "data" / "pending_photos",
            ),
            privacy=os.getenv("KOMOOT_PRIVACY", "private").strip().lower(),
            min_fit_kb=int(os.getenv("MIN_FIT_KB", "5")),
        )
