"""Telegram UI helpers (v5 Ultra): UI Themes, Health Score Badges, Smart Timestamps,
Tree Cards, themed progress bars, sparklines, and single-panel rendering."""
import contextvars, html, logging, datetime as dt
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, LinkPreviewOptions
from telegram.error import BadRequest, TelegramError
import config

log = logging.getLogger(__name__)
esc = lambda s: html.escape(str(s), quote=False)
NOPREVIEW = LinkPreviewOptions(is_disabled=True)
SPARK = "▁▂▃▄▅▆▇█"

THEMES = {
    "classic": {
        "name": "🎨 Classic Pro",
        "sep": "━" * 20,
        "br": ("╭", "├", "╰"),
        "bar": ("🟩", "🟨", "🟥", "⬛"),
        "title_icon": "⚡",
    },
    "minimal": {
        "name": "💎 Minimalist",
        "sep": "─" * 20,
        "br": ("┌", "├", "└"),
        "bar": ("█", "▓", "▒", "░"),
        "title_icon": "◈",
    },
    "neon": {
        "name": "🌈 High-Contrast",
        "sep": "═" * 20,
        "br": ("╔", "╠", "╚"),
        "bar": ("🟦", "🟪", "🟧", "⬛"),
        "title_icon": "🚀",
    },
}
THEME_ORDER = ["classic", "minimal", "neon"]
_CURRENT_THEME = contextvars.ContextVar("ui_theme", default="classic")


def set_theme(name):
    _CURRENT_THEME.set(name if name in THEMES else "classic")


def get_theme():
    return THEMES.get(_CURRENT_THEME.get(), THEMES["classic"])


def theme_name(key=None):
    k = key or _CURRENT_THEME.get()
    return THEMES.get(k, THEMES["classic"])["name"]


def sep():
    return get_theme()["sep"]


_KEY_ICONS = {
    "Accounts": "👤", "Account": "👤", "AWS_ID": "🆔", "IAM_User": "🔑",
    "CloudFront": "☁️", "Dists": "☁️", "EC2_Reg": "🌍", "FT_Expiry": "⏳", "FT_Left": "⏳",
    "Alerts": "🔔", "Digest": "🌅", "Digest_Hr": "⏰", "CF_Alert": "📊", "CPU_Alert": "🔥",
    "Status": "⚡", "State": "⚡", "Port": "🔌", "HTTP_Port": "🔌", "HTTPS_Pt": "🔒",
    "HTTP": "🚀", "Cache": "⚡", "Viewer": "🔐", "Protocol": "🔒", "Compress": "📦",
    "IPv6": "🌐", "Price": "💲", "Geo": "🌍", "WAF": "🛡",
    "Data_7d": "📦", "Data_Mo": "📦", "Data_24h": "📦", "Data_GB": "📦", "CF_Data": "📦",
    "Reqs_7d": "📈", "Reqs_Mo": "📈", "Reqs_24h": "📈", "Requests": "📈", "CF_Reqs": "📈",
    "Last_24h": "⏱", "Err_24h": "⚠️", "Err_4xx": "⚠️", "Err_5xx": "🔥",
    "Origin": "🎯", "Origin_ID": "🆔", "Edge_443": "🌐", "Timeout": "⏱", "Headers": "🛡",
    "ElasticIP": "🌐", "CPU_1h": "🔥", "Net_Out": "📤", "Net_In": "📥", "Ports": "🛡",
    "EC2": "🖥", "EC2_Hrs": "🖥", "EC2_Run": "🖥", "EBS_GB": "💽",
    "Bill_USD": "💵", "Est_Bill": "💵", "Bill_Alert": "💵",
    "Failover": "🤖", "DB_Backup": "📦", "Zombies": "🧹",
    "Health": "💚", "Theme": "🎨", "Forecast": "🔮", "Schedule": "⏰", "Audit": "🛡",
}


def btn(text, data):
    if len(data.encode()) > 64:
        raise ValueError(f"callback_data ndefu mno: {data}")
    return InlineKeyboardButton(text, callback_data=data)


def nav(back=None, label="⬅️ Back"):
    row = [btn(label, back)] if back else []
    return row + [btn("🏠 Menu Kuu", "home")]


def grid(buttons, n=2):
    return [buttons[i:i + n] for i in range(0, len(buttons), n)]


def head(*parts):
    return " › ".join(parts) + "\n" + sep() + "\n"


def card(text, expandable=False):
    tag = "blockquote expandable" if expandable else "blockquote"
    return f"<{tag}>{text}</{tag}>"


