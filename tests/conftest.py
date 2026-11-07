"""Shared set-up: the modules import from the repo root, and the build cache and the installed skill live in temporary directories."""

import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))
os.environ.setdefault("DIFFER_CACHE", tempfile.mkdtemp(prefix="differ-tests-"))
os.environ.setdefault("DIFFER_SKILLS", tempfile.mkdtemp(prefix="differ-tests-skills-"))
os.environ["CLAUDE_CONFIG_DIR"] = tempfile.mkdtemp(prefix="differ-tests-claude-")
