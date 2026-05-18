---
inclusion: manual
---

# Playbook — Redis إلزامي في الإنتاج

> **متى يُستدعى:** عند تنفيذ بند "REDIS_URL إلزامي" من القائمة الموحّدة (رقم 4 Critical).
> **المرتبط به:** Rate-limiting (Flask-Limiter)، Job queue (RQ)، Settings cache.
> **المدة المتوقعة:** نصف يوم (~4 ساعات).

---

## 1) السياق الحالي في الكود

ثلاث أماكن تستخدم Redis بشكل اختياري (fail-open):

### أ) Flask-Limiter — `app.py` (~سطر 170-195)

```python
_redis_url = os.getenv("REDIS_URL")
_limiter_kwargs = {"app": app, "default_limits": ["200 per minute"]}
if _redis_url:
    _limiter_kwargs["storage_uri"] = _redis_url
    _limiter_kwargs["strategy"] = "fixed-window"
limiter = Limiter(get_remote_address, **_limiter_kwargs)
if _redis_url:
    log.info("Flask-Limiter using Redis storage backend.")
```

**المشكلة:** بدون `REDIS_URL`، الـlimiter يستخدم in-memory storage → كل worker له حصة منفصلة → مهاجم يوزّع طلباته على workers يتجاوز الحد. حالياً `-w 1` لذا السقف صحيح، لكن أي زيادة تكسر الحماية بصمت.

### ب) RQ Queue — `app.py` (~سطر 280-330) + `worker_rq.py`

```python
_redis_url = os.getenv("REDIS_URL")
if _redis_url:
    # استخدم RQ
else:
    # fallback: in-memory Queue + thread
    log.warning("⚠️ REDIS_URL is not set. Orders are queued in-memory "
                "and will be LOST if the process restarts.")
```

**المشكلة:** gunicorn restart (deploy / OOM / crash) = **فقدان كل الطلبات المعلقة**. هذا خطر حقيقي على منتج يتعامل بأموال.

### ج) Settings Cache (`_SETTINGS_CACHE`) — `app.py`

In-memory dict per worker. لا invalidation بين workers. بعد تعديل Admin لإعداد، worker آخر يبقى يعرض القديم حتى 30 ثانية.

---

## 2) خطة التنفيذ

### الخطوة 1 — Boot check صارم

أضِف في `app.py` بعد تحميل `load_dotenv()` وقبل إنشاء Flask `Limiter`:

```python
# V53 CRITICAL: Redis إلزامي في الإنتاج — رفض الإقلاع بدونه.
# In-memory fallback يخلق ثلاث مشاكل في الإنتاج:
#   1. Rate-limiter: كل worker حصة منفصلة → bypass عبر توزيع الحمل.
#   2. RQ queue: فقدان الطلبات عند restart (خسارة مالية فعلية).
#   3. Settings cache: عدم توافق بين workers لـ30 ثانية.
_redis_url = os.getenv("REDIS_URL", "").strip()
if os.getenv("FLASK_ENV") == "production" and not _redis_url:
    raise RuntimeError(
        "REDIS_URL is required in production. "
        "Set it to a valid redis:// URL (Upstash/Railway/Redis Cloud) "
        "or explicitly set FLASK_ENV=development for local testing."
    )
```

### الخطوة 2 — ping اختياري عند الإقلاع (best-effort)

```python
if _redis_url:
    try:
        import redis as _redis_lib
        _r = _redis_lib.from_url(_redis_url, socket_connect_timeout=3)
        _r.ping()
        log.info("Redis reachable at %s", _redis_url.split("@")[-1])
    except Exception as exc:
        if os.getenv("FLASK_ENV") == "production":
            raise RuntimeError(f"Cannot reach REDIS_URL: {exc}") from exc
        log.warning("Redis unreachable (dev mode — continuing): %s", exc)
```

### الخطوة 3 — Procfile: worker منفصل + ترقية -w قابلة للضبط

