#!/usr/bin/env python3
"""V53 one-time migration: backfill deposits.proof_filename from proof text.

Existing deposits store the proof image path inside the `proof` TEXT column
as "صورة: /uploads/proof/<filename>". This script extracts the filename and
writes it to the new `proof_filename` column so the IDOR-safe DB ownership
check works for old deposits too.

Usage:
    python tools/migrate_proof_filenames.py

Safe to run multiple times (skips rows that already have proof_filename set).
"""

import os
import re
import sys

# Allow running from project root or tools/ directory
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from database import db_conn

# Pattern matches "صورة: /uploads/proof/<filename>" in the proof text
PROOF_PATH_RE = re.compile(r"/uploads/proof/([^\s]+)")


def migrate():
    migrated = 0
    skipped = 0
    no_file = 0

    with db_conn() as conn:
        # Only process deposits that have no proof_filename yet but have proof text
        rows = conn.execute(
            "SELECT id, proof FROM deposits WHERE proof_filename IS NULL AND proof IS NOT NULL AND proof != ''"
        ).fetchall()

        for row in rows:
            deposit_id = row["id"]
            proof_text = row["proof"] or ""

            match = PROOF_PATH_RE.search(proof_text)
            if not match:
                no_file += 1
                continue

            filename = match.group(1).strip()
            if not filename:
                no_file += 1
                continue

            conn.execute(
                "UPDATE deposits SET proof_filename=? WHERE id=?",
                (filename, deposit_id),
            )
            migrated += 1

        conn.commit()

    print(f"Migration complete:")
    print(f"  Migrated: {migrated} deposits (proof_filename backfilled)")
    print(f"  Skipped:  {skipped} deposits (already had proof_filename)")
    print(f"  No file:  {no_file} deposits (no image path in proof text)")


if __name__ == "__main__":
    migrate()
