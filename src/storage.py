"""SQLite storage: owners, encrypted AWS credentials, short callback tokens,
watches, settings, alert state, audit log, EC2 auto-schedules, and WAL-safe DB backups."""
import os, json, time, base64, sqlite3, hashlib, tempfile, threading
from cryptography.fernet import Fernet
import config

_lock = threading.RLock()
_db = None
_fernet = None

SCHEMA = """
CREATE TABLE IF NOT EXISTS owners(uid INTEGER PRIMARY KEY);
CREATE TABLE IF NOT EXISTS accounts(
    uid INTEGER,
    name TEXT,
    ak TEXT,
    sk TEXT,
    opened_at TEXT DEFAULT '',
    ft_months INTEGER DEFAULT 12,
    PRIMARY KEY(uid, name)
);
CREATE TABLE IF NOT EXISTS tokens(tok TEXT, uid INTEGER, payload TEXT, ts REAL, PRIMARY KEY(tok, uid));
CREATE TABLE IF NOT EXISTS watches(id INTEGER PRIMARY KEY AUTOINCREMENT, uid INTEGER, kind TEXT,
    acc TEXT, ref TEXT, extra TEXT, ts REAL, UNIQUE(uid, kind, acc, ref));
CREATE TABLE IF NOT EXISTS settings(uid INTEGER, k TEXT, v TEXT, PRIMARY KEY(uid, k));
CREATE TABLE IF NOT EXISTS state(k TEXT PRIMARY KEY, v TEXT);
CREATE TABLE IF NOT EXISTS audit_log(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts INTEGER NOT NULL,
    uid INTEGER NOT NULL,
    acc TEXT NOT NULL,
    action TEXT NOT NULL,
    detail TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS schedules(
    uid INTEGER NOT NULL,
    acc TEXT NOT NULL,
    region TEXT NOT NULL,
    iid TEXT NOT NULL,
    stop_hr INTEGER DEFAULT -1,
    start_hr INTEGER DEFAULT -1,
    enabled INTEGER DEFAULT 1,
    PRIMARY KEY(uid, acc, region, iid)
);
"""

DEFAULT_SETTINGS = {
    "ui_theme": "classic",
    "alerts_on": "1",
    "al_usage": "1",
    "al_5xx": "1",
    "al_ec2": "1",
    "alert_cf_pct": "80",
    "alert_cpu_pct": "90",
    "bill_alert_usd": "1.0",
    "auto_failover": "0",
    "digest_on": "1",
    "digest_hour": "8",
    "auto_backup_db": "1",
}


def init():
    global _db, _fernet
    os.makedirs(config.DATA_DIR, exist_ok=True)
    _fernet = Fernet(config.SECRET_KEY.encode())
    _db = sqlite3.connect(config.DB_PATH, check_same_thread=False, isolation_level=None)
    _db.execute("PRAGMA journal_mode=WAL")
    _db.executescript(SCHEMA)
    for sql in (
        "ALTER TABLE accounts ADD COLUMN opened_at TEXT DEFAULT ''",
        "ALTER TABLE accounts ADD COLUMN ft_months INTEGER DEFAULT 12",
    ):
        try:
            _db.execute(sql)
        except sqlite3.OperationalError:
            pass
    try:
        os.chmod(config.DB_PATH, 0o600)
    except OSError:
        pass
    _migrate_legacy()
    _x("DELETE FROM tokens WHERE ts < ?", (time.time() - 14 * 86400,))


def _x(sql, args=()):
    with _lock:
        return _db.execute(sql, args).fetchall()


def _enc(s): return _fernet.encrypt(s.encode()).decode()
def _dec(s): return _fernet.decrypt(s.encode()).decode()


def _migrate_legacy():
    old = os.path.join(config.DATA_DIR, "data.json")
    own = os.path.join(config.DATA_DIR, "owners.json")
    if os.path.exists(old):
        try:
            with open(old) as f: data = json.load(f)
            for uid, accs in data.items():
                for name, v in accs.items():
                    _x("INSERT OR IGNORE INTO accounts(uid,name,ak,sk) VALUES(?,?,?,?)",
                       (int(uid), name, v["ak"], v["sk"]))
            os.rename(old, old + ".migrated")
        except Exception:
            pass
    if os.path.exists(own):
        try:
            with open(own) as f:
                for uid in json.load(f): add_owner(int(uid))
            os.rename(own, own + ".migrated")
        except Exception:
            pass


