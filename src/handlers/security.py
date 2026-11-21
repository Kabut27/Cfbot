"""v5 Ultra: 1-Click Security Audit across all EC2 Security Groups & CloudFront Distributions."""
import asyncio
import storage as store
from core import aws
from core.router import route
from core.ui import show, btn, nav, head, card, tree_card, health_badge, stamp, esc
from handlers.common import tok_data, edit_dist, cf_list_cached, back_accounts
from services import cloudfront as cfs, ec2 as ec2s


@route("sec", slow=True)
async def h_sec(u, c, p):
    uid, acc = u.effective_user.id, p[1]
    try:
        regs = await aws.run(ec2s.enabled_regions, aws.client(uid, acc, "ec2", "us-east-1"))
    except Exception:
        regs = list(ec2s.REGIONS.keys())

    async def _one_reg(r):
        try:
            return await aws.run(ec2s.audit_region_sgs, aws.client(uid, acc, "ec2", r), r)
        except Exception:
            return []

    sg_chunks, ds = await asyncio.gather(
        aws.gather_limited([_one_reg(r) for r in regs], 10),
        cf_list_cached(uid, acc, force=True),
    )
    sg_findings = [f for ch in sg_chunks for f in ch]

    cf_findings = []
    for d in ds:
        if not d.get("Enabled"):
            continue
        did = d["Id"]
        dcb = d.get("DefaultCacheBehavior") or {}
        if dcb.get("ViewerProtocolPolicy") == "allow-all":
            cf_findings.append({
                "sev": "HIGH", "score_pen": 15, "did": did, "fix": "force_https",
                "msg": f"CF {did} ({cfs.dlabel(d)[:16]}) inaruhusu HTTP bila redirect-to-https",
            })
        if d.get("HttpVersion") == "http1.1":
            cf_findings.append({
                "sev": "MEDIUM", "score_pen": 5, "did": did, "fix": "http3",
                "msg": f"CF {did} bado inatumia HTTP/1.1 (boresha iwe HTTP/3)",
            })

    total_pen = sum(x["score_pen"] for x in sg_findings) + sum(x["score_pen"] for x in cf_findings)
    score = max(10, 100 - total_pen)
    crit_cnt = sum(1 for x in sg_findings + cf_findings if x["sev"] == "CRITICAL")
    high_cnt = sum(1 for x in sg_findings + cf_findings if x["sev"] == "HIGH")
    med_cnt = sum(1 for x in sg_findings + cf_findings if x["sev"] == "MEDIUM")

    tcard = tree_card([
        ("Account", f'"{acc}"'),
        ("Health", f'"{health_badge(score)}"'),
        ("Audit", f"{crit_cnt} Critical · {high_cnt} High · {med_cnt} Med"),
    ])

    sev_icon = {"CRITICAL": "🔴", "HIGH": "🟠", "MEDIUM": "🟡"}
    lines = []
    fix_rows = []
    all_items = sg_findings + cf_findings
    if not all_items:
        lines.append("🟢 <b>Hongera!</b> Hakuna hatari ya kiusalama iliyogundulika kwenye Security Groups wala CloudFront.")
    else:
        for item in sg_findings[:10]:
            lines.append(f"{sev_icon[item['sev']]} <b>{item['sev']}:</b> {esc(item['msg'])}")
            if item["fix"] == "lock_cf" and len(fix_rows) < 6:
                tok = store.ref(uid, {"a": acc, "r": item["reg"], "sg": item["sg"], "pt": item["port"]})
                fix_rows.append([btn(f"🔒 Lock {item['port']} → CF ({item['sg']})", f"sec_lck|{tok}")])
            elif item["fix"] == "close" and len(fix_rows) < 6:
                tok = store.ref(uid, {"a": acc, "r": item["reg"], "sg": item["sg"], "rule": item["rule"], "p": item["port"]})
                plbl = "ALL" if item["port"] is None else str(item["port"])
                fix_rows.append([btn(f"🗑 Close {plbl} ({item['sg']})", f"sec_cls|{tok}")])
        for item in cf_findings[:6]:
            lines.append(f"{sev_icon[item['sev']]} <b>{item['sev']}:</b> {esc(item['msg'])}")
            if item["fix"] == "force_https" and len(fix_rows) < 8:
                fix_rows.append([btn(f"🔐 Force HTTPS ({item['did']})", f"sec_https|{acc}|{item['did']}")])
            elif item["fix"] == "http3" and len(fix_rows) < 8:
                fix_rows.append([btn(f"🚀 Enable HTTP/3 ({item['did']})", f"sec_h3|{acc}|{item['did']}")])

    rows = fix_rows + [[btn("🔄 Scan Tena", f"sec|{acc}")], nav(back_accounts(u, "sec"))]
    text = (
        head("🏠 <b>Home</b>", f"👤 <b>{esc(acc)}</b>", "🛡 <b>Security Audit</b>")
        + tcard + "\n\n"
        + card("\n".join(lines), expandable=len(lines) > 6)
        + stamp()
    )
    await show(u, c, text, rows)


@route("sec_lck", slow=True)
async def h_sec_lck(u, c, p):
    d = tok_data(u, p[1])
    cl = aws.client(u.effective_user.id, d["a"], "ec2", d["r"])
    await aws.run(ec2s.lock_port_to_cf, cl, d["sg"], d["pt"])
    store.audit(u.effective_user.id, d["a"], "SEC_LOCK_CF", f"{d['sg']} tcp/{d['pt']}")
    await h_sec(u, c, ["sec", d["a"]])


@route("sec_cls")
async def h_sec_cls(u, c, p):
    d = tok_data(u, p[1])
    plbl = "ALL Traffic" if d["p"] is None else f"Port {d['p']}"
    await show(
        u, c,
        f"⚠️ <b>Funga {plbl} (0.0.0.0/0) kwenye <code>{d['sg']}</code> ({d['r']})?</b>\n"
        + card("Rule hii ya umma itafutwa mara moja kulinda server yako."),
        [[btn("🗑 Ndiyo, Funga", f"sec_clsx|{p[1]}"), btn("❌ Hapana", f"sec|{d['a']}")]],
    )


@route("sec_clsx", slow=True)
async def h_sec_clsx(u, c, p):
    d = tok_data(u, p[1])
    cl = aws.client(u.effective_user.id, d["a"], "ec2", d["r"])
    await aws.run(ec2s.revoke, cl, d["sg"], d["rule"])
    store.audit(u.effective_user.id, d["a"], "SEC_CLOSE_RULE", f"{d['sg']} {d['p'] or 'ALL'}")
    await h_sec(u, c, ["sec", d["a"]])


@route("sec_https", slow=True)
async def h_sec_https(u, c, p):
    acc, did = p[1], p[2]
    await edit_dist(u, acc, did, lambda cfg: cfs.set_viewer(cfg, "r"), audit_act="SEC_FORCE_HTTPS")
    await h_sec(u, c, ["sec", acc])


@route("sec_h3", slow=True)
async def h_sec_h3(u, c, p):
    acc, did = p[1], p[2]
    await edit_dist(u, acc, did, lambda cfg: cfg.__setitem__("HttpVersion", "http2and3"), audit_act="SEC_HTTP3")
    await h_sec(u, c, ["sec", acc])
