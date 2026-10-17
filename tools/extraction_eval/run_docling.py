"""Run local, offline Docling with already downloaded and pinned model artifacts."""

from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path

os.environ["HF_HUB_OFFLINE"] = "1"
os.environ["TRANSFORMERS_OFFLINE"] = "1"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--models", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if not (args.models / "model-lock.json").is_file():
        raise ValueError("Pinned model-lock.json is required before processing documents")
    from docling.datamodel.accelerator_options import (
        AcceleratorDevice,
        AcceleratorOptions,
    )
    from docling.datamodel.base_models import InputFormat
    from docling.datamodel.pipeline_options import PdfPipelineOptions
    from docling.document_converter import DocumentConverter, PdfFormatOption

    options = PdfPipelineOptions(artifacts_path=args.models)
    options.do_ocr = False  # Native digital corpus only; do not label this an OCR score.
    options.do_table_structure = True
    options.enable_remote_services = False
    options.allow_external_plugins = False
    options.accelerator_options = AcceleratorOptions(num_threads=4, device=AcceleratorDevice.CPU)
    converter = DocumentConverter(
        format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=options)}
    )
    args.output.mkdir(parents=True, exist_ok=True)
    summaries = []
    for item in json.loads(args.manifest.read_text()):
        source = Path(item["source"])
        if not source.is_file():
            raise ValueError("Source must be an existing local file")
        started = time.perf_counter()
        result = converter.convert(source)
        document = result.document
        (args.output / (item["sample_id"] + ".json")).write_text(
            json.dumps(document.export_to_dict(), indent=2)
        )
        (args.output / (item["sample_id"] + ".md")).write_text(document.export_to_markdown())
        summaries.append(
            {
                "sample_id": item["sample_id"],
                "status": str(result.status),
                "seconds": time.perf_counter() - started,
                "tables": len(document.tables),
            }
        )
        print(json.dumps(summaries[-1]), flush=True)
    (args.output / "summary.json").write_text(json.dumps(summaries, indent=2))


if __name__ == "__main__":
    main()
