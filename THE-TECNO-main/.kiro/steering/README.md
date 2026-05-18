---
inclusion: manual
---

# فهرس الـSteering Playbooks

هذا المجلد يحوي playbooks قابلة للتنفيذ — كل ملف = خطة تنفيذ كاملة لبند محدَّد.

## الملفات الدائمة (always on)

- `project-context.md` — الحالة الحالية للمشروع، تُقرأ تلقائياً.
- `workflow.md` — قواعد Git/PRs.

## Playbooks حسب الأولوية (manual — تُستدعى بالاسم)

### 🔴 Critical (الأسبوع الأول)

| الملف | البند الموحَّد | المدة |
|-------|---------------|------|
| `csp-nonce.md` | #1 CSP Nonce | يوم |
| `sqlite-connection-leaks.md` | #2 SQLite leak | يوم |
| `redis-enforcement.md` | #4 Redis إلزامي | نصف يوم |
| `backup-strategy.md` | #7 Backup | يوم |

> البنود الأخرى في "Critical" تحتاج playbooks لاحقة: #3 LIKE escape، #5 In-memory queue، #6 Legal pages، #8 Session invalidation، #9 Promo codes، #10 Referral.

### 🟠 High (2-4 أسابيع)

| الملف | البند الموحَّد | المدة |
|-------|---------------|------|
| `cloudflare-setup.md` | #11 Cloudflare | نصف يوم |
| `idor-xss-fixes.md` | #13 + #14 IDOR/XSS | يوم |
| `app-split-phase1.md` | #21 Blueprint split — المرحلة 1 | أسبوع |
| `mobile-ux.md` | #24 Mobile UX | 2-3 أيام |

## كيفية الاستخدام

عند بدء العمل على بند، في رسالتك:

> "اقرأ `.kiro/steering/redis-enforcement.md` ونفِّذ البنود بالترتيب"

أو:

> "طبّق playbook CSP nonce"

الـagent يقرأ الملف ويتبع الخطوات المحدَّدة فيه.

## ترتيب تنفيذ موصى به

```
الأسبوع 1:
  sqlite-connection-leaks  →  PR #1 (Reliability critical)
  redis-enforcement        →  PR #2 (مربوط بالـ1)
  csp-nonce                →  PR #3 (Security critical)
  idor-xss-fixes           →  PR #4 (Security high)

الأسبوع 2:
  backup-strategy          →  PR #5 (Data safety)
  cloudflare-setup         →  لا PR — configuration خارجي
  mobile-ux                →  PR #6 (UX high-impact)

الأسبوع 3-4:
  app-split-phase1         →  PR #7 (Maintainability — تحت إشراف كامل)
```

## تحديث الـplaybooks

عندما تكتشف خطوة مفقودة أو تحسيناً أثناء التنفيذ:
1. عدّل الـplaybook في نفس الـPR.
2. أضِف سطراً في "Change log" أسفل الـplaybook.
