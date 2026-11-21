"""Start/claim, v5 Ultra Home Panel, UI Theme Switcher, Settings & Logs, Audit Log, Help, Account Picker."""
import hmac, io, datetime as dt
from telegram.error import TelegramError
import config
import storage as store
from core import aws
from core.router import route, flow, ROUTES
from core.ui import (
    show, loading, btn, nav, head, card, tree_card, esc,
    set_theme, get_theme, theme_name, THEME_ORDER, health_badge, stamp,
)
from handlers.common import prompt
from services import cloudfront as cfs


async def cmd(u, c):
    uid = u.effective_user.id
    try: await u.message.delete()
    except TelegramError: pass
    if not store.has_admin():
        code = c.args[0] if c.args else ""
        if config.CLAIM_CODE and hmac.compare_digest(code, config.CLAIM_CODE):
            store.add_owner(uid)
        else:
            await u.effective_chat.send_message("🔐 Bot haijafungwa kwa mmiliki.\nTuma: /start <claim code>")
            return
    elif not store.is_admin(uid):
        return
    set_theme(store.get_setting(uid, "ui_theme", "classic"))
    c.user_data.pop("flow", None)
    c.user_data.pop("panel", None)
    await h_home(u, c, ["home"])


@route("home")
async def h_home(u, c, p):
    uid = u.effective_user.id
    accs = store.accounts_meta(uid)
    st = store.user_settings(uid)
    alerts_on = st["alerts_on"]
    alerts_txt = "ON" if alerts_on else "OFF"
    acc_list = ", ".join(list(accs.keys())[:4]) if accs else "none"
    sched_cnt = len(store.list_schedules(uid))

    # Home Health Score calculation
    h_score = 100
    if not accs:
        h_score = 60
    if not alerts_on:
        h_score -= 15
    exp_line = "not set  # Weka kwenye Free Tier"
    for name, info in accs.items():
        ex = cfs.calc_ft_expiry(info["opened_at"], info["ft_months"])
        if ex:
            if ex["days_left"] <= 0:
                h_score -= 30
                exp_line = f"EXPIRED  # {name} ({ex['end']})"
            elif ex["days_left"] <= 30:
                h_score -= 15
                exp_line = f"{ex['days_left']}d left  # {name} → {ex['end']}"
            else:
                exp_line = f"{ex['days_left']}d left  # {name} → {ex['end']}"
            break

    th = get_theme()
    b_top, b_mid, b_bot = th["br"]
    tcard = tree_card([
        ("Health", f'"{health_badge(h_score)}"'),
        ("Accounts", f"{len(accs)}  # {acc_list}"),
        ("FT_Expiry", exp_line),
        ("Alerts", f'{"true" if alerts_on else "false"}  # CF≥{st["alert_cf_pct"]}% · Bill≥${st["bill_alert_usd"]:.2f}'),
        ("Schedule", f"{sched_cnt} EC2 active  # Digest {st['digest_hour']:02d}:00 EAT"),
    ])
    hint = (
        card(
            f"{b_top} 🟢 <b>Mfumo upo tayari (v5 Ultra · {theme_name()})</b>\n"
            f"{b_mid} ☁️ CloudFront: Data, 10M Reqs, Forecast, Clone &amp; 5xx Auto-Failover\n"
            f"{b_bot} 🖥 EC2 &amp; Sec: 750h/EBS Tracker, Audit, Auto-Schedule &amp; /find"
        )
        if accs
        else card(
            "⚠️ <b>Bado hujaunganisha AWS account.</b>\n"
            "Bonyeza <b>👤 Accounts → ➕ Add Account</b> kuanza."
        )
    )
    text = f"{th['title_icon']} <b>AWS CONTROL PANEL · v5 Ultra</b>\n{th['sep']}\n{tcard}\n\n{hint}{stamp()}"
    rows = [
        [btn("☁️ CloudFront", "pk|dl"), btn("🖥 EC2 Servers", "pk|ec")],
        [btn("➕ Create CF", "pk|cr"), btn("🔎 Scan Regions", "pk|es")],
        [btn("📊 CF Analytics", "pk|ua"), btn("🛡 Free Tier", "pk|ft")],
        [btn("🛡 Security Audit", "pk|sec"), btn("🧹 Hunt Zombies", "pk|zomb")],
        [btn("🔍 Search (/find)", "find"), btn("⚡ Bulk Actions", "pk|bulk")],
        [btn("🌐 Overview", "ov"), btn("📥 Export CSV", "exp_csv")],
        [btn("👤 Accounts", "acc"), btn(f"🔔 Alerts: {alerts_txt}", "tgl_al")],
        [btn("⚙️ Settings & Logs", "bset"), btn("❓ Msaada", "help")],
    ]
    await show(u, c, text, rows)


