"""Telegram UI helpers: single-panel rendering, buttons, cards, small formatters."""
import html, logging, datetime as dt
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, LinkPreviewOptions
from telegram.error import BadRequest, TelegramError

log = logging.getLogger(__name__)
esc = lambda s: html.escape(str(s), quote=False)
SEP = "━" * 20
NOPREVIEW = LinkPreviewOptions(is_disabled=True)
SPARK = "▁▂▃▄▅▆▇█"


def btn(text, data):
    if len(data.encode()) > 64:                      # Telegram hard limit
        raise ValueError(f"callback_data ndefu mno: {data}")
    return InlineKeyboardButton(text, callback_data=data)


def nav(back=None, label="⬅️ Back"):
    row = [btn(label, back)] if back else []
    return row + [btn("🏠 Menu", "home")]


def grid(buttons, n=2):
    return [buttons[i:i + n] for i in range(0, len(buttons), n)]


def head(*parts): return " › ".join(parts) + "\n" + SEP + "\n"


def card(text, expandable=False):
    tag = "blockquote expandable" if expandable else "blockquote"
    return f"<{tag}>{text}</{tag}>"


def mk(rows): return InlineKeyboardMarkup(rows) if rows else None


async def show(u, c, text, rows=None):
    """Render as the single 'panel': edit the tapped/last message, else send a new one."""
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


async def loading(u, text="⏳ <i>Inawasiliana na AWS...</i>"):
    """Replace the panel with a loading line (also removes buttons -> no double taps)."""
    try:
        await u.callback_query.edit_message_text(text, parse_mode="HTML")
    except TelegramError:
        pass


# ---- formatters ----
def bar(pct, width=10):
    n = max(0, min(width, round(pct / 100 * width)))
    return "█" * n + "░" * (width - n)

def spark(vals):
    if not vals: return ""
    hi = max(vals) or 1
    return "".join(SPARK[min(7, int(v / hi * 7))] for v in vals)

def gb(x): return "—" if x is None else f"{x:,.1f} GB"
def pct(x): return "—" if x is None else f"{x:.1f}%"

def num(n):
    if n is None: return "—"
    for unit, d in (("B", 1e9), ("M", 1e6), ("K", 1e3)):
        if n >= d: return f"{n / d:.1f}{unit}"
    return str(int(n))

def ago(t):
    if not t: return "—"
    if t.tzinfo is None: t = t.replace(tzinfo=dt.timezone.utc)
    s = int((dt.datetime.now(dt.timezone.utc) - t).total_seconds())
    for unit, d in (("d", 86400), ("h", 3600), ("m", 60)):
        if s >= d: return f"{s // d}{unit} ago"
    return "just now"
