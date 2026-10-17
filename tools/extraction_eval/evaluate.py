"""Local PDF text-retention and table-row checks; no network or document uploads.

Gold annotations stay outside this source tree. Text presence is not semantic
field extraction accuracy. Reviewer approval is recorded, never inferred.
"""

from __future__ import annotations

import argparse
import importlib.metadata
import json
import re
import time
from pathlib import Path


def normalize(value: str | None) -> str:
    return re.sub(r"\s+", " ", value or "").strip()


def evaluate(source: Path, gold: dict) -> dict:
    import fitz
    import pdfplumber

    started = time.perf_counter()
    document = fitz.open(source)
    pages = [page.get_text() for page in document]
    fields = []
    for field in gold["fields"]:
        page = document[field["page"] - 1]
        matches = [list(box) for box in page.search_for(field["value"])]
        fields.append(
            {
                "name": field["name"],
                "expected": field["value"],
                "page": field["page"],
                "text_present": normalize(field["value"]) in normalize(pages[field["page"] - 1]),
                "candidate_boxes": matches,
                "coordinate_system": "PDF points, top-left origin",
                "source_correctness": "candidate boxes only; repeated values require review",
            }
        )
    tables = []
    with pdfplumber.open(source) as pdf:
        for page_number, page in enumerate(pdf.pages, 1):
            for table in page.find_tables():
                tables.append(
                    {"page": page_number, "bbox": list(table.bbox), "rows": table.extract()}
                )
    row_checks = []
    for table in gold.get("tables", []):
        candidates = [
            row for found in tables if found["page"] == table["page"] for row in found["rows"]
        ]
        for expected in table["rows"]:
            expected_norm = [normalize(cell) for cell in expected]
            exact = any([normalize(cell) for cell in row] == expected_norm for row in candidates)
            row_checks.append(
                {"table": table["name"], "expected": expected, "exact_row_recovered": exact}
            )
    return {
        "sample_id": gold["sample_id"],
        "review_status": gold["review_status"],
        "pipeline": "native text via PyMuPDF + pdfplumber default line tables; no OCR",
        "versions": {name: importlib.metadata.version(name) for name in ["PyMuPDF", "pdfplumber"]},
        "page_count": len(pages),
        "elapsed_seconds": time.perf_counter() - started,
        "field_text_checks": fields,
        "table_row_checks": row_checks,
        "tables": tables,
        "limitations": [
            "Selected text retention, not end-to-end field semantics",
            "No confidence calibration",
            "No scan or handwriting evidence",
            "Gold reviewed by assistant; independent human sign-off pending",
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    manifest = json.loads(args.manifest.read_text())
    results = [evaluate(Path(item["source"]), item) for item in manifest]
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(results, indent=2))
    print(json.dumps({"documents": len(results), "output_written": True}))


if __name__ == "__main__":
    main()