@route("tgl_al")
async def h_tgl_al(u, c, p):
    uid = u.effective_user.id
    st = store.user_settings(uid)
    nv = "0" if st["alerts_on"] else "1"
    for k in ("alerts_on", "al_usage", "al_5xx", "al_ec2"):
        store.set_setting(uid, k, nv)
    await h_home(u, c, ["home"])


@route("bset")
async def h_bset(u, c, p):
    uid = u.effective_user.id
    st = store.user_settings(uid)
    tcard = tree_card([
        ("Theme", f'"{theme_name(st["ui_theme"])}"'),
        ("Alerts", "true" if st["alerts_on"] else "false"),
        ("CF_Alert", f"{st['alert_cf_pct']}%  # GB & Requests"),
        ("CPU_Alert", f"{st['alert_cpu_pct']}%  # All Active Regions"),
        ("Bill_Alert", f"${st['bill_alert_usd']:.2f} USD  # CloudWatch Billing"),
        ("Failover", "true" if st["auto_failover"] else "false"),
        ("Digest", f'{"true" if st["digest_on"] else "false"}  # {st["digest_hour"]:02d}:00 EAT'),
        ("DB_Backup", "true" if st["auto_backup_db"] else "false"),
    ])
    text = head("🏠 <b>Home</b>", "⚙️ <b>Bot Settings</b>") + tcard + stamp()
    rows = [
        [btn(f"🎨 Theme: {theme_name(st['ui_theme'])}", "cyc_theme")],
        [btn("🔔 Toggle Alerts", "sw|alerts_on"), btn("🌅 Toggle Digest", "sw|digest_on")],
        [btn("🤖 Auto-Failover", "sw|auto_failover"), btn("📦 Weekly DB Backup", "sw|auto_backup_db")],
        [btn("📊 CF % Alert", "set_cfp"), btn("🔥 CPU % Alert", "set_cpup")],
        [btn("💵 Bill $ Alert", "set_bill"), btn("⏰ Saa ya Digest", "set_dgh")],
        [btn("📨 Tuma Digest Sasa", "send_dg"), btn("📦 Download DB Sasa", "dl_db")],
        [btn("📜 Audit Log", "audit"), btn("🧹 Clear Cache", "clr_cache")],
        nav(),
    ]
    await show(u, c, text, rows)


@route("cyc_theme")
async def h_cyc_theme(u, c, p):
    uid = u.effective_user.id
    cur = store.get_setting(uid, "ui_theme", "classic")
    nxt = THEME_ORDER[(THEME_ORDER.index(cur) + 1) % len(THEME_ORDER)] if cur in THEME_ORDER else "classic"
    store.set_setting(uid, "ui_theme", nxt)
    set_theme(nxt)
    store.audit(uid, "bot", "UI_THEME", nxt)
    await h_bset(u, c, ["bset"])


@route("se")
async def h_se(u, c, p):
    await h_bset(u, c, p)


@route("sw")
async def h_sw(u, c, p):
    uid, k = u.effective_user.id, p[1]
    if k in ("alerts_on", "digest_on", "auto_failover", "auto_backup_db", "al_usage", "al_5xx", "al_ec2"):
        cur = store.get_setting(uid, k, "1") == "1"
        nv = "0" if cur else "1"
        store.set_setting(uid, k, nv)
        if k == "alerts_on":
            for sub in ("al_usage", "al_5xx", "al_ec2"):
                store.set_setting(uid, sub, nv)
        if k == "auto_failover":
            store.audit(uid, "bot", "AUTO_FAILOVER", "ON" if nv == "1" else "OFF")
    await h_bset(u, c, p)


