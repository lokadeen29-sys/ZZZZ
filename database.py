import os
import re
import json
import sqlite3
import time
import threading
import secrets
from contextlib import contextmanager
from werkzeug.security import generate_password_hash, check_password_hash

DB_PATH = os.path.join(os.path.dirname(__file__), "data", "site.db")


class InsufficientBalance(Exception):
    """Raised by create_order when the user's balance is too low."""


_PRAGMAS_APPLIED = False
_PRAGMAS_LOCK = threading.Lock()

def connect():
    """Open a SQLite connection with WAL + sane pragmas for high-concurrency reads.

    WAL mode lets readers and a writer work in parallel (huge speed-up for the
    site's mixed read/write traffic) and `busy_timeout` avoids the dreaded
    "database is locked" error during bursts of writes.
    """
    global _PRAGMAS_APPLIED
    conn = sqlite3.connect(DB_PATH, timeout=15, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    if not _PRAGMAS_APPLIED:
        with _PRAGMAS_LOCK:
            if not _PRAGMAS_APPLIED:
                conn.execute("PRAGMA journal_mode=WAL")
                conn.execute("PRAGMA synchronous=NORMAL")
                conn.execute("PRAGMA temp_store=MEMORY")
                conn.execute("PRAGMA mmap_size=134217728")  # 128 MB
                conn.execute("PRAGMA cache_size=-20000")    # ~20 MB page cache
                globals()["_PRAGMAS_APPLIED"] = True
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=15000")
    return conn


@contextmanager
def db_conn():
    """V53 CRITICAL: context manager that guarantees connection closure even on exceptions.

    Use instead of the old pattern:
        conn = connect()
        ...
        conn.close()   # may never execute on exception

    New pattern:
        with db_conn() as conn:
            ...   # close is guaranteed
    """
    conn = connect()
    try:
        yield conn
    finally:
        try:
            conn.close()
        except Exception:
            pass



def ensure_indexes():
    with db_conn() as conn:
        indexes = [
            "CREATE INDEX IF NOT EXISTS idx_orders_user_id ON orders(user_id)",
            "CREATE INDEX IF NOT EXISTS idx_orders_status ON orders(status)",
            "CREATE INDEX IF NOT EXISTS idx_orders_created_at ON orders(created_at)",
            "CREATE INDEX IF NOT EXISTS idx_orders_user_created ON orders(user_id, created_at DESC)",
            "CREATE INDEX IF NOT EXISTS idx_orders_status_created ON orders(status, created_at DESC)",
            "CREATE INDEX IF NOT EXISTS idx_deposits_user_id ON deposits(user_id)",
            "CREATE INDEX IF NOT EXISTS idx_deposits_status ON deposits(status)",
            "CREATE INDEX IF NOT EXISTS idx_deposits_user_created ON deposits(user_id, created_at DESC)",
            "CREATE INDEX IF NOT EXISTS idx_users_email ON users(email)",
            "CREATE INDEX IF NOT EXISTS idx_users_email_token ON users(email_token)",
            "CREATE INDEX IF NOT EXISTS idx_users_reset_token ON users(reset_token)",
            "CREATE INDEX IF NOT EXISTS idx_products_game ON products(provider, game_key)",
            "CREATE INDEX IF NOT EXISTS idx_products_game_key ON products(game_key)",
            "CREATE INDEX IF NOT EXISTS idx_products_active_sort ON products(active, sort_order)",
            "CREATE INDEX IF NOT EXISTS idx_games_active ON games(active, sort_order)",
            "CREATE INDEX IF NOT EXISTS idx_settings_key ON settings(key)",
        ]
        for q in indexes:
            try:
                conn.execute(q)
            except Exception:
                pass
        # V42 batch2: wishlist + google oauth columns
        try:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS wishlist (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    provider TEXT NOT NULL,
                    game_key TEXT NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(user_id, provider, game_key)
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_wishlist_user ON wishlist(user_id, created_at DESC)")
        except Exception:
            pass
        try:
            conn.execute("ALTER TABLE users ADD COLUMN google_sub TEXT")
        except Exception:
            pass
        try:
            conn.execute("CREATE INDEX IF NOT EXISTS idx_users_google_sub ON users(google_sub)")
        except Exception:
            pass
        # V53 security: session_version — incremented on password change to
        # invalidate all other sessions for the user.
        try:
            conn.execute("ALTER TABLE users ADD COLUMN session_version INTEGER NOT NULL DEFAULT 1")
        except Exception:
            pass
        # V53 security: IDOR fix — store proof filename in deposits table
        # so ownership can be verified via DB instead of filename prefix.
        try:
            conn.execute("ALTER TABLE deposits ADD COLUMN proof_filename TEXT")
        except Exception:
            pass
        conn.commit()


# ============================================================
# V42 batch2: Wishlist helpers
# ============================================================
def wishlist_list(user_id):
    with db_conn() as conn:
        return [dict(r) for r in conn.execute("""
            SELECT w.provider, w.game_key, w.created_at,
                   g.name AS name, g.image_url AS image_url
            FROM wishlist w
            LEFT JOIN games g ON g.provider=w.provider AND g.game_key=w.game_key
            WHERE w.user_id=?
            ORDER BY w.created_at DESC
        """, (user_id,)).fetchall()]


def wishlist_has(user_id, provider, game_key):
    with db_conn() as conn:
        row = conn.execute(
            "SELECT 1 FROM wishlist WHERE user_id=? AND provider=? AND game_key=?",
            (user_id, provider, game_key)
        ).fetchone()
        return bool(row)


def wishlist_toggle(user_id, provider, game_key):
    """returns True if added, False if removed."""
    with db_conn() as conn:
        cur = conn.execute(
            "SELECT id FROM wishlist WHERE user_id=? AND provider=? AND game_key=?",
            (user_id, provider, game_key)
        ).fetchone()
        if cur:
            conn.execute("DELETE FROM wishlist WHERE id=?", (cur["id"],))
            added = False
        else:
            conn.execute(
                "INSERT INTO wishlist(user_id, provider, game_key) VALUES (?,?,?)",
                (user_id, provider, game_key)
            )
            added = True
        conn.commit()
        return added


def _escape_like(q):
    """Escape special LIKE wildcard characters to prevent injection."""
    return q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def search_suggest(q, limit=8):
    """Lightweight autocomplete across games (name) + product groups (label)."""
    with db_conn() as conn:
        qlike = f"%{_escape_like(q)}%"
        games = [dict(r) for r in conn.execute("""
            SELECT 'game' AS kind, provider, game_key, name AS label, image_url
            FROM games
            WHERE active=1 AND name LIKE ? ESCAPE '\\' COLLATE NOCASE
            ORDER BY name LIMIT ?
        """, (qlike, limit)).fetchall()]
        remaining = max(1, limit - len(games))
        products = [dict(r) for r in conn.execute("""
            SELECT 'product' AS kind, provider, game_key, name AS label
            FROM products
            WHERE active=1 AND name LIKE ? ESCAPE '\\' COLLATE NOCASE
            GROUP BY provider, game_key, name
            ORDER BY name LIMIT ?
        """, (qlike, remaining)).fetchall()]
        return games + products


def get_user_by_google_sub(sub):
    with db_conn() as conn:
        row = conn.execute("SELECT * FROM users WHERE google_sub=?", (sub,)).fetchone()
        return dict(row) if row else None


def link_user_google_sub(user_id, sub):
    with db_conn() as conn:
        conn.execute("UPDATE users SET google_sub=? WHERE id=?", (sub, user_id))
        conn.commit()


def create_user_oauth(name, email, google_sub):
    """Create a user from OAuth (no password, email already verified)."""
    import secrets as _secrets, time as _t
    with db_conn() as conn:
        try:
            random_pw = generate_password_hash(_secrets.token_urlsafe(32))
            cur = conn.execute(
                "INSERT INTO users(name, email, phone, password_hash, role, email_verified, google_sub, created_at) "
                "VALUES (?, ?, '', ?, 'user', 1, ?, ?)",
                (name or email.split("@")[0], email, random_pw, google_sub, int(_t.time()))
            )
            conn.commit()
            uid = cur.lastrowid
            return uid
        except Exception:
            return None


def init_db():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    with db_conn() as conn:
        _init_db_inner(conn)


def _init_db_inner(conn):
    cur = conn.cursor()

    cur.execute("""
    CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        email TEXT UNIQUE NOT NULL,
        phone TEXT,
        password_hash TEXT NOT NULL,
        role TEXT NOT NULL DEFAULT 'user',
        balance REAL NOT NULL DEFAULT 0,
        active INTEGER NOT NULL DEFAULT 1,
        email_verified INTEGER NOT NULL DEFAULT 0,
        email_token TEXT,
        email_token_created_at INTEGER,
        reset_token TEXT,
        reset_token_created_at INTEGER,
        created_at INTEGER NOT NULL
    )
    """)

    # ترقية جدول المستخدمين لإضافة تفعيل البريد الإلكتروني
    for sql in [
        "ALTER TABLE users ADD COLUMN email_verified INTEGER NOT NULL DEFAULT 0",
        "ALTER TABLE users ADD COLUMN email_token TEXT",
        "ALTER TABLE users ADD COLUMN email_token_created_at INTEGER",
        "ALTER TABLE users ADD COLUMN reset_token TEXT",
        "ALTER TABLE users ADD COLUMN reset_token_created_at INTEGER",
        "ALTER TABLE users ADD COLUMN pending_email TEXT",
        "ALTER TABLE users ADD COLUMN pending_email_token TEXT",
        "ALTER TABLE users ADD COLUMN pending_email_created_at INTEGER",
        # V51 task B: admin 2FA (TOTP + one-time backup codes).
        # Columns are nullable — 2FA is opt-in per-admin and backfills cleanly.
        "ALTER TABLE users ADD COLUMN totp_secret TEXT",
        "ALTER TABLE users ADD COLUMN totp_enabled INTEGER NOT NULL DEFAULT 0",
        "ALTER TABLE users ADD COLUMN totp_backup_codes TEXT",
        "ALTER TABLE users ADD COLUMN totp_enabled_at INTEGER"
    ]:
        try:
            cur.execute(sql)
        except Exception:
            pass

    cur.execute("""
    CREATE TABLE IF NOT EXISTS games (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        provider TEXT NOT NULL,
        game_key TEXT NOT NULL,
        name TEXT NOT NULL,
        emoji TEXT DEFAULT '🎮',
        image_url TEXT DEFAULT '',
        active INTEGER NOT NULL DEFAULT 1,
        UNIQUE(provider, game_key)
    )
    """)

    try:
        cur.execute("ALTER TABLE games ADD COLUMN image_url TEXT DEFAULT ''")
    except Exception:
        pass

    try:
        cur.execute("ALTER TABLE games ADD COLUMN pricing_currency TEXT DEFAULT 'GLOBAL'")
    except Exception:
        pass

    # V55: admin-controlled homepage visibility. When no rows have show_on_home=1,
    # the homepage falls back to showing the first N active games (see app.home()).
    try:
        cur.execute("ALTER TABLE games ADD COLUMN show_on_home INTEGER NOT NULL DEFAULT 0")
    except Exception:
        pass

    # V55: optional manual ordering of homepage games. 0 = use default order.
    try:
        cur.execute("ALTER TABLE games ADD COLUMN home_sort_order INTEGER NOT NULL DEFAULT 0")
    except Exception:
        pass


    cur.execute("""
    CREATE TABLE IF NOT EXISTS products (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        provider TEXT NOT NULL,
        game_key TEXT NOT NULL,
        provider_product_id TEXT NOT NULL,
        name TEXT NOT NULL,
        base_price REAL NOT NULL,
        sell_price REAL NOT NULL,
        sort_order INTEGER NOT NULL DEFAULT 0,
        active INTEGER NOT NULL DEFAULT 1,
        UNIQUE(provider, provider_product_id)
    )
    """)

    try:
        cur.execute("ALTER TABLE products ADD COLUMN sort_order INTEGER NOT NULL DEFAULT 0")
    except Exception:
        pass

    cur.execute("""
    CREATE TABLE IF NOT EXISTS product_groups (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        provider TEXT NOT NULL,
        game_key TEXT NOT NULL,
        name TEXT NOT NULL,
        image_url TEXT DEFAULT '',
        sort_order INTEGER NOT NULL DEFAULT 1,
        active INTEGER NOT NULL DEFAULT 1,
        created_at INTEGER NOT NULL,
        UNIQUE(provider, game_key, name)
    )
    """)
    try:
        cur.execute("ALTER TABLE products ADD COLUMN group_id INTEGER")
    except Exception:
        pass


    try:
        cur.execute("ALTER TABLE products ADD COLUMN fixed_syp_price REAL NOT NULL DEFAULT 0")
    except Exception:
        pass

    try:
        cur.execute("ALTER TABLE products ADD COLUMN pricing_mode TEXT DEFAULT 'usd'")
    except Exception:
        pass

    try:
        cur.execute("ALTER TABLE products ADD COLUMN manual_price_syp REAL NOT NULL DEFAULT 0")
    except Exception:
        pass


    cur.execute("""
    CREATE TABLE IF NOT EXISTS orders (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        order_code TEXT UNIQUE NOT NULL,
        user_id INTEGER NOT NULL,
        provider TEXT NOT NULL,
        game_key TEXT NOT NULL,
        game_name TEXT NOT NULL,
        product_id INTEGER NOT NULL,
        product_name TEXT NOT NULL,
        player_id TEXT NOT NULL,
        price REAL NOT NULL,
        status TEXT NOT NULL DEFAULT 'waiting',
        provider_order_id TEXT,
        note TEXT,
        created_at INTEGER NOT NULL,
        updated_at INTEGER NOT NULL
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS deposits (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        deposit_code TEXT UNIQUE NOT NULL,
        user_id INTEGER NOT NULL,
        amount REAL NOT NULL,
        method TEXT NOT NULL,
        proof TEXT,
        status TEXT NOT NULL DEFAULT 'pending',
        created_at INTEGER NOT NULL
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS payment_methods (
        id TEXT PRIMARY KEY,
        name TEXT NOT NULL,
        emoji TEXT NOT NULL DEFAULT '💳',
        address TEXT NOT NULL DEFAULT '',
        instructions TEXT NOT NULL DEFAULT '',
        active INTEGER NOT NULL DEFAULT 1,
        currency TEXT NOT NULL DEFAULT 'USD'
    )
    """)

    # ترقية قواعد البيانات القديمة
    try:
        cur.execute("ALTER TABLE payment_methods ADD COLUMN currency TEXT NOT NULL DEFAULT 'USD'")
    except Exception:
        pass
    try:
        cur.execute("ALTER TABLE deposits ADD COLUMN currency TEXT DEFAULT 'USD'")
    except Exception:
        pass
    try:
        cur.execute("ALTER TABLE deposits ADD COLUMN amount_usd REAL DEFAULT 0")
    except Exception:
        pass

    cur.execute("""
    CREATE TABLE IF NOT EXISTS settings (
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL
    )
    """)

    # V52 (task D): structured audit trail for admin + privileged actions.
    # Complements the existing log.warning("ADMIN_*") feed by giving us a
    # queryable on-disk record: who did what, to which target, when, and
    # with what before/after state. Rows are append-only; no UPDATE or
    # DELETE is exposed by the public API.
    cur.execute("""
    CREATE TABLE IF NOT EXISTS audit_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ts INTEGER NOT NULL,
        action TEXT NOT NULL,
        actor_id INTEGER,
        actor_email TEXT,
        target_type TEXT,
        target_id TEXT,
        ip TEXT,
        user_agent TEXT,
        old_value TEXT,
        new_value TEXT,
        metadata TEXT
    )
    """)
    # Index the columns we will query most: recent-first listing, per-actor
    # history, per-target history, and action-type filtering.
    for _q in (
        "CREATE INDEX IF NOT EXISTS idx_audit_ts ON audit_log(ts DESC)",
        "CREATE INDEX IF NOT EXISTS idx_audit_actor ON audit_log(actor_id, ts DESC)",
        "CREATE INDEX IF NOT EXISTS idx_audit_target ON audit_log(target_type, target_id)",
        "CREATE INDEX IF NOT EXISTS idx_audit_action ON audit_log(action, ts DESC)",
    ):
        try:
            cur.execute(_q)
        except Exception:
            pass


    default_methods = [
        ("usdt", "USDT (TRC20)", "🪙", "ضع عنوان USDT هنا", "حوّل بالدولار إلى العنوان أدناه ثم أرسل إثبات الدفع.", "USD"),
        ("binance", "Binance Pay", "💳", "ضع Binance ID هنا", "حوّل بالدولار عبر Binance Pay ثم أرسل إثبات الدفع.", "USD"),
        ("sham_syr", "شام كاش سوري", "🇸🇾", "ضع رقم الحساب هنا", "حوّل بالليرة السورية ثم أرسل إثبات الدفع.", "SYP"),
        ("sham_usd", "شام كاش دولار", "💵", "ضع رقم الحساب هنا", "حوّل بالدولار ثم أرسل إثبات الدفع.", "USD"),
        ("syriatel", "سيرياتيل كاش", "📱", "ضع رقم الهاتف هنا", "حوّل بالليرة السورية فقط ثم أرسل إثبات الدفع.", "SYP"),
        ("center", "ضمن المركز", "🏢", "عنوان المركز", "الدفع ضمن المركز بالليرة السورية فقط.", "SYP")
    ]
    for m in default_methods:
        cur.execute("""
            INSERT OR IGNORE INTO payment_methods (id, name, emoji, address, instructions, active, currency)
            VALUES (?, ?, ?, ?, ?, 1, ?)
        """, m)

    cur.execute("INSERT OR IGNORE INTO settings (key,value) VALUES (?,?)", ("support_contact", "@support"))
    cur.execute("INSERT OR IGNORE INTO settings (key,value) VALUES (?,?)", ("usd_syp_rate", "15000"))
    cur.execute("INSERT OR IGNORE INTO settings (key,value) VALUES (?,?)", ("manual_orders", "0"))
    cur.execute("INSERT OR IGNORE INTO settings (key,value) VALUES (?,?)", ("enable_player_check", "0"))
    cur.execute("INSERT OR IGNORE INTO settings (key,value) VALUES (?,?)", ("show_server1", "1"))
    cur.execute("INSERT OR IGNORE INTO settings (key,value) VALUES (?,?)", ("show_server2", "1"))
    cur.execute("INSERT OR IGNORE INTO settings (key,value) VALUES (?,?)", ("email_verification_enabled", "0"))
    cur.execute("INSERT OR IGNORE INTO settings (key,value) VALUES (?,?)", ("public_catalog_enabled", "1"))
    cur.execute("INSERT OR IGNORE INTO settings (key,value) VALUES (?,?)", ("profit_margin", "1.20"))
    cur.execute("INSERT OR IGNORE INTO settings (key,value) VALUES (?,?)", ("site_theme", "theme-aurora"))
    cur.execute("INSERT OR IGNORE INTO settings (key,value) VALUES (?,?)", ("local_catalog_seeded", "0"))

    conn.commit()


def seed_admin(email, password):
    with db_conn() as conn:
        cur = conn.cursor()
        cur.execute("SELECT id FROM users WHERE email=?", (email,))
        if not cur.fetchone():
            cur.execute(
                "INSERT INTO users (name,email,password_hash,role,balance,email_verified,created_at) VALUES (?,?,?,?,?,?,?)",
                ("Admin", email, generate_password_hash(password), "admin", 0, 1, int(time.time()))
            )
        conn.commit()


def set_setting(key, value):
    with db_conn() as conn:
        conn.execute("INSERT OR REPLACE INTO settings (key,value) VALUES (?,?)", (key, str(value)))
        conn.commit()


def get_setting(key, default=None):
    with db_conn() as conn:
        row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        return row["value"] if row else default


def create_user(name, email, phone, password, email_verified=0, email_token=None):
    with db_conn() as conn:
        try:
            cur = conn.cursor()
            cur.execute(
                """INSERT INTO users
                   (name,email,phone,password_hash,role,balance,email_verified,email_token,email_token_created_at,created_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?)""",
                (
                    name,
                    email.lower(),
                    phone,
                    generate_password_hash(password),
                    "user",
                    0,
                    int(email_verified),
                    email_token,
                    int(time.time()) if email_token else None,
                    int(time.time())
                )
            )
            conn.commit()
            return True, None
        except sqlite3.IntegrityError:
            return False, "البريد مستخدم مسبقًا"


def authenticate(email, password):
    with db_conn() as conn:
        row = conn.execute("SELECT * FROM users WHERE email=? AND active=1", (email.lower(),)).fetchone()
        if row and check_password_hash(row["password_hash"], password):
            return dict(row)
        return None


# --- V51 task B: admin 2FA helpers ---------------------------------------
def set_user_totp_secret(user_id, secret):
    """Store a NEW (unverified) TOTP secret. Does not flip `totp_enabled`
    — that happens via `enable_user_totp` once the first code is confirmed."""
    with db_conn() as conn:
        conn.execute(
            "UPDATE users SET totp_secret=?, totp_enabled=0, totp_backup_codes=NULL, totp_enabled_at=NULL WHERE id=?",
            (secret, int(user_id)),
        )
        conn.commit()


def enable_user_totp(user_id, backup_codes_json):
    """Flip `totp_enabled` on (called after the user confirms a valid code).
    `backup_codes_json` is produced by security_2fa.serialize_backup_codes."""
    with db_conn() as conn:
        conn.execute(
            "UPDATE users SET totp_enabled=1, totp_backup_codes=?, totp_enabled_at=? WHERE id=?",
            (backup_codes_json, int(time.time()), int(user_id)),
        )
        conn.commit()


def disable_user_totp(user_id):
    """Wipe every 2FA column for the user (setup must restart from scratch)."""
    with db_conn() as conn:
        conn.execute(
            "UPDATE users SET totp_secret=NULL, totp_enabled=0, totp_backup_codes=NULL, totp_enabled_at=NULL WHERE id=?",
            (int(user_id),),
        )
        conn.commit()


def update_user_backup_codes(user_id, backup_codes_json):
    """Replace the stored backup-codes blob (used after consuming a code or
    after regenerating)."""
    with db_conn() as conn:
        conn.execute(
            "UPDATE users SET totp_backup_codes=? WHERE id=?",
            (backup_codes_json, int(user_id)),
        )
        conn.commit()


def get_user_by_email(email):
    with db_conn() as conn:
        row = conn.execute("SELECT * FROM users WHERE email=?", (email.lower(),)).fetchone()
        return dict(row) if row else None


def verify_user_email(token):
    with db_conn() as conn:
        row = conn.execute("SELECT * FROM users WHERE email_token=?", (token,)).fetchone()
        if not row:
            return False, "رابط التفعيل غير صحيح"

        token_created = row["email_token_created_at"] or 0
        # صلاحية الرابط 24 ساعة
        if int(time.time()) - int(token_created) > 86400:
            return False, "انتهت صلاحية رابط التفعيل. سجل مرة أخرى أو اطلب رابطًا جديدًا."

        conn.execute(
            "UPDATE users SET email_verified=1, email_token=NULL, email_token_created_at=NULL WHERE id=?",
            (row["id"],)
        )
        conn.commit()
        return True, None


def set_user_email_token(user_id, token):
    with db_conn() as conn:
        conn.execute(
            "UPDATE users SET email_token=?, email_token_created_at=? WHERE id=?",
            (token, int(time.time()), user_id)
        )
        conn.commit()


def set_password_reset_token(user_id, token):
    with db_conn() as conn:
        conn.execute(
            "UPDATE users SET reset_token=?, reset_token_created_at=? WHERE id=?",
            (token, int(time.time()), user_id)
        )
        conn.commit()


def get_user_by_reset_token(token):
    with db_conn() as conn:
        row = conn.execute("SELECT * FROM users WHERE reset_token=?", (token,)).fetchone()
        return dict(row) if row else None


def reset_user_password(token, new_password):
    with db_conn() as conn:
        row = conn.execute("SELECT * FROM users WHERE reset_token=?", (token,)).fetchone()
        if not row:
            return False, "رابط الاستعادة غير صحيح"

        token_created = row["reset_token_created_at"] or 0
        if int(time.time()) - int(token_created) > 3600:
            return False, "انتهت صلاحية رابط الاستعادة. اطلب رابطًا جديدًا."

        conn.execute(
            "UPDATE users SET password_hash=?, reset_token=NULL, reset_token_created_at=NULL, session_version=COALESCE(session_version,1)+1 WHERE id=?",
            (generate_password_hash(new_password), row["id"])
        )
        conn.commit()
        return True, None


def get_user(user_id):
    with db_conn() as conn:
        row = conn.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
        return dict(row) if row else None


def update_user_profile(user_id, name=None, phone=None):
    with db_conn() as conn:
        conn.execute(
            "UPDATE users SET name=COALESCE(?,name), phone=COALESCE(?,phone) WHERE id=?",
            (name, phone, int(user_id))
        )
        conn.commit()


def set_pending_email_change(user_id, new_email, token):
    with db_conn() as conn:
        conn.execute(
            "UPDATE users SET pending_email=?, pending_email_token=?, pending_email_created_at=? WHERE id=?",
            (new_email.lower().strip(), token, int(time.time()), int(user_id))
        )
        conn.commit()


def confirm_pending_email_change(token):
    with db_conn() as conn:
        row = conn.execute("SELECT * FROM users WHERE pending_email_token=?", (token,)).fetchone()
        if not row:
            return False, "رابط تغيير البريد غير صحيح"

        created = row["pending_email_created_at"] or 0
        if int(time.time()) - int(created) > 86400:
            return False, "انتهت صلاحية رابط تغيير البريد"

        new_email = row["pending_email"]
        exists = conn.execute("SELECT id FROM users WHERE email=? AND id!=?", (new_email, row["id"])).fetchone()
        if exists:
            return False, "هذا البريد مستخدم في حساب آخر"

        conn.execute(
            "UPDATE users SET email=?, email_verified=1, pending_email=NULL, pending_email_token=NULL, pending_email_created_at=NULL WHERE id=?",
            (new_email, row["id"])
        )
        conn.commit()
        return True, None


def set_user_balance(user_id, amount):
    with db_conn() as conn:
        conn.execute("UPDATE users SET balance = ? WHERE id=?", (float(amount or 0), int(user_id)))
        conn.commit()


def change_balance(user_id, amount):
    with db_conn() as conn:
        conn.execute("UPDATE users SET balance = balance + ? WHERE id=?", (amount, user_id))
        conn.commit()


def upsert_game(provider, game_key, name, emoji="🎮", active=1):
    with db_conn() as conn:
        conn.execute("""
            INSERT INTO games (provider, game_key, name, emoji, active)
            VALUES (?,?,?,?,?)
            ON CONFLICT(provider,game_key) DO UPDATE SET
                name=excluded.name,
                emoji=excluded.emoji
        """, (provider, game_key, name, emoji, active))
        conn.commit()


def add_custom_game(provider, game_key, name, emoji="🎮", image_url="", active=1):
    with db_conn() as conn:
        conn.execute("""
            INSERT INTO games (provider, game_key, name, emoji, image_url, active)
            VALUES (?,?,?,?,?,?)
            ON CONFLICT(provider,game_key) DO UPDATE SET
                name=excluded.name,
                emoji=excluded.emoji,
                image_url=excluded.image_url,
                active=excluded.active
        """, (provider, game_key, name, emoji, image_url, active))
        conn.commit()


def set_game_active(provider, game_key, active):
    with db_conn() as conn:
        conn.execute("UPDATE games SET active=? WHERE provider=? AND game_key=?", (1 if active else 0, provider, game_key))
        conn.commit()


# V55: admin-controlled homepage visibility.
def set_game_show_on_home(provider, game_key, show):
    with db_conn() as conn:
        conn.execute(
            "UPDATE games SET show_on_home=? WHERE provider=? AND game_key=?",
            (1 if show else 0, provider, game_key),
        )
        conn.commit()


# V68: ترتيب ظهور اللعبة في الواجهة الرئيسية.
# 0 = الترتيب الافتراضي (حسب الاسم). أي رقم أكبر من 0 يعطي ترتيبًا يدويًا.
def set_game_home_sort_order(provider, game_key, sort_order):
    try:
        v = int(sort_order or 0)
    except Exception:
        v = 0
    if v < 0:
        v = 0
    with db_conn() as conn:
        conn.execute(
            "UPDATE games SET home_sort_order=? WHERE provider=? AND game_key=?",
            (v, provider, game_key),
        )
        conn.commit()


def list_home_games():
    """Return only games flagged by admin as visible on homepage, with product_count & min_price.
    Falls back to an empty list; caller decides the fallback policy."""
    with db_conn() as conn:
        return [dict(r) for r in conn.execute("""
            SELECT g.*,
                   COUNT(p.id) AS product_count,
                   MIN(p.sell_price) AS min_price
            FROM games g
            LEFT JOIN products p ON p.provider=g.provider AND p.game_key=g.game_key AND p.active=1
            WHERE g.active=1 AND g.show_on_home=1
            GROUP BY g.id
            ORDER BY CASE WHEN COALESCE(g.home_sort_order,0)=0 THEN 999999 ELSE g.home_sort_order END ASC,
                     g.name ASC
        """).fetchall()]


def upsert_product(provider, game_key, provider_product_id, name, base_price, sell_price, active=1):
    with db_conn() as conn:
        conn.execute("""
            INSERT INTO products (provider, game_key, provider_product_id, name, base_price, sell_price, active)
            VALUES (?,?,?,?,?,?,?)
            ON CONFLICT(provider, provider_product_id) DO UPDATE SET
                name=excluded.name,
                base_price=excluded.base_price,
                sell_price=excluded.sell_price
        """, (provider, game_key, str(provider_product_id), name, base_price, sell_price, active))
        conn.commit()


def delete_products_for_game(provider, game_key):
    """حذف باقات لعبة محددة قبل إعادة مزامنتها لتجنب بقاء مناطق/باقات قديمة."""
    with db_conn() as conn:
        conn.execute("DELETE FROM products WHERE provider=? AND game_key=?", (provider, game_key))
        conn.commit()


def list_games(provider=None, only_active=True):
    with db_conn() as conn:
        q = "SELECT * FROM games WHERE 1=1"
        args = []
        if provider:
            q += " AND provider=?"; args.append(provider)
        if only_active:
            q += " AND active=1"
        q += " ORDER BY active DESC, name ASC, id ASC"
        return [dict(r) for r in conn.execute(q, args).fetchall()]


def translate_product_name(name):
    name = str(name or "")
    replacements = [
        ("MENA Direct Topup", ""),
        ("Mena Direct Topup", ""),
        ("Direct Topup", "شحن مباشر"),
        ("direct topup", "شحن مباشر"),
        ("PUBG Mobile UC", "شدات ببجي"),
        ("PUBG UC", "شدات ببجي"),
        ("UC", "شدات"),
        ("Unknown Cash", "شدات"),
        ("Diamonds", "جواهر"),
        ("Diamond", "جوهرة"),
        ("Coins", "عملات"),
        ("Coin", "عملة"),
        ("Vouchers", "قسائم"),
        ("Voucher", "قسيمة"),
        ("Cards", "بطاقات"),
        ("Card", "بطاقة"),
        ("Topup", "شحن"),
        ("Top Up", "شحن"),
        ("Package", "باقة"),
    ]
    for old, new in replacements:
        name = name.replace(old, new)
    name = re.sub(r"\\s+", " ", name).strip(" -–—|")
    return name


def list_product_groups(provider, game_key, only_active=True):
    with db_conn() as conn:
        q = "SELECT * FROM product_groups WHERE provider=? AND game_key=?"
        args = [provider, game_key]
        if only_active:
            q += " AND active=1"
        q += " ORDER BY CASE WHEN COALESCE(sort_order,0)=0 THEN 999999 ELSE sort_order END ASC, name ASC"
        return [dict(r) for r in conn.execute(q, args).fetchall()]


def get_product_group(group_id):
    with db_conn() as conn:
        row = conn.execute("SELECT * FROM product_groups WHERE id=?", (int(group_id),)).fetchone()
        return dict(row) if row else None


def create_product_group(provider, game_key, name, image_url="", sort_order=1, active=1):
    with db_conn() as conn:
        now = int(time.time())
        cur = conn.cursor()
        cur.execute("""
            INSERT OR IGNORE INTO product_groups (provider, game_key, name, image_url, sort_order, active, created_at)
            VALUES (?,?,?,?,?,?,?)
        """, (provider, game_key, str(name or "").strip(), image_url or "", int(sort_order or 1), int(active), now))
        conn.commit()
        row = conn.execute("SELECT * FROM product_groups WHERE provider=? AND game_key=? AND name=?", (provider, game_key, str(name or "").strip())).fetchone()
        return dict(row) if row else None


def update_product_group(group_id, name, image_url="", sort_order=1, active=1):
    with db_conn() as conn:
        conn.execute("UPDATE product_groups SET name=?, image_url=?, sort_order=?, active=? WHERE id=?", (str(name or "").strip(), image_url or "", int(sort_order or 1), int(active), int(group_id)))
        conn.commit()


def delete_product_group(group_id):
    with db_conn() as conn:
        conn.execute("UPDATE products SET group_id=NULL WHERE group_id=?", (int(group_id),))
        conn.execute("DELETE FROM product_groups WHERE id=?", (int(group_id),))
        conn.commit()


def update_manual_syp_prices(price_updates):
    with db_conn() as conn:
        for product_id, manual_price_syp in price_updates:
            try:
                value = float(manual_price_syp or 0)
            except Exception:
                value = 0.0
            conn.execute("UPDATE products SET manual_price_syp=? WHERE id=?", (value, int(product_id)))
        conn.commit()


def update_products_admin(product_updates, usd_syp_rate=15000):
    with db_conn() as conn:
        try:
            rate = float(usd_syp_rate or 15000)
        except Exception:
            rate = 15000.0

        for item in product_updates:
            product_id = int(item["product_id"])
            sort_order = int(item.get("sort_order") or 0)
            group_id = int(item["group_id"]) if item.get("group_id") else None
            pricing_mode = item.get("pricing_mode") or "usd"
            if pricing_mode not in ("usd", "auto_syp", "fixed_syp"):
                pricing_mode = "usd"

            try:
                fixed_syp_price = float(item.get("fixed_syp_price") or 0)
            except Exception:
                fixed_syp_price = 0.0

            if pricing_mode == "fixed_syp" and fixed_syp_price > 0 and rate > 0:
                sell_price = round(fixed_syp_price / rate, 4)
                conn.execute(
                    "UPDATE products SET sort_order=?, group_id=?, pricing_mode=?, fixed_syp_price=?, sell_price=? WHERE id=?",
                    (sort_order, group_id, pricing_mode, fixed_syp_price, sell_price, product_id)
                )
            else:
                conn.execute(
                    "UPDATE products SET sort_order=?, group_id=?, pricing_mode=?, fixed_syp_price=? WHERE id=?",
                    (sort_order, group_id, pricing_mode, 0, product_id)
                )
        conn.commit()


def update_game_pricing(provider, game_key, pricing_currency):
    value = pricing_currency if pricing_currency in ("GLOBAL", "USD", "SYP") else "GLOBAL"
    with db_conn() as conn:
        conn.execute("UPDATE games SET pricing_currency=? WHERE provider=? AND game_key=?", (value, provider, game_key))
        conn.commit()


def list_products(provider, game_key, only_active=True, group_id=None):
    with db_conn() as conn:
        positive_count = 0
        if only_active:
            positive_count = conn.execute(
                "SELECT COUNT(*) FROM products WHERE provider=? AND game_key=? AND active=1 AND COALESCE(sort_order,0)>0",
                (provider, game_key)
            ).fetchone()[0]

        q = "SELECT * FROM products WHERE provider=? AND game_key=?"
        args = [provider, game_key]
        if only_active:
            q += " AND active=1"
            if positive_count > 0:
                q += " AND COALESCE(sort_order,0)>0"
        if group_id is not None:
            q += " AND group_id=?"
            args.append(int(group_id))
        q += " ORDER BY CASE WHEN COALESCE(sort_order,0)=0 THEN 999999 ELSE sort_order END ASC, sell_price ASC, id ASC"
        rows = [dict(r) for r in conn.execute(q, args).fetchall()]

        if only_active and not rows and group_id is None:
            rows = [dict(r) for r in conn.execute(
                "SELECT * FROM products WHERE provider=? AND game_key=? ORDER BY CASE WHEN COALESCE(sort_order,0)=0 THEN 999999 ELSE sort_order END ASC, sell_price ASC, id ASC",
                (provider, game_key)
            ).fetchall()]

        for row in rows:
            row["display_name"] = translate_product_name(row.get("name"))

        return rows


def list_public_product_groups_for_home():
    with db_conn() as conn:
        return [dict(r) for r in conn.execute("""
            SELECT pg.*,
                   g.name AS game_name,
                   g.emoji AS game_emoji,
                   g.image_url AS game_image_url,
                   COUNT(p.id) AS product_count,
                   MIN(p.sell_price) AS min_price
            FROM product_groups pg
            JOIN games g ON g.provider=pg.provider AND g.game_key=pg.game_key
            LEFT JOIN products p ON p.provider=pg.provider AND p.game_key=pg.game_key AND p.group_id=pg.id AND p.active=1
            WHERE pg.active=1 AND g.active=1
            GROUP BY pg.id
            ORDER BY CASE WHEN COALESCE(pg.sort_order,0)=0 THEN 999999 ELSE pg.sort_order END ASC, g.name ASC, pg.name ASC
        """).fetchall()]


def list_public_games(only_active=True):
    with db_conn() as conn:
        q = """
            SELECT g.*,
                   COUNT(p.id) AS product_count,
                   MIN(p.sell_price) AS min_price
            FROM games g
            LEFT JOIN products p ON p.provider=g.provider AND p.game_key=g.game_key AND p.active=1
            WHERE 1=1
        """
        args = []
        if only_active:
            q += " AND g.active=1"
        q += " GROUP BY g.id ORDER BY g.active DESC, g.name ASC"
        return [dict(r) for r in conn.execute(q, args).fetchall()]


def list_all_game_groups():
    with db_conn() as conn:
        return [dict(r) for r in conn.execute("""
            SELECT g.*,
                   COUNT(p.id) AS product_count,
                   MIN(p.sell_price) AS min_price
            FROM games g
            LEFT JOIN products p ON p.provider=g.provider AND p.game_key=g.game_key
            GROUP BY g.id
            ORDER BY g.provider, g.name
        """).fetchall()]


def list_product_games_from_products():
    """اكتشاف ألعاب موجودة في جدول المنتجات حتى لو لم تظهر في جدول games."""
    with db_conn() as conn:
        return [dict(r) for r in conn.execute("""
            SELECT provider, game_key, COUNT(*) AS product_count, MIN(sell_price) AS min_price
            FROM products
            GROUP BY provider, game_key
            ORDER BY provider, game_key
        """).fetchall()]


def accounting_summary():
    with db_conn() as conn:
        total_sales = conn.execute("SELECT COALESCE(SUM(price),0) s FROM orders WHERE status='completed'").fetchone()["s"]
        total_cost = conn.execute("""
            SELECT COALESCE(SUM(p.base_price),0) s
            FROM orders o
            LEFT JOIN products p ON p.id=o.product_id
            WHERE o.status='completed'
        """).fetchone()["s"]
        total_profit = float(total_sales or 0) - float(total_cost or 0)
        orders_count = conn.execute("SELECT COUNT(*) c FROM orders WHERE status='completed'").fetchone()["c"]

        by_game = [dict(r) for r in conn.execute("""
            SELECT o.game_name,
                   COUNT(*) AS orders_count,
                   COALESCE(SUM(o.price),0) AS sales,
                   COALESCE(SUM(p.base_price),0) AS cost,
                   COALESCE(SUM(o.price - COALESCE(p.base_price,0)),0) AS profit
            FROM orders o
            LEFT JOIN products p ON p.id=o.product_id
            WHERE o.status='completed'
            GROUP BY o.game_name
            ORDER BY profit DESC
        """).fetchall()]

        recent = [dict(r) for r in conn.execute("""
            SELECT o.id, o.order_code, o.game_name, o.product_name, o.price,
                   COALESCE(p.base_price,0) AS cost,
                   (o.price - COALESCE(p.base_price,0)) AS profit,
                   o.created_at, u.email AS user_email
            FROM orders o
            LEFT JOIN products p ON p.id=o.product_id
            LEFT JOIN users u ON u.id=o.user_id
            WHERE o.status='completed'
            ORDER BY o.id DESC
            LIMIT 100
        """).fetchall()]
    sales_override_raw = get_setting("sales_override", "")
    try:
        sales_override = float(sales_override_raw) if str(sales_override_raw).strip() != "" else None
    except Exception:
        sales_override = None
    display_sales = sales_override if sales_override is not None else total_sales
    display_profit = float(display_sales or 0) - float(total_cost or 0)
    return {
        "sales": total_sales,
        "display_sales": display_sales,
        "sales_override": sales_override_raw,
        "cost": total_cost,
        "profit": total_profit,
        "display_profit": display_profit,
        "orders_count": orders_count,
        "by_game": by_game,
        "recent": recent,
    }



def get_product(product_id):
    with db_conn() as conn:
        row = conn.execute("SELECT * FROM products WHERE id=? AND active=1", (product_id,)).fetchone()
        return dict(row) if row else None


def get_product_by_id(product_id):
    """V48: fetch a product by internal DB id even if inactive.
    Used by RQ worker when re-resolving an order's product to send to supplier."""
    with db_conn() as conn:
        row = conn.execute("SELECT * FROM products WHERE id=?", (product_id,)).fetchone()
        return dict(row) if row else None


def get_game(provider, game_key):
    with db_conn() as conn:
        row = conn.execute("SELECT * FROM games WHERE provider=? AND game_key=?", (provider, game_key)).fetchone()
        return dict(row) if row else None


def _rate():
    try:
        return float(get_setting("usd_syp_rate", "15000") or 15000)
    except Exception:
        return 15000.0


def _manual_prices_enabled():
    return get_setting("manual_price_edit_enabled", "0") == "1"


def _amount_to_usd(amount, currency):
    """Database balance is ALWAYS stored in USD. Display conversion happens only in templates/app."""
    try:
        amount = float(amount or 0)
    except Exception:
        amount = 0.0
    currency = currency or "USD"
    rate = _rate()
    if currency == "SYP" and rate > 0:
        return round(amount / rate, 4)
    return round(amount, 4)


def _product_price_usd(product):
    """Order price is ALWAYS stored in USD. Manual SYP prices are converted to USD at order time."""
    rate = _rate()
    try:
        sell_usd = float(product["sell_price"] or 0)
    except Exception:
        sell_usd = 0.0
    try:
        manual_syp = float(product["manual_price_syp"] or 0) if "manual_price_syp" in product.keys() else 0.0
    except Exception:
        manual_syp = 0.0

    if _manual_prices_enabled() and manual_syp > 0 and rate > 0:
        return round(manual_syp / rate, 4)

    return round(sell_usd, 4)



def create_order(user, product, game, player_id):
    """Create an order and atomically deduct balance.

    V47: The balance check and deduction are now inside a single BEGIN IMMEDIATE
    transaction so two concurrent requests cannot both pass the balance check and
    double-spend.  The UPDATE only touches rows where balance >= price, and we
    verify rowcount afterwards — if it is 0 the user had insufficient funds and
    we roll back + raise InsufficientBalance.
    """
    now = int(time.time())
    # V50 SECURITY (C2): order_code was previously f"ORD{now}{user_id}" which
    # is trivially predictable — an attacker who knows a user_id and the rough
    # time of order creation can guess order codes and probe any endpoint that
    # treats the code as a bearer credential. Use a cryptographically random
    # token instead. Collision risk at 10 bytes (~13 chars) is negligible
    # (2^80 keyspace) and orders.order_code has a UNIQUE constraint.
    order_code = f"ORD{secrets.token_urlsafe(10)}"
    final_price = _product_price_usd(product)
    product_label = translate_product_name(product.get("display_name") or product.get("name") or "")
    with db_conn() as conn:
        try:
            # BEGIN IMMEDIATE acquires a write lock immediately, preventing
            # concurrent writers from sneaking in between our read and write.
            conn.execute("BEGIN IMMEDIATE")
            cur = conn.cursor()
            # Atomic deduction: only succeeds when balance is sufficient.
            cur.execute(
                "UPDATE users SET balance = balance - ? WHERE id = ? AND balance >= ?",
                (final_price, user["id"], final_price),
            )
            if cur.rowcount == 0:
                conn.rollback()
                raise InsufficientBalance("رصيدك غير كافٍ")
            cur.execute("""
                INSERT INTO orders (order_code,user_id,provider,game_key,game_name,product_id,product_name,player_id,price,status,created_at,updated_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
            """, (order_code, user["id"], product["provider"], product["game_key"], game["name"], product["id"], product_label, player_id, final_price, "waiting", now, now))
            conn.commit()
            order_id = cur.lastrowid
        except InsufficientBalance:
            raise
        except Exception:
            conn.rollback()
            raise
    return order_id, order_code


def update_order(order_id, status, provider_order_id=None, note=None):
    with db_conn() as conn:
        try:
            conn.execute("BEGIN IMMEDIATE")
            cur = conn.cursor()
            old_order = cur.execute("SELECT status, user_id, price FROM orders WHERE id=?", (order_id,)).fetchone()
            if not old_order:
                conn.rollback()
                return False

            # V69.1: حماية من الانتقالات غير الآمنة. سابقاً كان يمكن نقل
            # طلب completed → rejected وهذا يُعيد المبلغ للمستخدم رغم أن
            # المنتج سُلِّم له (استرداد مزدوج). القاعدة:
            #   - completed و rejected حالات نهائية لا يُسمح بالخروج منها
            #     إلى حالة مختلفة (إعادة فتح طلب نهائي يجب أن تتم يدوياً
            #     في SQL مع تدقيق ومراجعة محاسبية).
            #   - يُسمح بـ no-op (نفس الحالة) لأن tasks.py قد تستدعينا
            #     لتحديث note فقط، والحماية من ازدواج الاسترداد محفوظة
            #     بالشرط `old_status != 'rejected'` أدناه.
            old_status = old_order["status"]
            if old_status in ("completed", "rejected") and old_status != status:
                conn.rollback()
                return False

            cur.execute("UPDATE orders SET status=?, provider_order_id=?, note=?, updated_at=? WHERE id=?",
                        (status, provider_order_id, note, int(time.time()), order_id))

            # إرجاع الرصيد فقط إذا كانت الحالة السابقة ليست مرفوضة والحالة الجديدة مرفوضة
            if status == "rejected" and old_status != "rejected":
                cur.execute("UPDATE users SET balance = balance + ? WHERE id=?",
                            (old_order["price"], old_order["user_id"]))
            conn.commit()
            return True
        except Exception:
            conn.rollback()
            raise



def list_user_orders(user_id):
    with db_conn() as conn:
        return [dict(r) for r in conn.execute("SELECT * FROM orders WHERE user_id=? ORDER BY id DESC LIMIT 50", (user_id,)).fetchall()]


def list_orders(status=None):
    with db_conn() as conn:
        if status:
            return [dict(r) for r in conn.execute("SELECT * FROM orders WHERE status=? ORDER BY id DESC", (status,)).fetchall()]
        else:
            return [dict(r) for r in conn.execute("SELECT * FROM orders ORDER BY id DESC LIMIT 200").fetchall()]


def get_order(order_id):
    with db_conn() as conn:
        row = conn.execute("SELECT * FROM orders WHERE id=?", (order_id,)).fetchone()
        return dict(row) if row else None


def stats():
    with db_conn() as conn:
        return {
            "users": conn.execute("SELECT COUNT(*) c FROM users").fetchone()["c"],
            "orders": conn.execute("SELECT COUNT(*) c FROM orders").fetchone()["c"],
            "processing": conn.execute("SELECT COUNT(*) c FROM orders WHERE status='processing'").fetchone()["c"],
            "completed": conn.execute("SELECT COUNT(*) c FROM orders WHERE status='completed'").fetchone()["c"],
            "pending": conn.execute("SELECT COUNT(*) c FROM orders WHERE status='pending'").fetchone()["c"],
            "revenue": conn.execute("SELECT COALESCE(SUM(price),0) s FROM orders WHERE status='completed'").fetchone()["s"],
        }


def list_users():
    with db_conn() as conn:
        return [dict(r) for r in conn.execute("SELECT id,name,email,phone,role,balance,active,email_verified,created_at FROM users ORDER BY id DESC").fetchall()]


def search_users(q=None):
    with db_conn() as conn:
        if q:
            like = f"%{_escape_like(q)}%"
            args = [like, like, like]
            extra_ids = []
            if str(q).isdigit():
                extra_ids.append(int(q))
            # B608 suppressed: only a STATIC fragment (" OR u.id=?") may be
            # appended below; all user input is bound via parameters.
            _sql = (
                "SELECT DISTINCT u.id,u.name,u.email,u.phone,u.role,u.balance,"
                "u.active,u.email_verified,u.created_at "
                "FROM users u "
                "LEFT JOIN orders o ON o.user_id=u.id "
                "WHERE u.name LIKE ? ESCAPE '\\' OR u.email LIKE ? ESCAPE '\\' OR u.phone LIKE ? ESCAPE '\\' "
                "OR o.player_id LIKE ? ESCAPE '\\'"
                + (" OR u.id=?" if extra_ids else "")
                + " ORDER BY u.id DESC LIMIT 300"
            )
            return [dict(r) for r in conn.execute(_sql, args + [like] + extra_ids).fetchall()]  # nosec B608
        else:
            return [dict(r) for r in conn.execute("SELECT id,name,email,phone,role,balance,active,email_verified,created_at FROM users ORDER BY id DESC LIMIT 300").fetchall()]


def get_user_by_id(user_id):
    with db_conn() as conn:
        row = conn.execute("SELECT id,name,email,phone,role,balance,active,email_verified,created_at FROM users WHERE id=?", (int(user_id),)).fetchone()
        return dict(row) if row else None


def user_financial_summary(user_id):
    # V49-HOTFIX: the previous version used SUM(amount) which was WRONG because
    # `amount` is in the deposit method's native currency (SYP or USD) — so a
    # user with a 5000 SYP deposit and a 10 USD deposit had a reported total
    # of 5010, mixing two unrelated currencies. Always sum `amount_usd` so the
    # total is expressed in a single unit (USD internally, then rendered by
    # wallet_money in the template).
    with db_conn() as conn:
        return {
            "deposits_count": conn.execute("SELECT COUNT(*) c FROM deposits WHERE user_id=?", (user_id,)).fetchone()["c"],
            "deposits_approved": conn.execute("SELECT COUNT(*) c FROM deposits WHERE user_id=? AND status='approved'", (user_id,)).fetchone()["c"],
            "deposits_total_paid": conn.execute(
                "SELECT COALESCE(SUM(COALESCE(amount_usd, 0)),0) s "
                "FROM deposits WHERE user_id=? AND status='approved'", (user_id,)
            ).fetchone()["s"],
            "orders_count": conn.execute("SELECT COUNT(*) c FROM orders WHERE user_id=?", (user_id,)).fetchone()["c"],
            "orders_total": conn.execute("SELECT COALESCE(SUM(price),0) s FROM orders WHERE user_id=?", (user_id,)).fetchone()["s"],
        }


def list_user_deposits_admin(user_id):
    with db_conn() as conn:
        return [dict(r) for r in conn.execute("SELECT * FROM deposits WHERE user_id=? ORDER BY id DESC LIMIT 300", (int(user_id),)).fetchall()]


# --- Payment Methods & Deposits ---

def list_payment_methods(only_active=False):
    with db_conn() as conn:
        q = "SELECT * FROM payment_methods"
        if only_active:
            q += " WHERE active=1"
        q += " ORDER BY name"
        return [dict(r) for r in conn.execute(q).fetchall()]


def get_payment_method(method_id):
    with db_conn() as conn:
        row = conn.execute("SELECT * FROM payment_methods WHERE id=?", (method_id,)).fetchone()
        return dict(row) if row else None


def update_payment_method(method_id, name=None, emoji=None, address=None, instructions=None, active=None, currency=None):
    method = get_payment_method(method_id)
    if not method:
        return False
    name = method["name"] if name is None else name
    emoji = method["emoji"] if emoji is None else emoji
    address = method["address"] if address is None else address
    instructions = method["instructions"] if instructions is None else instructions
    active = method["active"] if active is None else (1 if active else 0)
    currency = method.get("currency", "USD") if currency is None else currency
    with db_conn() as conn:
        conn.execute("""
            UPDATE payment_methods SET name=?, emoji=?, address=?, instructions=?, active=?, currency=?
            WHERE id=?
        """, (name, emoji, address, instructions, active, currency, method_id))
        conn.commit()
    return True


def can_download_proof(user_id: int, is_admin: bool, filename: str) -> bool:
    """V53: IDOR fix — verify proof ownership via DB, not filename prefix.

    Admins can download any proof. Regular users can only download proofs
    that are linked to one of their own deposits (proof_filename column).
    """
    if is_admin:
        return True
    with db_conn() as conn:
        row = conn.execute(
            "SELECT 1 FROM deposits WHERE user_id=? AND proof_filename=? LIMIT 1",
            (user_id, filename),
        ).fetchone()
    return row is not None


def create_deposit(user_id, amount, method_id, proof, amount_usd=None, proof_filename=None):
    method = get_payment_method(method_id)
    if not method:
        return None
    currency = method.get("currency", "USD")
    # V69: server-side dedup. Prevents the "double-click / lost-network /
    # back-button-resubmit" pattern where the same user creates two
    # identical pending deposits within seconds, which then both have to
    # be reviewed and (worse) approved by the admin team.
    # Window: 60 seconds. Two pending deposits with the same user, method,
    # and amount inside that window are treated as the same submission and
    # the existing deposit's id/code are returned (idempotent reply, so the
    # caller still flashes "تم استلام طلب الشحن" once).
    try:
        _amount_check = float(amount or 0)
    except Exception:
        _amount_check = 0.0
    if _amount_check > 0:
        with db_conn() as conn:
            row = conn.execute(
                """
                SELECT id, deposit_code FROM deposits
                WHERE user_id=?
                  AND method=?
                  AND status='pending'
                  AND ABS(amount - ?) < 0.005
                  AND created_at >= ?
                ORDER BY id DESC LIMIT 1
                """,
                (
                    int(user_id),
                    method["name"],
                    _amount_check,
                    int(time.time()) - 60,
                ),
            ).fetchone()
            if row:
                return row["id"], row["deposit_code"]
    # V49-HOTFIX (defense in depth): always recompute amount_usd server-side
    # from the amount + method currency + current rate, regardless of what the
    # caller passed. This guarantees that:
    #   1. A 5000 SYP deposit is stored as amount=5000, currency='SYP',
    #      amount_usd=(5000/rate), NOT 5000 USD.
    #   2. Approving the deposit credits the correct USD value to the wallet.
    #   3. A compromised form handler cannot inflate amount_usd by a factor
    #      of the exchange rate (which was the user-reported symptom:
    #      "5000 SYP was treated as $5000 multiplied by the rate").
    try:
        _amt = float(amount or 0)
    except Exception:
        _amt = 0.0
    if currency == "SYP":
        try:
            _rate_val = float(get_setting("usd_syp_rate", "15000") or 15000)
        except Exception:
            _rate_val = 15000.0
        amount_usd = round(_amt / _rate_val, 4) if _rate_val > 0 else 0.0
    else:
        amount_usd = round(_amt, 4)
    now = int(time.time())
    # V50 SECURITY (CA): same predictability issue as order_code. Use a
    # random token so deposit codes cannot be enumerated by attackers.
    code = f"DEP{secrets.token_urlsafe(10)}"
    with db_conn() as conn:
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO deposits (deposit_code,user_id,amount,method,proof,status,created_at,currency,amount_usd,proof_filename)
            VALUES (?,?,?,?,?,?,?,?,?,?)
        """, (code, user_id, amount, method["name"], proof, "pending", now, currency, amount_usd, proof_filename))
        conn.commit()
        deposit_id = cur.lastrowid
    return deposit_id, code


def list_deposits_for_user(user_id):
    with db_conn() as conn:
        return [dict(r) for r in conn.execute("""
            SELECT *
            FROM deposits
            WHERE user_id=?
            ORDER BY id DESC
            LIMIT 200
        """, (int(user_id),)).fetchall()]


def list_deposits(status=None):
    with db_conn() as conn:
        if status:
            return [dict(r) for r in conn.execute("""
                SELECT d.*, u.name user_name, u.email user_email
                FROM deposits d JOIN users u ON u.id=d.user_id
                WHERE d.status=?
                ORDER BY d.id DESC
            """, (status,)).fetchall()]
        else:
            return [dict(r) for r in conn.execute("""
                SELECT d.*, u.name user_name, u.email user_email
                FROM deposits d JOIN users u ON u.id=d.user_id
                ORDER BY d.id DESC LIMIT 200
            """).fetchall()]


def get_deposit(deposit_id):
    with db_conn() as conn:
        row = conn.execute("SELECT * FROM deposits WHERE id=?", (deposit_id,)).fetchone()
        return dict(row) if row else None


def update_deposit(deposit_id, status):
    with db_conn() as conn:
        try:
            conn.execute("BEGIN IMMEDIATE")
            cur = conn.cursor()
            cur.execute(
                "UPDATE deposits SET status=? WHERE id=? AND status='pending'",
                (status, deposit_id)
            )
            if cur.rowcount == 0:
                conn.rollback()
                return False  # تمت المعالجة مسبقاً
            if status == "approved":
                dep = cur.execute("SELECT * FROM deposits WHERE id=?", (deposit_id,)).fetchone()
                # V49-HOTFIX: `dep` is a sqlite3.Row which has NO .get() method —
                # the previous call `dep.get("currency", "USD")` raised AttributeError
                # and caused a 500 error every time an admin clicked Approve.
                # Also, we now prefer the pre-computed `amount_usd` column (filled
                # by create_deposit at submission time) over re-converting `amount`.
                # This way approval uses the exact rate that was shown to the user
                # when they submitted the deposit — not today's rate if it changed.
                dep_keys = dep.keys()
                amount_usd_stored = None
                if "amount_usd" in dep_keys:
                    try:
                        v = dep["amount_usd"]
                        if v is not None and float(v) > 0:
                            amount_usd_stored = float(v)
                    except Exception:
                        amount_usd_stored = None
                if amount_usd_stored is not None:
                    amount_to_add = round(amount_usd_stored, 4)
                else:
                    # Legacy deposits (amount_usd missing/0): fall back to converting
                    # the paid amount using the deposit's currency column.
                    dep_currency = dep["currency"] if "currency" in dep_keys else "USD"
                    amount_to_add = _amount_to_usd(dep["amount"], dep_currency or "USD")
                cur.execute("UPDATE users SET balance = balance + ? WHERE id=?",
                             (amount_to_add, dep["user_id"]))
            conn.commit()
            return True
        except Exception:
            conn.rollback()
            raise


def list_orders_for_auto_refresh():
    """طلبات لديها رقم طلب مورد وتحتاج تحديث حالة."""
    with db_conn() as conn:
        return [dict(r) for r in conn.execute("""
            SELECT * FROM orders
            WHERE status IN ('supplier_pending','processing')
              AND provider_order_id IS NOT NULL
              AND provider_order_id != ''
            ORDER BY id ASC
            LIMIT 100
        """).fetchall()]


def get_order_public(order_id, user_id=None):
    # V50 SECURITY (CC): previously `user_id=None` would return the order
    # regardless of ownership — a latent IDOR if any caller forgot to pass
    # user_id. Require an explicit owner id (or admin sentinel) to look up.
    # Pass user_id="*" from admin code paths when cross-user access is
    # intentionally required.
    if user_id is None:
        raise ValueError("get_order_public requires an explicit user_id; "
                         "use user_id='*' for admin access")
    with db_conn() as conn:
        if user_id == "*":
            row = conn.execute("SELECT * FROM orders WHERE id=?", (order_id,)).fetchone()
        else:
            row = conn.execute("SELECT * FROM orders WHERE id=? AND user_id=?", (order_id, user_id)).fetchone()
        return dict(row) if row else None


def update_game_image(provider, game_key, image_url):
    with db_conn() as conn:
        conn.execute("UPDATE games SET image_url=? WHERE provider=? AND game_key=?", (image_url, provider, game_key))
        conn.commit()


def list_all_games_for_admin():
    with db_conn() as conn:
        return [dict(r) for r in conn.execute("SELECT * FROM games ORDER BY provider, name").fetchall()]


def list_all_products_for_admin(provider, game_key):
    with db_conn() as conn:
        rows = [dict(r) for r in conn.execute("""
            SELECT p.*, g.name AS group_name
            FROM products p
            LEFT JOIN product_groups g ON p.group_id=g.id
            WHERE p.provider=? AND p.game_key=?
            ORDER BY COALESCE(p.group_id,0), p.sort_order ASC, p.sell_price ASC, p.id ASC
        """, (provider, game_key)).fetchall()]
        for row in rows:
            row["display_name"] = translate_product_name(row.get("name"))
        return rows


def update_product_sort_orders(order_pairs):
    with db_conn() as conn:
        for product_id, sort_order in order_pairs:
            conn.execute("UPDATE products SET sort_order=? WHERE id=?", (int(sort_order), int(product_id)))
        conn.commit()


def update_profit_margin(margin):
    """
    Apply the new profit margin to ALL products immediately.
    This previously failed to take effect because:
      - products with pricing_mode='fixed_syp' kept their fixed_syp_price
        and recomputed sell_price from that, ignoring the new margin.
      - products with manual_price_syp > 0 (when manual editing was on) used
        the manual SYP price instead of the margin-based USD sell_price.
    To make "save margin" actually update prices everywhere, we now reset
    those overrides as part of the operation.
    """
    margin = float(margin)
    with db_conn() as conn:
        cur = conn.cursor()
        cur.execute("INSERT OR REPLACE INTO settings (key,value) VALUES (?,?)",
                    ("profit_margin", str(margin)))
        # Recompute sell_price for every product from its base cost.
        cur.execute("UPDATE products SET sell_price = ROUND(COALESCE(base_price,0) * ?, 2)",
                    (margin,))
        # Drop fixed-SYP overrides so the new margin is what the user sees.
        try:
            cur.execute("UPDATE products SET pricing_mode='usd', fixed_syp_price=0 "
                        "WHERE pricing_mode='fixed_syp'")
        except Exception:
            pass
        # Drop manual SYP price overrides; they would otherwise hide the margin.
        try:
            cur.execute("UPDATE products SET manual_price_syp=0 WHERE manual_price_syp>0")
        except Exception:
            pass
        conn.commit()



def _slugify_game_key(text):
    text = str(text or "game").strip().lower()
    text = re.sub(r"[^a-z0-9]+", "_", text)
    text = re.sub(r"_+", "_", text).strip("_")
    return text[:70] or "game"


def _safe_float(value):
    try:
        return float(value or 0)
    except Exception:
        return 0.0


def seed_local_provider_catalog(force=False):
    """
    يجهز كتالوج المورد 1 من ملف g2bulk_services.json الموجود داخل المشروع.
    الهدف: ظهور كل ألعاب/تصنيفات المورد في لوحة الإدارة بدون انتظار API.
    الألعاب تكون غير مفعلة افتراضيًا، ويمكن للأدمن تفعيل ما يريده.
    """
    with db_conn() as conn:
        current = conn.execute("SELECT value FROM settings WHERE key='local_catalog_seeded'").fetchone()
        if current and current["value"] == "1" and not force:
            return

        margin_row = conn.execute("SELECT value FROM settings WHERE key='profit_margin'").fetchone()
        try:
            margin = float(margin_row["value"] if margin_row else 1.20)
        except Exception:
            margin = 1.20

        path = os.path.join(os.path.dirname(__file__), "g2bulk_services.json")
        if not os.path.exists(path):
            conn.execute("INSERT OR REPLACE INTO settings (key,value) VALUES (?,?)", ("local_catalog_seeded", "1"))
            conn.commit()
            return

        try:
            with open(path, "r", encoding="utf-8") as f:
                services = json.load(f)
        except Exception:
            return

        featured_categories = {
            "freefire middle east": ("freefire", "Free Fire", "🔥", 1),
            "free fire middle east": ("freefire", "Free Fire", "🔥", 1),
            "pubg mobile": ("pubg_mobile", "PUBG Mobile", "🔫", 1),
            "pubg": ("pubg_mobile", "PUBG Mobile", "🔫", 1),
            "fc mobile": ("fc_mobile", "FC Mobile", "⚽", 1),
            "ea fc mobile": ("fc_mobile", "FC Mobile", "⚽", 1),
        }

        grouped = {}
        for item in services:
            category = str(item.get("category") or "Other").strip() or "Other"
            grouped.setdefault(category, []).append(item)

        for category, items in grouped.items():
            cat_low = category.lower().strip()
            if cat_low in featured_categories:
                game_key, game_name, emoji, active = featured_categories[cat_low]
            elif "freefire middle east" in cat_low or "free fire middle east" in cat_low:
                game_key, game_name, emoji, active = "freefire", "Free Fire", "🔥", 1
            elif "pubg" in cat_low:
                game_key, game_name, emoji, active = "pubg_mobile", "PUBG Mobile", "🔫", 1
            elif "fc mobile" in cat_low or "ea fc" in cat_low:
                game_key, game_name, emoji, active = "fc_mobile", "FC Mobile", "⚽", 1
            else:
                game_key = _slugify_game_key(category)
                game_name = category
                emoji = "🎮"
                active = 0

            conn.execute("""
                INSERT INTO games (provider, game_key, name, emoji, active)
                VALUES (?,?,?,?,?)
                ON CONFLICT(provider, game_key) DO UPDATE SET
                    name=excluded.name,
                    emoji=excluded.emoji
            """, ("server1", game_key, game_name, emoji, active))

            for svc in items:
                service_id = svc.get("service")
                if not service_id:
                    continue
                name = str(svc.get("name") or category)
                if game_key == "freefire":
                    name = name.replace("Freefire Middle East - ", "").replace("Free Fire Middle East - ", "")
                base_price = _safe_float(svc.get("rate", 0))
                sell_price = round(base_price * margin, 2)
                conn.execute("""
                    INSERT INTO products (provider, game_key, provider_product_id, name, base_price, sell_price, active)
                    VALUES (?,?,?,?,?,?,1)
                    ON CONFLICT(provider, provider_product_id) DO UPDATE SET
                        game_key=excluded.game_key,
                        name=excluded.name,
                        base_price=excluded.base_price,
                        sell_price=excluded.sell_price
                """, ("server1", game_key, str(service_id), name, base_price, sell_price))

        for provider, game_key, game_name, emoji, active in [
            ("server2", "freefire", "Free Fire", "🔥", 0),
            ("server2", "pubg_mobile", "PUBG Mobile", "🔫", 0),
        ]:
            conn.execute("""
                INSERT INTO games (provider, game_key, name, emoji, active)
                VALUES (?,?,?,?,?)
                ON CONFLICT(provider,game_key) DO UPDATE SET name=excluded.name, emoji=excluded.emoji
            """, (provider, game_key, game_name, emoji, active))

        conn.execute("INSERT OR REPLACE INTO settings (key,value) VALUES (?,?)", ("local_catalog_seeded", "1"))
        conn.commit()


# ===================== V44: attach generated game posters =====================
# V63: 107 new posters were added (game-covers package). Many regional
# variants share the same artwork (e.g. genshin_impact_brazil should reuse
# genshin_impact.webp), and a few keys differ slightly from the poster
# filename (e.g. eafc_mobile_singapore -> fc_mobile, arknight_endfield ->
# arknights_endfield). The lookup below adds:
#   1. base-name fallback (drop trailing region/suffix segments).
#   2. an explicit alias table for keys that don't follow the pattern.
_POSTER_ALIASES = {
    # Free Fire family (all regions share the same cover).
    "freefire": "free_fire",
    "freefire_bangladesh": "free_fire",
    "freefire_brazil": "free_fire",
    "freefire_europe": "free_fire",
    "freefire_global": "free_fire",
    "freefire_indonesia": "free_fire",
    "freefire_latam": "free_fire",
    "freefire_middle_east": "free_fire",
    "freefire_sg": "free_fire",
    "freefire_sgmy": "free_fire",
    "freefire_taiwan": "free_fire",
    "freefire_thailand": "free_fire",
    "freefire_vietnam": "free_fire",
    # FC Mobile / EAFC family.
    "eafc_24": "fc_mobile",
    "eafc_mobile_cambodia": "fc_mobile",
    "eafc_mobile_malaysia": "fc_mobile",
    "eafc_mobile_singapore": "fc_mobile",
    # Slight name mismatches between catalog slug and poster filename.
    "age_of_empire_mobile": "age_of_empires_mobile",
    "arknight_endfield": "arknights_endfield",
    "cats_crash_arena_turbo_stars": "cats_arena",
    "crossfire_legend": "crossfire_mobile",
    "garena_deltaforce_malaysia": "delta_force",
    "garena_deltaforce_singapore": "delta_force",
    "gov_nikke": "goddess_of_victory_nikke",
    "harry_potter_magic_awaken": "harry_potter_magic_awakened",
    "legend_of_the_phoenix": "legend_of_phoenix",
    "lord_of_the_rings_rise_to_war": "lord_of_rings_war",
    "puzzles_and_survival": "puzzles_survival",
    "ragnarok_crush": "ragnarok_origin",
    "ragnarok_idle_adventure_plus": "ragnarok_origin",
    "sky_children_of_the_light": "sky_children_light",
    "undawn_global": "garena_undawn",
    # V64: Common short keys that admins might use manually.
    "pubg": "pubg_mobile",
    "mlbb": "mobile_legends",
    "ml": "mobile_legends",
    "cod": "call_of_duty_mobile",
    "cod_mobile": "call_of_duty_mobile",
    "lol": "league_of_legends",
    "ff": "free_fire",
    "free_fire_middle_east": "free_fire",
    # Mobile Legends variants.
    "mobile_legends_exclusive": "mobile_legends",
    "mobile_legends_limited_promo": "mobile_legends",
    "mobile_legends_special": "mobile_legends",
}


def _resolve_poster_key(gk, available):
    """Return the poster basename to use for a given game_key, or None.

    Resolution order:
      1. exact match against `available`
      2. explicit alias table (`_POSTER_ALIASES`)
      3. progressively drop trailing _segment(s) (e.g. genshin_impact_brazil
         -> genshin_impact_brazil? no -> genshin_impact_brazil drop tail
         -> genshin_impact -> match).
    """
    if not gk:
        return None
    if gk in available:
        return gk
    alias = _POSTER_ALIASES.get(gk)
    if alias and alias in available:
        return alias
    parts = gk.split("_")
    while len(parts) > 1:
        parts.pop()
        cand = "_".join(parts)
        if cand in available:
            return cand
        cand_alias = _POSTER_ALIASES.get(cand)
        if cand_alias and cand_alias in available:
            return cand_alias
    return None


def attach_generated_posters():
    """For every game whose image_url is empty (or points to a missing
    file), attach the closest matching poster from static/img/games/.

    See `_resolve_poster_key` for the matching strategy (exact -> alias ->
    base-name fallback). Returns the number of rows updated.

    V65: posters can now be `.jpg` (new high-res artwork) or `.webp` (legacy
    thumbnails). JPG is preferred when both exist for the same game_key.

    V66 SELF-HEAL: V65 replaced 125 webp posters with jpg files but the DB
    still had the old `/static/img/games/<key>.webp` paths cached, so most
    games rendered a broken image. We now also clear/refresh any
    auto-generated `/static/img/games/...` URL whose target file no longer
    exists on disk. Admin-uploaded URLs (everything that does NOT start with
    `/static/img/games/`) are still left untouched.
    """
    import os as _os
    static_root = _os.path.join(_os.path.dirname(__file__), "static")
    poster_dir = _os.path.join(static_root, "img", "games")
    if not _os.path.isdir(poster_dir):
        return 0
    ext_map = {}
    for f in _os.listdir(poster_dir):
        if f.endswith(".jpg"):
            ext_map[f[:-4]] = "jpg"
        elif f.endswith(".webp") and f[:-5] not in ext_map:
            ext_map[f[:-5]] = "webp"
    if not ext_map:
        return 0
    available = set(ext_map.keys())

    def _is_auto_path_stale(image_url):
        """True iff image_url is an auto-generated /static/img/games/... path
        whose file is missing on disk. Admin uploads (other prefixes such as
        /uploads/, /static/img/games/web/, http(s)://...) are never touched.
        """
        if not image_url:
            return False
        if not image_url.startswith("/static/img/games/"):
            return False
        # Subdirectories like /static/img/games/web/... are admin/web posters,
        # not auto-attached covers — leave them alone.
        rel = image_url[len("/static/img/games/"):]
        if "/" in rel:
            return False
        # Build the on-disk path safely (no path traversal — rel has no slash).
        on_disk = _os.path.join(poster_dir, rel)
        return not _os.path.isfile(on_disk)

    with db_conn() as conn:
        cur = conn.cursor()
        rows = cur.execute("SELECT id, game_key, image_url FROM games").fetchall()
        updated = 0
        for r in rows:
            current = (r["image_url"] or "").strip()
            if current and not _is_auto_path_stale(current):
                continue
            gk = (r["game_key"] or "").lower()
            match = _resolve_poster_key(gk, available)
            if match:
                url = f"/static/img/games/{match}.{ext_map[match]}"
                if url != current:
                    cur.execute("UPDATE games SET image_url=? WHERE id=?", (url, r["id"]))
                    updated += 1
            elif current:
                # No replacement found for a stale auto path — clear it so the
                # display layer can fall back to the smart SVG.
                cur.execute("UPDATE games SET image_url='' WHERE id=?", (r["id"],))
                updated += 1
        conn.commit()
    return updated


# ============================================================
# V52 (task D): Audit log helpers
# ============================================================
def insert_audit_log(
    action,
    actor_id=None,
    actor_email=None,
    target_type=None,
    target_id=None,
    ip=None,
    user_agent=None,
    old_value=None,
    new_value=None,
    metadata=None,
):
    """Append a row to ``audit_log``.

    All parameters are optional except ``action``. Callers should pass
    already-redacted / already-jsonified strings for ``old_value``,
    ``new_value``, and ``metadata`` — this function does NOT scrub secrets
    on its own (that is the responsibility of ``audit.log_audit``).

    Returns the inserted row id, or None if the write failed. Never
    raises — observability must not break the request that called it.
    """
    if not action:
        return None
    try:
        with db_conn() as conn:
            cur = conn.cursor()
            cur.execute(
                """
                INSERT INTO audit_log (
                    ts, action, actor_id, actor_email,
                    target_type, target_id, ip, user_agent,
                    old_value, new_value, metadata
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    int(time.time()),
                    str(action)[:120],
                    int(actor_id) if actor_id is not None else None,
                    (actor_email or None) and str(actor_email)[:120],
                    (target_type or None) and str(target_type)[:60],
                    (target_id or None) and str(target_id)[:120],
                    (ip or None) and str(ip)[:64],
                    user_agent,
                    old_value,
                    new_value,
                    metadata,
                ),
            )
            row_id = cur.lastrowid
            conn.commit()
            return row_id
    except Exception:
        return None


def list_audit_logs(limit=200, action=None, actor_id=None, target_type=None, target_id=None):
    """Fetch recent audit rows, newest first. Admin-only consumers.

    All filters are optional; when omitted, returns the last ``limit`` rows
    across the whole table. ``limit`` is clamped to [1, 1000] to keep admin
    pages responsive.
    """
    try:
        limit = max(1, min(int(limit or 200), 1000))
    except Exception:
        limit = 200

    clauses = []
    params = []
    if action:
        clauses.append("action = ?")
        params.append(str(action)[:120])
    if actor_id is not None:
        clauses.append("actor_id = ?")
        params.append(int(actor_id))
    if target_type:
        clauses.append("target_type = ?")
        params.append(str(target_type)[:60])
    if target_id:
        clauses.append("target_id = ?")
        params.append(str(target_id)[:120])

    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    sql = (
        "SELECT id, ts, action, actor_id, actor_email, target_type, target_id, "
        "       ip, user_agent, old_value, new_value, metadata "
        f"FROM audit_log {where} ORDER BY ts DESC, id DESC LIMIT ?"
    )
    params.append(limit)

    try:
        with db_conn() as conn:
            rows = [dict(r) for r in conn.execute(sql, tuple(params)).fetchall()]
            return rows
    except Exception:
        return []


def count_audit_logs():
    """Return total number of audit rows (for pagination hints)."""
    try:
        with db_conn() as conn:
            row = conn.execute("SELECT COUNT(*) AS n FROM audit_log").fetchone()
            return int(row["n"]) if row else 0
    except Exception:
        return 0
