"""EC2 v4 Pro UI: Multi-Region Scan, Tree Card Detail, Start Auto-DNS Prompt,
Metrics Sparklines, EBS Snapshots (Create & Delete), Launch Templates."""
import asyncio
import storage as store
from core import aws
from core.router import route, flow
from core.ui import show, btn, nav, head, card, tree_card, progress_bar, spark, health_badge, stamp, esc
from handlers.common import (tok_data, itok, ec2c, mark_state, add_monitored, set_monitored,
                             region_buttons, back_accounts, ec2_list_cached, scan_ec2_all_regions)
from services import cloudfront as cfs, ec2 as ec2s, metrics as ms


def _crumb(acc, *more): return head("🏠 <b>Home</b>", f"👤 <b>{esc(acc)}</b>", "🖥 <b>EC2</b>", *more)
def _ilabel(i): return f"{ec2s.icon(i['state'])} {i['name'] or i['id']} ({i['type']})"


# ======================= regions / scan / list =======================
@route("ec")
async def h_ec(u, c, p):
    acc, uid = p[1], u.effective_user.id
    def_reg = store.get_setting(uid, f"region:{acc}", "us-east-1")
    rows = [[btn("🔎 Scan All Active Regions", f"es|{acc}")]] + region_buttons(uid, acc, f"er|{acc}|{{r}}") + [nav(back_accounts(u, "ec"))]
    await show(
        u, c,
        _crumb(acc, "<b>Regions</b>") + card(f"⭐ Default Region: <code>{def_reg}</code>\nChagua region au bonyeza <b>Scan All</b>:"),
        rows,
    )


@route("er", slow=True)
async def h_er(u, c, p):
    acc, region, uid = p[1], p[2], u.effective_user.id
    force = (len(p) > 3 and p[3] == "1")
    store.set_setting(uid, f"region:{acc}", region)
    items = await ec2_list_cached(uid, acc, region, force=force)
    if items: add_monitored(uid, acc, region)
    run_cnt = sum(1 for i in items if i["state"] == "running")
    rows = [[btn(_ilabel(i)[:56], f"ei|{itok(u, acc, region, i['id'])}")] for i in items[:20]]
    rows += [
        [btn("➕ Launch Template", f"lt|{acc}|{region}"), btn("🔄 Refresh", f"er|{acc}|{region}|1")],
        [btn("🌍 Regions", f"ec|{acc}"), btn("🏠 Menu Kuu", "home")],
    ]
    await show(
        u, c,
        head("🏠 <b>Home</b>", f"👤 <b>{esc(acc)}</b>", f"🖥 <b>{region}</b>")
        + card(f"📍 <b>{ec2s.rname(region)}</b>\n🖥 <b>Instances:</b> <code>{len(items)}</code> (🟢 {run_cnt} running)"),
        rows,
    )


@route("rg")
async def h_rg(u, c, p):
    await h_ec(u, c, p)


@route("es", slow=True)
async def h_es(u, c, p):
    acc, uid = p[1], u.effective_user.id
    force = (len(p) > 2 and p[2] == "1")
    res = await scan_ec2_all_regions(uid, acc, force=force)
    found_regs = [(r, it) for r, it, e in res if it]
    all_insts = [(r, i) for r, it in found_regs for i in it]
    set_monitored(uid, acc, [r for r, _ in found_regs])
    run_cnt = sum(1 for _, i in all_insts if i["state"] == "running")

    rows = []
    for r, i in all_insts[:20]:
        ip = i["ip"] or "no-ip"
        rows.append([btn(f"{ec2s.icon(i['state'])} {i['name'] or i['id']} · {r} · {ip}"[:56], f"ei|{itok(u, acc, r, i['id'])}")])
    rows += [
        [btn("🌍 Chagua Region", f"ec|{acc}"), btn("🔄 Rescan", f"es|{acc}|1")],
        nav(back_accounts(u, "es")),
    ]
    summary = card(
        f"╭ 🖥 <b>Zilizopatikana:</b> <code>{len(all_insts)}</code> (regions {len(res)})\n"
        f"╰ 🟢 <b>Running:</b> <code>{run_cnt}</code> · 🔴 <b>Stopped:</b> <code>{len(all_insts) - run_cnt}</code>"
    )
    await show(u, c, _crumb(acc, "🔎 <b>Scan</b>") + summary, rows)


