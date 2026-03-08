# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for ZettleBank server executable.

Packages
--------
* bootstrapper.py     — entry point; imports server as a module
* server.py           — FastAPI application (bundled as importable module)
* shadowbox.py        — ChromaDB ShadowBox (at project root)
* en_core_web_sm      — spaCy model (CPU-only; en_core_web_trf unavailable on Py 3.13/Windows)
* chromadb migrations — SQL migration files required at runtime
* spaCy lang data     — per-language tokeniser data shipped with spaCy itself

Runtime hook
------------
rthooks/hook-spacy-models.py patches spacy.util.get_package_path so that model data
is resolved from sys._MEIPASS instead of the normal importlib path, which fails inside
a frozen binary.

Build
-----
    pip install pyinstaller
    pyinstaller build.spec

The output executable is written to  dist/zettlebank-server/zettlebank-server.exe
(one-folder mode — preferred over one-file for large ML binaries because it avoids
the slow extraction on every launch).
"""

import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Resolve bundled data paths relative to this spec file's directory.
# PyInstaller sets SPECPATH to the directory containing the .spec file.
# ---------------------------------------------------------------------------

SPEC_DIR   = Path(SPECPATH)                               # project root

# Resolve site-packages cross-platform (works on Windows venv AND Linux venv).
import sysconfig as _sc
VENV_SP = Path(_sc.get_path("purelib"))

# spaCy en_core_web_sm — the versioned data subdirectory
SM_ROOT    = VENV_SP / "en_core_web_sm"
SM_DATA    = next(SM_ROOT.glob("en_core_web_sm-*"), SM_ROOT)  # versioned dir if present

# ChromaDB SQL migration files
CHROMA_MIG = VENV_SP / "chromadb" / "migrations"

# spaCy built-in lang data (tokeniser tables, stop words, etc.)
SPACY_LANG = VENV_SP / "spacy" / "lang"

# ---------------------------------------------------------------------------
# Data files — (source_path, dest_dir_inside_bundle)
# ---------------------------------------------------------------------------

datas = [
    # spaCy model
    (str(SM_DATA), "en_core_web_sm"),

    # ChromaDB migrations
    (str(CHROMA_MIG), "chromadb/migrations"),

    # spaCy lang data (tokeniser tables for all languages bundled with spaCy)
    (str(SPACY_LANG), "spacy/lang"),

    # server.py is not auto-discovered because bootstrapper imports it by name,
    # not via a relative path — include it explicitly so uvicorn can find it.
    (str(SPEC_DIR / "server.py"), "."),
]

# ---------------------------------------------------------------------------
# Hidden imports — modules loaded dynamically that PyInstaller's static
# analyser cannot detect.
# ---------------------------------------------------------------------------

hiddenimports = [
    # FastAPI / Starlette internals
    "uvicorn.logging",
    "uvicorn.loops",
    "uvicorn.loops.auto",
    "uvicorn.protocols",
    "uvicorn.protocols.http",
    "uvicorn.protocols.http.auto",
    "uvicorn.protocols.websockets",
    "uvicorn.protocols.websockets.auto",
    "uvicorn.lifespan",
    "uvicorn.lifespan.on",

    # spaCy model entry-point registration
    "en_core_web_sm",

    # spaCy pipeline components (registered via entry points)
    "spacy.pipeline.tok2vec",
    "spacy.pipeline.tagger",
    "spacy.pipeline.dep_parser",
    "spacy.pipeline.senter",
    "spacy.pipeline.ner",
    "spacy.pipeline.lemmatizer",
    "spacy.pipeline.attribute_ruler",
    "spacy.lang.en",
    "spacy.lang.en.stop_words",

    # NetworkX graph algorithms
    "networkx.algorithms.community",
    "networkx.algorithms.link_analysis",

    # leidenalg + igraph
    "leidenalg",
    "igraph",
    "igraph.vendor",

    # BERTopic internals
    "bertopic._bertopic",
    "bertopic.representation",
    "bertopic.vectorizers",
    "bertopic.dimensionality",
    "bertopic.cluster",
    "bertopic.plotting",

    # sentence-transformers (used by BERTopic default embedding model)
    "sentence_transformers",
    "sentence_transformers.models",

    # scikit-learn dynamic loaders
    "sklearn.utils._cython_blas",
    "sklearn.neighbors.typedefs",
    "sklearn.neighbors._partition_nodes",

    # ChromaDB dynamic loaders
    "chromadb.api.segment",
    "chromadb.segment.impl.manager.local",
    "chromadb.segment.impl.metadata.sqlite",
    "chromadb.segment.impl.vector.local_persistent_hnsw",
    "chromadb.migrations",

    # pydantic v2 validators
    "pydantic.v1",
    "pydantic_core",

    # Ollama library (optional — used by bootstrapper with httpx fallback)
    "ollama",

    # ShadowBox (ChromaDB semantic index) — imported dynamically by server.py
    "shadowbox",

    # httpx transports
    "httpx._transports.default",
    "httpx._transports.asgi",
]

# ---------------------------------------------------------------------------
# Analysis
# ---------------------------------------------------------------------------

block_cipher = None

a = Analysis(
    [str(SPEC_DIR / "bootstrapper.py")],
    pathex=[str(SPEC_DIR), str(SPEC_DIR / "backend")],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[str(SPEC_DIR / "rthooks" / "hook-spacy-models.py")],
    excludes=[
        # Exclude heavy GPU/CUDA packages — ZettleBank runs CPU-only
        "torch.cuda",
        "torchvision",
        "torchaudio",
        "tensorflow",
        "jax",
        # Exclude test frameworks
        "pytest",
        "hypothesis",
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,   # one-folder mode
    name="zettlebank-server",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=True,            # keep console so Ollama pull progress is visible
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="zettlebank-server",
)
