#!/usr/bin/env bash
# V53: hourly backup using SQLite's live-safe `.backup` command.
# This is SAFE while the app is running (no WAL corruption, no locks blocking).
set -euo pipefail

SRC_DB="${SRC_DB:-site.db}"
BACKUP_DIR="${BACKUP_DIR:-/tmp/tecnogems-backups}"
S3_BUCKET="${BACKUP_S3_BUCKET:?Set BACKUP_S3_BUCKET}"
S3_ENDPOINT="${BACKUP_S3_ENDPOINT:-}"   # for R2/MinIO, leave empty for AWS
TS=$(date -u +%Y%m%dT%H%M%SZ)

mkdir -p "$BACKUP_DIR"

# 1. Consistent snapshot (uses SQLite online backup API)
TMP_DB="$BACKUP_DIR/site-$TS.db"
sqlite3 "$SRC_DB" ".backup '$TMP_DB'"

# 2. Integrity check — abort if corrupted
if ! sqlite3 "$TMP_DB" "PRAGMA integrity_check;" | grep -q "^ok$"; then
    echo "CORRUPT backup, aborting" >&2
    rm -f "$TMP_DB"
    exit 1
fi

# 3. Compress
gzip -9 "$TMP_DB"
OUT_FILE="$TMP_DB.gz"

# 4. Upload (R2 / S3)
AWS_ARGS=(--no-progress)
if [ -n "$S3_ENDPOINT" ]; then
    AWS_ARGS+=(--endpoint-url "$S3_ENDPOINT")
fi

aws s3 cp "${AWS_ARGS[@]}" "$OUT_FILE" "s3://$S3_BUCKET/hourly/$(basename "$OUT_FILE")" \
    --sse AES256

# 5. Uploads directory (receipts — much smaller, weekly is enough)
if [ "$(date +%H)" = "03" ] && [ "$(date +%u)" = "7" ]; then
    tar czf "$BACKUP_DIR/uploads-$TS.tar.gz" data/uploads/
    aws s3 cp "${AWS_ARGS[@]}" "$BACKUP_DIR/uploads-$TS.tar.gz" \
        "s3://$S3_BUCKET/weekly-uploads/" --sse AES256
    rm -f "$BACKUP_DIR/uploads-$TS.tar.gz"
fi

# 6. Local cleanup — keep only last 24 hours locally
find "$BACKUP_DIR" -name "site-*.db.gz" -mtime +1 -delete

# 7. Heartbeat ping — notify Dead Man's Snitch / healthchecks.io that backup succeeded
if [ -n "${BACKUP_HEARTBEAT_URL:-}" ]; then
    curl -fsS -m 10 --retry 3 "$BACKUP_HEARTBEAT_URL" >/dev/null || true
fi

echo "OK: uploaded $(basename "$OUT_FILE") to s3://$S3_BUCKET/hourly/"