def tree_card(rows):
    """Themed v5 Ultra Tree Card inside Telegram blockquote."""
    if not rows:
        return ""
    b_top, b_mid, b_bot = get_theme()["br"]
    lines = []
    n = len(rows)
    for i, (k, raw_v) in enumerate(rows):
        branch = b_top if i == 0 and n > 1 else (b_bot if i == n - 1 and n > 1 else (b_mid if n > 1 else "•"))
        icon = _KEY_ICONS.get(k, "•")
        label = k.replace("_", " ")
        val = str(raw_v).strip()
        note = ""
        if "  # " in val:
            val, note = val.split("  # ", 1)
        elif " # " in val:
            val, note = val.split(" # ", 1)
        val = val.strip()
        if len(val) >= 2 and val[0] == '"' and val[-1] == '"':
            val = val[1:-1]
        low = val.lower()
        if low == "true":
            val_html = "🟢 <b>ON</b>"
        elif low == "false":
            val_html = "🔴 <b>OFF</b>"
        else:
            val_html = f"<code>{esc(val)}</code>"
        note_html = f" <i>({esc(note.strip())})</i>" if note.strip() else ""
        lines.append(f"{branch} {icon} <b>{esc(label)}:</b> {val_html}{note_html}")
    return f"<blockquote>{chr(10).join(lines)}</blockquote>"


def health_badge(score):
    """Returns a visual Health Score badge (0–100)."""
    s = max(0, min(100, int(round(score))))
    if s >= 90: return f"🟢 A+ Safi ({s}/100)"
    if s >= 75: return f"🟢 A Nzuri ({s}/100)"
    if s >= 55: return f"🟡 B Tahadhari ({s}/100)"
    if s >= 35: return f"🟠 C Kagua ({s}/100)"
    return f"🔴 D Hatari ({s}/100)"


def stamp(cache_age_sec=None):
    """Relative timestamp / cache indicator footer."""
    now_t = dt.datetime.now(config.LOCAL_TZ).strftime("%H:%M:%S EAT")
    if cache_age_sec is not None and cache_age_sec >= 2:
        return f"\n<i>⏱ Cache: {cache_age_sec}s iliyopita · {now_t}</i>"
    return f"\n<i>⏱ Live: sasa hivi · {now_t}</i>"


def mk(rows): return InlineKeyboardMarkup(rows) if rows else None


async def show(u, c, text, rows=None):
    """Render as the single panel: edit the tapped/last message, else send a new one."""
    kw = dict(parse_mode="HTML", reply_markup=mk(rows), link_preview_options=NOPREVIEW)
    q = u.callback_query
    try:
        if q:
            await q.edit_message_text(text, **kw)
            c.user_data["panel"] = (q.message.chat_id, q.message.message_id)
            return
        panel = c.user_data.get("panel")
        if panel:
            await c.bot.edit_message_text(text, chat_id=panel[0], message_id=panel[1], **kw)
            return
    except BadRequest as e:
        if "not modified" in str(e).lower():
            return
        log.info("edit failed (%s); sending a new message", e)
    chat = u.effective_chat
    m = await chat.send_message(text, **kw)
    c.user_data["panel"] = (chat.id, m.message_id)


async def loading(u, msg="Inawasiliana na AWS..."):
    q = u.callback_query
    if not q:
        return
    try:
        await q.edit_message_text(
            f"⏳ <b>Tafadhali subiri...</b>\n<blockquote><i>{esc(msg)}</i></blockquote>",
            parse_mode="HTML",
        )
    except TelegramError:
        pass


# ---- themed visual formatters ----
def progress_bar(val, max_val, width=10):
    ok_b, warn_b, bad_b, empty_b = get_theme()["bar"]
    pct_f = 0.0 if max_val <= 0 else max(0.0, min(1.0, float(val) / float(max_val)))
    filled = int(round(pct_f * width))
    if val > 0 and filled == 0:
        filled = 1
    block = bad_b if pct_f >= 0.85 else (warn_b if pct_f >= 0.60 else ok_b)
    return block * filled + empty_b * (width - filled)


def bar(pct_val, width=10):
    return progress_bar(pct_val, 100.0, width)


def spark(vals, width=7):
    vals = [max(0.0, float(v)) for v in (vals or [])[-width:]]
    if not vals:
        return "▁" * width
    if len(vals) < width:
        vals = [0.0] * (width - len(vals)) + vals
    hi = max(vals)
    if hi <= 0:
        return "▁" * len(vals)
    return "".join(SPARK[min(7, int(v / hi * 7.99))] for v in vals)


def gb(x): return "—" if x is None else f"{x:,.2f} GB"
def pct(x): return "—" if x is None else f"{x:.1f}%"


def num(n):
    if n is None: return "—"
    n = float(n)
    if n >= 1e9: return f"{n / 1e9:.2f}B"
    if n >= 1e6: return f"{n / 1e6:.2f}M"
    if n >= 1e3: return f"{n / 1e3:.1f}K"
    return str(int(round(n)))


def ago(t):
    if not t: return "—"
    if t.tzinfo is None: t = t.replace(tzinfo=dt.timezone.utc)
    s = int((dt.datetime.now(dt.timezone.utc) - t).total_seconds())
    for unit, d in (("d", 86400), ("h", 3600), ("m", 60)):
        if s >= d: return f"{s // d}{unit} ago"
    return "just now"
