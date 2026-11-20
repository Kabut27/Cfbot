"""EC2 tools: firewall (security groups), Elastic IP, SSM commands."""
import asyncio, ipaddress
import storage as store
from core import aws
from core.router import route, flow
from core.ui import show, btn, nav, head, card, esc, grid
from handlers.common import tok_data, itok, ec2c, prompt, valid_port
from services import ec2 as ec2s


def _crumb(d, *more): return head("🏠 Home", f"👤 {esc(d['a'])}", "🖥 EC2", f"<code>{d['i']}</code>", *more)


# ======================= firewall =======================
async def _cfpl(u, d):
    async def f():
        try: return await aws.run(ec2s.cf_prefix_list, ec2c(u, d))
        except Exception: return None
    return await aws.cached(("cfpl", u.effective_user.id, d["a"], d["r"]), 3600, f)


async def sg_view(u, c, tokS, note=""):
    d = tok_data(u, tokS)
    (name, rules), cfpl = await asyncio.gather(aws.run(ec2s.sg_rules, ec2c(u, d), d["sg"]), _cfpl(u, d))
    lines = [f"{i + 1}. {esc(ec2s.fmt_rule(r, cfpl))}" for i, r in enumerate(rules[:25])]
    body = card(f"🛡 <b>{esc(name)}</b> <code>{d['sg']}</code>\nInbound rules: <b>{len(rules)}</b>\n" + ("\n".join(lines) or "—"),
                expandable=len(lines) > 6)
    tokE = itok(u, d["a"], d["r"], d["i"])
    await show(u, c, _crumb(d, "🛡 Firewall") + body + (f"\n{note}" if note else ""),
               [[btn("➕ Open port", f"fo|{tokS}"), btn("➖ Close rule", f"fc|{tokS}")],
                [btn("🔄 Refresh", f"fs|{tokS}")], nav(f"ei|{tokE}")])


@route("fw", slow=True)
async def h_fw(u, c, p):
    d, uid = tok_data(u, p[1]), u.effective_user.id
    i, _ = await aws.run(ec2s.get_instance, ec2c(u, d), d["r"], d["i"])
    toks = [(sg, n, store.ref(uid, {**d, "sg": sg})) for sg, n in i["sgs"]]
    if len(toks) == 1: return await sg_view(u, c, toks[0][2])
    await show(u, c, _crumb(d, "🛡 Firewall") + "Chagua security group:",
               [[btn(f"🛡 {n} ({sg})"[:60], f"fs|{t}")] for sg, n, t in toks] + [nav(f"ei|{p[1]}")])


@route("fs", slow=True)
async def h_fs(u, c, p): await sg_view(u, c, p[1])


@route("fo")
async def h_fo(u, c, p):
    d = tok_data(u, p[1])
    ports = [btn(str(x), f"fp|{p[1]}|{x}") for x in (22, 80, 443, 8080, 8443)] + [btn("✏️ Custom", f"fp|{p[1]}|x")]
    await show(u, c, _crumb(d, "➕ Open port") + "Chagua port (TCP):", grid(ports, 3) + [nav(f"fs|{p[1]}")])


async def _source_choice(u, c, tokS, port):
    d = tok_data(u, tokS)
    await show(u, c, _crumb(d, f"➕ Port {port}") + "Ruhusu kutoka wapi?\n" +
               card("☁️ <b>CloudFront only</b> = salama zaidi (prefix list inahesabiwa ~55 rules kwenye SG quota)\n🌍 <b>Anywhere</b> = wazi kwa kila mtu"),
               [[btn("☁️ CloudFront only", f"fq|{tokS}|{port}|c")], [btn("🌍 Anywhere", f"fq|{tokS}|{port}|a")],
                [btn("✏️ Custom IP/CIDR", f"fq|{tokS}|{port}|x")], nav(f"fs|{tokS}")])


@route("fp")
async def h_fp(u, c, p):
    if p[2] == "x":
        return await prompt(u, c, {"t": "fwport", "tok": p[1]}, "✏️ Tuma <b>port</b> (1-65535):", f"fs|{p[1]}")
    await _source_choice(u, c, p[1], int(p[2]))


