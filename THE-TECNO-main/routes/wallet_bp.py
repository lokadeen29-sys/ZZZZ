"""V53 REFACTOR (phase 3): wallet routes extracted from app.py.

This Blueprint owns the two routes that make up the user-facing wallet:
  GET/POST /wallet            -> wallet.wallet
  GET/POST /wallet/deposit    -> wallet.wallet  (alias)
  GET/POST /legacy/wallet     -> wallet.wallet  (alias)
  GET      /wallet/transactions -> wallet.wallet_transactions

Endpoint names live under the ``wallet.`` namespace, e.g.
``url_for("wallet.wallet")``. Templates referring to these endpoints have
been updated wholesale in this same PR.

Design notes
------------
- Like ``routes/auth_bp.py`` and ``routes/public_bp.py``, this module
  imports a handful of helpers from ``app.py``. The blueprint is
  registered at the *end* of ``app.py``'s module body — after every
  helper has been defined — so the imports cannot crash on a circular
  reference.
- The deposit-creation flow (image-upload validation, USD conversion,
  ceiling check) is moved verbatim. The only changes are:

    * ``@app.route(...)``                     -> ``@bp.route(...)``
    * ``url_for("wallet")``                   -> ``url_for("wallet.wallet")``
    * ``app.config["UPLOAD_FOLDER"]``         -> ``current_app.config["UPLOAD_FOLDER"]``

- Every decorator from the original is preserved. ``@login_required``
  remains the only middleware on both routes (rate limiting is enforced
  globally by Flask-Limiter's defaults; the two wallet routes never had
  per-route limits in app.py).
"""
from __future__ import annotations

import os
import secrets

from flask import (
    Blueprint,
    current_app,
    flash,
    redirect,
    render_template,
    request,
    url_for,
)
from werkzeug.utils import secure_filename

from app import (
    MAX_DEPOSIT_USD,
    MAX_PROOF_TEXT_LEN,
    _ext_ok,
    _proof_magic_ok,
    create_deposit,
    current_user,
    get_payment_method,
    get_setting,
    get_usd_syp_rate,
    list_deposits_for_user,
    list_payment_methods,
    login_required,
)


bp = Blueprint("wallet", __name__)


