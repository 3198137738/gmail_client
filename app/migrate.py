"""Alembic 命令行入口（不需要 alembic.ini）。

    python -m app.migrate upgrade head
    python -m app.migrate current
    python -m app.migrate revision -m "add xxx" --autogenerate
"""
from __future__ import annotations

import sys

from alembic.config import CommandLine

from .database import alembic_config


def main(argv: list[str] | None = None) -> None:
    cli = CommandLine(prog="python -m app.migrate")
    options = cli.parser.parse_args(argv if argv is not None else sys.argv[1:])
    if not hasattr(options, "cmd"):
        cli.parser.error("too few arguments")
    cli.run_cmd(alembic_config(), options)


if __name__ == "__main__":
    main()
