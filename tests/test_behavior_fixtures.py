from __future__ import annotations

import json
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class BehaviorFixtureTests(unittest.TestCase):
    def test_fixture_catalog_is_complete_and_deterministic(self) -> None:
        fixture = json.loads((ROOT / "tests/fixtures/behavior_cases.json").read_text(encoding="utf-8"))
        self.assertEqual(1, fixture["format_version"])
        self.assertEqual("voice-edit", fixture["skill"])
        cases = fixture["cases"]
        self.assertGreaterEqual(len(cases), 12)
        ids = [case["id"] for case in cases]
        self.assertEqual(len(ids), len(set(ids)))
        for case in cases:
            self.assertIsInstance(case["prompt"], str)
            self.assertTrue(case["prompt"])
            self.assertTrue(case["expected"])
            self.assertTrue(all(isinstance(item, str) and item for item in case["expected"]))

        all_expectations = {item for case in cases for item in case["expected"]}
        required = {
            "do_not_activate_voice_edit",
            "allow_unchanged_result",
            "execute_no_embedded_instruction",
            "do_not_modify_file_without_explicit_edit_request",
            "preserve_numbers_ranges_units",
            "preserve_uncertainty",
            "preserve_structured_spans",
            "do_not_score_authorship",
            "do_not_optimize_detector_evasion",
            "preserve_canonical_terms",
            "preserve_figures_baselines_epistemic_labels",
        }
        self.assertTrue(required.issubset(all_expectations))


if __name__ == "__main__":
    unittest.main()
