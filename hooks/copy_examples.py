"""MkDocs hook: publish example reports that live outside docs/.

Files under examples/ are copied verbatim into the built site so the
Examples chapter can embed and link them without duplicating them in docs/.
"""

import shutil
from pathlib import Path

# (source directory relative to repo root, glob, target directory inside site)
EXAMPLE_FILES = [
    ("examples/abene_agentic", "*.html", "examples/abene_agentic"),
]


def on_post_build(config, **kwargs):
    root = Path(config["config_file_path"]).parent
    site = Path(config["site_dir"])
    for src_dir, pattern, dst_dir in EXAMPLE_FILES:
        target = site / dst_dir
        target.mkdir(parents=True, exist_ok=True)
        for src in (root / src_dir).glob(pattern):
            shutil.copy2(src, target / src.name)
