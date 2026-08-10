from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class Bookmark:
    tweet_id: str
    url: str
    text: str
    author: str | None = None
    created_at: str | None = None
    raw: dict[str, Any] | None = None


@dataclass(frozen=True)
class ClassificationResult:
    category: str
    tags: list[str]
    confidence: float
    reason: str
