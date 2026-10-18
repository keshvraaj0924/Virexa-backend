"""CI diagnostic of the SAME restrictions using only a built-in synthetic PDF.

Never accepts document input. Production suppresses model/exception logs; this
separate synthetic-only executable may show a traceback to diagnose host setup.
It does not disable or relax any parser security boundary.
"""
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tests"))
sys.path.insert(0, str(ROOT / "app/domain/documents"))
from layout_subprocess import _landlock, _parse, _restrict  # noqa: E402
from test_layout_smoke import synthetic_pdf  # noqa: E402

if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("Supply the provisioned asset root only.")
    data = synthetic_pdf()
    with tempfile.TemporaryDirectory(prefix="layout-synthetic-diagnostic-") as temporary:
        scratch = Path(temporary)
        os.environ.update(HOME=temporary, TMPDIR=temporary, HF_HOME=temporary,
                          HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1",
                          OMP_NUM_THREADS="2", MKL_NUM_THREADS="2", OPENBLAS_NUM_THREADS="2",
                          HF_HUB_DISABLE_TELEMETRY="1", DO_NOT_TRACK="1")
        _restrict()
        _landlock(Path(sys.argv[1]), scratch)
        result = _parse(data, Path(sys.argv[1]))
        print("Synthetic same-sandbox conversion succeeded; pages:", result["page_count"])