# ---- access control ----
def add_owner(uid):
    _x("INSERT OR IGNORE INTO owners VALUES(?)", (uid,))
    try: open(os.path.join(config.DATA_DIR, ".claimed"), "a").close()
    except OSError: pass

def has_admin(): return bool(config.EXTRA_ADMINS) or bool(_x("SELECT 1 FROM owners LIMIT 1"))
def is_admin(uid): return uid in config.EXTRA_ADMINS or bool(_x("SELECT 1 FROM owners WHERE uid=?", (uid,)))
def all_users(): return sorted(config.EXTRA_ADMINS | {r[0] for r in _x("SELECT uid FROM owners")})


# ---- accounts ----
def accounts(uid):
    return [r[0] for r in _x("SELECT name FROM accounts WHERE uid=? ORDER BY rowid", (uid,))]

def accounts_meta(uid):
    rows = _x("SELECT name, opened_at, ft_months FROM accounts WHERE uid=? ORDER BY rowid", (uid,))
    return {
        r[0]: {
            "opened_at": r[1] or "",
            "ft_months": int(r[2] or 12),
            "region": get_setting(uid, f"region:{r[0]}", "us-east-1"),
        }
        for r in rows
    }

def account_meta(uid, name):
    rows = _x("SELECT opened_at, ft_months FROM accounts WHERE uid=? AND name=?", (uid, name))
    if not rows:
        raise KeyError(f"Account '{name}' haipo")
    return {
        "opened_at": rows[0][0] or "",
        "ft_months": int(rows[0][1] or 12),
        "region": get_setting(uid, f"region:{name}", "us-east-1"),
    }

def has_account(uid, name):
    return bool(_x("SELECT 1 FROM accounts WHERE uid=? AND name=?", (uid, name)))

def creds(uid, name):
    rows = _x("SELECT ak, sk FROM accounts WHERE uid=? AND name=?", (uid, name))
    if not rows: raise KeyError(f"Account '{name}' haipo")
    return _dec(rows[0][0]), _dec(rows[0][1])

def add_account(uid, name, ak, sk, opened_at="", ft_months=12):
    _x("INSERT OR REPLACE INTO accounts(uid,name,ak,sk,opened_at,ft_months) VALUES(?,?,?,?,?,?)",
       (uid, name, _enc(ak), _enc(sk), opened_at or "", int(ft_months or 12)))

def set_account_meta(uid, name, opened_at=None, ft_months=None):
    if opened_at is not None:
        _x("UPDATE accounts SET opened_at=? WHERE uid=? AND name=?", (opened_at, uid, name))
    if ft_months is not None:
        _x("UPDATE accounts SET ft_months=? WHERE uid=? AND name=?", (int(ft_months), uid, name))

def del_account(uid, name):
    _x("DELETE FROM accounts WHERE uid=? AND name=?", (uid, name))
    _x("DELETE FROM watches WHERE uid=? AND acc=?", (uid, name))
    _x("DELETE FROM schedules WHERE uid=? AND acc=?", (uid, name))
    _x("DELETE FROM settings WHERE uid=? AND k LIKE ?", (uid, f"%:{name}"))


# ---- short callback tokens (Telegram callback_data <= 64 bytes) ----
def ref(uid, payload):
    s = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    tok = base64.urlsafe_b64encode(hashlib.sha1(f"{uid}{s}".encode()).digest())[:8].decode()
    _x("INSERT OR REPLACE INTO tokens VALUES(?,?,?,?)", (tok, uid, s, time.time()))
    return tok

def deref(uid, tok):
    rows = _x("SELECT payload FROM tokens WHERE tok=? AND uid=?", (tok, uid))
    return json.loads(rows[0][0]) if rows else None


# ---- watches ----
def add_watch(uid, kind, acc, ref_, extra=None):
    _x("INSERT OR REPLACE INTO watches(uid,kind,acc,ref,extra,ts) VALUES(?,?,?,?,?,?)",
       (uid, kind, acc, ref_, json.dumps(extra or {}), time.time()))

def list_watches():
    return [dict(id=r[0], uid=r[1], kind=r[2], acc=r[3], ref=r[4], extra=json.loads(r[5] or "{}"), ts=r[6])
            for r in _x("SELECT id,uid,kind,acc,ref,extra,ts FROM watches")]

def del_watch(wid): _x("DELETE FROM watches WHERE id=?", (wid,))


