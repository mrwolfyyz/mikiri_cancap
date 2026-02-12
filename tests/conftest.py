"""
Test configuration and shared fixtures.

Adds gcp/shared/ and gcp/functions/api_gateway/ to sys.path so that
shared utility modules and api_gateway validation functions can be
imported directly in tests.

Note: Each Cloud Function has its own main.py and a local copy of
shared modules (retry_utils, chat_handler_base, etc.).  Tests that need
to import a function's main module should use the ``load_function_module``
helper below, which temporarily puts the function directory first on
sys.path so that intra-function imports resolve correctly.
"""

import importlib.util
import sys
from pathlib import Path

# Root of the repository
REPO_ROOT = Path(__file__).resolve().parent.parent

# Add shared utilities to import path
sys.path.insert(0, str(REPO_ROOT / "gcp" / "shared"))

# Add api_gateway to import path (for testing validation helpers via exec)
sys.path.insert(0, str(REPO_ROOT / "gcp" / "functions" / "api_gateway"))


def load_function_module(function_name: str, module_alias: str):
    """Load a Cloud Function's main.py as a uniquely-named module.

    Temporarily inserts the function directory at the front of sys.path so
    that function-local imports (e.g. ``from retry_utils import ...``) resolve
    to the copy bundled with that function rather than another function's copy.

    Args:
        function_name: Directory name under gcp/functions/ (e.g. "aggregator").
        module_alias: Unique name to register in sys.modules (e.g. "aggregator_main").

    Returns:
        The loaded module object.
    """
    fn_dir = str(REPO_ROOT / "gcp" / "functions" / function_name)
    main_py = str(REPO_ROOT / "gcp" / "functions" / function_name / "main.py")

    # Put the function dir first so its local modules win
    sys.path.insert(0, fn_dir)
    try:
        spec = importlib.util.spec_from_file_location(module_alias, main_py)
        mod = importlib.util.module_from_spec(spec)
        sys.modules[module_alias] = mod
        spec.loader.exec_module(mod)
    finally:
        # Remove the function dir to avoid polluting later imports
        try:
            sys.path.remove(fn_dir)
        except ValueError:
            pass

    return mod


# ---------------------------------------------------------------------------
# Golden-set CLI options (must live in conftest.py for pytest to register them)
# ---------------------------------------------------------------------------


def pytest_addoption(parser):
    parser.addoption("--golden-url", action="store", default=None, help="API Gateway base URL")
    parser.addoption("--golden-token", action="store", default=None, help="Firebase ID token")
    parser.addoption(
        "--golden-save-reports",
        action="store_true",
        default=False,
        help="Save markdown reports to tests/golden_reports/ for inspection",
    )
    parser.addoption(
        "--golden-workflow",
        action="store",
        default="skiptrace",
        choices=["skiptrace", "origination"],
        help="Workflow to test: 'skiptrace' or 'origination' (default: skiptrace)",
    )
