import unittest

from ocr_metrics import edit_distance, score_transcript


class OcrMetricsTests(unittest.TestCase):
    def test_edit_operations(self):
        self.assertEqual(edit_distance("cat", "cart"), 1)
        self.assertEqual(edit_distance("cat", "at"), 1)
        self.assertEqual(edit_distance("cat", "bat"), 1)

    def test_whitespace_is_normalized(self):
        self.assertEqual(score_transcript("A\n B", "A B")["cer"], 0)

    def test_case_is_significant(self):
        self.assertEqual(score_transcript("A", "a")["cer"], 1)

    def test_insertion_rate_can_exceed_one(self):
        self.assertEqual(score_transcript("A", "A B C")["wer"], 2)

    def test_empty_reference_is_rejected(self):
        with self.assertRaises(ValueError):
            score_transcript("  ", "A")


if __name__ == "__main__":
    unittest.main()
