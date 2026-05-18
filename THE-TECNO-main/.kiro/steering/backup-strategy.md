---
inclusion: manual
---

# Playbook — استراتيجية النسخ الاحتياطي

> **متى يُستدعى:** تنفيذ البند Critical رقم 7 (Backup Strategy).
> **الخطر الحالي:** لا cron، لا snapshot، لا off-site copy. حذف عرضي / crash disk = خسارة كاملة.
> **المدة المتوقعة:** يوم (سكربت + cron + اختبار استرجاع).

---

## 1) السياق

- **DB حالياً:** SQLite (`site.db` في مجلد العمل).
- **Uploads:** `data/uploads/proof/` (إيصالات الدفع).
- **Secrets:** `.secret_key` file (لا يُنسَخ — يُولَّد محلياً من env).
- **لا وجود** أي آلية نسخ احتياطي حالياً.

---

## 2) الأهداف (SLO)

- **RPO (Recovery Point Objective):** ≤ ساعة واحدة.
- **RTO (Recovery Time Objective):** ≤ 30 دقيقة.
- **Retention:**
  - يومي: آخر 7 نسخ.
  - أسبوعي: آخر 4 نسخ.
  - شهري: آخر 12 نسخة.
- **Off-site:** S3-compatible (Cloudflare R2 مفضَّل — لا رسوم egress).
- **تشفير:** SSE at-rest + client-side gzip.

---

## 3) المعمارية المقترحة

```
[app server]                     [cloud storage]
    │                                   │
    │   كل ساعة (cron)                 │
    ├─── sqlite3 .backup ──> site.db.N  │
    │                                   │
    ├─── gzip + timestamp               │
    │                                   │
    └─── aws s3 cp ─────────────────────┤── R2 bucket
                                        │      ├── hourly/
                                        │      ├── daily/
                                        │      ├── weekly/
                                        │      └── monthly/
                                        │
                                        │   (lifecycle rules)
```

---

## 4) التنفيذ

### الخطوة 1 — سكربت النسخ: `tools/backup.sh`

```bash
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

# 5. Uploads directory (إيصالات — أصغر بكثير، أسبوعياً يكفي)
if [ "$(date +%H)" = "03" ] && [ "$(date +%u)" = "7" ]; then
    tar czf "$BACKUP_DIR/uploads-$TS.tar.gz" data/uploads/
    aws s3 cp "${AWS_ARGS[@]}" "$BACKUP_DIR/uploads-$TS.tar.gz" \
        "s3://$S3_BUCKET/weekly-uploads/" --sse AES256
    rm -f "$BACKUP_DIR/uploads-$TS.tar.gz"
fi

# 6. Local cleanup — اترك آخر 24 ساعة فقط محلياً
find "$BACKUP_DIR" -name "site-*.db.gz" -mtime +1 -delete

echo "OK: uploaded $(basename "$OUT_FILE") to s3://$S3_BUCKET/hourly/"
```

اجعله قابلاً للتنفيذ: `chmod +x tools/backup.sh`.

### الخطوة 2 — سكربت الاسترجاع: `tools/restore.sh`

```bash
#!/usr/bin/env bash
# V53: استرجاع نسخة احتياطية محدَّدة. استخدمه فقط بعد توقف الـapp.
set -euo pipefail

if [ $# -lt 1 ]; then
    echo "Usage: $0 <backup-s3-key>   # e.g. hourly/site-20260512T080000Z.db.gz"
    exit 1
fi

KEY="$1"
S3_BUCKET="${BACKUP_S3_BUCKET:?}"
S3_ENDPOINT="${BACKUP_S3_ENDPOINT:-}"
DEST="${DEST:-site.db}"

AWS_ARGS=()
[ -n "$S3_ENDPOINT" ] && AWS_ARGS+=(--endpoint-url "$S3_ENDPOINT")

# 1. تنزيل
TMP=$(mktemp --suffix=.db.gz)
aws s3 cp "${AWS_ARGS[@]}" "s3://$S3_BUCKET/$KEY" "$TMP"

# 2. فك الضغط + فحص
gunzip "$TMP"
TMP_DB="${TMP%.gz}"
if ! sqlite3 "$TMP_DB" "PRAGMA integrity_check;" | grep -q "^ok$"; then
    echo "FATAL: downloaded backup is CORRUPT" >&2
    exit 1
fi

# 3. احفظ الحالي كـsafety (لو كان موجوداً)
if [ -f "$DEST" ]; then
    mv "$DEST" "$DEST.pre-restore-$(date +%s)"
fi

mv "$TMP_DB" "$DEST"
echo "OK: restored to $DEST"
echo "REMINDER: restart gunicorn + worker_rq now."
```

### الخطوة 3 — Cron

