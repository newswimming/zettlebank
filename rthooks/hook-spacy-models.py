"""PyInstaller runtime hook — spaCy model path resolution.

spaCy's ``get_package_path(name)`` calls ``importlib.metadata.packages_distributions()``
and then resolves the install path via ``importlib.util.find_spec(name)``.  Inside a
frozen binary both calls may fail because the model package isn't importable through
the normal Python path — it lives flat inside ``sys._MEIPASS``.

This hook patches ``spacy.util.get_package_path`` before any model is loaded so it
returns ``Path(sys._MEIPASS) / name`` when running frozen.  For development (non-frozen)
the original function is left untouched.
"""

import sys
from pathlib import Path


def _frozen_get_package_path(name: str) -> Path:  # type: ignore[return]
    """Return the bundled model directory inside the PyInstaller temp tree."""
    candidate = Path(sys._MEIPASS) / name  # type: ignore[attr-defined]
    if candidate.exists():
        return candidate
    # Hard fail with a clear message rather than a cryptic AttributeError deep in spaCy.
    raise OSError(
        f"[ZettleBank frozen] spaCy model '{name}' not found at {candidate}.  "
        "Re-run the PyInstaller build with the model data included in datas."
    )


# Only patch when running as a frozen executable.
if hasattr(sys, "_MEIPASS"):
    try:
        import spacy.util  # noqa: PLC0415
        spacy.util.get_package_path = _frozen_get_package_path
    except ImportError:
        pass  # spaCy not yet importable at this point — will be resolved at import time
