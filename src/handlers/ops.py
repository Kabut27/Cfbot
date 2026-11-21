"""v5 Ultra Operations: Built-in EC2 750h & EBS 30GB Free Tier Tracker, Traffic & Cost Forecasting,
Global Multi-Account Overview with Health Scores, Zombie Resource Hunter, and Daily Digest."""
import asyncio, re, datetime as dt
import config
import storage as store
from core import aws
from core.router import route, flow
from core.ui import show, btn, nav, head, card, tree_card, progress_bar, health_badge, stamp, esc, num
from handlers.common import prompt, tok_data, cf_list_cached, scan_ec2_all_regions
from services import cloudfront as cfs, ec2 as ec2s, metrics as ms


async def _collect_ec2_ebs_ft(uid, acc):
    """Built-in collector for EC2 750h/mo + EBS 30GB across all enabled regions."""
    try:
        regs = await aws.run(ec2s.enabled_regions, aws.client(uid, acc, "ec2", "us-east-1"))
    except Exception:
        regs = list(ec2s.REGIONS.keys())

    async def _one(r):
        try:
            return await aws.run(ec2s.freetier_region_stats, aws.client(uid, acc, "ec2", r), r)
        except Exception:
            return {"region": r, "hours": 0.0, "proj_hours": 0.0, "running": [], "total": 0, "ebs_gb": 0}

    chunks = await aws.gather_limited([_one(r) for r in regs], 10)
    return {
        "hours": sum(c["hours"] for c in chunks),
        "proj_hours": sum(c["proj_hours"] for c in chunks),
        "running_cnt": sum(len(c["running"]) for c in chunks),
        "total_cnt": sum(c["total"] for c in chunks),
        "ebs_gb": sum(c["ebs_gb"] for c in chunks),
    }


