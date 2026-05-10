from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from investment_automation.db import restore_sqlite_database
from investment_automation.settings import Settings


def _default_database_path() -> Path:
    settings = Settings.from_env()
    settings.validate()
    return settings.database_path


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Restore the trading SQLite database from a backup file."
    )
    parser.add_argument(
        "--database",
        type=Path,
        help="Path to the destination SQLite database. Defaults to app settings.",
    )
    parser.add_argument(
        "--input", type=Path, required=True, help="Path to the backup SQLite file to restore from."
    )
    return parser


def main(argv: Optional[list[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    destination_path = args.database or _default_database_path()
    restored = restore_sqlite_database(args.input, destination_path)
    print(restored)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
