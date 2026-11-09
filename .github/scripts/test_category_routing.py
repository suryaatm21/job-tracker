#!/usr/bin/env python3
"""
Category routing tests: Simplify's short role labels reach the role digests,
and never reach DM alerts.

Run: python -m unittest discover -s .github/scripts -p 'test_category_routing.py'
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import job_filtering as jf  # noqa: E402

SIMPLIFY_REPO = "SimplifyJobs/Summer2027-Internships"
SWE_DIGEST = ["Software Engineering", "Data Science, AI & Machine Learning"]

# (Simplify label, title with no SWE/ML title keywords, role channel's canonical category)
ROLE_LABELS = [
    ("Hardware", "Hardware Engineering Intern", "Hardware Engineering"),
    ("Product", "Product Manager Intern", "Product Management"),
    ("Quant", "Quantitative Trader Intern", "Quantitative Finance"),
]


def simplify_item(category, title):
    return {
        "category": category,
        "title": title,
        "company_name": "Acme",
        "url": "https://example.com/job",
        "active": True,
        "is_visible": True,
    }


class RoleDigestsReceiveShortLabels(unittest.TestCase):
    def test_each_role_digest_accepts_its_short_label(self):
        for label, title, canonical in ROLE_LABELS:
            with self.subTest(label=label):
                ok, reason = jf.should_process_digest_item(simplify_item(label, title), [canonical], "false")
                self.assertEqual((ok, reason), (True, "allowed"))

    def test_short_label_match_is_case_insensitive(self):
        ok, _ = jf.should_process_digest_item(simplify_item("hardware", "Hardware Engineering Intern"), ["Hardware Engineering"], "false")
        self.assertTrue(ok)

    def test_role_digest_rejects_other_role_labels(self):
        ok, reason = jf.should_process_digest_item(simplify_item("Quant", "Quantitative Trader Intern"), ["Hardware Engineering"], "false")
        self.assertEqual((ok, reason), (False, "category"))

    def test_short_labels_stay_out_of_swe_digest(self):
        for label, title, _ in ROLE_LABELS:
            with self.subTest(label=label):
                ok, reason = jf.should_process_digest_item(simplify_item(label, title), SWE_DIGEST, "true")
                self.assertEqual((ok, reason), (False, "category"))


class DmAlertsUnchanged(unittest.TestCase):
    def test_dm_rejects_hardware_pm_quant(self):
        for label, title, _ in ROLE_LABELS:
            with self.subTest(label=label):
                ok, reason = jf.should_process_repo_item(simplify_item(label, title), SIMPLIFY_REPO)
                self.assertEqual((ok, reason), (False, "category"))

    def test_dm_classifier_ignores_digest_aliases(self):
        for label, title, _ in ROLE_LABELS:
            with self.subTest(label=label):
                self.assertIsNone(jf.classify_job_category(simplify_item(label, title)))

    def test_shared_mapping_has_no_role_aliases(self):
        for label, _, _ in ROLE_LABELS:
            with self.subTest(label=label):
                self.assertNotIn(label, jf.SIMPLIFY_CATEGORY_MAPPING)

    def test_dm_still_accepts_swe_and_ml(self):
        for label, title in [("Software", "Software Engineer Intern"), ("AI/ML/Data", "Machine Learning Intern")]:
            with self.subTest(label=label):
                ok, reason = jf.should_process_repo_item(simplify_item(label, title), SIMPLIFY_REPO)
                self.assertEqual((ok, reason), (True, "allowed"))


if __name__ == "__main__":
    unittest.main()
