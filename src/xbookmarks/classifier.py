from __future__ import annotations

import json
import re
import socket
import urllib.error
import urllib.request

from .models import ClassificationResult


class RuleBasedClassifier:
    def __init__(self, rules: dict[str, list[str]]) -> None:
        self.rules = rules

    def classify(self, text: str) -> ClassificationResult:
        normalized = text.lower()
        matches: list[tuple[str, list[str]]] = []
        for category, keywords in self.rules.items():
            hit_keywords = [
                keyword
                for keyword in keywords
                if _keyword_matches(normalized, keyword.lower())
            ]
            if hit_keywords:
                matches.append((category, hit_keywords))

        if not matches:
            return ClassificationResult(
                category="General",
                tags=[],
                confidence=0.2,
                reason="No category keyword matched.",
            )

        category, tags = max(
            matches,
            key=lambda item: (len(item[1]), sum(len(keyword) for keyword in item[1])),
        )
        confidence = min(0.95, 0.45 + len(tags) * 0.15)
        return ClassificationResult(
            category=category,
            tags=tags,
            confidence=confidence,
            reason=f"Matched keywords: {', '.join(tags)}",
        )


class OllamaClassifier:
    def __init__(
        self,
        categories: list[str],
        category_descriptions: dict[str, str] | None = None,
        model: str = "qwen2.5:7b",
        base_url: str = "http://127.0.0.1:11434",
        timeout_seconds: int = 60,
    ) -> None:
        self.categories = categories
        self.category_descriptions = category_descriptions or {}
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds

    def classify(self, text: str) -> ClassificationResult:
        prompt = self._build_prompt(text)
        payload = json.dumps(
            {
                "model": self.model,
                "prompt": prompt,
                "stream": False,
            }
        ).encode("utf-8")
        request = urllib.request.Request(
            f"{self.base_url}/api/generate",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )

        try:
            with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                body = json.loads(response.read().decode("utf-8"))
        except (TimeoutError, socket.timeout) as exc:
            raise RuntimeError(
                f"Ollama request timed out after {self.timeout_seconds} seconds"
            ) from exc
        except urllib.error.URLError as exc:
            raise RuntimeError(f"Ollama request failed: {exc}") from exc

        raw_result = body.get("response")
        if not isinstance(raw_result, str):
            raise RuntimeError("Ollama response did not contain a text response")

        return self._parse_response(raw_result)

    def _build_prompt(self, text: str) -> str:
        category_lines = "\n".join(
            f"- {category}: {self.category_descriptions.get(category) or 'No description.'}"
            for category in self.categories
        )
        return (
            "/no_think\n"
            "Classify this X/Twitter bookmark into exactly one category.\n"
            "Allowed categories and meanings:\n"
            f"{category_lines}\n"
            "- General: Use only when none of the specific categories fit.\n"
            "Return compact JSON only: category, tags, confidence, reason.\n"
            "Use short English tags. confidence is 0 to 1.\n\n"
            f"Text:\n{text[:2000]}"
        )

    def _parse_response(self, raw_result: str) -> ClassificationResult:
        parsed = _loads_json_object(raw_result)
        category = str(parsed.get("category") or "General").strip()
        if category not in self.categories:
            category = "General"

        raw_tags = parsed.get("tags") or []
        tags = [str(tag).strip() for tag in raw_tags if str(tag).strip()]

        try:
            confidence = float(parsed.get("confidence", 0.5))
        except (TypeError, ValueError):
            confidence = 0.5

        return ClassificationResult(
            category=category,
            tags=tags[:8],
            confidence=max(0.0, min(1.0, confidence)),
            reason=str(parsed.get("reason") or "Classified by Ollama.").strip(),
        )


def _keyword_matches(text: str, keyword: str) -> bool:
    if re.fullmatch(r"[a-z0-9][a-z0-9 ._+-]*", keyword):
        return re.search(rf"\b{re.escape(keyword)}\b", text) is not None
    return keyword in text


def _loads_json_object(raw_text: str) -> dict:
    text = raw_text.strip()
    if not text:
        raise RuntimeError("Ollama returned an empty classification response")

    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start == -1 or end == -1 or end <= start:
            raise RuntimeError(f"Ollama response is not JSON: {text[:200]}")
        parsed = json.loads(text[start : end + 1])

    if not isinstance(parsed, dict):
        raise RuntimeError("Ollama classification response must be a JSON object")
    return parsed
