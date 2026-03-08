"""ZettleBank backend bootstrapper.

Startup sequence
----------------
1. Ping the local Ollama service.  Exit with a clear message if it is not
   running so users don't get an opaque connection-refused error at
   analysis time.
2. Check that every required model is installed.  If one is missing, run
   ``ollama pull <model>`` automatically — the same pull that the user
   would otherwise have to discover and run manually.
3. Hand off to uvicorn.

Works in two modes
------------------
* **Development** : ``python bootstrapper.py``
  Reads OLLAMA_BASE_URL / OLLAMA_MODEL / NARRATIVE_AUDITOR_MODEL from the
  environment (or .env) and spawns the FastAPI app via uvicorn.

* **Frozen executable** (PyInstaller) : ``zettlebank-server.exe``
  The same logic runs, but uvicorn.run() is called directly on the
  imported server.app object, which avoids the string-based import that
  uvicorn uses with --reload (incompatible with frozen binaries).

Ollama library usage
--------------------
The script uses the ``ollama`` Python library for model discovery when it
is available (``pip install ollama``).  If the library is absent it falls
back to the Ollama REST API via httpx (already required by server.py), so
the bootstrapper has no hard dependency on the ollama package itself.
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("bootstrapper")

# ---------------------------------------------------------------------------
# Configuration (env-overridable, same keys as server.py)
# ---------------------------------------------------------------------------

# Load .env before reading env vars so local overrides work in dev mode.
try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent / ".env")
except ImportError:
    pass

OLLAMA_BASE_URL        = os.environ.get("OLLAMA_BASE_URL",         "http://localhost:11434")
OLLAMA_MODEL           = os.environ.get("OLLAMA_MODEL",            "llama3.2")
NARRATIVE_AUDITOR_MODEL = os.environ.get("NARRATIVE_AUDITOR_MODEL", "llama3.1")

# Models that must be present before the server is allowed to start.
REQUIRED_MODELS: list[str] = [m for m in (OLLAMA_MODEL, NARRATIVE_AUDITOR_MODEL) if m]

HOST = os.environ.get("ZETTLEBANK_HOST", "127.0.0.1")
PORT = int(os.environ.get("ZETTLEBANK_PORT", "8000"))

# ---------------------------------------------------------------------------
# This import is intentionally at module level.
#
# In a PyInstaller frozen binary, static analysis must see ``import server``
# here so PyInstaller bundles server.py as a Python module.  The import is
# otherwise harmless — server.py's module-level code only initialises the
# FastAPI app object and reads env vars; no heavy I/O happens until uvicorn
# calls the on_startup handler.
# ---------------------------------------------------------------------------
import server as _server_module  # noqa: E402  (must follow dotenv load)

# ---------------------------------------------------------------------------
# Ollama model discovery
# ---------------------------------------------------------------------------


def _list_via_ollama_library() -> list[str]:
    """Return installed model base names using the ollama Python library.

    Uses ``ollama.list()`` which pings the local daemon and returns a
    ListResponse.  We strip the ``:tag`` suffix (e.g. ``:latest``) because
    model names are specified without tags in OLLAMA_MODEL.

    Raises:
        ImportError: if the ollama package is not installed.
        Exception:   propagated as-is for the caller to handle.
    """
    import ollama  # type: ignore[import]

    response = ollama.list()
    # ollama >= 0.3: response is a ListResponse dataclass with .models
    # ollama <  0.3: response is a plain dict with "models" key
    models_raw = getattr(response, "models", None)
    if models_raw is None and isinstance(response, dict):
        models_raw = response.get("models", [])

    names: list[str] = []
    for m in (models_raw or []):
        # Each entry is a Model dataclass (>=0.3) or a dict (<0.3)
        raw_name = getattr(m, "model", None) or (
            m.get("model") or m.get("name") if isinstance(m, dict) else None
        )
        if raw_name:
            names.append(raw_name.split(":")[0])
    return names


def _list_via_http() -> list[str]:
    """Fallback: call GET /api/tags on the Ollama REST API using httpx."""
    import httpx  # already in requirements.txt

    r = httpx.get(f"{OLLAMA_BASE_URL}/api/tags", timeout=5)
    r.raise_for_status()
    return [
        m.get("name", "").split(":")[0]
        for m in r.json().get("models", [])
        if m.get("name")
    ]


def get_installed_models() -> list[str]:
    """Return base names of models installed in the local Ollama daemon.

    Tries the ollama Python library first; falls back to direct HTTP if the
    library is not installed.  Raises on network error (caller handles).
    """
    try:
        names = _list_via_ollama_library()
        logger.debug("Model list obtained via ollama library: %s", names)
        return names
    except ImportError:
        logger.warning(
            "ollama Python library not installed — using REST API fallback "
            "(run 'pip install ollama' for richer integration)"
        )
        names = _list_via_http()
        logger.debug("Model list obtained via HTTP: %s", names)
        return names


# ---------------------------------------------------------------------------
# Model pull
# ---------------------------------------------------------------------------


def pull_model(model: str) -> None:
    """Pull *model* by running ``ollama pull <model>`` as a subprocess.

    Using subprocess (rather than the Python library's pull()) keeps
    progress output streaming to the terminal, which is important for
    multi-gigabyte models.

    Exits the process with code 1 on failure so the server never starts
    with a missing model.
    """
    logger.info("Pulling '%s' — this may take several minutes …", model)
    result = subprocess.run(["ollama", "pull", model], check=False)
    if result.returncode != 0:
        logger.error(
            "ollama pull '%s' exited with code %d.  "
            "Check that Ollama is running and that the model name is correct.",
            model,
            result.returncode,
        )
        sys.exit(1)
    logger.info("Model '%s' is ready.", model)


# ---------------------------------------------------------------------------
# Pre-flight checks
# ---------------------------------------------------------------------------


def preflight() -> None:
    """Run all checks before starting the server.

    1. Verify Ollama is reachable.
    2. Ensure every model in REQUIRED_MODELS is installed.
    """
    logger.info("Pre-flight: checking Ollama at %s …", OLLAMA_BASE_URL)

    try:
        installed = get_installed_models()
    except Exception as exc:
        logger.error(
            "Cannot reach Ollama at %s: %s\n"
            "\n"
            "  Make sure Ollama is running before starting ZettleBank:\n"
            "    ollama serve\n"
            "\n"
            "  Download Ollama from: https://ollama.com",
            OLLAMA_BASE_URL,
            exc,
        )
        sys.exit(1)

    if installed:
        logger.info("Installed models: %s", ", ".join(installed))
    else:
        logger.info("No models installed yet.")

    for model in REQUIRED_MODELS:
        if model in installed:
            logger.info("  ✓  %s", model)
        else:
            logger.warning("  ✗  %s — not found, pulling now …", model)
            pull_model(model)


# ---------------------------------------------------------------------------
# Server entry point
# ---------------------------------------------------------------------------


def run_server() -> None:
    """Start the uvicorn ASGI server.

    Development mode
    ~~~~~~~~~~~~~~~~
    ``uvicorn.run("server:app", reload=False)`` — string-based import works
    when running from source.

    Frozen mode (PyInstaller)
    ~~~~~~~~~~~~~~~~~~~~~~~~~
    ``uvicorn.run(app_object)`` — passes the already-imported app object
    directly.  String-based imports fail in frozen executables because
    uvicorn's internal importlib call cannot reach the bootstrapper's
    sys.path in all edge cases.
    """
    import uvicorn

    is_frozen = hasattr(sys, "_MEIPASS")

    logger.info(
        "Starting ZettleBank server on http://%s:%d  (frozen=%s)",
        HOST,
        PORT,
        is_frozen,
    )

    if is_frozen:
        # In a frozen binary, pass the already-imported app object directly.
        uvicorn.run(
            _server_module.app,
            host=HOST,
            port=PORT,
            reload=False,
            log_level="info",
        )
    else:
        uvicorn.run(
            "server:app",
            host=HOST,
            port=PORT,
            reload=False,
            log_level="info",
        )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    preflight()
    run_server()
