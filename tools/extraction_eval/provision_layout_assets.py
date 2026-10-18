"""Explicit deployment-time provisioning, never called by API or parser requests.

Run in a trusted build environment with network access, then mount the resulting
assets root read-only in the parser service. Models are official, pinned commits.
The integrity manifest is operator trust material, not a runtime download permit.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from download_models import MODELS
from huggingface_hub import snapshot_download

REQUIRED_FILES = (
    "ds4sd--docling-layout-heron/config.json",
    "ds4sd--docling-layout-heron/preprocessor_config.json",
    "ds4sd--docling-layout-heron/model.safetensors",
    "ds4sd--docling-models/model_artifacts/tableformer/accurate/tm_config.json",
    "ds4sd--docling-models/model_artifacts/tableformer/accurate/tableformer_accurate.safetensors",
)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    root = args.output.resolve()
    root.mkdir(parents=True, exist_ok=True)
    for model in MODELS:
        snapshot_download(
            repo_id=model["repo_id"],
            revision=model["revision"],
            allow_patterns=model["allow_patterns"],
            local_dir=root / model["repo_id"].replace("/", "--"),
        )
    manifest = {}
    for relative in REQUIRED_FILES:
        path = root / relative
        if not path.resolve().is_relative_to(root):
            raise ValueError("Model file is outside the provisioned root")
        with path.open("rb") as stream:
            manifest[relative] = hashlib.file_digest(stream, "sha256").hexdigest()
    (root / "model-lock.json").write_text(json.dumps(MODELS, indent=2))
    (root / "layout-assets-sha256.json").write_text(json.dumps(manifest, indent=2))
    print("Pinned layout model assets and integrity manifest provisioned.")


if __name__ == "__main__":
    main()
