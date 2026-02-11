"""
Test configuration and shared fixtures.

Adds gcp/shared/ and gcp/functions/api_gateway/ to sys.path so that
shared utility modules and api_gateway validation functions can be
imported directly in tests.
"""

import sys
from pathlib import Path

# Root of the repository
REPO_ROOT = Path(__file__).resolve().parent.parent

# Add shared utilities to import path
sys.path.insert(0, str(REPO_ROOT / "gcp" / "shared"))

# Add api_gateway to import path (for testing validation helpers)
sys.path.insert(0, str(REPO_ROOT / "gcp" / "functions" / "api_gateway"))
