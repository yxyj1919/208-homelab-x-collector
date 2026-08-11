from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any


DEFAULT_SECRET_PATH = Path("data/secrets/x-oauth.json")


@dataclass(frozen=True)
class OAuthSecrets:
    access_token: str | None = None
    refresh_token: str | None = None
    client_id: str | None = None

    @property
    def has_refresh(self) -> bool:
        return bool(self.refresh_token and self.client_id)


class SecretStore:
    def __init__(self, path: Path = DEFAULT_SECRET_PATH) -> None:
        self.path = path

    def load_oauth(self) -> OAuthSecrets:
        if not self.path.exists():
            return OAuthSecrets()
        payload = json.loads(self.path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError(f"Secret file must contain a JSON object: {self.path}")
        return OAuthSecrets(
            access_token=_clean_secret(payload.get("access_token")),
            refresh_token=_clean_secret(payload.get("refresh_token")),
            client_id=_clean_secret(payload.get("client_id")),
        )

    def save_oauth(
        self,
        access_token: str | None = None,
        refresh_token: str | None = None,
        client_id: str | None = None,
    ) -> None:
        current = self.load_oauth()
        payload = {
            "access_token": access_token or current.access_token or "",
            "refresh_token": refresh_token or current.refresh_token or "",
            "client_id": client_id or current.client_id or "",
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        os.chmod(tmp_path, 0o600)
        tmp_path.replace(self.path)
        os.chmod(self.path, 0o600)


def _clean_secret(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None