# ======================= instance detail (v4 Pro Tree Card) =======================
async def inst_view(u, c, tok, banner=""):
    uid = u.effective_user.id
    d = tok_data(u, tok)
    acc, reg, iid = d["a"], d["r"], d["i"]
    cl = ec2c(u, d, "ec2")
    cw = ec2c(u, d, "cloudwatch")

    async def _safe_met():
        try: return await aws.run(ms.ec2_metrics, cw, iid)
        except Exception: return {"cpu": 0.0, "cpu_vals": [], "net_out_gb": 0.0, "net_in_gb": 0.0}

    (i, eips), met = await asyncio.gather(aws.run(ec2s.get_instance, cl, reg, iid), _safe_met())
    sg_ids = [g for g, _ in i["sgs"]]
    try:
        sg_sum = await aws.run(ec2s.sg_summary, cl, sg_ids)
    except Exception:
        sg_sum = f"{len(sg_ids)} SG"

    info = store.account_meta(uid, acc)
    ex = cfs.calc_ft_expiry(info["opened_at"], info["ft_months"])
    sch = store.get_schedule(uid, acc, reg, iid)
    e_score = 100
    if i["state"] != "running": e_score -= 20
    if met["cpu"] >= 90: e_score -= 35
    elif met["cpu"] >= 75: e_score -= 15
    if "ALL(Pub)" in sg_sum: e_score -= 25
    y_rows = [
        ("Health", f'"{health_badge(e_score)}"'),
        ("State", f'"{i["state"].upper()}"  # {i["type"]}'),
        ("ElasticIP", "true" if eips else "false"),
        ("CPU_1h", f"{spark(met['cpu_vals'], 8)}  # {met['cpu']:.1f}%"),
        ("Net_Out", f"{met['net_out_gb']:.2f} GB  # last 24h"),
        ("Ports", f'"{sg_sum[:30]}"  # {len(sg_ids)} SG'),
    ]
    if sch and sch["enabled"]:
        st_s = f"Stop {sch['stop_hr']:02d}h" if sch["stop_hr"] >= 0 else "Stop —"
        up_s = f"Start {sch['start_hr']:02d}h" if sch["start_hr"] >= 0 else "Start —"
        y_rows.append(("Schedule", f'"{st_s} / {up_s}"  # EAT'))
    if ex:
        y_rows.append(("FT_Left", f"{max(0, ex['days_left'])}d  # exp {ex['end']}"))

    copy_block = card(
        f"╭ 🆔 <b>ID:</b> <code>{iid}</code>\n"
        f"├ 🌐 <b>Public IP:</b> <code>{i['ip'] or '—'}</code>\n"
        f"├ 🔗 <b>Public DNS:</b> <code>{esc(i['dns'] or '—')}</code>\n"
        f"╰ 🏠 <b>Private IP:</b> <code>{i['pip'] or '—'}</code>"
    )
    text = (
        head("🏠 <b>Home</b>", f"👤 <b>{esc(acc)}</b>", f"🖥 <b>{reg}</b>")
        + f"{ec2s.icon(i['state'])} <b>{esc(i['name'] or iid)}</b>\n"
        + tree_card(y_rows) + "\n\n" + copy_block
        + (f"\n{banner}" if banner else "") + stamp()
    )
    power_btn = btn("⏹ Stop", f"eq|{tok}|stop") if i["state"] == "running" else btn("▶️ Start", f"ea|{tok}|start")
    cf_lbl = "☁️ Sasisha CloudFront na DNS Hii Mpya" if banner else "☁️ Set as CF Origin"
    rows = [
        [power_btn, btn("🔄 Reboot", f"eq|{tok}|reboot")],
    ]
    if i["dns"]:
        rows.append([btn(cf_lbl, f"lk|{tok}")])
    rows += [
        [btn("🛡 Firewall / SG", f"fw|{tok}"), btn("📊 Metrics", f"em|{tok}")],
        [btn("🌐 Elastic IP", f"ip|{tok}"), btn("📸 Snapshot", f"sn|{tok}")],
        [btn("⏰ Auto-Schedule", f"sch|{tok}"), btn("💻 Run Command", f"cm|{tok}")],
        [btn("🗑 Terminate", f"ek|{tok}"), btn("🔄 Refresh", f"ei|{tok}")],
        [btn("⬅️ Back", f"er|{acc}|{reg}"), btn("🏠 Menu Kuu", "home")],
    ]
    await show(u, c, text, rows)


@route("ei", slow=True)
async def h_ei(u, c, p): await inst_view(u, c, p[1])


# ======================= power + Start Auto-DNS Prompt =======================
@route("eq")
async def h_eq(u, c, p):
    d = tok_data(u, p[1])
    verb = {"stop": "⏹ Stop", "reboot": "🔄 Reboot"}[p[2]]
    await show(u, c, _crumb(d["a"], f"<code>{d['i']}</code>") + f"{verb} instance hii?",
               [[btn("✅ Ndiyo", f"ea|{p[1]}|{p[2]}"), btn("❌ Hapana", f"ei|{p[1]}")]])


