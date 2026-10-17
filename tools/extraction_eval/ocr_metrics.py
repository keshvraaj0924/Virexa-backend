"""Whitespace-normalized OCR error counts, without exposing transcript contents."""

from __future__ import annotations

import argparse
import json
from collections.abc import Sequence
from pathlib import Path
from typing import TypeVar

Token = TypeVar("Token")


def edit_distance(reference: Sequence[Token], hypothesis: Sequence[Token]) -> int:
    """Unit-cost Levenshtein distance with linear auxiliary memory."""
    previous = list(range(len(hypothesis) + 1))
    for reference_index, reference_token in enumerate(reference, 1):
        current = [reference_index]
        for hypothesis_index, hypothesis_token in enumerate(hypothesis, 1):
            current.append(
                min(
                    current[-1] + 1,
                    previous[hypothesis_index] + 1,
                    previous[hypothesis_index - 1] + (reference_token != hypothesis_token),
                )
            )
        previous = current
    return previous[-1]


def score_transcript(reference: str, hypothesis: str) -> dict[str, int | float]:
    """Preserve case/punctuation; divide edits by reference length, never cap rates."""
    reference = " ".join(reference.split())
    hypothesis = " ".join(hypothesis.split())
    if not reference:
        raise ValueError("Reference transcript must contain non-whitespace text")
    reference_words = reference.split()
    character_edits = edit_distance(reference, hypothesis)
    word_edits = edit_distance(reference_words, hypothesis.split())
    return {
        "character_edits": character_edits,
        "reference_characters": len(reference),
        "cer": character_edits / len(reference),
        "word_edits": word_edits,
        "reference_words": len(reference_words),
        "wer": word_edits / len(reference_words),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--reference", type=Path, required=True)
    parser.add_argument("--hypothesis", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(score_transcript(args.reference.read_text(), args.hypothesis.read_text())))


if __name__ == "__main__":
    main()
