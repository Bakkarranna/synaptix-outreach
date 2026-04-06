"""Move db.sqlite3 from project root to data/."""
from pathlib import Path


def forward(root_dir: Path):
    data_dir = root_dir / "data"
    data_dir.mkdir(exist_ok=True)

    old = root_dir / "db.sqlite3"
    new = data_dir / "db.sqlite3"
    if old.exists() and not new.exists():
        old.rename(new)