على خادم الإنتاج:
```cron
# hourly full backup
0 * * * * cd /app && FLASK_ENV=production /app/tools/backup.sh >> /var/log/backup.log 2>&1
```

على Heroku: استخدم Heroku Scheduler add-on → تشغيل `tools/backup.sh` كل ساعة.
على Railway: Cron Jobs feature في الـdashboard.

### الخطوة 4 — Lifecycle في R2/S3

من R2 dashboard أو عبر `aws s3api put-bucket-lifecycle-configuration`:

```json
{
  "Rules": [
    {
      "Id": "hourly-to-daily",
      "Status": "Enabled",
      "Filter": {"Prefix": "hourly/"},
      "Expiration": {"Days": 2}
    },
    {
      "Id": "daily-to-weekly",
      "Status": "Enabled",
      "Filter": {"Prefix": "daily/"},
      "Expiration": {"Days": 30}
    },
    {
      "Id": "weekly",
      "Status": "Enabled",
      "Filter": {"Prefix": "weekly-uploads/"},
      "Expiration": {"Days": 120}
    }
  ]
}
```

ثم سكربت يومي ينسخ أحدث hourly إلى `daily/`:
```bash
# tools/promote_daily.sh (cron يومياً 04:00)
LATEST=$(aws s3 ls "s3://$S3_BUCKET/hourly/" | sort | tail -1 | awk '{print $4}')
aws s3 cp "s3://$S3_BUCKET/hourly/$LATEST" "s3://$S3_BUCKET/daily/$LATEST"
```

### الخطوة 5 — تحديث `.env.example`

```bash
# Backup (V53)
BACKUP_S3_BUCKET=tecnogems-backups
BACKUP_S3_ENDPOINT=https://<account>.r2.cloudflarestorage.com
AWS_ACCESS_KEY_ID=...
AWS_SECRET_ACCESS_KEY=...
AWS_DEFAULT_REGION=auto
```

---

## 5) اختبار الاسترجاع (إلزامي)

> **المبدأ:** نسخة احتياطية غير مُختبَرة = لا نسخة احتياطية.

### اختبار أولي (قبل الـmerge)

```bash
# 1. شغّل backup مرة
./tools/backup.sh

# 2. تأكد من الرفع
aws s3 ls "s3://$BACKUP_S3_BUCKET/hourly/"

# 3. جرّب الاسترجاع في بيئة معزولة
export DEST=/tmp/restored-test.db
./tools/restore.sh hourly/<latest-file>

# 4. تحقق
sqlite3 /tmp/restored-test.db "SELECT COUNT(*) FROM users;"
sqlite3 /tmp/restored-test.db "SELECT COUNT(*) FROM orders;"
```

### اختبار ربع سنوي (DRP)

مرة كل 3 أشهر: استرجاع كامل في staging environment + تشغيل smoke tests. وثّق التاريخ في `docs/backup-drills.md`.

---

## 6) المراقبة (Monitoring)

أضِف في `tools/backup.sh` بعد الرفع الناجح:

```bash
# heartbeat ping — يُخبر Dead Man's Snitch / healthchecks.io أن الـbackup تم
if [ -n "${BACKUP_HEARTBEAT_URL:-}" ]; then
    curl -fsS -m 10 --retry 3 "$BACKUP_HEARTBEAT_URL" >/dev/null || true
fi
```

اشترك مجاناً في [healthchecks.io](https://healthchecks.io) — خطة مجانية تكفي. يرسل تنبيه إيميل/WhatsApp إذا لم يصل heartbeat ساعة.

---

## 7) التزامات Rollout

- [ ] R2/S3 bucket أُنشئ + IAM user بصلاحيات Put/Get/Delete على `hourly/`, `daily/`, `weekly/` فقط.
- [ ] Secrets في الإنتاج (env vars).
- [ ] Cron يعمل ليلة كاملة بنجاح → 24 نسخة في R2.
- [ ] اختبار restore ناجح.
- [ ] Healthcheck heartbeat مُفعَّل.
- [ ] `docs/runbooks/disaster-recovery.md` مكتوب (خطوات الاسترجاع للفريق).

---

## 8) تحديث `project-context.md`

- أضِف البند للمُنجزة.
- قرار معماري جديد:
  > **نسخ احتياطي ساعياً إلى R2 مع lifecycle (hourly→daily→weekly→monthly)** — يستخدم SQLite's online `.backup` (آمن مع التطبيق شغال). Healthchecks.io heartbeat للمراقبة. سيتم استبدال هذا بـ `pg_dump` + PITR بعد الترحيل إلى PostgreSQL (بند 27 في القائمة الموحّدة).
- أضِف متغيرات البيئة الجديدة لقسم "متغيرات البيئة المهمة".