@route("ea", slow=True)
async def h_ea(u, c, p):
    d, act, uid = tok_data(u, p[1]), p[2], u.effective_user.id
    cl = ec2c(u, d)
    await aws.run(ec2s.power, cl, d["i"], act)
    banner = ""
    if act == "start":
        for _ in range(5):
            await asyncio.sleep(2)
            inst, eips = await aws.run(ec2s.get_instance, cl, d["r"], d["i"])
            if inst["dns"]:
                if not eips:
                    banner = "⚡ <i>Server imewaka na kupata DNS mpya! Bonyeza <b>☁️ Sasisha CloudFront</b> hapo chini kuiunganisha haraka.</i>"
                break
    else:
        await asyncio.sleep(1.5)
    want = {"start": "running", "stop": "stopped", "terminate": "terminated"}.get(act)
    if want:
        store.add_watch(uid, "ec2", d["a"], f"{d['r']}|{d['i']}", {"want": want})
    if act in ("stop", "terminate"):
        mark_state(uid, d["a"], d["r"], d["i"], "stopping")
    aws.invalidate(("ec2_list", uid, d["a"]))
    aws.invalidate(("ec2_scan", uid, d["a"]))
    store.audit(uid, d["a"], f"EC2_{act.upper()}", f"{d['i']} ({d['r']})")
    if act == "terminate":
        return await show(u, c, f"🗑 Termination imeanzishwa kwa <code>{d['i']}</code>.",
                          [[btn("⬅️ EC2 List", f"er|{d['a']}|{d['r']}|1")], nav()])
    await inst_view(u, c, p[1], banner or f"✅ {act.upper()} imetumwa.")


@route("ek")
async def h_ek(u, c, p):
    d = tok_data(u, p[1])
    await show(u, c, f"🚨 <b>HATARI: Terminate EC2 <code>{d['i']}</code>?</b>\n\n"
               + card("Hatua hii itafuta server hii na disk yake <b>MOJA KWA MOJA</b>. Hutaweza kuirudisha!"),
               [[btn("🔥 Ndiyo, Endelea (1/2)", f"ek2|{p[1]}"), btn("❌ Hapana", f"ei|{p[1]}")]])

@route("ek2")
async def h_ek2(u, c, p):
    d = tok_data(u, p[1])
    await show(u, c, f"⚠️ <b>Uthibitisho wa Mwisho (2/2)</b>\nBonyeza hapa chini kufuta <code>{d['i']}</code> ({d['r']}) kabisa:",
               [[btn("💀 TERMINATE SASA", f"ea|{p[1]}|terminate"), btn("❌ Ghairi", f"ei|{p[1]}")]])


# ======================= metrics & snapshots (Create & Delete) =======================
@route("em", slow=True)
async def h_em(u, c, p):
    d = tok_data(u, p[1])
    m = await aws.run(ms.ec2_metrics, ec2c(u, d, "cloudwatch"), d["i"])
    tcard = tree_card([
        ("CPU_1h", f"{spark(m['cpu_vals'], 8)}  # {m['cpu']:.1f}%"),
        ("Net_Out", f"{m['net_out_gb']:.3f} GB  # 24h"),
        ("Net_In", f"{m['net_in_gb']:.3f} GB  # 24h"),
    ])
    text = (
        _crumb(d["a"], f"<code>{d['i']}</code>", "📊 <b>Metrics</b>")
        + card(f"🔥 <b>CPU Utilization:</b> <b>{m['cpu']:.1f}%</b>\n{progress_bar(m['cpu'], 100.0)}")
        + "\n\n" + tcard
    )
    await show(u, c, text, [[btn("🔄 Refresh", f"em|{p[1]}")], [btn("⬅️ Back", f"ei|{p[1]}")]])


@route("sn", slow=True)
async def h_sn(u, c, p):
    uid, d = u.effective_user.id, tok_data(u, p[1])
    cl = ec2c(u, d)
    i, _ = await aws.run(ec2s.get_instance, cl, d["r"], d["i"])
    vols = i["vols"]
    snaps = await aws.run(ec2s.list_instance_snapshots, cl, vols)
    lines = [
        f"• <code>{s['SnapshotId']}</code> · {s['State']} ({s['Progress']}) · {s['StartTime'].strftime('%d/%m %H:%M')}"
        for s in snaps
    ]
    rows = [[btn(f"📸 Create Snapshot ({v})", "snm|" + store.ref(uid, {**d, "v": v, "et": p[1]}))] for v in vols]
    for s in snaps[:5]:
        rows.append([btn(f"🗑 Futa {s['SnapshotId']} ({s['StartTime'].strftime('%d/%m')})",
                         "snr|" + store.ref(uid, {**d, "s": s["SnapshotId"], "et": p[1]}))])
    rows.append([btn("⬅️ Back", f"ei|{p[1]}")])
    body = card(f"<b>Volumes:</b> <code>{', '.join(vols) or 'Hakuna'}</code>\n" + ("\n".join(lines) if lines else "<i>Hakuna snapshots za hivi karibuni.</i>"))
    await show(u, c, _crumb(d["a"], f"<code>{d['i']}</code>", "📸 <b>EBS Snapshots</b>") + body, rows)


