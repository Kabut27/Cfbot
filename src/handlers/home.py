"""Start/claim, home menu, help, settings, account picker."""
import hmac
from telegram.error import TelegramError
import config
import storage as store
from core.router import route, ROUTES
from core.ui import show, loading, btn, nav, head, card

ALERTS = [("al_usage", "📈 Usage 80%/95%"), ("al_5xx", "🚨 5xx (backend)"), ("al_ec2", "🖥 EC2 down/CPU")]


async def cmd(u, c):
    uid = u.effective_user.id
    try: await u.message.delete()                 # hides the claim code
    except TelegramError: pass
    if not store.has_admin():                     # unclaimed: first /start <code> wins
        code = c.args[0] if c.args else ""
        if config.CLAIM_CODE and hmac.compare_digest(code, config.CLAIM_CODE):
            store.add_owner(uid)
        else:
            await u.effective_chat.send_message("🔐 Bot haijafungwa kwa mmiliki.\nTuma: /start <claim code>")
            return
    elif not store.is_admin(uid):
        return
    c.user_data.pop("flow", None)
    c.user_data.pop("panel", None)                # start a fresh panel
    await h_home(u, c, ["home"])


@route("home")
async def h_home(u, c, p):
    uid = u.effective_user.id
    n = len(store.accounts(uid))
    on = lambda k: "✅" if store.get_setting(uid, k, "1") == "1" else "❌"
    text = (head("🏠 Home") + "☁️ <b>CloudFront & EC2 Manager</b>\n\n" +
            card(f"👤 Accounts: <b>{n}</b>\n🔔 Alerts: usage {on('al_usage')} · 5xx {on('al_5xx')} · EC2 {on('al_ec2')}") +
            "\nChagua:")
    await show(u, c, text, [
        [btn("☁️ CloudFront", "pk|dl"), btn("🖥 EC2", "pk|ec")],
        [btn("➕ Create CDN", "pk|cr"), btn("📊 Usage", "pk|ua")],
        [btn("👤 Accounts", "acc"), btn("⚙️ Settings", "se")],
        [btn("❓ Help", "help")]])


@route("help")
async def h_help(u, c, p):
    await show(u, c, head("🏠 Home", "❓ Help") + card(
        "1️⃣ <b>Accounts</b> → Add (IAM keys)\n"
        "2️⃣ <b>Create CDN</b> → origin (EC2 DNS) + port\n"
        "3️⃣ Fungua distribution: Origin, Settings, Domains &amp; SSL, Analytics, Purge, Backup\n"
        "4️⃣ <b>EC2</b>: Region/Scan, Start/Stop, Firewall, Elastic IP, Metrics, Snapshot, Commands\n"
        "5️⃣ <b>☁️ Set as CF Origin</b> kwenye EC2 inabadilisha origin bila kunakili DNS\n\n"
        "🔔 Bot inakutaarifu: deploy imekamilika, cheti ISSUED, usage 80%/95%, 5xx, EC2 kuzima.\n"
        "🔒 Cloudflare DNS: CNAME → domain ya CloudFront, proxy <b>DNS only</b>."), [nav()])


@route("pk")
async def h_pk(u, c, p):
    purpose = p[1]
    names = store.accounts(u.effective_user.id)
    if not names:
        return await show(u, c, head("🏠 Home") + "Bado hujaunganisha account.",
                          [[btn("➕ Add account", "aa")], nav()])
    if len(names) == 1:                           # skip the picker: fewer taps
        await loading(u)
        return await ROUTES[purpose][0](u, c, [purpose, names[0]])
    titles = {"dl": "☁️ CloudFront", "ec": "🖥 EC2", "cr": "➕ Create CDN", "ua": "📊 Usage"}
    cb = lambda n: f"{purpose}|{n}|0" if purpose == "dl" else f"{purpose}|{n}"
    await show(u, c, head("🏠 Home", titles[purpose]) + "Chagua account:",
               [[btn(f"👤 {n}", cb(n))] for n in names] + [nav()])


@route("se")
async def h_se(u, c, p):
    uid = u.effective_user.id
    rows = [[btn(f"{'✅' if store.get_setting(uid, k, '1') == '1' else '❌'} {label}", f"sw|{k}")] for k, label in ALERTS]
    await show(u, c, head("🏠 Home", "⚙️ Settings") + card(
        "Arifa za moja kwa moja (kila dakika 15 / saa 1).\n"
        "Arifa za <i>Deployed</i> na <i>cheti ISSUED</i> huwa zinatumwa kila mara.") +
        "\nBonyeza kubadilisha:", rows + [nav()])


@route("sw")
async def h_sw(u, c, p):
    uid = u.effective_user.id
    if p[1] in dict(ALERTS):
        store.set_setting(uid, p[1], "0" if store.get_setting(uid, p[1], "1") == "1" else "1")
    await h_se(u, c, p)
