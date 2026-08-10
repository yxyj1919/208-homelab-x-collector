from __future__ import annotations

import unittest

from xbookmarks.classifier import OllamaClassifier, RuleBasedClassifier
from xbookmarks.config import load_category_config, load_category_rules


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


class CategoryConfigTest(unittest.TestCase):
    def test_load_category_config_reads_descriptions_and_keywords(self) -> None:
        definitions = load_category_config()

        self.assertIn("Tools", definitions)
        self.assertIn("browser extensions", definitions["Tools"].description)
        self.assertIn("singlefile", definitions["Tools"].keywords)


if __name__ == "__main__":
    unittest.main()
