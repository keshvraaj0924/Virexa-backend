"""Score offline Docling JSON with the same bounded private annotations."""

import argparse
import json
import re
from pathlib import Path


def normalize(text):
    return re.sub(r"\s+", " ", text or "").strip()


def score(document, gold):
    page_text = {}
    tables = []
    for item in document["texts"]:
        for source in item["prov"]:
            page_text.setdefault(source["page_no"], []).append(item["text"])
    for table in document["tables"]:
        data = table["data"]
        rows = [[""] * data["num_cols"] for _ in range(data["num_rows"])]
        for cell in data["table_cells"]:
            rows[cell["start_row_offset_idx"]][cell["start_col_offset_idx"]] = cell["text"]
        for source in table["prov"]:
            page_text.setdefault(source["page_no"], []).extend(
                cell["text"] for cell in data["table_cells"]
            )
            tables.append({"page": source["page_no"], "rows": rows, "provenance": source})
    fields = [
        {
            "name": field["name"],
            "text_present": normalize(field["value"])
            in normalize(" ".join(page_text.get(field["page"], []))),
        }
        for field in gold["fields"]
    ]
    row_checks = []
    for table in gold.get("tables", []):
        candidates = [
            row for found in tables if found["page"] == table["page"] for row in found["rows"]
        ]
        for expected in table["rows"]:
            exact = any(
                [normalize(cell) for cell in expected] == [normalize(cell) for cell in candidate]
                for candidate in candidates
            )
            row_checks.append({"table": table["name"], "exact_row_recovered": exact})
    return {
        "sample_id": gold["sample_id"],
        "field_text_checks": fields,
        "table_row_checks": row_checks,
        "tables": tables,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--documents", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    results = []
    for gold in json.loads(args.manifest.read_text()):
        document = json.loads((args.documents / (gold["sample_id"] + ".json")).read_text())
        results.append(score(document, gold))
    args.output.write_text(json.dumps(results, indent=2))


if __name__ == "__main__":
    main()
