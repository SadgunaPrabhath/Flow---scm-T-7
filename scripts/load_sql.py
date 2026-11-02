"""Execute a .sql file against the configured MySQL/MariaDB server.

Usage
-----
    python scripts/load_sql.py sql/schema.sql sql/seed_data.sql

The file is split on semicolons at end-of-line (good enough for our scripts,
which contain no stored procedures) and each statement is executed with the
credentials from the environment (.env).
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import mysql.connector                                    # noqa: E402

from config import config                                  # noqa: E402


def split_statements(sql: str) -> list[str]:
    """Split on semicolons that are outside of quoted strings and comments.

    A tiny hand-written scanner is enough here: the generated scripts contain
    no stored procedures or DELIMITER blocks.
    """
    statements: list[str] = []
    buffer: list[str] = []
    in_single = False
    in_double = False
    escaped = False
    i = 0
    lines = sql.splitlines(keepends=True)
    text = "".join(lines)
    while i < len(text):
        ch = text[i]
        nxt = text[i + 1] if i + 1 < len(text) else ""
        # comments: skip to end of line, but only when not inside a string
        if not in_single and not in_double:
            if ch == "-" and nxt == "-":
                while i < len(text) and text[i] != "\n":
                    i += 1
                continue
            if ch == "#":
                while i < len(text) and text[i] != "\n":
                    i += 1
                continue
        if escaped:
            buffer.append(ch)
            escaped = False
        elif ch == "\\" and (in_single or in_double):
            buffer.append(ch)
            escaped = True
        elif ch == "'" and not in_double:
            in_single = not in_single
            buffer.append(ch)
        elif ch == '"' and not in_single:
            in_double = not in_double
            buffer.append(ch)
        elif ch == ";" and not in_single and not in_double:
            statement = "".join(buffer).strip()
            if statement:
                statements.append(statement)
            buffer = []
        else:
            buffer.append(ch)
        i += 1
    tail = "".join(buffer).strip()
    if tail:
        statements.append(tail)
    return statements


def load_sql_file(path: str | Path, *, verbose: bool = True) -> int:
    path = Path(path)
    sql = path.read_text(encoding="utf-8")
    statements = split_statements(sql)

    # schema.sql creates the database, so connect without a database first
    kwargs = config.db_kwargs()
    kwargs.pop("database", None)
    conn = mysql.connector.connect(**kwargs)
    executed = 0
    try:
        cur = conn.cursor()
        for statement in statements:
            try:
                cur.execute(statement)
                # drain any result sets (SELECT statements inside seed files)
                if cur.with_rows:
                    cur.fetchall()
                executed += 1
            except mysql.connector.Error as exc:
                print(f"[FAIL] {exc}\n  statement: {statement[:180]}...", file=sys.stderr)
                raise
        conn.commit()
        cur.close()
    finally:
        conn.close()
    if verbose:
        print(f"{path.name}: {executed} statements executed")
    return executed


if __name__ == "__main__":
    targets = sys.argv[1:] or ["sql/schema.sql", "sql/seed_data.sql"]
    for target in targets:
        load_sql_file(ROOT / target if not Path(target).is_absolute() else target)
