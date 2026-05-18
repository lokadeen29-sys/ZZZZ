# V69: gunicorn يدعم ترقية آمنة لعدد الـ workers.
#
# لماذا -w 2 افتراضياً (وليس -w 1)؟
#   - مع -w 1 وحده، أي طلب طويل (SMTP بطيء، مزوّد بطيء قبل تسليمه لـ RQ،
#     توليد صورة Pillow ثقيلة...) يحجز thread، ومع 4 خيوط فقط يكفي
#     burst صغير لتعطّل الموقع.
#   - wsgi.py يُهيّئ DB + Indexes + Admin + Catalog إيجارًا، وكل العمليات
#     idempotent (INSERT OR IGNORE / IF NOT EXISTS)، فإعادة تنفيذها في
#     كل worker آمنة وكلفتها أجزاء من الثانية مرة واحدة عند الإقلاع.
#   - SQLite مُشغَّل بـ WAL → قُرّاء متعددون + كاتب واحد عبر الـ workers بدون قفل.
#
# لماذا بدون --preload؟
#   - redis-py / RQ / Flask-Limiter (storage_uri=REDIS_URL) كلها تُنشئ
#     اتصالات Redis عند الـ import في app.py / tasks.py. لو استخدمنا
#     --preload فإن fork() يُورّث نفس الـ socket FD لكل أبناء العملية،
#     وهذا يُفسد ترتيب الردود ("Reader/Writer mismatched response").
#   - بدون preload كل worker يستورد بمعزل → connection pool مستقل = آمن.
#
# قابلية التوسّع:
#   عدد الـ workers والخيوط والمهلات كلها قابلة للضبط من بيئة الاستضافة
#   دون تعديل الـ Procfile (Heroku/Railway/Render/Fly جميعها تدعم env vars).
#   - WEB_CONCURRENCY=3   لرفع الـ workers إلى 3 على dyno بحجم Standard-2X
#   - WEB_THREADS=6       لزيادة التزامن للطلبات I/O-bound
#   - WEB_TIMEOUT=90      لو أصبح هناك مزوّد بطيء جداً
web: gunicorn -k gthread -w ${WEB_CONCURRENCY:-2} --threads ${WEB_THREADS:-4} --timeout ${WEB_TIMEOUT:-60} --graceful-timeout ${WEB_GRACEFUL_TIMEOUT:-30} --max-requests ${WEB_MAX_REQUESTS:-1000} --max-requests-jitter ${WEB_MAX_REQUESTS_JITTER:-100} --access-logfile - --error-logfile - -b 0.0.0.0:${PORT:-5000} wsgi:app
worker: python worker_rq.py
