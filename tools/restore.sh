#!/usr/bin/env bash
# V53: restore a specific backup. Only use AFTER stopping the app.
set -euo pipefail

if [ $# -lt 1 ]; then
    echo "Usage: $0 <backup-s3-key>   # e.g. hourly/site-20260512T080000Z.db.gz"
    exit 1
fi

KEY="$1"
S3_BUCKET="${BACKUP_S3_BUCKET:?Set BACKUP_S3_BUCKET}"
S3_ENDPOINT="${BACKUP_S3_ENDPOINT:-}"
DEST="${DEST:-site.db}"

AWS_ARGS=()
[ -n "$S3_ENDPOINT" ] && AWS_ARGS+=(--endpoint-url "$S3_ENDPOINT")

# 1. Download
TMP=$(mktemp --suffix=.db.gz)
aws s3 cp "${AWS_ARGS[@]}" "s3://$S3_BUCKET/$KEY" "$TMP"

# 2. Decompress + integrity check
gunzip "$TMP"
TMP_DB="${TMP%.gz}"
if ! sqlite3 "$TMP_DB" "PRAGMA integrity_check;" | grep -q "^ok$"; then
    echo "FATAL: downloaded backup is CORRUPT" >&2
    exit 1
fi

# 3. Keep current DB as safety backup (if it exists)
if [ -f "$DEST" ]; then
    mv "$DEST" "$DEST.pre-restore-$(date +%s)"
fi

mv "$TMP_DB" "$DEST"
echo "OK: restored to $DEST"
echo "REMINDER: restart gunicorn + worker_rq now."