@flow("fwport")
async def f_fwport(u, c, f, txt):
    port = valid_port(txt)
    if not port: return await prompt(u, c, f, "❌ Port si sahihi. Jaribu tena:", f"fs|{f['tok']}")
    await _source_choice(u, c, f["tok"], port)


async def _open(u, c, tokS, port, kind, src, label):
    d = tok_data(u, tokS)
    await aws.run(ec2s.authorize, ec2c(u, d), d["sg"], port, kind, src)
    await sg_view(u, c, tokS, f"✅ Port {port} imefunguliwa ({label}).")


@route("fq", slow=True)
async def h_fq(u, c, p):
    tokS, port, how = p[1], int(p[2]), p[3]
    d = tok_data(u, tokS)
    if how == "c":
        pl = await aws.run(ec2s.cf_prefix_list, ec2c(u, d))
        return await _open(u, c, tokS, port, "pl", pl, "CloudFront only")
    if how == "a":
        return await _open(u, c, tokS, port, "cidr", "0.0.0.0/0", "Anywhere")
    await prompt(u, c, {"t": "fwcidr", "tok": tokS, "port": port}, f"✏️ Tuma IP au CIDR ya port {port} (mfano <code>1.2.3.4</code> au <code>10.0.0.0/24</code>):", f"fs|{tokS}")


@flow("fwcidr")
async def f_fwcidr(u, c, f, txt):
    try: net = ipaddress.IPv4Network(txt, strict=False)
    except ValueError:
        return await prompt(u, c, f, "❌ IPv4/CIDR si sahihi. Jaribu tena:", f"fs|{f['tok']}")
    await _open(u, c, f["tok"], f["port"], "cidr", str(net), str(net))


@route("fc", slow=True)
async def h_fc(u, c, p):
    d, uid = tok_data(u, p[1]), u.effective_user.id
    cfpl = await _cfpl(u, d)
    _, rules = await aws.run(ec2s.sg_rules, ec2c(u, d), d["sg"])
    rules = [r for r in rules if r["kind"] != "sg"][:20]
    rows = [[btn(f"🔒 {ec2s.fmt_rule(r, cfpl)}"[:60], "fd|" + store.ref(uid, {**d, "rule": r}))] for r in rules]
    await show(u, c, _crumb(d, "➖ Close rule") + ("Chagua rule ya kufunga:" if rows else "Hakuna rules za kufunga."),
               rows + [nav(f"fs|{p[1]}")])


@route("fd")
async def h_fd(u, c, p):
    d = tok_data(u, p[1]); r = d["rule"]
    warn = "\n⚠️ <b>Hii ni SSH.</b> Ukiifunga unaweza kujifungia nje." if r.get("fp") == 22 else ""
    sg = store.ref(u.effective_user.id, {k: v for k, v in d.items() if k != "rule"})
    await show(u, c, _crumb(d, "➖ Close rule") + f"Futa rule: <b>{esc(ec2s.fmt_rule(r))}</b>?{warn}",
               [[btn("🗑 Futa", f"fr|{p[1]}"), btn("❌ Hapana", f"fs|{sg}")]])


@route("fr", slow=True)
async def h_fr(u, c, p):
    d = tok_data(u, p[1])
    await aws.run(ec2s.revoke, ec2c(u, d), d["sg"], d["rule"])
    sg = store.ref(u.effective_user.id, {k: v for k, v in d.items() if k != "rule"})
    await sg_view(u, c, sg, "✅ Rule imefutwa.")


# ======================= Elastic IP =======================
async def eip_view(u, c, tokE, note=""):
    d = tok_data(u, tokE)
    i, eips = await aws.run(ec2s.get_instance, ec2c(u, d), d["r"], d["i"])
    lines = "\n".join(f"📌 <code>{a['PublicIp']}</code>" for a in eips) or "Hakuna Elastic IP."
    rows = [[btn("🗑 Detach & release", f"ir|{tokE}")]] if eips else [[btn("➕ Allocate & attach", f"ia|{tokE}")]]
    if i["dns"]: rows.append([btn("☁️ Set as CF Origin", f"lk|{tokE}")])
    await show(u, c, _crumb(d, "🌐 Elastic IP") + card(lines + (f"\n🔗 <code>{i['dns']}</code>" if i["dns"] else "")) + (f"\n{note}" if note else ""),
               rows + [nav(f"ei|{tokE}")])