# ======================= Free Tier & Account Expiry (with Built-in EC2 750h + EBS 30GB + Forecast) =======================
@route("ft", slow=True)
async def h_ft(u, c, p):
    uid, acc = u.effective_user.id, p[1]
    info = store.account_meta(uid, acc)
    ak, sk = store.creds(uid, acc)
    opened_at, ft_months = info["opened_at"], info["ft_months"]

    if not opened_at:
        auto_date = await aws.run(cfs.try_account_created_date, ak, sk)
        if auto_date:
            opened_at = auto_date
            store.set_account_meta(uid, acc, opened_at=opened_at)

    exp = cfs.calc_ft_expiry(opened_at, ft_months)
    cw_us = aws.client(uid, acc, "cloudwatch", "us-east-1")
    ds, (ft_items, ft_err), bill_usd, ec2_ft = await asyncio.gather(
        cf_list_cached(uid, acc),
        aws.run(cfs.try_freetier_api, ak, sk),
        aws.run(ms.billing_usd, cw_us),
        _collect_ec2_ebs_ft(uid, acc),
    )

    async def _one_cf(d):
        try: return await aws.run(ms.cf_month_usage, cw_us, d["Id"])
        except Exception: return (0.0, 0.0)
    usage = await aws.gather_limited([_one_cf(d) for d in ds[:25]], 8)
    tot_gb = sum(x[0] for x in usage)
    tot_req = sum(x[1] for x in usage)

    fc_gb = ms.forecast_usage(tot_gb, config.FREE_TIER_GB, "GB")
    ec2_hrs, ec2_proj = ec2_ft["hours"], ec2_ft["proj_hours"]
    ebs_gb = ec2_ft["ebs_gb"]

    # Free Tier Health Score
    score = 100
    if exp and exp["days_left"] <= 0: score -= 35
    elif exp and exp["days_left"] <= 30: score -= 15
    if tot_gb / config.FREE_TIER_GB >= 0.85: score -= 20
    if ec2_proj > 750.0: score -= 20
    if ebs_gb > 30: score -= 15
    if bill_usd is not None and bill_usd >= 1.0: score -= 20

    if exp:
        exp_block = card(
            f"{exp['badge']} <b>Muda wa Account ({exp['months']}m Free Tier): {exp['status']}</b>\n"
            f"{progress_bar(exp['days_used'], exp['total_days'])}\n"
            f"╭ 📅 <b>Ilifunguliwa:</b> <code>{exp['start']}</code>\n"
            f"├ 🏁 <b>Inaisha:</b> <code>{exp['end']}</code>\n"
            f"╰ ⏳ <b>Zilizobaki:</b> <b>{max(0, exp['days_left'])} siku</b> "
            f"(zimetumika {exp['days_used']}/{exp['total_days']}d)"
        )
    else:
        exp_block = card(
            "📅 <b>Tarehe ya kufungua AWS haijawekwa bado.</b>\n"
            "<i>Bonyeza <b>📅 Weka Tarehe</b> chini kuingiza siku uliyofungua akaunti (mfano <code>2026-03-15</code>) "
            "au ongeza <code>account:GetAccountInformation</code> kwenye IAM Policy.</i>"
        )

    gb_pct = tot_gb / config.FREE_TIER_GB * 100
    req_pct = tot_req / config.FREE_TIER_REQ * 100
    hrs_pct = ec2_hrs / 750.0 * 100
    ebs_pct = ebs_gb / 30.0 * 100

    u_lines = [
        f"💚 <b>Free Tier Score:</b> <b>{health_badge(score)}</b>",
        "",
        f"📦 <b>CF Data:</b> <code>{tot_gb:.1f} / {config.FREE_TIER_GB:.0f} GB</code> ({gb_pct:.0f}%)",
        progress_bar(tot_gb, config.FREE_TIER_GB),
        f"📈 <b>CF Reqs:</b> <code>{num(tot_req)} / 10M</code> ({req_pct:.0f}%)",
        progress_bar(tot_req, config.FREE_TIER_REQ),
        f"🖥 <b>EC2 750h:</b> <code>{ec2_hrs:.0f} / 750h</code> ({hrs_pct:.0f}%) · <i>proj {ec2_proj:.0f}h</i>",
        progress_bar(ec2_hrs, 750.0),
        f"💽 <b>EBS 30GB:</b> <code>{ebs_gb} / 30 GB</code> ({ebs_pct:.0f}%) · <b>EC2 Run:</b> <code>{ec2_ft['running_cnt']}/{ec2_ft['total_cnt']}</code>",
        progress_bar(ebs_gb, 30.0),
    ]
    if bill_usd is not None:
        b_dot = "🟢" if bill_usd <= 0.01 else ("🟡" if bill_usd < 1.0 else "🔴")
        u_lines.append(f"💵 <b>Est. Bill (USD):</b> {b_dot} <code>${bill_usd:.2f}</code>")
    else:
        u_lines.append("💵 <b>Est. Bill (USD):</b> <i>Washa 'Receive Billing Alerts' AWS</i>")
    u_lines.append(f"🔮 <i>{esc(fc_gb['summary_sw'])}</i>")
    bars = card("\n".join(u_lines))

    api_block = ""
    if ft_items is not None:
        top = sorted(
            [x for x in ft_items if float(x.get("actualUsageAmount", 0)) > 0],
            key=lambda x: float(x.get("actualUsageAmount", 0)) / max(1.0, float(x.get("limit", 1))),
            reverse=True,
        )[:6]
        ec2_api_ft = any(k in (x.get("service", "") + " " + x.get("usageType", ""))
                         for x in ft_items for k in ("EC2", "Elastic Compute", "BoxUsage"))
        hdr = "🟢 <b>AWS Billing Free Tier</b> · EC2 750h Active" if ec2_api_ft else "🔵 <b>AWS Billing Free Tier (Live)</b>"
        alines = [hdr]
        for x in top:
            av, lv = float(x.get("actualUsageAmount", 0)), float(x.get("limit", 0))
            unit = x.get("unit", "")
            svc = x.get("service", "AWS").replace("Amazon ", "").replace("AWS ", "")
            if "Request" in unit or lv >= 10_000:
                a_s, l_s = num(av), num(lv)
            else:
                a_s = f"{av:.1f}" if av % 1 else f"{int(av)}"
                l_s = f"{lv:.0f}"
            alines.append(f"• {esc(svc)}: <code>{a_s}/{l_s}</code> {esc(unit)}")
        api_block = "\n\n" + card("\n".join(alines), expandable=True)
    elif ft_err == "denied":
        api_block = "\n<i>💡 Tip: Ongeza <code>freetier:GetFreeTierUsage</code> na <code>account:GetAccountInformation</code> kwenye IAM Policy.</i>"

    next_m = 6 if ft_months == 12 else 12
    rows = [
        [btn("📅 Weka/Badili Tarehe", f"ft_date|{acc}"), btn(f"⏱ Badili kuwa {next_m}m", f"ft_mon|{acc}|{next_m}")],
        [btn("🧹 Hunt Zombies", f"zomb|{acc}"), btn("🛡 Security Audit", f"sec|{acc}")],
        [btn("🔄 Refresh", f"ft|{acc}"), btn("🏠 Menu Kuu", "home")],
    ]
    await show(u, c, head("🏠 <b>Home</b>", f"👤 <b>{esc(acc)}</b>", "🛡 <b>Free Tier &amp; Forecast</b>")
               + exp_block + "\n\n" + bars + api_block + stamp(), rows)