@route("clr_cache")
async def h_clr_cache(u, c, p):
    aws.clear_user_cache(u.effective_user.id)
    await show(u, c, "🧹 <b>Cache imesafishwa!</b>\n" + card("Data zitavutwa upya moja kwa moja kutoka AWS."),
               [[btn("⬅️ Settings", "bset")], nav()])


@route("dl_db", slow=True)
async def h_dl_db(u, c, p):
    raw = await aws.run(store.backup_bytes)
    buf = io.BytesIO(raw)
    fname = f"cfbot_{dt.datetime.now(config.LOCAL_TZ).strftime('%Y%m%d_%H%M')}.db"
    await u.effective_chat.send_document(
        document=buf,
        filename=fname,
        caption="📦 <b>Encrypted Bot Backup (cfbot.db)</b>\n<i>Keys zimesimbwa kwa Fernet. Hifadhi SECRET_KEY yako salama.</i>",
        parse_mode="HTML",
    )
    await h_bset(u, c, p)


@route("audit")
async def h_audit(u, c, p):
    items = store.get_audit(u.effective_user.id, limit=20)
    lines = []
    for row in items:
        ts_str = dt.datetime.fromtimestamp(row["ts"], tz=config.LOCAL_TZ).strftime("%d/%m %H:%M")
        det = f" · {esc(row['detail'])}" if row["detail"] else ""
        lines.append(f"• <code>{ts_str}</code> [<b>{esc(row['acc'])}</b>] {esc(row['action'])}{det}")
    body = card("\n".join(lines), expandable=True) if lines else card("<i>Hakuna historia ya mabadiliko bado.</i>")
    await show(u, c, head("🏠 <b>Home</b>", "⚙️ <b>Settings</b>", "📜 <b>Audit Log</b>") + body + stamp(),
               [[btn("🔄 Refresh", "audit"), btn("⬅️ Settings", "bset")], nav()])


@route("set_cfp")
async def h_set_cfp(u, c, p):
    await prompt(u, c, {"t": "set_cfp"}, "📊 Tuma asilimia ya tahadhari ya CloudFront (10–100, mfano <code>80</code>):", "bset")

@route("set_cpup")
async def h_set_cpup(u, c, p):
    await prompt(u, c, {"t": "set_cpup"}, "🔥 Tuma asilimia ya tahadhari ya EC2 CPU (10–100, mfano <code>90</code>):", "bset")

@route("set_bill")
async def h_set_bill(u, c, p):
    await prompt(u, c, {"t": "set_bill"}, "💵 Tuma kikomo cha tahadhari ya bili kwa USD (mfano <code>0.50</code> au <code>1.00</code>):", "bset")

@route("set_dgh")
async def h_set_dgh(u, c, p):
    await prompt(u, c, {"t": "set_dgh"},
                 f"⏰ Tuma saa ya Daily Digest (0–23 EAT, UTC+{config.TZ_OFFSET_HRS}). Mfano: <code>8</code> kwa saa 2:00 asubuhi:", "bset")


@flow("set_cfp")
async def f_set_cfp(u, c, f, txt):
    if not txt.isdigit() or not 10 <= int(txt) <= 100:
        return await prompt(u, c, f, "❌ Weka namba kati ya 10 na 100:", "bset")
    store.set_setting(u.effective_user.id, "alert_cf_pct", int(txt))
    await h_bset(u, c, ["bset"])

@flow("set_cpup")
async def f_set_cpup(u, c, f, txt):
    if not txt.isdigit() or not 10 <= int(txt) <= 100:
        return await prompt(u, c, f, "❌ Weka namba kati ya 10 na 100:", "bset")
    store.set_setting(u.effective_user.id, "alert_cpu_pct", int(txt))
    await h_bset(u, c, ["bset"])

