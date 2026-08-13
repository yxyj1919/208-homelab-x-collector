from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from xbookmarks.classifier import OllamaClassifier, RuleBasedClassifier
from xbookmarks.cli import _build_classifier, main
from xbookmarks.config import (
    category_config_for_interests,
    load_category_config,
    load_category_rules,
)
from xbookmarks.providers import ProviderOptions, build_provider


class RuleBasedClassifierTest(unittest.TestCase):
    def setUp(self) -> None:
        self.classifier = RuleBasedClassifier(load_category_rules())

    def test_programming_keywords(self) -> None:
        result = self.classifier.classify("Python Projects for Beginners #programming")
        self.assertEqual(result.category, "Programming")

    def test_devops_keywords(self) -> None:
        result = self.classifier.classify("Dockerfiles and Docker Compose Basics")
        self.assertEqual(result.category, "DevOps")

    def test_tools_chinese_keywords(self) -> None:
        result = self.classifier.classify("#工程师工具 SingleFile 浏览器扩展")
        self.assertEqual(result.category, "Tools")

    def test_finance_chinese_keywords(self) -> None:
        result = self.classifier.classify("全球理财没那么难 ETF 投资入门")
        self.assertEqual(result.category, "Finance")

    def test_vcf_stays_more_specific_than_vmware(self) -> None:
        result = self.classifier.classify("VMware Cloud Foundation lifecycle notes")
        self.assertEqual(result.category, "VCF")

    def test_security_keywords(self) -> None:
        result = self.classifier.classify("Ransomware and top vulnerabilities for SOC teams")
        self.assertEqual(result.category, "Security")

    def test_learning_keywords(self) -> None:
        result = self.classifier.classify("Free hands on tutorial and guide")
        self.assertEqual(result.category, "Learning")

    def test_language_keywords(self) -> None:
        result = self.classifier.classify("Prepositions of time and place English review")
        self.assertEqual(result.category, "Language")

    def test_career_keywords(self) -> None:
        result = self.classifier.classify("Students with SA pro certs are struggling in bootcamp")
        self.assertEqual(result.category, "Career")

    def test_life_keywords(self) -> None:
        result = self.classifier.classify("7 Netflix Documentaries You Need To Watch")
        self.assertEqual(result.category, "Life")

    def test_vmware_powercli_keyword(self) -> None:
        result = self.classifier.classify("PowerCLI Script: VM Info Report")
        self.assertEqual(result.category, "VMware")

    def test_data_science_keywords(self) -> None:
        result = self.classifier.classify("NumPy and Pandas for DataScience")
        self.assertEqual(result.category, "Data")


class OllamaClassifierTest(unittest.TestCase):
    def test_parse_response_extracts_json_object(self) -> None:
        classifier = OllamaClassifier(categories=["AI", "Programming"])
        result = classifier._parse_response(
            'Here is the result: {"category":"AI","tags":["ollama"],'
            '"confidence":0.8,"reason":"local model"}'
        )

        self.assertEqual(result.category, "AI")
        self.assertEqual(result.tags, ["ollama"])
        self.assertEqual(result.confidence, 0.8)

    def test_parse_response_rejects_unknown_category(self) -> None:
        classifier = OllamaClassifier(categories=["AI", "Programming"])
        result = classifier._parse_response(
            '{"category":"Unknown","tags":[],"confidence":0.7,"reason":"test"}'
        )

        self.assertEqual(result.category, "General")

    def test_parse_response_falls_back_for_malformed_json(self) -> None:
        classifier = OllamaClassifier(categories=["AI", "Programming"])
        result = classifier._parse_response(
            '{"category":"AI","tags":["broken"],"confidence":0.7'
        )

        self.assertEqual(result.category, "General")
        self.assertEqual(result.tags, [])
        self.assertEqual(result.confidence, 0.0)
        self.assertIn("Ollama response", result.reason)

    def test_prompt_includes_category_descriptions(self) -> None:
        classifier = OllamaClassifier(
            categories=["Tools", "Productivity"],
            category_descriptions={
                "Tools": "Software tools and browser extensions.",
                "Productivity": "Workflows and information management.",
            },
        )
        prompt = classifier._build_prompt("SingleFile browser extension")

        self.assertIn("Tools: Software tools and browser extensions.", prompt)
        self.assertIn("Productivity: Workflows and information management.", prompt)

    def test_build_classifier_uses_remote_ollama_url(self) -> None:
        classifier = _build_classifier(
            provider="ollama",
            definitions=category_config_for_interests(["ai"]),
            ollama_model="qwen2.5:7b",
            ollama_url="http://192.168.31.10:11434/",
            ollama_timeout=30,
        )

        self.assertIsInstance(classifier, OllamaClassifier)
        self.assertEqual(classifier.base_url, "http://192.168.31.10:11434")
        self.assertEqual(classifier.timeout_seconds, 30)

    def test_ollama_check_command_reports_available_model(self) -> None:
        with TemporaryDirectory() as temp_dir:
            db = Path(temp_dir) / "bookmarks.sqlite"
            with patch.object(
                OllamaClassifier,
                "check",
                return_value=["qwen2.5:7b", "llama3.2:3b"],
            ):
                exit_code = main(
                    [
                        "--db",
                        str(db),
                        "ollama-check",
                        "--ollama-url",
                        "http://192.168.31.10:11434",
                        "--ollama-model",
                        "qwen2.5:7b",
                    ]
                )

        self.assertEqual(exit_code, 0)