@route("ft_mon", slow=True)
async def h_ft_mon(u, c, p):
    uid, acc, m = u.effective_user.id, p[1], int(p[2])
    store.set_account_meta(uid, acc, ft_months=m)
    store.audit(uid, acc, "FT_DURATION", f"{m} months")
    await h_ft(u, c, ["ft", acc])


@route("ft_date")
async def h_ft_date(u, c, p):
    acc = p[1]
    await prompt(
        u, c, {"t": "ft_date", "acc": acc},
        "📅 Tuma <b>tarehe uliyofungua AWS account hii</b> kwa muundo <code>YYYY-MM-DD</code>\nMfano: <code>2026-03-15</code>",
        f"ft|{acc}",
    )


@flow("ft_date")
async def f_ft_date(u, c, f, txt):
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", txt):
        return await prompt(u, c, f, "❌ Tumia muundo <code>YYYY-MM-DD</code> (mfano <code>2026-03-15</code>):", f"ft|{f['acc']}")
    dt.datetime.strptime(txt, "%Y-%m-%d")
    store.set_account_meta(u.effective_user.id, f["acc"], opened_at=txt)
    store.audit(u.effective_user.id, f["acc"], "FT_START_DATE", txt)
    await h_ft(u, c, ["ft", f["acc"]])


# ======================= Global Multi-Account Overview (with Health Scores) =======================
@route("ov", slow=True)
async def h_ov(u, c, p):
    uid = u.effective_user.id
    accs = store.accounts_meta(uid)
    if not accs:
        return await show(u, c, "⚠️ <b>Hakuna AWS Account</b>\n" + card("Unganisha account yako ya AWS kwanza."),
                          [[btn("➕ Add Account", "aa")], nav()])

    async def _one_acc(acc, info):
        try:
            cw_us = aws.client(uid, acc, "cloudwatch", "us-east-1")
            ds, ec2_ft, bill_usd = await asyncio.gather(
                cf_list_cached(uid, acc),
                _collect_ec2_ebs_ft(uid, acc),
                aws.run(ms.billing_usd, cw_us),
            )
            async def _cf_u(d):
                try: return await aws.run(ms.cf_month_usage, cw_us, d["Id"])
                except Exception: return (0.0, 0.0)
            usage = await aws.gather_limited([_cf_u(d) for d in ds[:20]], 8)
            tot_gb = sum(x[0] for x in usage)
            tot_req = sum(x[1] for x in usage)
            ex = cfs.calc_ft_expiry(info["opened_at"], info["ft_months"])

            score = 100
            if tot_gb / config.FREE_TIER_GB >= 0.85: score -= 25
            if ec2_ft["proj_hours"] > 750: score -= 20
            if ec2_ft["ebs_gb"] > 30: score -= 15
            if bill_usd is not None and bill_usd >= 1.0: score -= 20
            if ex and ex["days_left"] <= 0: score -= 25

            rows = [
                ("Account", f'"{acc}"  # {len(ds)} CF · {ec2_ft["running_cnt"]}/{ec2_ft["total_cnt"]} EC2'),
                ("Health", f'"{health_badge(score)}"'),
                ("CF_Data", f"{tot_gb:.1f}/{config.FREE_TIER_GB:.0f} GB  # {tot_gb / config.FREE_TIER_GB * 100:.0f}%"),
                ("CF_Reqs", f"{num(tot_req)}/10M  # {tot_req / config.FREE_TIER_REQ * 100:.0f}%"),
                ("EC2_Hrs", f"{ec2_ft['hours']:.0f}/750h · {ec2_ft['ebs_gb']}GB EBS"),
                ("Est_Bill", f"${bill_usd:.2f} USD" if bill_usd is not None else "n/a"),
            ]
            if ex:
                ft_s = "EXPIRED" if ex["days_left"] <= 0 else f"{ex['days_left']}d left"
                rows.append(("FT_Left", f"{ft_s}  # exp {ex['end']}"))
            return tree_card(rows)
        except Exception as e:
            return card(f"⚠️ <b>{esc(acc)}:</b> {esc(str(e)[:120])}")

    cards = await asyncio.gather(*(_one_acc(n, info) for n, info in accs.items()))
    text = head("🏠 <b>Home</b>", f"🌐 <b>Global Overview ({len(accs)} Accounts)</b>") + "\n\n".join(cards) + stamp()
    await show(u, c, text, [[btn("🔄 Refresh", "ov"), btn("📥 Export CSV", "exp_csv")], nav()])


