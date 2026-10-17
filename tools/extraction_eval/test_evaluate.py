import unittest

from evaluate import normalize


class NormalizeTests(unittest.TestCase):
    def test_multiline(self):
        self.assertEqual(normalize(" Example\n value  "), "Example value")

    def test_preserve_identifiers(self):
        self.assertEqual(normalize("001.50"), "001.50")

    def test_null(self):
        self.assertEqual(normalize(None), "")


class DoclingScoreTests(unittest.TestCase):
    def test_requires_correct_page(self):
        from score_docling import score

        document = {"texts": [{"text": "ABC001", "prov": [{"page_no": 2}]}], "tables": []}
        gold = {"sample_id": "synthetic", "fields": [{"name": "id", "value": "ABC001", "page": 1}]}
        self.assertFalse(score(document, gold)["field_text_checks"][0]["text_present"])

    def test_column_swap_fails(self):
        from score_docling import score

        cells = [
            {"start_row_offset_idx": 0, "start_col_offset_idx": i, "text": text}
            for i, text in enumerate(["B", "A"])
        ]
        document = {
            "texts": [],
            "tables": [
                {
                    "prov": [{"page_no": 1}],
                    "data": {"num_rows": 1, "num_cols": 2, "table_cells": cells},
                }
            ],
        }
        gold = {
            "sample_id": "synthetic",
            "fields": [],
            "tables": [{"name": "grid", "page": 1, "rows": [["A", "B"]]}],
        }
        self.assertFalse(score(document, gold)["table_row_checks"][0]["exact_row_recovered"])


if __name__ == "__main__":
    unittest.main()
