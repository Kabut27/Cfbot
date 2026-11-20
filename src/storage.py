"""SQLite storage: owners, encrypted AWS credentials, short callback tokens,
watches (background notifications), settings and alert state.
All access goes through one lock, so concurrent handlers/jobs cannot corrupt data."""
import os, json, time, base64, sqlite3, hashlib, threading
from cryptography.fernet import Fernet
import config

_lock = threading.RLock()
_db = None
_fernet = None

SCHEMA = """
CREATE TABLE IF NOT EXISTS owners(uid INTEGER PRIMARY KEY);
CREATE TABLE IF NOT EXISTS accounts(uid INTEGER, name TEXT, ak TEXT, sk TEXT, PRIMARY KEY(uid, name));
CREATE TABLE IF NOT EXISTS tokens(tok TEXT, uid INTEGER, payload TEXT, ts REAL, PRIMARY KEY(tok, uid));
CREATE TABLE IF NOT EXISTS watches(id INTEGER PRIMARY KEY AUTOINCREMENT, uid INTEGER, kind TEXT,
    acc TEXT, ref TEXT, extra TEXT, ts REAL, UNIQUE(uid, kind, acc, ref));
CREATE TABLE IF NOT EXISTS settings(uid INTEGER, k TEXT, v TEXT, PRIMARY KEY(uid, k));
CREATE TABLE IF NOT EXISTS state(k TEXT PRIMARY KEY, v TEXT);
"""


def init():
    global _db, _fernet
    os.makedirs(config.DATA_DIR, exist_ok=True)
    _fernet = Fernet(config.SECRET_KEY.encode())
    _db = sqlite3.connect(config.DB_PATH, check_same_thread=False, isolation_level=None)
    _db.execute("PRAGMA journal_mode=WAL")
    _db.executescript(SCHEMA)
    try: os.chmod(config.DB_PATH, 0o600)
    except OSError: pass
    _migrate_legacy()
    _x("DELETE FROM tokens WHERE ts < ?", (time.time() - 14 * 86400,))


def _x(sql, args=()):
    with _lock:
        return _db.execute(sql, args).fetchall()


def _enc(s): return _fernet.encrypt(s.encode()).decode()
def _dec(s): return _fernet.decrypt(s.encode()).decode()


def _migrate_legacy():
    """Import data.json / owners.json from the first (single-file) version."""
    old = os.path.join(config.DATA_DIR, "data.json")
    own = os.path.join(config.DATA_DIR, "owners.json")
    if os.path.exists(old):
        try:
            with open(old) as f: data = json.load(f)
            for uid, accs in data.items():
                for name, v in accs.items():  # already Fernet-encrypted with the same key
                    _x("INSERT OR IGNORE INTO accounts VALUES(?,?,?,?)", (int(uid), name, v["ak"], v["sk"]))
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
    try: open(os.path.join(config.DATA_DIR, ".claimed"), "a").close()  # install.sh reads this marker
    except OSError: pass

def has_admin(): return bool(config.EXTRA_ADMINS) or bool(_x("SELECT 1 FROM owners LIMIT 1"))
def is_admin(uid): return uid in config.EXTRA_ADMINS or bool(_x("SELECT 1 FROM owners WHERE uid=?", (uid,)))
def all_users(): return sorted(config.EXTRA_ADMINS | {r[0] for r in _x("SELECT uid FROM owners")})


# ---- accounts ----
def accounts(uid): return [r[0] for r in _x("SELECT name FROM accounts WHERE uid=? ORDER BY rowid", (uid,))]
def has_account(uid, name): return bool(_x("SELECT 1 FROM accounts WHERE uid=? AND name=?", (uid, name)))

def creds(uid, name):
    rows = _x("SELECT ak, sk FROM accounts WHERE uid=? AND name=?", (uid, name))
    if not rows: raise KeyError(f"Account '{name}' haipo")
    return _dec(rows[0][0]), _dec(rows[0][1])

def add_account(uid, name, ak, sk):
    _x("INSERT OR REPLACE INTO accounts VALUES(?,?,?,?)", (uid, name, _enc(ak), _enc(sk)))

def del_account(uid, name):
    _x("DELETE FROM accounts WHERE uid=? AND name=?", (uid, name))
    _x("DELETE FROM watches WHERE uid=? AND acc=?", (uid, name))
    _x("DELETE FROM settings WHERE uid=? AND k LIKE ?", (uid, f"%:{name}"))


# ---- short callback tokens (Telegram callback_data is limited to 64 bytes) ----
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


# ---- settings / state ----
def get_setting(uid, k, default=None):
    r = _x("SELECT v FROM settings WHERE uid=? AND k=?", (uid, k))
    return r[0][0] if r else default

def set_setting(uid, k, v): _x("INSERT OR REPLACE INTO settings VALUES(?,?,?)", (uid, k, str(v)))

def get_state(k, default=None):
    r = _x("SELECT v FROM state WHERE k=?", (k,))
    return r[0][0] if r else default

def set_state(k, v): _x("INSERT OR REPLACE INTO state VALUES(?,?)", (k, str(v)))
def del_state(k): _x("DELETE FROM state WHERE k=?", (k,))