@flow("set_bill")
async def f_set_bill(u, c, f, txt):
    try:
        val = float(txt.replace("$", "").strip())
        if not 0.01 <= val <= 10000.0:
            raise ValueError
    except ValueError:
        return await prompt(u, c, f, "❌ Weka kiasi halali cha USD (mfano <code>0.50</code> au <code>1.00</code>):", "bset")
    store.set_setting(u.effective_user.id, "bill_alert_usd", f"{val:.2f}")
    store.audit(u.effective_user.id, "bot", "SET_BILL_ALERT", f"${val:.2f}")
    await h_bset(u, c, ["bset"])

@flow("set_dgh")
async def f_set_dgh(u, c, f, txt):
    if not txt.isdigit() or not 0 <= int(txt) <= 23:
        return await prompt(u, c, f, "❌ Weka saa (0–23) kwa mfumo wa masaa 24:", "bset")
    store.set_setting(u.effective_user.id, "digest_hour", int(txt))
    await h_bset(u, c, ["bset"])


@route("help")
async def h_help(u, c, p):
    th = get_theme()
    b_top, b_mid, b_bot = th["br"]
    text = (
        f"❓ <b>MWONGOZO WA AWS CONTROL BOT (v5 Ultra)</b>\n{th['sep']}\n"
        + card(
            f"{b_top} 1️⃣ <b>CloudFront:</b> Data + 10M Requests, Sparklines, 🔮 Traffic Forecast, 🩺 Origin Tester, 📋 Clone 1-Click, 🔀 Failover &amp; 🤖 5xx Auto-Failover.\n"
            f"{b_mid} 2️⃣ <b>EC2 &amp; Auto-Schedule:</b> Multi-Region Scan, Auto-DNS Prompt, ⏰ Sleep/Wake Timer, Multi-SG Firewall (🔒 Lock-to-CF + TCP/UDP + Custom IP), EIP, Snapshots &amp; SSM.\n"
            f"{b_mid} 3️⃣ <b>🛡 Free Tier &amp; Cost:</b> EC2 750h/mwezi + EBS 30GB Tracker, 💵 Live USD Bill Watchdog, 🛡 1-Click Security Audit &amp; 🧹 Zombie Hunter.\n"
            f"{b_bot} 4️⃣ <b>🔍 Search &amp; Ops:</b> Tumia <code>/find neno</code> kutafuta haraka, ⚡ Bulk Actions, 📥 Export CSV, 🎨 UI Themes, 🌐 Overview &amp; 📦 DB Backup."
        )
    )
    await show(u, c, text, [nav()])


@route("pk")
async def h_pk(u, c, p):
    purpose = p[1]
    names = store.accounts(u.effective_user.id)
    if not names:
        return await show(u, c, "⚠️ <b>Hakuna AWS Account</b>\n" + card("Unganisha account yako ya AWS kwanza."),
                          [[btn("➕ Add Account", "aa")], nav()])
    if len(names) == 1:
        await loading(u)
        args = [purpose, names[0], "0"] if purpose == "dl" else [purpose, names[0]]
        return await ROUTES[purpose][0](u, c, args)
    titles = {
        "dl": "☁️ Chagua account (CloudFront):",
        "ec": "🖥 Chagua account (EC2 Servers):",
        "es": "🔎 Scan EC2 Regions za account ipi?",
        "cr": "➕ Create CloudFront kwenye account ipi?",
        "ua": "📊 CF Analytics za account ipi?",
        "ft": "🛡 Free Tier & Muda wa account ipi?",
        "zomb": "🧹 Hunt Zombies kwenye account ipi?",
        "sec": "🛡 Security Audit kwenye account ipi?",
        "bulk": "⚡ Bulk Actions kwenye account ipi?",
    }
    cb = lambda n: f"{purpose}|{n}|0" if purpose == "dl" else f"{purpose}|{n}"
    await show(u, c, f"<b>{titles.get(purpose, 'Chagua account:')}</b>",
               [[btn(f"👤 {n}", cb(n))] for n in names] + [nav()])