```procfile
# V69: -w 2 افتراضياً، قابل للرفع إلى 3 عبر WEB_CONCURRENCY بدون إعادة deploy.
# لا نستخدم --preload لأن redis-py + Flask-Limiter + RQ تُنشئ sockets عند
# الـ import؛ والـ fork() يُفسد ترتيب الردود ("Reader/Writer mismatched response").
web: gunicorn -k gthread -w ${WEB_CONCURRENCY:-2} --threads ${WEB_THREADS:-4} --timeout ${WEB_TIMEOUT:-60} --graceful-timeout ${WEB_GRACEFUL_TIMEOUT:-30} --max-requests ${WEB_MAX_REQUESTS:-1000} --max-requests-jitter ${WEB_MAX_REQUESTS_JITTER:-100} --access-logfile - --error-logfile - -b 0.0.0.0:${PORT:-5000} wsgi:app
worker: python worker_rq.py
```

> **لماذا -w 2 آمن الآن:** `wsgi.py` يُنفِّذ `init_db / ensure_indexes / seed_admin / seed_local_provider_catalog` إيجارًا، وكلها idempotent (`INSERT OR IGNORE` + `IF NOT EXISTS`). SQLite في وضع WAL يدعم قُرّاء متعددين عبر processes متعددة. كل worker سيعيد التهيئة بأمان مرة واحدة عند الإقلاع (تكلفة بسيطة في الـ cold start).
>
> **لماذا ليس --preload:** `app.py` و `tasks.py` يُنشئان عميل Redis عند الـ import (Flask-Limiter storage_uri، تهيئة RQ في `tasks.py`). مع `--preload` تُنسخ الـ socket FDs إلى كل أبناء العملية وتنكسر بقاء الاستجابات تحت الحمل. التكلفة الحقيقية لإعادة الـ import في كل worker = أجزاء من الثانية مرة واحدة عند الإقلاع، وهي أرخص من إعادة هيكلة كل عملاء Redis في `post_fork()` hook.
>
> **مهم:** على منصّات heroku-like يحتاج المستخدم تفعيل الـworker dyno يدوياً. على Railway/Render يُضاف كخدمة منفصلة. **وثّق هذا في `README.md`.**

### الخطوة 4 — `.env.example` — REDIS_URL غير مُعلَّق

```bash
# قبل:
# REDIS_URL=redis://localhost:6379/0

# بعد:
REDIS_URL=redis://localhost:6379/0   # ELZAMI في الإنتاج
```

### الخطوة 5 — Settings cache → Redis (مؤجَّل إلى PR منفصل)

هذا تغيير أوسع. دوّنه في `project-context.md` كبند متبقٍّ لكن لا تنفّذه هنا.

---

## 3) الاختبار

```bash
# 1. production بدون REDIS_URL — يجب أن يرفض
FLASK_ENV=production SECRET_KEY=x python -c "import wsgi"
# توقَّع: RuntimeError: REDIS_URL is required in production

# 2. production مع REDIS_URL صالح
FLASK_ENV=production SECRET_KEY=x REDIS_URL=redis://localhost:6379/0 python -c "import wsgi"
# توقَّع: يُقلع بدون استثناء

# 3. development بدون REDIS_URL — لا يزال يعمل
FLASK_ENV=development SECRET_KEY=x python -c "import wsgi"
# توقَّع: يُقلع + log.warning فقط
```

أضِف اختبار pytest في `tests/test_boot.py`:

```python
def test_production_requires_redis_url(monkeypatch):
    monkeypatch.setenv("FLASK_ENV", "production")
    monkeypatch.delenv("REDIS_URL", raising=False)
    import importlib, sys
    for mod in ("app", "wsgi"):
        sys.modules.pop(mod, None)
    with pytest.raises(RuntimeError, match="REDIS_URL is required"):
        import wsgi  # noqa
```

---

## 4) التزامات Rollout

- [ ] Redis managed يعمل قبل merge (Upstash مجاني يكفي للبداية).
- [ ] `REDIS_URL` مضاف في بيئة الإنتاج (Heroku/Railway/etc).
- [ ] `worker` dyno مُفعَّل وتشتغل.
- [ ] أول deploy يُراقَب 15 دقيقة ضد 500s.

---

## 5) تحديث `project-context.md` بعد الإنجاز

- أضف البند إلى قسم "المُنجزة" برقم PR.
- أضف قراراً معمارياً جديداً:
  > **Redis إلزامي في production** — in-memory fallback يُسبّب: rate-limit bypass، فقدان طلبات عند restart، وتفاوت settings بين workers. الكلفة (~$0 للبداية عبر Upstash free tier) لا تبرر المخاطرة.
- حدّث "متغيرات البيئة المهمة" — انقل `REDIS_URL` من "اختيارية" إلى "إلزامية في الإنتاج".
