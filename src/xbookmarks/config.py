from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


DEFAULT_CATEGORIES = Path("config/categories.yaml")

INTEREST_PRESETS: dict[str, dict[str, list[str]]] = {
    "virtualization": {
        "VMware": ["vmware", "powercli", "esxi", "vsphere", "vsan", "aria"],
        "vCenter": ["vcenter", "vcsa", "vpxd", "vmon"],
        "VCF": ["vcf", "vmware cloud foundation", "sddc manager"],
    },
    "kubernetes": {
        "Kubernetes": ["kubernetes", "k8s", "kubectl", "containerd", "helm"],
        "VKS": ["vks", "tanzu", "vmwaretanzu", "tkgs", "supervisor cluster"],
    },
    "homelab": {
        "Homelab": ["homelab", "nas", "proxmox", "minipc", "router"],
        "Networking": ["network", "tcp", "http", "dns", "bgp", "cilium"],
    },
    "ai": {
        "AI": ["ai", "llm", "ollama", "openai", "rag", "embedding"],
    },
    "security": {
        "Security": ["security", "cve", "vulnerability", "ransomware", "soc", "tls"],
    },
}


@dataclass(frozen=True)
class CategoryDefinition:
    description: str
    keywords: list[str]


def load_category_config(
    path: Path = DEFAULT_CATEGORIES,
) -> dict[str, CategoryDefinition]:
    if not path.exists():
        raise FileNotFoundError(f"Category config not found: {path}")

    definitions: dict[str, CategoryDefinition] = {}
    current_category: str | None = None
    current_field: str | None = None
    descriptions: dict[str, str] = {}
    keywords_by_category: dict[str, list[str]] = {}

    for line_number, raw_line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = raw_line.split("#", 1)[0].rstrip()
        if not line.strip():
            continue

        if not line.startswith(" ") and line.endswith(":"):
            current_category = line[:-1].strip()
            current_field = None
            if not current_category:
                raise ValueError(f"Empty category name at {path}:{line_number}")
            descriptions.setdefault(current_category, "")
            keywords_by_category.setdefault(current_category, [])
            continue

        if current_category is None:
            raise ValueError(f"Category field without category at {path}:{line_number}")

        stripped = line.strip()
        if stripped.startswith("- "):
            keyword = stripped[2:].strip()
            if keyword:
                keywords_by_category[current_category].append(keyword)
            continue

        if stripped == "keywords:":
            current_field = "keywords"
            continue

        if stripped.startswith("description:"):
            current_field = "description"
            descriptions[current_category] = stripped.split(":", 1)[1].strip()
            continue

        if current_field == "keywords" and stripped.startswith("- "):
            keyword = stripped[2:].strip()
            if keyword:
                keywords_by_category[current_category].append(keyword)
            continue

        raise ValueError(f"Unsupported category config syntax at {path}:{line_number}: {raw_line}")

    for category, keywords in keywords_by_category.items():
        definitions[category] = CategoryDefinition(
            description=descriptions.get(category, ""),
            keywords=keywords,
        )
    return definitions


def load_category_rules(path: Path = DEFAULT_CATEGORIES) -> dict[str, list[str]]:
    return {
        category: definition.keywords
        for category, definition in load_category_config(path).items()
    }


def save_category_rules(rules: dict[str, list[str]], path: Path = DEFAULT_CATEGORIES) -> None:
    definitions = {
        category: CategoryDefinition(description="", keywords=keywords)
        for category, keywords in rules.items()
    }
    save_category_config(definitions, path)


def save_category_config(
    definitions: dict[str, CategoryDefinition], path: Path = DEFAULT_CATEGORIES
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines: list[str] = []
    for category, definition in definitions.items():
        lines.append(f"{category}:")
        lines.append(f"  description: {definition.description}")
        lines.append("  keywords:")
        for keyword in definition.keywords:
            lines.append(f"    - {keyword}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def merge_category_rules(
    base: dict[str, list[str]], additions: dict[str, list[str]]
) -> dict[str, list[str]]:
    merged = {category: list(keywords) for category, keywords in base.items()}
    for category, keywords in additions.items():
        existing = merged.setdefault(category, [])
        seen = {keyword.lower() for keyword in existing}
        for keyword in keywords:
            normalized = keyword.strip()
            if normalized and normalized.lower() not in seen:
                existing.append(normalized)
                seen.add(normalized.lower())
    return merged


def rules_for_interests(interests: list[str]) -> dict[str, list[str]]:
    selected: dict[str, list[str]] = {}
    for interest in interests:
        key = interest.strip().lower()
        if not key:
            continue
        preset = INTEREST_PRESETS.get(key)
        if preset is None:
            raise ValueError(
                f"Unknown interest preset: {interest}. "
                f"Available presets: {', '.join(sorted(INTEREST_PRESETS))}"
            )
        selected = merge_category_rules(selected, preset)
    return selected
