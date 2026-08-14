from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse


DEFAULT_SETTINGS = Path("config/settings.json")
DEFAULT_OLLAMA_URL = "http://127.0.0.1:11434"
DEFAULT_OLLAMA_MODEL = "qwen2.5:7b"
DEFAULT_OLLAMA_TIMEOUT = 180
OLLAMA_MODEL_DISCOVERY_TIMEOUT = 10


@dataclass(frozen=True)
class AppSettings:
    ollama_url: str = DEFAULT_OLLAMA_URL
    ollama_model: str = DEFAULT_OLLAMA_MODEL
    ollama_timeout: int = DEFAULT_OLLAMA_TIMEOUT


def load_settings(path: Path = DEFAULT_SETTINGS) -> AppSettings:
    if not path.exists():
        return AppSettings()
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("settings file must contain a JSON object")
    return settings_from_payload(payload)


def save_settings(settings: AppSettings, path: Path = DEFAULT_SETTINGS) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "ollama_url": settings.ollama_url,
        "ollama_model": settings.ollama_model,
        "ollama_timeout": settings.ollama_timeout,
    }
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def settings_from_payload(payload: dict[str, Any]) -> AppSettings:
    ollama_url = _text(payload.get("ollama_url")) or DEFAULT_OLLAMA_URL
    ollama_model = _text(payload.get("ollama_model")) or DEFAULT_OLLAMA_MODEL
    ollama_timeout = _int_value(
        payload.get("ollama_timeout"),
        "ollama_timeout",
        default=DEFAULT_OLLAMA_TIMEOUT,
        minimum=1,
        maximum=1800,
    )
    _validate_url(ollama_url)
    return AppSettings(
        ollama_url=ollama_url.rstrip("/"),
        ollama_model=ollama_model,
        ollama_timeout=ollama_timeout,
    )


def _validate_url(value: str) -> None:
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("ollama_url must be an http or https URL with a host")


def _text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _int_value(
    value: Any,
    name: str,
    *,
    default: int,
    minimum: int,
    maximum: int,
) -> int:
    if value in (None, ""):
        number = default
    elif isinstance(value, bool):
        raise ValueError(f"{name} must be an integer")
    else:
        try:
            number = int(value)
        except (TypeError, ValueError):
            raise ValueError(f"{name} must be an integer") from None
    if number < minimum or number > maximum:
        raise ValueError(f"{name} must be between {minimum} and {maximum}")
    return number
