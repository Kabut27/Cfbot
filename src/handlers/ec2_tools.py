"""EC2 tools (v4 Pro): Multi-SG Paginated Firewall + 1-Click Lock-to-CF + TCP/UDP + Quick Presets,
Elastic IP, and SSM Commands (Presets + Custom Bash)."""
import asyncio, ipaddress
import storage as store
from core import aws
from core.router import route, flow
from core.ui import show, btn, nav, head, card, esc, grid
from handlers.common import tok_data, itok, ec2c, prompt, valid_port
from services import ec2 as ec2s


def _crumb(d, *more): return head("🏠 <b>Home</b>", f"🖥 <code>{d['i']}</code>", *more)


# ======================= firewall (Multi-SG + Paginated + Lock-to-CF + Presets) =======================
async def _cfpl(u, d):
    async def f():
        try: return await aws.run(ec2s.cf_prefix_list, ec2c(u, d))
        except Exception: return None
    return await aws.cached(("cfpl", u.effective_user.id, d["a"], d["r"]), 3600, f)


async def sg_view(u, c, tokE, sgi=0, pg=0, note=""):
    uid, d = u.effective_user.id, tok_data(u, tokE)
    cl = ec2c(u, d)
    (inst, _), cfpl = await asyncio.gather(aws.run(ec2s.get_instance, cl, d["r"], d["i"]), _cfpl(u, d))
    sgs = inst["sgs"]
    if not sgs:
        raise ValueError("EC2 hii haina Security Group iliyounganishwa.")
    if sgi >= len(sgs): sgi = 0
    sgid, sgname = sgs[sgi]
    _, rules = await aws.run(ec2s.sg_rules, cl, sgid)

    lines = []
    rule_btns = []
    for r in rules:
        lines.append(f"• {esc(ec2s.fmt_rule(r, cfpl))}")
        if r["kind"] == "sg":
            continue
        rm_tok = store.ref(uid, {**d, "sg": sgid, "sgi": sgi, "pg": pg, "et": tokE, "rule": r})
        p_lbl = "ALL" if r["proto"] == "-1" else (f"{r['fp']}" if r["fp"] == r["tp"] else f"{r['fp']}-{r['tp']}")
        row_b = [btn(f"🗑 {r['proto'].upper()} {p_lbl} ({r['src']})"[:34], f"fd|{rm_tok}")]
        # 1-Click Lock to CloudFront for Public TCP non-SSH ports
        if r["proto"] == "tcp" and r["kind"] == "cidr" and r["src"] == "0.0.0.0/0" and r["fp"] is not None and r["fp"] == r["tp"] and int(r["fp"]) != 22:
            lk_tok = store.ref(uid, {**d, "sg": sgid, "sgi": sgi, "pg": pg, "et": tokE, "pt": int(r["fp"])})
            row_b.append(btn(f"🔒 Lock {r['fp']} → CF", f"sg_lck|{lk_tok}"))
        rule_btns.append(row_b)

    per = 5
    max_pg = max(0, (len(rule_btns) - 1) // per)
    if pg > max_pg: pg = max_pg
    page_btns = rule_btns[pg * per:(pg + 1) * per]

    sg_ctx = store.ref(uid, {**d, "sg": sgid, "sgi": sgi, "pg": pg, "et": tokE})
    rows = []
    if len(sgs) > 1:
        nxt = (sgi + 1) % len(sgs)
        rows.append([btn(f"🔀 Badili SG ({sgi + 1}/{len(sgs)})", f"fs|{tokE}|{nxt}|0")])
    rows += [
        [btn("🔓 TCP (Public)", f"fadd|{sg_ctx}|tcp|pub"), btn("☁️ TCP (CF Only)", f"fadd|{sg_ctx}|tcp|cf")],
        [btn("🔓 UDP (Public)", f"fadd|{sg_ctx}|udp|pub"), btn("⚡ Quick Presets", f"fpre|{sg_ctx}")],
        [btn("✏️ Custom IP/CIDR", f"fcid|{sg_ctx}")],
    ] + page_btns
    pager = ([btn("◀️ Rules", f"fs|{tokE}|{sgi}|{pg - 1}")] if pg > 0 else []) +             ([btn("Rules ▶️", f"fs|{tokE}|{sgi}|{pg + 1}")] if pg < max_pg else [])
    if pager: rows.append(pager)
    rows.append([btn("⬅️ Back", f"ei|{tokE}")])

    body = card(
        f"<b>Inbound Rules ({esc(sgname)} · {len(rules)} rules):</b>\n"
        + ("\n".join(lines) if lines else "<i>Hakuna inbound rules.</i>")
    )
    await show(u, c, _crumb(d, f"🛡 <b>SG #{sgi + 1}/{len(sgs)} ({sgid})</b>") + body + (f"\n{note}" if note else ""), rows)


@route("fw", slow=True)
async def h_fw(u, c, p): await sg_view(u, c, p[1], 0, 0)

@route("fs", slow=True)
async def h_fs(u, c, p): await sg_view(u, c, p[1], int(p[2]), int(p[3]))


@route("sg_lck", slow=True)
async def h_sg_lck(u, c, p):
    d = tok_data(u, p[1])
    await aws.run(ec2s.lock_port_to_cf, ec2c(u, d), d["sg"], d["pt"])
    store.audit(u.effective_user.id, d["a"], "SG_LOCK_CF", f"{d['sg']} tcp/{d['pt']}")
    await sg_view(u, c, d["et"], d["sgi"], d["pg"], f"✅ Port <code>{d['pt']}</code> imefungwa kwa CloudFront Only (zero downtime)!")


@route("fadd")
async def h_fadd(u, c, p):
    d = tok_data(u, p[1])
    pr, src = p[2], p[3]
    lbl = "CloudFront Only (Prefix List)" if src == "cf" else "Public (0.0.0.0/0)"
    await prompt(u, c, {"t": "sg_add", "d": d, "pr": pr, "src": src},
                 f"🔓 Fungua <b>{pr.upper()}</b> Port ({lbl}).\nTuma namba ya port (mfano <code>80</code>, <code>443</code>, <code>8080</code>):",
                 f"fs|{d['et']}|{d['sgi']}|{d['pg']}")


@flow("sg_add")
async def f_sg_add(u, c, f, txt):
    d = f["d"]
    port = valid_port(txt)
    if not port:
        return await prompt(u, c, f, "❌ Tuma namba ya port sahihi (1–65535):", f"fs|{d['et']}|{d['sgi']}|{d['pg']}")
    cl = ec2c(u, d)
    if f["src"] == "cf":
        pl = await aws.run(ec2s.cf_prefix_list, cl)
        await aws.run(ec2s.authorize, cl, d["sg"], port, "pl", pl, proto=f["pr"])
    else:
        await aws.run(ec2s.authorize, cl, d["sg"], port, "cidr", "0.0.0.0/0", proto=f["pr"])
    store.audit(u.effective_user.id, d["a"], "SG_OPEN", f"{d['sg']} {f['pr']}/{port} ({f['src']})")
    await sg_view(u, c, d["et"], d["sgi"], d["pg"], f"✅ Port {f['pr'].upper()}/{port} imefunguliwa.")


@route("fcid")
async def h_fcid(u, c, p):
    d = tok_data(u, p[1])
    await prompt(u, c, {"t": "sg_cid1", "d": d},
                 "✏️ Tuma <b>TCP Port</b> (1–65535) ya kufungua kwa IP maalum:", f"fs|{d['et']}|{d['sgi']}|{d['pg']}")

@flow("sg_cid1")
async def f_sg_cid1(u, c, f, txt):
    d = f["d"]
    port = valid_port(txt)
    if not port:
        return await prompt(u, c, f, "❌ Port si sahihi:", f"fs|{d['et']}|{d['sgi']}|{d['pg']}")
    await prompt(u, c, {"t": "sg_cid2", "d": d, "port": port},
                 f"✏️ Tuma IP au CIDR ya port {port} (mfano <code>1.2.3.4</code> au <code>10.0.0.0/24</code>):",
                 f"fs|{d['et']}|{d['sgi']}|{d['pg']}")

@flow("sg_cid2")
async def f_sg_cid2(u, c, f, txt):
    d = f["d"]
    try: net = ipaddress.IPv4Network(txt, strict=False)
    except ValueError:
        return await prompt(u, c, f, "❌ IPv4/CIDR si sahihi. Jaribu tena:", f"fs|{d['et']}|{d['sgi']}|{d['pg']}")
    await aws.run(ec2s.authorize, ec2c(u, d), d["sg"], f["port"], "cidr", str(net), proto="tcp")
    store.audit(u.effective_user.id, d["a"], "SG_OPEN", f"{d['sg']} tcp/{f['port']} ({net})")
    await sg_view(u, c, d["et"], d["sgi"], d["pg"], f"✅ Port {f['port']} imefunguliwa kwa <code>{net}</code>.")


@route("fpre")
async def h_fpre(u, c, p):
    d = tok_data(u, p[1])
    btns = [btn(lbl, f"fprex|{p[1]}|{k}") for k, (lbl, _, _, _) in ec2s.SG_QUICK_PRESETS.items()]
    await show(u, c, f"⚡ <b>Quick Firewall Presets ({d['sg']})</b>\n{'━' * 20}\n"
               + card("Bonyeza mara moja kufungua port husika:"),
               grid(btns, 2) + [[btn("⬅️ Back to Firewall", f"fs|{d['et']}|{d['sgi']}|{d['pg']}")]])


@route("fprex", slow=True)
async def h_fprex(u, c, p):
    d = tok_data(u, p[1])
    _, proto, port, src = ec2s.SG_QUICK_PRESETS[p[2]]
    cl = ec2c(u, d)
    if src == "cf":
        pl = await aws.run(ec2s.cf_prefix_list, cl)
        await aws.run(ec2s.authorize, cl, d["sg"], port, "pl", pl, proto=proto)
    else:
        await aws.run(ec2s.authorize, cl, d["sg"], port, "cidr", "0.0.0.0/0", proto=proto)
    store.audit(u.effective_user.id, d["a"], "SG_OPEN", f"{d['sg']} {proto}/{port} ({src})")
    await sg_view(u, c, d["et"], d["sgi"], d["pg"], f"✅ Port {proto.upper()}/{port} ({src.upper()}) imefunguliwa.")


@route("fd")
async def h_fd(u, c, p):
    d = tok_data(u, p[1]); r = d["rule"]
    warn = "\n⚠️ <b>Hii ni SSH (22).</b> Ukiifunga unaweza kujifungia nje." if r.get("fp") == 22 else ""
    await show(u, c, _crumb(d, "🗑 <b>Close Rule</b>") + f"Futa rule: <b>{esc(ec2s.fmt_rule(r))}</b>?{warn}",
               [[btn("🗑 Ndiyo, Futa", f"fr|{p[1]}"), btn("❌ Hapana", f"fs|{d['et']}|{d['sgi']}|{d['pg']}")]])


@route("fr", slow=True)
async def h_fr(u, c, p):
    d = tok_data(u, p[1])
    await aws.run(ec2s.revoke, ec2c(u, d), d["sg"], d["rule"])
    store.audit(u.effective_user.id, d["a"], "SG_CLOSE", f"{d['sg']} {d['rule']['proto']}/{d['rule'].get('fp', 'all')}")
    await sg_view(u, c, d["et"], d["sgi"], d["pg"], "✅ Rule imefutwa.")


# ======================= Elastic IP =======================
async def eip_view(u, c, tokE, note=""):
    d = tok_data(u, tokE)
    i, eips = await aws.run(ec2s.get_instance, ec2c(u, d), d["r"], d["i"])
    if eips:
        e = eips[0]
        info = f"🟢 <b>Attached EIP:</b> <code>{e['PublicIp']}</code>\nAllocation: <code>{e['AllocationId']}</code>"
        rows = [[btn("🔌 Disassociate & Release EIP", f"ir|{tokE}")]]
    else:
        info = "⚪ Hakuna Elastic IP iliyounganishwa na EC2 hii.\n<i>Kumbuka: AWS hutoza $0.005/saa kwa Public IPv4.</i>"
        rows = [[btn("➕ Allocate & Attach Elastic IP", f"ia|{tokE}")]]
    if i["dns"]:
        rows.append([btn("☁️ Set as CF Origin", f"lk|{tokE}")])
    rows.append([btn("⬅️ Back", f"ei|{tokE}")])
    await show(u, c, f"🌐 <b>Elastic IP · <code>{d['i']}</code></b>\n{'━' * 20}\n" + card(info) + (f"\n{note}" if note else ""), rows)


@route("ip", slow=True)
async def h_ip(u, c, p): await eip_view(u, c, p[1])

@route("ia")
async def h_ia(u, c, p):
    d = tok_data(u, p[1])
    await show(u, c, _crumb(d, "🌐 <b>Elastic IP</b>") + card("Tengeneza Elastic IP na uiambatishe? DNS ya instance itabadilika kuwa thabiti."),
               [[btn("✅ Ndiyo", f"iax|{p[1]}"), btn("❌ Hapana", f"ip|{p[1]}")]])

@route("iax", slow=True)
async def h_iax(u, c, p):
    d = tok_data(u, p[1])
    ip = await aws.run(ec2s.eip_attach, ec2c(u, d), d["i"])
    store.audit(u.effective_user.id, d["a"], "EIP_ATTACH", f"{ip} -> {d['i']}")
    await eip_view(u, c, p[1], f"✅ <code>{ip}</code> imeambatishwa.")

@route("ir")
async def h_ir(u, c, p):
    d = tok_data(u, p[1])
    await show(u, c, _crumb(d, "🌐 <b>Elastic IP</b>") + card("Ondoa na <b>ufute</b> Elastic IP? Public DNS itabadilika."),
               [[btn("🗑 Ndiyo", f"irx|{p[1]}"), btn("❌ Hapana", f"ip|{p[1]}")]])

@route("irx", slow=True)
async def h_irx(u, c, p):
    d = tok_data(u, p[1])
    cl = ec2c(u, d)
    _, eips = await aws.run(ec2s.get_instance, cl, d["r"], d["i"])
    for a in eips:
        await aws.run(ec2s.eip_release, cl, a)
        store.audit(u.effective_user.id, d["a"], "EIP_RELEASE", a["AllocationId"])
    await eip_view(u, c, p[1], "✅ Elastic IP imeondolewa.")


# ======================= SSM commands (Presets + Custom Bash) =======================
@route("cm", slow=True)
async def h_cm(u, c, p):
    d = tok_data(u, p[1])
    try:
        online = await aws.run(ec2s.ssm_online, ec2c(u, d, "ssm"), d["i"])
    except Exception:
        online = None
    btns = [btn(lbl, f"{'cy' if conf else 'cx'}|{p[1]}|{k}") for k, (lbl, _, conf) in ec2s.PRESETS.items()]
    rows = grid(btns, 2) + [
        [btn("⌨️ Custom Bash Command", f"cm_cus|{p[1]}")],
        [btn("⬅️ Back", f"ei|{p[1]}")],
    ]
    st_txt = "✅ SSM online." if online else "ℹ️ <i>Inahitaji SSM Agent &amp; IAM Role ya SSM kwenye EC2.</i>"
    await show(u, c, f"💻 <b>Run Command (AWS SSM) · <code>{d['i']}</code></b>\n{'━' * 20}\n" + card(st_txt), rows)


@route("cy")
async def h_cy(u, c, p):
    d = tok_data(u, p[1]); lbl = ec2s.PRESETS[p[2]][0]
    await show(u, c, _crumb(d, "💻 <b>SSM</b>") + f"Endesha <b>{esc(lbl)}</b>?",
               [[btn("✅ Endesha", f"cx|{p[1]}|{p[2]}"), btn("❌ Hapana", f"cm|{p[1]}")]])


async def _exec_ssm(u, c, tokE, d, lbl, cmd_str):
    status, out = await aws.run(ec2s.ssm_run, ec2c(u, d, "ssm"), d["i"], cmd_str)
    store.audit(u.effective_user.id, d["a"], "EC2_SSM", f"{d['i']}: {cmd_str[:40]}")
    body = (out or "(Hakuna output)").strip()[:3000]
    dot = "✅" if status == "Success" else "⚠️"
    await show(
        u, c,
        f"{dot} <b>SSM Result ({status})</b>\n<code>$ {esc(cmd_str)}</code>\n\n"
        f'<pre><code class="language-bash">{esc(body)}</code></pre>',
        [[btn("⬅️ Run Command", f"cm|{tokE}"), btn("🏠 Menu Kuu", "home")]],
    )


@route("cx", slow=True)
async def h_cx(u, c, p):
    d = tok_data(u, p[1]); lbl, cmd_str, _ = ec2s.PRESETS[p[2]]
    await _exec_ssm(u, c, p[1], d, lbl, cmd_str)


@route("cm_cus")
async def h_cm_cus(u, c, p):
    await prompt(u, c, {"t": "ssm_cus", "tok": p[1]},
                 "⌨️ Tuma <b>Bash command</b> ya kuendesha kwenye EC2:", f"cm|{p[1]}")


@flow("ssm_cus")
async def f_ssm_cus(u, c, f, txt):
    d = tok_data(u, f["tok"])
    await _exec_ssm(u, c, f["tok"], d, "Custom", txt)



# ======================= EC2 Auto-Schedule (Sleep/Wake Timer EAT) =======================
async def _sch_view(u, c, tokE, note=""):
    uid, d = u.effective_user.id, tok_data(u, tokE)
    sch = store.get_schedule(uid, d["a"], d["r"], d["i"])
    if sch:
        on = sch["enabled"]
        stop_s = f"{sch['stop_hr']:02d}:00 EAT" if sch["stop_hr"] >= 0 else "Haijawekwa"
        start_s = f"{sch['start_hr']:02d}:00 EAT" if sch["start_hr"] >= 0 else "Haijawekwa"
        info = (
            f"⚡ <b>Hali:</b> {'🟢 ON' if on else '🔴 OFF'}\n"
            f"🌙 <b>Auto-Stop:</b> <code>{stop_s}</code>\n"
            f"🌅 <b>Auto-Start:</b> <code>{start_s}</code>"
        )
        rows = [
            [btn("🌙 Weka Saa ya Stop", f"sch_st|{tokE}"), btn("🌅 Weka Saa ya Start", f"sch_up|{tokE}")],
            [btn("⏸ Zima Timer" if on else "▶️ Washa Timer", f"sch_tg|{tokE}"), btn("🗑 Futa Timer", f"sch_dl|{tokE}")],
            [btn("⬅️ Back", f"ei|{tokE}")],
        ]
    else:
        info = (
            "⚪ Hakuna Auto-Schedule iliyowekwa kwa server hii.\n"
            "<i>Okoa saa za Free Tier (750h/mwezi) kwa kuzima EC2 usiku na kuiwasha asubuhi kiotomatiki (EAT).</i>"
        )
        rows = [
            [btn("🌙 Weka Saa ya Stop", f"sch_st|{tokE}"), btn("🌅 Weka Saa ya Start", f"sch_up|{tokE}")],
            [btn("⬅️ Back", f"ei|{tokE}")],
        ]
    await show(
        u, c,
        _crumb(d, "⏰ <b>Auto-Schedule (EAT)</b>") + card(info) + (f"\n{note}" if note else ""),
        rows,
    )


@route("sch")
async def h_sch(u, c, p):
    await _sch_view(u, c, p[1])


@route("sch_tg")
async def h_sch_tg(u, c, p):
    uid, d = u.effective_user.id, tok_data(u, p[1])
    sch = store.get_schedule(uid, d["a"], d["r"], d["i"])
    if sch:
        store.set_schedule(uid, d["a"], d["r"], d["i"], sch["stop_hr"], sch["start_hr"], not sch["enabled"])
        store.audit(uid, d["a"], "EC2_SCHED_TOGGLE", f"{d['i']} -> {'ON' if not sch['enabled'] else 'OFF'}")
    await _sch_view(u, c, p[1], "✅ Hali ya timer imebadilishwa.")


@route("sch_dl")
async def h_sch_dl(u, c, p):
    uid, d = u.effective_user.id, tok_data(u, p[1])
    store.del_schedule(uid, d["a"], d["r"], d["i"])
    store.audit(uid, d["a"], "EC2_SCHED_DEL", d["i"])
    await _sch_view(u, c, p[1], "✅ Timer imefutwa.")


@route("sch_st")
async def h_sch_st(u, c, p):
    await prompt(
        u, c, {"t": "sch_hr", "tok": p[1], "kind": "stop"},
        "🌙 Tuma saa ya <b>Auto-Stop</b> (0–23 EAT, mfano <code>23</code> kwa saa 5 usiku, au <code>-1</code> kuzima):",
        f"sch|{p[1]}",
    )


@route("sch_up")
async def h_sch_up(u, c, p):
    await prompt(
        u, c, {"t": "sch_hr", "tok": p[1], "kind": "start"},
        "🌅 Tuma saa ya <b>Auto-Start</b> (0–23 EAT, mfano <code>7</code> kwa saa 1 asubuhi, au <code>-1</code> kuzima):",
        f"sch|{p[1]}",
    )


@flow("sch_hr")
async def f_sch_hr(u, c, f, txt):
    try:
        hr = int(txt)
        if not (-1 <= hr <= 23):
            raise ValueError
    except ValueError:
        return await prompt(u, c, f, "❌ Weka namba kati ya 0 na 23 (au <code>-1</code> kuondoa):", f"sch|{f['tok']}")
    uid, d = u.effective_user.id, tok_data(u, f["tok"])
    cur = store.get_schedule(uid, d["a"], d["r"], d["i"]) or {"stop_hr": -1, "start_hr": -1, "enabled": True}
    stop_hr = hr if f["kind"] == "stop" else cur["stop_hr"]
    start_hr = hr if f["kind"] == "start" else cur["start_hr"]
    store.set_schedule(uid, d["a"], d["r"], d["i"], stop_hr, start_hr, True)
    store.audit(uid, d["a"], "EC2_SCHED_SET", f"{d['i']} stop={stop_hr} start={start_hr}")
    await _sch_view(u, c, f["tok"], "✅ Ratiba imehifadhiwa.")