# ---------------------------------------------------------------------------
# /wallet   (also /wallet/deposit and /legacy/wallet)
# ---------------------------------------------------------------------------
@bp.route("/wallet", methods=["GET", "POST"])
@bp.route("/wallet/deposit", methods=["GET", "POST"])
@bp.route("/legacy/wallet", methods=["GET", "POST"])
@login_required
def wallet():
    user = current_user()
    if request.method == "POST":
        try:
            amount = float(request.form.get("amount", "0"))
        except Exception:
            amount = 0
        method_id = request.form.get("method_id", "")
        method = get_payment_method(method_id)
        proof_text = request.form.get("proof", "").strip()
        # V50 SECURITY (HE): cap proof-text length (2000 chars ~= 2KB).
        # Prevents a user from shoving MBs of garbage into deposits.proof.
        if len(proof_text) > MAX_PROOF_TEXT_LEN:
            flash("وصف الإثبات طويل جداً (الحد الأقصى 2000 حرف)", "danger")
            return redirect(url_for("wallet.wallet"))
        proof_file = request.files.get("proof_image")
        proof_parts = []
        proof_filename_saved = None

        if proof_text:
            proof_parts.append(proof_text)

        if proof_file and proof_file.filename:
            if not _ext_ok(proof_file.filename):
                flash("نوع الملف غير مدعوم. الأنواع المسموحة: jpg, png, webp, gif", "danger")
                return redirect(url_for("wallet.wallet"))
            # PATCH-H4: verify file content (magic bytes) — not just extension.
            if not _proof_magic_ok(proof_file.stream):
                flash("الملف لا يطابق نوعه المُعلَن. أرسل صورة (JPG/PNG/WebP/GIF) حقيقياً.", "danger")
                return redirect(url_for("wallet.wallet"))
            filename = secure_filename(proof_file.filename)
            ext = os.path.splitext(filename)[1].lower()
            filename = f"{user['id']}_{secrets.token_urlsafe(16)}{ext}"
            save_path = os.path.join(current_app.config["UPLOAD_FOLDER"], filename)
            proof_file.save(save_path)
            proof_filename_saved = filename
            # Use authenticated route instead of public /static/uploads/
            proof_parts.append(f"صورة: /uploads/proof/{filename}")


        proof = "\n".join(proof_parts)

        if amount <= 0:
            flash("أدخل مبلغًا صحيحًا", "danger")
            return redirect(url_for("wallet.wallet"))
        if not method:
            flash("طريقة الدفع غير صحيحة", "danger")
            return redirect(url_for("wallet.wallet"))
        # V67.3: الإيصال أصبح اختياريًا. الإدارة تراجع الطلبات يدويًا داخل
        # المركز ولا تحتاج إلى إيصال إجباري لكل طلب. السلوك السابق كان
        # يطلب إيصالًا في بعض الطلبات ويتجاوزه في طلبات أخرى (لأن الـ
        # التحقق كان مبنيًا على طول النص فقط)، وهو ما تسبب في إرباك
        # المستخدم. لم نعد نرفض الطلب إذا كان الإيصال فارغًا — نخزّن
        # نصًا افتراضيًا حتى لا تُكسر القيود في قاعدة البيانات.
        if not proof:
            proof = "(بدون إيصال — سيتم التحقق يدويًا من الإدارة)"

        rate = float(get_setting("usd_syp_rate", "15000") or 15000)
        if method.get("currency") == "SYP":
            amount_usd = round(amount / rate, 2)
        else:
            amount_usd = amount

        # V50 SECURITY (C3): cap the USD-equivalent deposit amount so abusive
        # users cannot submit fake deposits for billions that clutter the
        # admin queue or cause float overflow in financial aggregation.
        if amount_usd > MAX_DEPOSIT_USD:
            flash(f"المبلغ يتجاوز الحد الأقصى المسموح به ({MAX_DEPOSIT_USD:.0f}$)", "danger")
            return redirect(url_for("wallet.wallet"))

        dep = create_deposit(user["id"], amount, method_id, proof, amount_usd=amount_usd,
                             proof_filename=proof_filename_saved)
        if dep:
            # V69: لم نعد نستخدم flash() لرسالة "تم استلام طلب الشحن" لأن
            # كثيرًا من المستخدمين كانوا يرونها تظهر مرة أخرى عند تحديث
            # الصفحة (F5) بسبب مزيج من bfcache + StaleWhileRevalidate في
            # service worker + إعادة استهلاك الـ session flash بعد رحلة
            # ذهاب وإياب. الحل: نرفع الرسالة عبر query param في الـ URL
            # (?deposit_ok=<code>) ثم نُزيله من شريط العنوان بـ
            # history.replaceState فور عرض الـ toast، فلا يبقى أثر بعد
            # أول refresh.
            return redirect(url_for("wallet.wallet", deposit_ok=dep[1]))
        else:
            flash("فشل إرسال طلب الشحن", "danger")
        return redirect(url_for("wallet.wallet"))

    deposit_ok = (request.args.get("deposit_ok") or "").strip()[:64] or None
    return render_template(
        "wallet.html",
        methods=list_payment_methods(only_active=True),
        support=get_setting("support_contact", "@support"),
        deposits=list_deposits_for_user(user["id"]),
        usd_syp_rate=get_usd_syp_rate(),
        deposit_ok=deposit_ok,
    )


# ---------------------------------------------------------------------------
# /wallet/transactions
# ---------------------------------------------------------------------------
@bp.route("/wallet/transactions")
@login_required
def wallet_transactions():
    user = current_user()
    return render_template(
        "wallet_transactions.html", deposits=list_deposits_for_user(user["id"])
    )


__all__ = ["bp"]
