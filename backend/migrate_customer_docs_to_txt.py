"""
Rename generated customer Markdown source documents to .txt.

This migration leaves PDFs and JSON untouched. It updates the documents table
and renames generated_docs/*.md files so customer portals present plain text
source documents instead of Markdown files.
"""

from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent.parent
DB_PATH = BASE_DIR / "sosfiler.db"
DOCS_DIR = BASE_DIR / "generated_docs"


def table_exists(conn: sqlite3.Connection, table_name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
        (table_name,),
    ).fetchone()
    return row is not None


def migrate(dry_run: bool = False) -> dict:
    renamed = []
    for path in DOCS_DIR.glob("**/*.md"):
        target = path.with_suffix(".txt")
        renamed.append((path, target))
        if not dry_run:
            if target.exists():
                path.unlink()
            else:
                path.rename(target)

    db_updates = []
    if DB_PATH.exists():
        conn = sqlite3.connect(str(DB_PATH))
        conn.row_factory = sqlite3.Row
        try:
            if not table_exists(conn, "documents"):
                rows = []
            else:
                rows = conn.execute(
                    "SELECT id, filename, file_path FROM documents WHERE filename LIKE '%.md' OR file_path LIKE '%.md'"
                ).fetchall()
            for row in rows:
                filename = row["filename"]
                file_path = row["file_path"]
                new_filename = filename[:-3] + ".txt" if filename.endswith(".md") else filename
                new_file_path = file_path[:-3] + ".txt" if file_path.endswith(".md") else file_path
                db_updates.append((row["id"], new_filename, new_file_path))
                if not dry_run:
                    conn.execute(
                        "UPDATE documents SET filename = ?, file_path = ?, format = 'text' WHERE id = ?",
                        (new_filename, new_file_path, row["id"]),
                    )
            if not dry_run:
                conn.commit()
        finally:
            conn.close()

    return {
        "dry_run": dry_run,
        "renamed_files": len(renamed),
        "updated_document_rows": len(db_updates),
        "files": [{"from": str(src), "to": str(dst)} for src, dst in renamed],
        "document_rows": [
            {"id": row_id, "filename": filename, "file_path": file_path}
            for row_id, filename, file_path in db_updates
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Rename generated customer .md documents to .txt.")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    result = migrate(dry_run=args.dry_run)
    print(f"Renamed files: {result['renamed_files']}")
    print(f"Updated document rows: {result['updated_document_rows']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
