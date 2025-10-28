"""Test configuration and fixtures."""

import sys
from pathlib import Path

# Ensure the src/ directory is on sys.path so `import arbitrage` works without installation.
PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_PATH = PROJECT_ROOT / "src"
if SRC_PATH.exists():
    sys.path.insert(0, str(SRC_PATH))