class ProviderTest(unittest.TestCase):
    def test_build_rules_provider(self) -> None:
        provider = build_provider(
            ProviderOptions(
                name="rules",
                ollama_model="ignored",
                ollama_url="http://127.0.0.1:11434",
                ollama_timeout=30,
            ),
            category_config_for_interests(["virtualization"]),
        )

        self.assertEqual(provider.name, "rules")
        self.assertEqual(provider.model_label, "rules")
        self.assertFalse(provider.show_progress)
        self.assertIsInstance(provider.classifier, RuleBasedClassifier)

    def test_build_ollama_provider(self) -> None:
        provider = build_provider(
            ProviderOptions(
                name="ollama",
                ollama_model="qwen2.5:7b",
                ollama_url="http://192.168.31.10:11434/",
                ollama_timeout=30,
            ),
            category_config_for_interests(["ai"]),
        )

        self.assertEqual(provider.name, "ollama")
        self.assertEqual(provider.model_label, "qwen2.5:7b")
        self.assertTrue(provider.show_progress)
        self.assertIsInstance(provider.classifier, OllamaClassifier)
        self.assertEqual(provider.classifier.base_url, "http://192.168.31.10:11434")


class CategoryConfigTest(unittest.TestCase):
    def test_load_category_config_reads_descriptions_and_keywords(self) -> None:
        definitions = load_category_config()

        self.assertIn("Tools", definitions)
        self.assertIn("browser extensions", definitions["Tools"].description)
        self.assertIn("singlefile", definitions["Tools"].keywords)

    def test_interest_presets_include_descriptions(self) -> None:
        definitions = category_config_for_interests(["virtualization", "kubernetes"])

        self.assertIn("vCenter", definitions)
        self.assertIn("vCenter Server", definitions["vCenter"].description)
        self.assertIn("vks", definitions["VKS"].keywords)

    def test_init_writes_selected_interest_categories(self) -> None:
        with TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            db = base / "bookmarks.sqlite"
            categories = base / "categories.yaml"

            exit_code = main(
                [
                    "--db",
                    str(db),
                    "--categories",
                    str(categories),
                    "init",
                    "--write-categories",
                    "--interests",
                    "virtualization,kubernetes",
                ]
            )

            self.assertEqual(exit_code, 0)
            definitions = load_category_config(categories)
            self.assertEqual(
                list(definitions),
                ["VMware", "vCenter", "VCF", "Kubernetes", "VKS", "General"],
            )
            self.assertIn("VMware platform topics", definitions["VMware"].description)
            self.assertIn("Fallback category", definitions["General"].description)

    def test_category_add_creates_new_config(self) -> None:
        with TemporaryDirectory() as temp_dir:
            categories = Path(temp_dir) / "categories.yaml"

            exit_code = main(
                [
                    "--categories",
                    str(categories),
                    "category",
                    "add",
                    "Storage",
                    "--description",
                    "Storage systems, filesystems, NAS, and backup.",
                    "--keywords",
                    "storage, filesystem",
                    "--keyword",
                    "backup",
                ]
            )

            self.assertEqual(exit_code, 0)
            definitions = load_category_config(categories)
            self.assertEqual(
                definitions["Storage"].description,
                "Storage systems, filesystems, NAS, and backup.",
            )
            self.assertEqual(
                definitions["Storage"].keywords,
                ["backup", "storage", "filesystem"],
            )

    def test_category_add_merges_keywords_without_replace(self) -> None:
        with TemporaryDirectory() as temp_dir:
            categories = Path(temp_dir) / "categories.yaml"
            main(
                [
                    "--categories",
                    str(categories),
                    "category",
                    "add",
                    "Storage",
                    "--description",
                    "Storage systems.",
                    "--keywords",
                    "nas, backup",
                ]
            )

            exit_code = main(
                [
                    "--categories",
                    str(categories),
                    "category",
                    "add",
                    "Storage",
                    "--keywords",
                    "Backup, zfs",
                ]
            )

            self.assertEqual(exit_code, 0)
            definitions = load_category_config(categories)
            self.assertEqual(definitions["Storage"].description, "Storage systems.")
            self.assertEqual(definitions["Storage"].keywords, ["nas", "backup", "zfs"])


if __name__ == "__main__":
    unittest.main()
