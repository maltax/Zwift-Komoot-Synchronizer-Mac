from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx


class KomootError(RuntimeError):
    pass


@dataclass
class UploadResult:
    tour_id: int
    created: bool
    raw: dict[str, Any]


class KomootClient:
    """Minimal client for Komoot's internal API (v006/v007)."""

    LOGIN_URL = "https://api.komoot.de/v006/account/email/{email}/"
    UPLOAD_URL = "https://api.komoot.de/v007/tours/"
    TOUR_URL = "https://api.komoot.de/v007/tours/{tour_id}/"
    WEB_TOUR_URL = "https://www.komoot.com/tour/{tour_id}"

    def __init__(self, email: str, password: str, *, privacy: str = "private") -> None:
        self.email = email
        self.password = password
        self.privacy = privacy
        self._client = httpx.Client(timeout=60.0, follow_redirects=True)
        self.user_id: str | None = None
        self.username: str | None = None
        self._password_token: str | None = None

    def login(self) -> None:
        response = self._client.get(
            self.LOGIN_URL.format(email=self.email),
            auth=(self.email, self.password),
            headers={"User-Agent": "zwift-komoot-synchronizer/0.1"},
        )
        if response.status_code == 403:
            raise KomootError("Invalid Komoot credentials (403).")
        if response.status_code >= 400:
            raise KomootError(f"Komoot login failed: HTTP {response.status_code}")
        data = response.json()
        self.username = str(data.get("username") or data.get("email") or self.email)
        self.user_id = str(data.get("username") or "")
        # Login response puts the session token in the "password" field
        self._password_token = data.get("password") or self.password

    def _auth(self) -> tuple[str, str]:
        if not self._password_token:
            raise KomootError("Not authenticated. Call login() first.")
        return self.email, self._password_token

    def upload_fit(self, fit_path: Path, *, name: str, sport: str = "touringbicycle") -> UploadResult:
        params = {
            "sport": sport,
            "status": self.privacy,
            "data_type": "fit",
            "name": name,
        }
        data = fit_path.read_bytes()
        response = self._client.post(
            self.UPLOAD_URL,
            params=params,
            content=data,
            auth=self._auth(),
            headers={
                "User-Agent": "zwift-komoot-synchronizer/0.1",
                "Content-Type": "application/octet-stream",
            },
        )
        if response.status_code not in {201, 202}:
            raise KomootError(
                f"FIT upload failed ({response.status_code}): {response.text[:400]}"
            )
        payload = response.json()
        tour_id = int(payload["id"])
        return UploadResult(tour_id=tour_id, created=response.status_code == 201, raw=payload)

    def rename_tour(self, tour_id: int, name: str) -> None:
        response = self._client.patch(
            self.TOUR_URL.format(tour_id=tour_id),
            auth=self._auth(),
            headers={
                "User-Agent": "zwift-komoot-synchronizer/0.1",
                "Content-Type": "application/hal+json",
            },
            json={"name": name},
        )
        if response.status_code >= 400:
            raise KomootError(
                f"Rename tour {tour_id} failed ({response.status_code}): {response.text[:300]}"
            )

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> KomootClient:
        self.login()
        return self

    def __exit__(self, *args: object) -> None:
        self.close()


def tour_url(tour_id: int) -> str:
    return KomootClient.WEB_TOUR_URL.format(tour_id=tour_id)


def sport_from_fit(sport: str | None) -> str:
    mapping = {
        "cycling": "touringbicycle",
        "running": "jogging",
        "walking": "hike",
        "hiking": "hike",
    }
    if not sport:
        return "touringbicycle"
    return mapping.get(sport.lower(), "touringbicycle")
