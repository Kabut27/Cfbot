"""Per-distribution analytics and account-wide usage (parallel CloudWatch calls)."""
import asyncio
from core import aws
from core.router import route
from core.ui import show, btn, nav, head, card, esc, bar, spark, gb, pct, num
import config
from handlers.cf_ui import _crumb, _k
from handlers.common import back_accounts
from services import cloudfront as cfs, metrics as ms


@route("an", slow=True)
async def h_an(u, c, p):
    acc, did, uid = p[1], p[2], u.effective_user.id
    cw = aws.client(uid, acc, "cloudwatch")
    m, r1, r24 = await asyncio.gather(aws.run(ms.cf_month_detail, cw, did),
                                      aws.run(ms.cf_rates, cw, did, 60), aws.run(ms.cf_rates, cw, did, 1440))
    verdict = ""
    if r1["e5"] is not None and r1["e5"] >= 20 and r1["req"] >= 20:
        verdict = "\n🚨 <b>5xx ni juu:</b> backend (EC2/x-ui) huenda imekufa au port si sahihi."
    elif r1["req"] == 0:
        verdict = "\n💤 Hakuna trafiki saa 1 iliyopita."
    body = card(
        f"📅 <b>Mwezi huu</b>\n⬇️ <code>{gb(m['gb'])}</code> · 🔁 <code>{num(m['req'])}</code> requests\n"
        f"<code>{spark(m['daily'])}</code>\n\n"
        f"🕐 <b>Saa 1:</b> 4xx <code>{pct(r1['e4'])}</code> · 5xx <code>{pct(r1['e5'])}</code> · req <code>{num(r1['req'])}</code>\n"
        f"🕛 <b>Saa 24:</b> 4xx <code>{pct(r24['e4'])}</code> · 5xx <code>{pct(r24['e5'])}</code> · req <code>{num(r24['req'])}</code>")
    k = _k(acc, did)
    await show(u, c, _crumb(acc, did, "📊 Analytics") + body + verdict,
               [[btn("🔄 Refresh", f"an|{k}")], nav(f"d|{k}")])


@route("ua", slow=True)
async def h_ua(u, c, p):
    acc, uid = p[1], u.effective_user.id
    ds = (await aws.run(cfs.all_dists, aws.client(uid, acc)))[:40]
    cw = aws.client(uid, acc, "cloudwatch")
    async def one(d):
        try: return d, await aws.run(ms.cf_month_detail, cw, d["Id"])
        except Exception: return d, None
    res = await aws.gather_limited([one(d) for d in ds], 8)       # parallel, not one-by-one
    tot = sum(m["gb"] for _, m in res if m)
    pc = tot / config.FREE_TIER_GB * 100
    lines = [f"• <code>{d['Id']}</code> {esc(cfs.dlabel(d))[:28]}: <b>{gb(m['gb']) if m else '—'}</b>"
             for d, m in sorted(res, key=lambda x: -(x[1]["gb"] if x[1] else 0))]
    warn = "\n🚨 <b>Karibu kuisha!</b> Gawa trafiki kwenye account nyingine." if pc >= 80 else ""
    text = (head("🏠 Home", f"👤 {esc(acc)}", "📊 Usage") +
            card(f"<code>{bar(pc)}</code> <b>{tot:,.1f} / {config.FREE_TIER_GB} GB</b> ({pc:.0f}%)") + warn +
            "\n" + (card("\n".join(lines), expandable=len(lines) > 6) if lines else "Hakuna distributions."))
    await show(u, c, text, [[btn("🔄 Refresh", f"ua|{acc}")], nav(back_accounts(u, "ua"))])