# ======================= Zombie Resource Hunter =======================
@route("zomb", slow=True)
async def h_zomb(u, c, p):
    uid, acc = u.effective_user.id, p[1]
    try:
        regs = await aws.run(ec2s.enabled_regions, aws.client(uid, acc, "ec2", "us-east-1"))
    except Exception:
        regs = list(ec2s.REGIONS.keys())

    async def _one(reg):
        try:
            cl = aws.client(uid, acc, "ec2", reg)
            return await aws.run(ec2s.zombie_scan_region, cl, reg)
        except Exception:
            return {"vols": [], "eips": [], "stopped_eips": [], "snaps": []}

    chunks = await aws.gather_limited([_one(r) for r in regs], 10)
    vols = [x for ch in chunks for x in ch["vols"]]
    eips = [x for ch in chunks for x in ch["eips"]]
    st_eips = [x for ch in chunks for x in ch["stopped_eips"]]
    snaps = [x for ch in chunks for x in ch["snaps"]]

    tot_z = len(vols) + len(eips) + len(st_eips) + len(snaps)
    est_waste = (len(eips) + len(st_eips)) * 3.65 + sum(v["gb"] * 0.08 for v in vols) + sum(s["gb"] * 0.05 for s in snaps)

    lines, rows = [], []
    if not tot_z:
        lines.append("🟢 <b>Safi kabisa!</b> Hakuna volumes zilizotelekezwa, EIPs zilizolala, wala snapshots za zamani (&gt;30d).")
    else:
        lines.append(f"⚠️ Zimepatikana <b>{tot_z}</b> · Makadirio ya upotevu: <code>~${est_waste:.2f}/mwezi</code>")
        for v in vols[:6]:
            lines.append(f"💽 Vol <code>{v['id']}</code> ({v['reg']}) · {v['gb']}GB {v['type']}")
            tok = store.ref(uid, {"a": acc, "r": v["reg"], "vid": v["id"]})
            rows.append([btn(f"🗑 Futa Vol {v['id']} ({v['gb']}GB)", f"z_vol|{tok}")])
        for e in eips[:6]:
            lines.append(f"💸 Idle EIP <code>{e['ip']}</code> ({e['reg']})")
            tok = store.ref(uid, {"a": acc, "r": e["reg"], "aid": e["aid"], "ip": e["ip"]})
            rows.append([btn(f"🗑 Release EIP {e['ip']}", f"z_eip|{tok}")])
        for se in st_eips[:4]:
            lines.append(f"🟠 EIP kwenye Stopped EC2: <code>{se['ip']}</code> → <code>{se['iid']}</code> ({se['reg']})")
        for s in snaps[:6]:
            lines.append(f"📸 Old Snap <code>{s['id']}</code> ({s['reg']}) · {s['age']}d · {s['gb']}GB")
            tok = store.ref(uid, {"a": acc, "r": s["reg"], "sid": s["id"]})
            rows.append([btn(f"🗑 Futa Snap {s['id']} ({s['age']}d)", f"z_snp|{tok}")])

    rows += [[btn("🔄 Scan Tena", f"zomb|{acc}"), btn("🛡 Free Tier", f"ft|{acc}")], nav()]
    await show(u, c, head("🏠 <b>Home</b>", f"👤 <b>{esc(acc)}</b>", "🧹 <b>Zombie Resource Hunter</b>") + card("\n".join(lines)) + stamp(), rows)


@route("z_vol")
async def h_z_vol(u, c, p):
    d = tok_data(u, p[1])
    await show(u, c, f"⚠️ <b>Futa EBS Volume <code>{d['vid']}</code> ({d['r']})?</b>\n" + card("Data ndani ya volume hii zitafutwa kabisa."),
               [[btn("🗑 Ndiyo, Futa", f"z_volx|{p[1]}"), btn("❌ Hapana", f"zomb|{d['a']}")]])

@route("z_volx", slow=True)
async def h_z_volx(u, c, p):
    d = tok_data(u, p[1])
    await aws.run(aws.client(u.effective_user.id, d["a"], "ec2", d["r"]).delete_volume, VolumeId=d["vid"])
    store.audit(u.effective_user.id, d["a"], "ZOMBIE_DEL_VOL", f"{d['vid']} ({d['r']})")
    await h_zomb(u, c, ["zomb", d["a"]])

