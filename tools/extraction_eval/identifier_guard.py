"""Conservative token-loss guard for identifiers discovered in source regions.

The caller must identify the appropriate source region and identifier grammar.
A loss flag requires review; it does not prove the source token is correct.
"""

from __future__ import annotations

import re


def missing_identifier_tokens(source_text: str, extracted_text: str, pattern: str) -> list[str]:
    source_tokens = {match.group(0) for match in re.finditer(pattern, source_text)}
    extracted_tokens = {match.group(0) for match in re.finditer(pattern, extracted_text)}
    return sorted(source_tokens - extracted_tokens)
