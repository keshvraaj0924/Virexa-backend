"""Download only reviewed official Docling weights; never read customer files."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from huggingface_hub import snapshot_download

MODELS = [
    {
        "repo_id": "ds4sd/docling-layout-heron",
        "revision": "8f39ad3c0b4c58e9c2d2c84a38465abf757272d8",
        "license": "apache-2.0",
        "allow_patterns": [
            "config.json",
            "preprocessor_config.json",
            "model.safetensors",
            "README.md",
        ],
    },
    {
        "repo_id": "ds4sd/docling-models",
        "revision": "fc0f2d45e2218ea24bce5045f58a389aed16dc23",
        "license": "cdla-permissive-2.0; table model card also lists apache-2.0",
        "allow_patterns": ["model_artifacts/tableformer/accurate/*", "README.md", "LICENSE*"],
    },
]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    for model in MODELS:
        snapshot_download(
            repo_id=model["repo_id"],
            revision=model["revision"],
            allow_patterns=model["allow_patterns"],
            local_dir=args.output / model["repo_id"].replace("/", "--"),
        )
    (args.output / "model-lock.json").write_text(json.dumps(MODELS, indent=2))


if __name__ == "__main__":
    main()
