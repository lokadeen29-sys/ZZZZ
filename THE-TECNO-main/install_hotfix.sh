#!/bin/bash
# V49 Currency + UI Hotfix — one-shot installer.
#
# Usage (run on the server):
#   curl -fsSL https://raw.githubusercontent.com/alexkline3322-byte/tecnogems/hotfix/v49-currency-deposit-500/install_hotfix.sh | bash
#
# If GitHub's CDN is serving an old cached copy, pin to a commit SHA instead:
#   curl -fsSL https://raw.githubusercontent.com/alexkline3322-byte/tecnogems/<SHA>/install_hotfix.sh | bash
#
# What it does:
#   1. Downloads the modified files from the hotfix branch on GitHub
#   2. Builds a zip in /root/ (convenience backup)
#   3. Backs up the current /root/project files
#   4. Applies the hotfix
#   5. Smoke-tests + restarts + health-checks
#   6. Rolls back automatically if anything fails

set -u
PROJECT="/root/project"
BRANCH="hotfix/v49-currency-deposit-500"
BASE="https://raw.githubusercontent.com/alexkline3322-byte/tecnogems/${BRANCH}"
TS=$(date +%Y%m%d_%H%M%S)
BACKUP_DIR="/root/hotfix_backup_$TS"
SRC="/tmp/v49_hotfix_src_$TS"
ZIP="/root/tecnogems_V49_HOTFIX_$TS.zip"

# List of files patched by this hotfix.
# Format: "<relative-path-inside-project>"
FILES=(
    "database.py"
    "app.py"
    "templates/base.html"
    "templates/admin/user_detail.html"
    "templates/admin/deposits.html"
    "templates/admin/users.html"
    "templates/admin/games.html"
    "templates/wallet_transactions.html"
    "static/css/tecnogems.min.css"
)

echo "=== V49 Currency + UI Hotfix installer ($TS) ==="

# Sanity: /root/project must exist
if [ ! -d "$PROJECT" ]; then
    echo "!!! $PROJECT does not exist. Are you sure this is the right server?"
    exit 1
fi
if [ ! -x "$PROJECT/.venv/bin/python" ]; then
    echo "!!! $PROJECT/.venv/bin/python not found. The project may not have a virtualenv."
    exit 1
fi

# [1] Download modified files
echo "[1/7] Downloading ${#FILES[@]} modified files from branch $BRANCH ..."
rm -rf "$SRC"

download() {
    local url="$1" out="$2" min_size="${3:-200}"
    mkdir -p "$(dirname "$out")"
    if ! curl -fsSL -o "$out" "$url"; then
        echo "    !!! Failed to download: $url"
        exit 1
    fi
    local size=$(stat -c%s "$out" 2>/dev/null || wc -c < "$out")
    if [ -z "$size" ] || [ "$size" -lt "$min_size" ]; then
        echo "    !!! File suspiciously small ($size bytes < $min_size): $out"
        echo "    Content:"
        head -5 "$out"
        exit 1
    fi
    echo "    ok: $(basename $out) ($size bytes)"
}

for rel in "${FILES[@]}"; do
    download "$BASE/$rel" "$SRC/tecnogems/$rel"
done

# Verify database.py + app.py look like Python (catches 404 HTML pages)
for py in database.py app.py; do
    if ! head -5 "$SRC/tecnogems/$py" | grep -qE '^(import|from|#!)'; then
        echo "!!! $py does not look like Python. Aborting."
        head -5 "$SRC/tecnogems/$py"
        exit 1
    fi
done

# [2] Backup current files
echo "[2/7] Backing up current files to $BACKUP_DIR ..."
for rel in "${FILES[@]}"; do
    src="$PROJECT/$rel"
    dst="$BACKUP_DIR/$rel"
    if [ -f "$src" ]; then
        mkdir -p "$(dirname "$dst")"
        cp "$src" "$dst"
    else
        # Record that this file did not exist in the prior install; rollback
        # will remove it rather than restore a wrong version.
        mkdir -p "$(dirname "$dst")"
        echo "MISSING_IN_ORIGINAL" > "${dst}.missing"
    fi
done
echo "    ok"

# [3] Build a zip (non-fatal)
echo "[3/7] Packaging $ZIP ..."
if command -v zip >/dev/null 2>&1; then
    (cd "$SRC" && zip -rq "$ZIP" tecnogems/) && echo "    $(ls -lh $ZIP | awk '{print $5, $9}')"
elif command -v python3 >/dev/null 2>&1; then
    python3 - <<PY_ZIP
import os, zipfile
src = "$SRC"
out = "$ZIP"
with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
    for root, _, files in os.walk(src):
        for f in files:
            full = os.path.join(root, f)
            z.write(full, os.path.relpath(full, src))
print("    packaged via python zipfile:", out)
PY_ZIP
else
    echo "    (skipped: no zip and no python3; backup dir alone is enough for rollback)"
fi

# [4] Copy hotfix files over project
echo "[4/7] Applying hotfix files over $PROJECT ..."
for rel in "${FILES[@]}"; do
    mkdir -p "$(dirname "$PROJECT/$rel")"
    cp "$SRC/tecnogems/$rel" "$PROJECT/$rel"
done
rm -rf "$SRC"
echo "    ok"

# [5] Smoke test BEFORE restarting. CD into $PROJECT so wsgi is importable.
rollback() {
    for rel in "${FILES[@]}"; do
        if [ -f "$BACKUP_DIR/${rel}.missing" ]; then
            rm -f "$PROJECT/$rel"
        elif [ -f "$BACKUP_DIR/$rel" ]; then
            cp "$BACKUP_DIR/$rel" "$PROJECT/$rel"
        fi
    done
}

echo "[5/7] Smoke-testing the updated app (import check) ..."
SMOKE=$(cd "$PROJECT" && "$PROJECT/.venv/bin/python" -c "from wsgi import app; print('SMOKE_OK')" 2>&1 || true)
if ! echo "$SMOKE" | grep -q "SMOKE_OK"; then
    echo "!!! Smoke test FAILED. Rolling back WITHOUT restart (zero downtime)."
    echo "---- smoke output ----"
    echo "$SMOKE"
    echo "----------------------"
    rollback
    echo ">>> ROLLED BACK (backup kept at $BACKUP_DIR)"
    exit 1
fi
echo "    ok"

# [6] Restart + health check
echo "[6/7] Restarting game-topup ..."
systemctl restart game-topup
sleep 4
if curl -fsSI http://127.0.0.1:5000/ >/dev/null 2>&1; then
    echo "    health check OK"
else
    echo "!!! Health check FAILED. Rolling back."
    journalctl -u game-topup --no-pager -n 25
    rollback
    systemctl restart game-topup
    sleep 3
    echo ">>> ROLLED BACK"
    systemctl status game-topup --no-pager | head -8
    exit 1
fi

# [7] Done
echo "[7/7] >>> HOTFIX APPLIED SUCCESSFULLY <<<"
echo ""
echo "Summary:"
echo "  - Backup of old files : $BACKUP_DIR"
echo "  - Hotfix zip kept at  : $ZIP"
echo "  - Files patched       : ${#FILES[@]}"
echo ""
echo "To roll back manually later:"
for rel in "${FILES[@]}"; do
    echo "  cp $BACKUP_DIR/$rel $PROJECT/$rel"
done
echo "  systemctl restart game-topup"
echo ""
systemctl status game-topup --no-pager | head -10
