# Local extraction evaluation

This source tree contains no customer fixtures. Private source documents, renders,
annotations, and results must live in a separate access-controlled directory.
Do not commit them or send them to a remote model service.

## Baseline

`python evaluate.py --manifest /private/ground_truth.json --output /private/results.json`

The manifest is a JSON array. Each item has `sample_id`, `source`,
`review_status`, `fields` (`name`, `value`, one-based `page`), and optional
`tables` (`name`, one-based `page`, `rows` as arrays of expected cell strings).

The baseline extracts native PDF text using PyMuPDF and ruled tables with
pdfplumber's defaults. It measures selected target-string retention on the
correct page and exact annotated row recovery after whitespace normalization.
It exports candidate source boxes in PDF points with top-left origin.
It does **not** assign labels to values, disambiguate repeated values, validate
business semantics, or measure general extraction accuracy. Native text
extraction is not OCR. Empty cells, leading zeros, punctuation and case matter.

Annotations must be reviewed against rendered pages. An assistant visual review
is not independent human approval; record both states truthfully. Review dates,
units, multiple totals, source defects, column mapping, and clipping separately.
Keep genuine scans, handwritten documents, and artificially rasterized digital
pages in separate benchmark sets. Small curated examples are diagnostic cases,
not production accuracy estimates.

## Validation

`python -m unittest -v`

Dependencies are PyMuPDF and pdfplumber from reputable package sources. Record
actual package versions with each run. Docling is a separate optional engine;
a failed installation or model download must not be recorded as a scored run.

## Optional offline Docling run

The reviewed run uses Docling 2.60.1, torch 2.9.0+cpu and torchvision 0.24.0+cpu.
The full resolved environment is recorded in requirements-docling-lock.txt.
Install the two CPU wheels from the official PyTorch CPU index separately;
then resolve Docling from PyPI, avoiding a mixed-index dependency confusion.

Run download_models.py separately with no document paths to fetch the pinned
Heron and accurate TableFormer artifacts into a cache. Then run:

`python run_docling.py --manifest /private/ground_truth.json --models /cache/models --output /private/docling`

This configuration uses native text and layout/table models, not OCR, for digital
PDFs. It exports Docling JSON with provenance and Markdown. It sets offline model
loading, disables remote services and external plugins, and uses four CPU threads.
Heron weights use safetensors; the installed TableFormer loader explicitly uses
`torch.load(weights_only=True)`. No `trust_remote_code` is enabled. Official model
cards: https://huggingface.co/docling-project/docling-layout-heron and
https://huggingface.co/docling-project/docling-models. Model licenses and immutable
revisions are captured by the download script. Licenses should be reviewed again
before production distribution; model/package licensing are separate concerns.

## Identifier loss regression

identifier_guard.py compares grammar-matched source tokens within a caller-
selected source region with the same region's structured output. Its synthetic
seven-code regression deliberately omits one token and expects a review flag;
leading zeros remain significant. These examples contain no customer data.
Use this as one validation guard, not an extraction model or a proof of source
correctness. Retain the original page bbox and witness text with each flag.
Never silently add post-inspection discoveries to an existing score denominator.

## Dependency licensing

The optional native baseline uses PyMuPDF, dual-licensed under AGPL-3.0 or an
Artifex commercial license (https://pypi.org/project/pymupdf/1.26.6/). This harness
is a separate local diagnostic tool; it does not add PyMuPDF to application
runtime dependencies. Review licensing requirements before product integration
or distribution. Do not assume all optional parsers have permissive licenses.

## Separate handwriting/scan evaluation

ocr_metrics.py accepts local reference/hypothesis transcripts and prints only
character/word edit counts, denominators and CER/WER. It collapses Unicode
whitespace but preserves case and punctuation; unit-cost Levenshtein operates on
Unicode code points and whitespace-delimited words. Empty references are rejected,
and insertion-heavy rates may exceed 1. Aggregate by summed edits and reference
lengths, not an unweighted mean of page percentages. Record engine version,
language, page segmentation, image dimensions/resampling, transcript alignment,
reading order, writer diversity and dataset license alongside private results.
A printed-text OCR baseline on historical cursive cannot establish modern HTR
readiness. Do not mix that corpus with digital-PDF diagnostic scores.