@route("ip", slow=True)
async def h_ip(u, c, p): await eip_view(u, c, p[1])


@route("ia")
async def h_ia(u, c, p):
    d = tok_data(u, p[1])
    await show(u, c, _crumb(d, "🌐 Elastic IP") + "Tengeneza Elastic IP na uiambatishe? DNS ya instance itabadilika kuwa <b>thabiti</b>.\n<i>Public IPv4 hulipiwa kwa saa.</i>",
               [[btn("✅ Ndiyo", f"iax|{p[1]}"), btn("❌ Hapana", f"ip|{p[1]}")]])


@route("iax", slow=True)
async def h_iax(u, c, p):
    d = tok_data(u, p[1])
    ip = await aws.run(ec2s.eip_attach, ec2c(u, d), d["i"])
    await eip_view(u, c, p[1], f"✅ <code>{ip}</code> imeambatishwa. Weka origin ya CloudFront upya (DNS imebadilika).")


@route("ir")
async def h_ir(u, c, p):
    d = tok_data(u, p[1])
    await show(u, c, _crumb(d, "🌐 Elastic IP") + "Ondoa na <b>ufute</b> Elastic IP? Public DNS itabadilika.",
               [[btn("🗑 Ndiyo", f"irx|{p[1]}"), btn("❌ Hapana", f"ip|{p[1]}")]])


@route("irx", slow=True)
async def h_irx(u, c, p):
    d = tok_data(u, p[1])
    cl = ec2c(u, d)
    _, eips = await aws.run(ec2s.get_instance, cl, d["r"], d["i"])
    for a in eips: await aws.run(ec2s.eip_release, cl, a)
    await eip_view(u, c, p[1], "✅ Elastic IP imeondolewa. Weka origin ya CloudFront upya.")


# ======================= SSM commands =======================
@route("cm", slow=True)
async def h_cm(u, c, p):
    d = tok_data(u, p[1])
    ok = await aws.run(ec2s.ssm_online, ec2c(u, d, "ssm"), d["i"])
    if not ok:
        return await show(u, c, _crumb(d, "💻 Commands") + card(
            "❌ SSM Agent haipo/haijaunganishwa.\nInahitaji IAM role <code>AmazonSSMManagedInstanceCore</code> kwenye instance + SSM Agent (Ubuntu/Amazon Linux ina kwa default)."), [nav(f"ei|{p[1]}")])
    btns = [btn(lbl, f"{'cy' if conf else 'cx'}|{p[1]}|{k}") for k, (lbl, _, conf) in ec2s.PRESETS.items()]
    await show(u, c, _crumb(d, "💻 Commands") + "✅ SSM online. Chagua amri (zilizoidhinishwa tu):", grid(btns, 2) + [nav(f"ei|{p[1]}")])


@route("cy")
async def h_cy(u, c, p):
    d = tok_data(u, p[1]); lbl = ec2s.PRESETS[p[2]][0]
    await show(u, c, _crumb(d, "💻 Commands") + f"Endesha <b>{esc(lbl)}</b>?",
               [[btn("✅ Endesha", f"cx|{p[1]}|{p[2]}"), btn("❌ Hapana", f"cm|{p[1]}")]])


@route("cx", slow=True)
async def h_cx(u, c, p):
    d = tok_data(u, p[1]); lbl, cmd, _ = ec2s.PRESETS[p[2]]
    status, out = await aws.run(ec2s.ssm_run, ec2c(u, d, "ssm"), d["i"], cmd)
    out = (out.strip()[-3000:]) or "(hakuna output)"
    await show(u, c, _crumb(d, "💻 " + lbl) + f"{'✅' if status == 'Success' else '❌'} <b>{esc(lbl)}</b> · {status}\n<pre>{esc(out)}</pre>",
               [[btn("🔁 Tena", f"cx|{p[1]}|{p[2]}")], [btn("⬅️ Commands", f"cm|{p[1]}"), btn("🏠 Menu", "home")]])