# ---- EC2 Auto-Schedules (Sleep/Wake Timer) ----
def get_schedule(uid, acc, region, iid):
    rows = _x("SELECT stop_hr, start_hr, enabled FROM schedules WHERE uid=? AND acc=? AND region=? AND iid=?",
              (uid, acc, region, iid))
    if not rows:
        return None
    return {"stop_hr": int(rows[0][0]), "start_hr": int(rows[0][1]), "enabled": bool(rows[0][2])}

def set_schedule(uid, acc, region, iid, stop_hr=-1, start_hr=-1, enabled=1):
    _x("INSERT OR REPLACE INTO schedules(uid,acc,region,iid,stop_hr,start_hr,enabled) VALUES(?,?,?,?,?,?,?)",
       (uid, acc, region, iid, int(stop_hr), int(start_hr), 1 if enabled else 0))

def del_schedule(uid, acc, region, iid):
    _x("DELETE FROM schedules WHERE uid=? AND acc=? AND region=? AND iid=?", (uid, acc, region, iid))

def list_schedules(uid=None):
    if uid is not None:
        rows = _x("SELECT uid,acc,region,iid,stop_hr,start_hr,enabled FROM schedules WHERE uid=?", (uid,))
    else:
        rows = _x("SELECT uid,acc,region,iid,stop_hr,start_hr,enabled FROM schedules WHERE enabled=1")
    return [
        {"uid": r[0], "acc": r[1], "region": r[2], "iid": r[3],
         "stop_hr": int(r[4]), "start_hr": int(r[5]), "enabled": bool(r[6])}
        for r in rows
    ]


# ---- settings / state ----
def get_setting(uid, k, default=None):
    r = _x("SELECT v FROM settings WHERE uid=? AND k=?", (uid, k))
    if r: return r[0][0]
    if default is not None: return default
    return DEFAULT_SETTINGS.get(k)

def set_setting(uid, k, v): _x("INSERT OR REPLACE INTO settings VALUES(?,?,?)", (uid, k, str(v)))

def user_settings(uid):
    g = lambda k: get_setting(uid, k, DEFAULT_SETTINGS[k])
    return {
        "ui_theme": g("ui_theme"),
        "alerts_on": g("alerts_on") == "1",
        "al_usage": g("al_usage") == "1",
        "al_5xx": g("al_5xx") == "1",
        "al_ec2": g("al_ec2") == "1",
        "alert_cf_pct": int(g("alert_cf_pct")),
        "alert_cpu_pct": int(g("alert_cpu_pct")),
        "bill_alert_usd": float(g("bill_alert_usd")),
        "auto_failover": g("auto_failover") == "1",
        "digest_on": g("digest_on") == "1",
        "digest_hour": int(g("digest_hour")),
        "auto_backup_db": g("auto_backup_db") == "1",
    }

def get_state(k, default=None):
    r = _x("SELECT v FROM state WHERE k=?", (k,))
    return r[0][0] if r else default

def set_state(k, v): _x("INSERT OR REPLACE INTO state VALUES(?,?)", (k, str(v)))
def del_state(k): _x("DELETE FROM state WHERE k=?", (k,))

def alert_recently_sent(key, cooldown_sec=86400):
    now = int(time.time())
    prev = get_state(f"cd:{key}")
    if prev:
        try:
            if now - int(prev) < cooldown_sec:
                return True
        except ValueError:
            pass
    set_state(f"cd:{key}", str(now))
    return False


# ---- audit log ----
def audit(uid, acc, action, detail=""):
    _x("INSERT INTO audit_log(ts,uid,acc,action,detail) VALUES(?,?,?,?,?)",
       (int(time.time()), uid, str(acc)[:20], str(action)[:40], str(detail)[:160]))
    _x("DELETE FROM audit_log WHERE uid=? AND id NOT IN (SELECT id FROM audit_log WHERE uid=? ORDER BY id DESC LIMIT 80)",
       (uid, uid))

def get_audit(uid, limit=20):
    rows = _x("SELECT ts, acc, action, detail FROM audit_log WHERE uid=? ORDER BY id DESC LIMIT ?", (uid, limit))
    return [{"ts": r[0], "acc": r[1], "action": r[2], "detail": r[3]} for r in rows]


# ---- WAL-safe SQLite backup ----
def backup_bytes():
    with _lock:
        fd, tmp_path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        try:
            dst = sqlite3.connect(tmp_path)
            try:
                _db.backup(dst)
            finally:
                dst.close()
            with open(tmp_path, "rb") as f:
                return f.read()
        finally:
            try: os.remove(tmp_path)
            except OSError: pass
