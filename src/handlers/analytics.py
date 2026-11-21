"""Per-distribution & account-wide CloudFront analytics (Data + 10M Requests + Sparklines + Traffic Forecasting)."""
import config
from core import aws
from core.router import route
from core.ui import show, btn, nav, head, card, tree_card, progress_bar, spark, stamp, esc, num
from handlers.cf_ui import _crumb, _k
from handlers.common import back_accounts, cf_list_cached
from services import cloudfront as cfs, metrics as ms


@route("an", slow=True)
async def h_an(u, c, p):
    acc, did, uid = p[1], p[2], u.effective_user.id
    cw = aws.client(uid, acc, "cloudwatch")
    m = await aws.run(ms.cf_full_metrics, cw, did)
    gb_pct = m["gb"] / config.FREE_TIER_GB * 100
    req_pct = m["req"] / config.FREE_TIER_REQ * 100
    fc_gb = ms.forecast_usage(m["gb"], config.FREE_TIER_GB, "GB")
    fc_req = ms.forecast_usage(m["req"], config.FREE_TIER_REQ, "reqs")

    tcard = tree_card([
        ("Data_7d", f"{spark(m['gb_7d'], 7)}  # {m['gb']:.2f} GB ({gb_pct:.1f}%)"),
        ("Data_24h", f"{m['gb_24h']:.2f} GB  # avg {fc_gb['burn_daily']:.1f} GB/d"),
        ("Reqs_7d", f"{spark(m['req_7d'], 7)}  # {num(m['req'])} ({req_pct:.1f}%)"),
        ("Reqs_24h", f"{int(m['req_24h']):,}  # proj {num(fc_req['projected'])}/mo"),
        ("Err_4xx", f"{m['e4']:.2f}%  # last 24h"),
        ("Err_5xx", f"{m['e5']:.2f}%  # last 24h"),
        ("Forecast", f"{fc_gb['projected']:.1f} GB/mo  # {fc_gb['proj_pct']:.0f}%"),
    ])
    bars = card(
        f"📦 <b>Bandwidth (Mwezi):</b> <b>{m['gb']:.2f} / {config.FREE_TIER_GB:.0f} GB</b> ({gb_pct:.1f}%)\n"
        f"{progress_bar(m['gb'], config.FREE_TIER_GB)}\n\n"
        f"📈 <b>Requests (Mwezi):</b> <b>{num(m['req'])} / 10M</b> ({req_pct:.1f}%)\n"
        f"{progress_bar(m['req'], config.FREE_TIER_REQ)}\n\n"
        f"🔮 <i>{esc(fc_gb['summary_sw'])}</i>"
    )
    k = _k(acc, did)
    await show(u, c, _crumb(acc, did, "📊 <b>Analytics</b>") + bars + "\n\n" + tcard + stamp(),
               [[btn("🔄 Refresh", f"an|{k}")], [btn("⬅️ Back", f"d|{k}")]])


@route("ua", slow=True)
async def h_ua(u, c, p):
    acc, uid = p[1], u.effective_user.id
    force = (len(p) > 2 and p[2] == "1")
    ds = (await cf_list_cached(uid, acc, force=force))[:30]
    cw = aws.client(uid, acc, "cloudwatch")

    async def one(d):
        try: return d, await aws.run(ms.cf_month_usage, cw, d["Id"])
        except Exception: return d, (0.0, 0.0)

    res = await aws.gather_limited([one(d) for d in ds], 8)
    tot_gb = sum(x[1][0] for x in res)
    tot_req = sum(x[1][1] for x in res)
    gb_pct = tot_gb / config.FREE_TIER_GB * 100
    req_pct = tot_req / config.FREE_TIER_REQ * 100
    fc_gb = ms.forecast_usage(tot_gb, config.FREE_TIER_GB, "GB")
    fc_req = ms.forecast_usage(tot_req, config.FREE_TIER_REQ, "reqs")

    paired = sorted(res, key=lambda x: (x[1][1], x[1][0]), reverse=True)
    lines = []
    for d, (g, r) in paired:
        dot = "🟢" if d["Enabled"] else "🔴"
        lines.append(f"{dot} <code>{d['Id']}</code> {esc(cfs.dlabel(d)[:18])}\n    └ <b>{g:.2f} GB</b> · <b>{num(r)} reqs</b>")

    tcard = tree_card([
        ("Account", f'"{acc}"'),
        ("Dists", f"{len(ds)}"),
        ("Data_GB", f"{tot_gb:.2f} / {config.FREE_TIER_GB:.0f}  # {fc_gb['burn_daily']:.1f} GB/siku"),
        ("Requests", f"{int(tot_req):,}  # proj {num(fc_req['projected'])}/mo"),
        ("Forecast", f"{fc_gb['projected']:.1f} GB/mo  # {fc_gb['proj_pct']:.0f}%"),
    ])
    bars = card(
        f"📦 <b>Data:</b> <b>{tot_gb:.1f} / {config.FREE_TIER_GB:.0f} GB</b> ({gb_pct:.1f}%)\n"
        f"{progress_bar(tot_gb, config.FREE_TIER_GB)}\n\n"
        f"📈 <b>Requests:</b> <b>{num(tot_req)} / 10M</b> ({req_pct:.1f}%)\n"
        f"{progress_bar(tot_req, config.FREE_TIER_REQ)}\n\n"
        f"🔮 <b>Utabiri:</b> <i>{esc(fc_gb['summary_sw'])}</i>"
    )
    breakdown = ("\n" + card("<b>📋 Mgawanyo wa Distributions:</b>\n" + "\n".join(lines), expandable=True)) if lines else "\n<i>Hakuna distributions.</i>"
    text = head("🏠 <b>Home</b>", f"👤 <b>{esc(acc)}</b>", "📊 <b>CloudFront Analytics</b>") + bars + "\n\n" + tcard + breakdown + stamp()
    await show(u, c, text, [[btn("🔄 Refresh", f"ua|{acc}|1")], nav(back_accounts(u, "ua"))])
