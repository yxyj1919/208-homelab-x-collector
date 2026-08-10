from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


DEFAULT_CATEGORIES = Path("config/categories.yaml")


@dataclass(frozen=True)
class CategoryDefinition:
    description: str
    keywords: list[str]


INTEREST_PRESETS: dict[str, dict[str, CategoryDefinition]] = {
    "virtualization": {
        "VMware": CategoryDefinition(
            description=(
                "VMware platform topics including ESXi, vSphere, vSAN, "
                "PowerCLI, Aria, and VMware operations outside narrower "
                "vCenter or VCF scope."
            ),
            keywords=["vmware", "powercli", "esxi", "vsphere", "vsan", "aria"],
        ),
        "vCenter": CategoryDefinition(
            description=(
                "vCenter Server, VCSA, vpxd, vmon, service health, inventory "
                "management, and vCenter troubleshooting."
            ),
            keywords=["vcenter", "vcsa", "vpxd", "vmon"],
        ),
        "VCF": CategoryDefinition(
            description=(
                "VMware Cloud Foundation, SDDC Manager, lifecycle management, "
                "bring-up, upgrade, and VCF architecture."
            ),
            keywords=["vcf", "vmware cloud foundation", "sddc manager"],
        ),
    },
    "kubernetes": {
        "Kubernetes": CategoryDefinition(
            description=(
                "Kubernetes core platform, kubectl, clusters, workloads, "
                "Helm, container runtime, and cloud native operations."
            ),
            keywords=["kubernetes", "k8s", "kubectl", "containerd", "helm"],
        ),
        "VKS": CategoryDefinition(
            description=(
                "VMware Kubernetes Service, Tanzu, supervisor clusters, TKGS, "
                "and VMware-specific Kubernetes integration."
            ),
            keywords=["vks", "tanzu", "vmwaretanzu", "tkgs", "supervisor cluster"],
        ),
    },
    "homelab": {
        "Homelab": CategoryDefinition(
            description=(
                "Home lab infrastructure, NAS, routers, mini PCs, Proxmox, "
                "home networking, and personal infrastructure experiments."
            ),
            keywords=["homelab", "nas", "proxmox", "minipc", "router"],
        ),
        "Networking": CategoryDefinition(
            description=(
                "Networking protocols and troubleshooting including TCP, HTTP, "
                "DNS, BGP, Cilium, Gateway API, packet flow, and request latency."
            ),
            keywords=["network", "tcp", "http", "dns", "bgp", "cilium"],
        ),
    },
    "ai": {
        "AI": CategoryDefinition(
            description=(
                "Artificial intelligence, LLMs, Ollama, OpenAI, RAG, embeddings, "
                "prompts, AI tools, and model workflows."
            ),
            keywords=["ai", "llm", "ollama", "openai", "rag", "embedding"],
        ),
    },
    "security": {
        "Security": CategoryDefinition(
            description=(
                "Security topics including CVEs, vulnerabilities, ransomware, "
                "SOC, zero trust, TLS, hacking, and defensive operations."
            ),
            keywords=["security", "cve", "vulnerability", "ransomware", "soc", "tls"],
        ),
    },
}


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


def merge_category_config(
    base: dict[str, CategoryDefinition],
    additions: dict[str, CategoryDefinition],
) -> dict[str, CategoryDefinition]:
    merged = {
        category: CategoryDefinition(
            description=definition.description,
            keywords=list(definition.keywords),
        )
        for category, definition in base.items()
    }
    for category, definition in additions.items():
        current = merged.get(category)
        if current is None:
            merged[category] = CategoryDefinition(
                description=definition.description,
                keywords=list(definition.keywords),
            )
            continue
        merged_rules = merge_category_rules(
            {category: current.keywords}, {category: definition.keywords}
        )
        merged[category] = CategoryDefinition(
            description=current.description or definition.description,
            keywords=merged_rules[category],
        )
    return merged


def category_config_for_interests(
    interests: list[str],
) -> dict[str, CategoryDefinition]:
    selected: dict[str, CategoryDefinition] = {}
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
        selected = merge_category_config(selected, preset)
    return selected


def rules_for_interests(interests: list[str]) -> dict[str, list[str]]:
    return {
        category: definition.keywords
        for category, definition in category_config_for_interests(interests).items()
    }
