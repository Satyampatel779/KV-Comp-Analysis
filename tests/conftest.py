"""Make the flat ``scripts/`` modules importable from tests.

The API imports its siblings flatly (e.g. ``from comp_ranking_service import ...``)
and runs via ``uvicorn --app-dir scripts``. Mirror that here so unit tests can
import the engine without a package install.
"""

import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))
