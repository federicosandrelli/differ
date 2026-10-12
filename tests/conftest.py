"""Shared set-up: the modules import from the repo root, and the build cache lives in a temporary directory."""

import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))
os.environ.setdefault("DIFF_REVIEW_CACHE", tempfile.mkdtemp(prefix="diff-review-tests-"))