@route("snm", slow=True)
async def h_snm(u, c, p):
    d = tok_data(u, p[1])
    sid = await aws.run(ec2s.create_volume_snapshot, ec2c(u, d), d["v"], d["i"])
    store.audit(u.effective_user.id, d["a"], "EBS_SNAPSHOT", f"{sid} ({d['i']})")
    await h_sn(u, c, ["sn", d["et"]])


@route("snr")
async def h_snr(u, c, p):
    d = tok_data(u, p[1])
    await show(u, c, f"⚠️ <b>Futa EBS Snapshot <code>{d['s']}</code>?</b>\n" + card("Hatua hii haiwezi kurudishwa."),
               [[btn("🗑 Ndiyo, Futa", f"snrx|{p[1]}"), btn("❌ Hapana", f"sn|{d['et']}")]])


@route("snrx", slow=True)
async def h_snrx(u, c, p):
    d = tok_data(u, p[1])
    await aws.run(ec2s.delete_snapshot, ec2c(u, d), d["s"])
    store.audit(u.effective_user.id, d["a"], "EBS_SNAP_DEL", f"{d['s']} ({d['r']})")
    await h_sn(u, c, ["sn", d["et"]])


# ======================= launch from template =======================
@route("lt", slow=True)
async def h_lt(u, c, p):
    acc, region, uid = p[1], p[2], u.effective_user.id
    lts = await aws.run(ec2s.launch_templates, aws.client(uid, acc, "ec2", region))
    rows = [[btn(f"🚀 {t['LaunchTemplateName']}"[:55],
                 "lt2|" + store.ref(uid, {"a": acc, "r": region, "t": t["LaunchTemplateId"], "n": t["LaunchTemplateName"]}))]
            for t in lts[:10]]
    body = "Chagua Launch Template ya kuwasha server mpya:" if lts else card("<i>Hakuna Launch Templates kwenye region hii.</i>")
    await show(u, c, _crumb(acc, f"➕ <b>Launch ({region})</b>") + body, rows + [nav(f"er|{acc}|{region}")])


@route("lt2")
async def h_lt2(u, c, p):
    d = tok_data(u, p[1])
    c.user_data["flow"] = {"t": "lname", "tok": p[1]}
    await show(u, c, _crumb(d["a"], "➕ <b>Launch</b>") + f"Template: <b>{esc(d['n'])}</b>\nTuma <b>jina</b> la instance (tag Name):",
               [[btn("⏭ Skip jina", f"lt3|{p[1]}")], [btn("✖️ Ghairi", f"er|{d['a']}|{d['r']}")]])


async def _launch_confirm(u, c, d, name):
    tok = store.ref(u.effective_user.id, {**d, "nm": name})
    await show(u, c, _crumb(d["a"], "➕ <b>Launch</b>")
               + card(f"🧩 Template: <b>{esc(d['n'])}</b>\n📍 {d['r']}\n🏷 Name: <b>{esc(name) or '—'}</b>")
               + "\nWasha instance hii sasa?",
               [[btn("🚀 Launch", f"lt4|{tok}"), btn("❌ Hapana", f"er|{d['a']}|{d['r']}")]])


@route("lt3")
async def h_lt3(u, c, p): await _launch_confirm(u, c, tok_data(u, p[1]), "")

@flow("lname")
async def f_lname(u, c, f, txt): await _launch_confirm(u, c, tok_data(u, f["tok"]), txt[:100])

@route("lt4", slow=True)
async def h_lt4(u, c, p):
    d, uid = tok_data(u, p[1]), u.effective_user.id
    iid = await aws.run(ec2s.launch, ec2c(u, d), d["t"], d["nm"])
    add_monitored(uid, d["a"], d["r"])
    aws.invalidate(("ec2_list", uid, d["a"]))
    aws.invalidate(("ec2_scan", uid, d["a"]))
    store.add_watch(uid, "ec2", d["a"], f"{d['r']}|{iid}", {"want": "running"})
    store.audit(uid, d["a"], "EC2_LAUNCH", f"{iid} ({d['r']})")
    tok = itok(u, d["a"], d["r"], iid)
    await show(u, c, _crumb(d["a"], "➕ <b>Launch</b>") + f"✅ Imezinduliwa: <code>{iid}</code>\nBot itakutaarifu ikiwa <b>running</b>.",
               [[btn("🖥 Open", f"ei|{tok}")], nav(f"er|{d['a']}|{d['r']}")])
