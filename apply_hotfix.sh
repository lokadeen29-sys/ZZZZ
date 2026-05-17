#!/bin/bash
# V49 Currency Hotfix — apply in-place over an already-deployed V49.
# Usage:
#   1. scp tecnogems_V49_HOTFIX_currency.zip root@server:/root/
#   2. scp apply_hotfix.sh root@server:/root/
#   3. ssh root@server
#   4. chmod +x /root/apply_hotfix.sh && /root/apply_hotfix.sh /root/tecnogems_V49_HOTFIX_currency.zip
#
# The script backs up the 4 files being modified, extracts the hotfix,
# restarts the service, and rolls back if the smoke test fails.

set -u
HOTFIX_ZIP="${1:-}"
if [ -z "$HOTFIX_ZIP" ] || [ ! -f "$HOTFIX_ZIP" ]; then
    echo "Usage: $0 /path/to/tecnogems_V49_HOTFIX_currency.zip"
    exit 1
fi

TS=$(date +%Y%m%d_%H%M%S)
BACKUP_DIR="/root/hotfix_backup_$TS"
PROJECT="/root/project"

echo "=== V49 Currency Hotfix ($TS) ==="

# 1) Verify zip
if ! unzip -tq "$HOTFIX_ZIP" >/dev/null 2>&1; then
    echo "!!! zip is corrupt"; exit 1
fi
echo "[1/7] zip OK"

# 2) Backup files we are about to overwrite
mkdir -p "$BACKUP_DIR/templates/admin"
cp "$PROJECT/database.py"                       "$BACKUP_DIR/database.py"
cp "$PROJECT/templates/admin/user_detail.html"  "$BACKUP_DIR/templates/admin/user_detail.html"
cp "$PROJECT/templates/admin/deposits.html"     "$BACKUP_DIR/templates/admin/deposits.html"
cp "$PROJECT/templates/wallet_transactions.html" "$BACKUP_DIR/templates/wallet_transactions.html"
echo "[2/7] backup -> $BACKUP_DIR"

# 3) Extract hotfix over project
UNPACK=/tmp/v49_hotfix_unpack_$TS
mkdir -p "$UNPACK"
unzip -oq "$HOTFIX_ZIP" -d "$UNPACK"
rsync -a "$UNPACK/tecnogems/" "$PROJECT/"
rm -rf "$UNPACK"
echo "[3/7] hotfix applied"

# 4) Smoke-test imports BEFORE restarting
SMOKE=$(/root/project/.venv/bin/python -c "import database; from wsgi import app; print('OK')" 2>&1)
if ! echo "$SMOKE" | grep -q "OK"; then
    echo "!!! Smoke test FAILED - rolling back"
    echo "$SMOKE"
    cp "$BACKUP_DIR/database.py"                        "$PROJECT/database.py"
    cp "$BACKUP_DIR/templates/admin/user_detail.html"   "$PROJECT/templates/admin/user_detail.html"
    cp "$BACKUP_DIR/templates/admin/deposits.html"      "$PROJECT/templates/admin/deposits.html"
    cp "$BACKUP_DIR/templates/wallet_transactions.html" "$PROJECT/templates/wallet_transactions.html"
    echo ">>> ROLLED BACK (no restart yet, no downtime)"
    exit 1
fi
echo "[4/7] smoke test OK"

# 5) Restart service (short downtime, a few seconds)
systemctl restart game-topup
sleep 4
echo "[5/7] service restarted"

# 6) Health check
if curl -fsSI http://127.0.0.1:5000/ >/dev/null 2>&1; then
    echo "[6/7] health check OK"
else
    echo "!!! Health check FAILED - rolling back"
    journalctl -u game-topup --no-pager -n 25
    cp "$BACKUP_DIR/database.py"                        "$PROJECT/database.py"
    cp "$BACKUP_DIR/templates/admin/user_detail.html"   "$PROJECT/templates/admin/user_detail.html"
    cp "$BACKUP_DIR/templates/admin/deposits.html"      "$PROJECT/templates/admin/deposits.html"
    cp "$BACKUP_DIR/templates/wallet_transactions.html" "$PROJECT/templates/wallet_transactions.html"
    systemctl restart game-topup
    sleep 3
    systemctl status game-topup --no-pager | head -8
    echo ">>> ROLLED BACK"
    exit 1
fi

# 7) Done
echo "[7/7] >>> HOTFIX APPLIED SUCCESSFULLY <<<"
echo ""
echo "Backup kept at: $BACKUP_DIR"
echo "To roll back manually later:"
echo "  cp -r $BACKUP_DIR/* /root/project/ && systemctl restart game-topup"
echo ""
systemctl status game-topup --no-pager | head -10
