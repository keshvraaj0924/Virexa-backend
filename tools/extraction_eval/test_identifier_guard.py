import unittest

from identifier_guard import missing_identifier_tokens


class IdentifierGuardTests(unittest.TestCase):
    def test_missing_identifier_requires_review(self):
        source = "HS: 12340001, 12340002, 12340003, 12340004, 12340005, 12340006, 12340007"
        extracted = source.replace("12340004, ", "")
        self.assertEqual(missing_identifier_tokens(source, extracted, r"\b\d{8}\b"), ["12340004"])

    def test_leading_zeros_are_significant(self):
        self.assertEqual(
            missing_identifier_tokens("00123456", "123456", r"\b\d{8}\b"), ["00123456"]
        )

    def test_no_loss(self):
        self.assertEqual(missing_identifier_tokens("00123456", "Code: 00123456", r"\b\d{8}\b"), [])


if __name__ == "__main__":
    unittest.main()
