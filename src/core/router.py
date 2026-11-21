"""Callback/text routing with decorator registry, auto UI-Theme injection, loading banners & friendly errors."""
import logging
from telegram.error import TelegramError
import storage as store
from core.errors import friendly
from core.ui import show, loading, btn, esc, set_theme

log = logging.getLogger(__name__)
ROUTES = {}   # name -> (handler, slow)
FLOWS = {}    # flow type -> handler(u, c, flow, text)


def route(name, slow=False):
    def deco(fn):
        assert name not in ROUTES, f"duplicate route {name}"
        ROUTES[name] = (fn, slow)
        return fn
    return deco


def flow(name):
    def deco(fn):
        FLOWS[name] = fn
        return fn
    return deco


async def fail(u, c, e):
    if isinstance(e, ValueError): log.info("user error: %s", e)
    else: log.exception("handler error")
    last = c.user_data.get("last")
    row = ([btn("🔁 Jaribu tena", last)] if last else []) + [btn("🏠 Menu Kuu", "home")]
    await show(u, c, f"❌ <b>Hitilafu</b>\n<blockquote><code>{esc(friendly(e))}</code></blockquote>", [row])


async def on_cb(u, c):
    q = u.callback_query
    uid = u.effective_user.id
    if not store.is_admin(uid):
        return await q.answer()
    set_theme(store.get_setting(uid, "ui_theme", "classic"))
    p = q.data.split("|")
    r = ROUTES.get(p[0])
    if not r:
        return await q.answer("Kitufe hakijulikani")
    fn, slow = r
    c.user_data.pop("flow", None)
    c.user_data["last"] = q.data
    await q.answer("⏳ Inapakia..." if slow else None)
    if slow:
        await loading(u)
    try:
        await fn(u, c, p)
    except Exception as e:
        await fail(u, c, e)


async def on_text(u, c):
    uid = u.effective_user.id
    if not store.is_admin(uid):
        return
    set_theme(store.get_setting(uid, "ui_theme", "classic"))
    f = c.user_data.get("flow")
    if not f:
        return await ROUTES["home"][0](u, c, ["home"])
    try: await u.message.delete()
    except TelegramError: pass
    try:
        await FLOWS[f["t"]](u, c, f, (u.message.text or "").strip())
    except Exception as e:
        await fail(u, c, e)