@route("z_eip")
async def h_z_eip(u, c, p):
    d = tok_data(u, p[1])
    await show(u, c, f"⚠️ <b>Release Elastic IP <code>{d['ip']}</code> ({d['r']})?</b>\n" + card("IP hii itaondolewa na haitatozwa tena."),
               [[btn("🗑 Ndiyo, Release", f"z_eipx|{p[1]}"), btn("❌ Hapana", f"zomb|{d['a']}")]])

@route("z_eipx", slow=True)
async def h_z_eipx(u, c, p):
    d = tok_data(u, p[1])
    await aws.run(aws.client(u.effective_user.id, d["a"], "ec2", d["r"]).release_address, AllocationId=d["aid"])
    store.audit(u.effective_user.id, d["a"], "ZOMBIE_REL_EIP", f"{d['ip']} ({d['r']})")
    await h_zomb(u, c, ["zomb", d["a"]])

@route("z_snp")
async def h_z_snp(u, c, p):
    d = tok_data(u, p[1])
    await show(u, c, f"⚠️ <b>Futa Snapshot <code>{d['sid']}</code> ({d['r']})?</b>",
               [[btn("🗑 Ndiyo, Futa", f"z_snpx|{p[1]}"), btn("❌ Hapana", f"zomb|{d['a']}")]])

@route("z_snpx", slow=True)
async def h_z_snpx(u, c, p):
    d = tok_data(u, p[1])
    await aws.run(aws.client(u.effective_user.id, d["a"], "ec2", d["r"]).delete_snapshot, SnapshotId=d["sid"])
    store.audit(u.effective_user.id, d["a"], "ZOMBIE_DEL_SNAP", f"{d['sid']} ({d['r']})")
    await h_zomb(u, c, ["zomb", d["a"]])


# ======================= Daily Morning Digest =======================
async def send_daily_digest(bot_obj, uid):
    accs = store.accounts_meta(uid)
    if not accs:
        return
    now_local = dt.datetime.now(config.LOCAL_TZ).strftime("%d %b %Y · %H:%M EAT")
    sections = []
    for acc, info in accs.items():
        try:
            cw = aws.client(uid, acc, "cloudwatch", "us-east-1")
            ds, ec2_ft, bill_usd = await asyncio.gather(
                cf_list_cached(uid, acc, force=True),
                _collect_ec2_ebs_ft(uid, acc),
                aws.run(ms.billing_usd, cw),
            )
            async def _cf_u(d):
                try: return await aws.run(ms.cf_month_usage, cw, d["Id"])
                except Exception: return (0.0, 0.0)
            usage = await aws.gather_limited([_cf_u(d) for d in ds[:25]], 8)
            tot_gb = sum(x[0] for x in usage)
            tot_req = sum(x[1] for x in usage)
            ex = cfs.calc_ft_expiry(info["opened_at"], info["ft_months"])
            rows = [
                ("Account", f'"{acc}"'),
                ("CF_Data", f"{tot_gb:.1f}/{config.FREE_TIER_GB:.0f} GB  # {tot_gb / config.FREE_TIER_GB * 100:.0f}%"),
                ("CF_Reqs", f"{num(tot_req)}/10M  # {tot_req / config.FREE_TIER_REQ * 100:.0f}%"),
                ("EC2_Hrs", f"{ec2_ft['hours']:.0f}/750h · {ec2_ft['running_cnt']}/{ec2_ft['total_cnt']} run · {ec2_ft['ebs_gb']}GB EBS"),
            ]
            if bill_usd is not None:
                rows.append(("Bill_USD", f"${bill_usd:.2f} USD"))
            if ex:
                rows.append(("FT_Left", f"{max(0, ex['days_left'])} days  # exp {ex['end']}"))
            sections.append(tree_card(rows))
        except Exception as e:
            sections.append(card(f"⚠️ <b>{esc(acc)}:</b> {esc(str(e)[:120])}"))

    msg = f"🌅 <b>AWS DAILY DIGEST</b> · <i>{now_local}</i>\n{'━' * 20}\n" + "\n\n".join(sections)
    await bot_obj.send_message(chat_id=uid, text=msg, parse_mode="HTML")


@route("send_dg", slow=True)
async def h_send_dg(u, c, p):
    from handlers.home import h_bset
    await send_daily_digest(c.bot, u.effective_user.id)
    await h_bset(u, c, ["bset"])
