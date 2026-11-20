"""EC2: regions/scan, instance list & detail, power control, metrics, snapshot, launch templates."""
import storage as store
from core import aws
from core.router import route, flow
from core.ui import show, btn, nav, head, card, esc, spark, ago
from handlers.common import (tok_data, itok, ec2c, mark_state, add_monitored, set_monitored,
                             region_buttons, back_accounts)
from services import ec2 as ec2s, metrics as ms


def _crumb(acc, *more): return head("🏠 Home", f"👤 {esc(acc)}", "🖥 EC2", *more)
def _ilabel(i): return f"{ec2s.icon(i['state'])} {i['name'] or i['id']} · {i['type']}"


# ======================= list / region / scan =======================
async def ec_list(u, c, acc, region):
    uid = u.effective_user.id
    region = region or store.get_setting(uid, f"region:{acc}", "us-east-1")
    store.set_setting(uid, f"region:{acc}", region)
    items = await aws.run(ec2s.list_instances, aws.client(uid, acc, "ec2", region), region)
    if items: add_monitored(uid, acc, region)
    rows = [[btn(_ilabel(i)[:60], f"ei|{itok(u, acc, region, i['id'])}")] for i in items[:20]]
    rows += [[btn("🌎 Region", f"rg|{acc}"), btn("🔎 Scan all", f"es|{acc}")],
             [btn("➕ Launch", f"lt|{acc}|{region}"), btn("🔄 Refresh", f"er|{acc}|{region}")],
             nav(back_accounts(u, "ec"))]
    body = f"📍 <b>{ec2s.rname(region)}</b> <code>{region}</code>\nInstances: <b>{len(items)}</b>" + ("" if items else "\n<i>Hakuna instance hapa. Jaribu 🔎 Scan all.</i>")
    await show(u, c, _crumb(acc) + body, rows)


@route("ec", slow=True)
async def h_ec(u, c, p): await ec_list(u, c, p[1], None)

@route("er", slow=True)
async def h_er(u, c, p): await ec_list(u, c, p[1], p[2])


@route("rg")
async def h_rg(u, c, p):
    acc = p[1]
    await show(u, c, _crumb(acc, "🌎 Region") + "Chagua region (⭐ = ina instances):",
               region_buttons(u.effective_user.id, acc, f"er|{acc}|{{r}}") + [nav(f"ec|{acc}")])


@route("es", slow=True)
async def h_es(u, c, p):
    acc, uid = p[1], u.effective_user.id
    regs = await aws.run(ec2s.enabled_regions, aws.client(uid, acc, "ec2", "us-east-1"))
    async def one(r):
        try: return r, await aws.run(ec2s.list_instances, aws.client(uid, acc, "ec2", r), r), None
        except Exception as e: return r, [], e
    res = await aws.gather_limited([one(r) for r in regs], 10)
    found = [(r, it) for r, it, e in res if it]
    errs = [r for r, it, e in res if e]
    set_monitored(uid, acc, [r for r, _ in found])
    lines = [f"{ec2s.rname(r)}: <b>{len(it)}</b> ({''.join(ec2s.icon(i['state']) for i in it)})" for r, it in found]
    body = card("\n".join(lines), expandable=len(lines) > 6) if lines else "Hakuna EC2 kwenye regions zote zilizowashwa."
    if errs: body += f"\n<i>⚠️ Regions zilizoshindwa: {', '.join(errs[:6])}</i>"
    rows = [[btn(f"{ec2s.rname(r)} ({len(it)})", f"er|{acc}|{r}")] for r, it in found]
    await show(u, c, _crumb(acc, "🔎 Scan") + f"Regions zilizochunguzwa: <b>{len(regs)}</b>\n" + body + "\n🔔 Regions hizi sasa zinafuatiliwa kwa arifa.",
               rows + [nav(f"ec|{acc}")])


# ======================= instance detail =======================
def render_inst(acc, i, eips, note=""):
    ips = ", ".join(a["PublicIp"] for a in eips) if eips else "—"
    lines = [f"🌐 <b>IP:</b> <code>{i['ip']}</code>" if i["ip"] else "🌐 <b>IP:</b> —",
             f"🔗 <b>DNS:</b> <code>{i['dns']}</code>" if i["dns"] else "🔗 <b>DNS:</b> —",
             f"🏠 <b>Private:</b> <code>{i['pip'] or '—'}</code>",
             f"📍 <b>AZ:</b> {i['az']} · ⏱ {ago(i['launch'])}",
             f"📌 <b>Elastic IP:</b> <code>{ips}</code>",
             "🛡 <b>SG:</b> " + esc(", ".join(f"{n} ({g})" for g, n in i["sgs"]) or "—")]
    return (_crumb(acc, f"<code>{i['id']}</code>") +
            f"{ec2s.icon(i['state'])} <b>{esc(i['name'] or i['id'])}</b> · <i>{i['state']}</i> · {i['type']}\n\n" +
            card("\n".join(lines)) + (f"\n{note}" if note else ""))


def inst_rows(tok, i):
    st, rows = i["state"], []
    if st == "stopped": rows.append([btn("▶️ Start", f"ea|{tok}|start")])
    elif st == "running": rows.append([btn("⏹ Stop", f"eq|{tok}|stop"), btn("🔄 Reboot", f"eq|{tok}|reboot")])
    if i["dns"]: rows.append([btn("☁️ Set as CF Origin", f"lk|{tok}")])
    rows += [[btn("🛡 Firewall", f"fw|{tok}"), btn("🌐 Elastic IP", f"ip|{tok}")],
             [btn("📊 Metrics", f"em|{tok}"), btn("📸 Snapshot", f"sn|{tok}")],
             [btn("💻 Commands", f"cm|{tok}"), btn("🔄 Refresh", f"ei|{tok}")]]
    if st not in ("shutting-down", "terminated"): rows.append([btn("🗑 Terminate", f"ek|{tok}")])
    rows.append(nav(None))
    return rows


async def inst_view(u, c, tok, note=""):
    d = tok_data(u, tok)
    i, eips = await aws.run(ec2s.get_instance, ec2c(u, d), d["r"], d["i"])
    rows = inst_rows(tok, i)
    rows[-1] = [btn("⬅️ Back", f"er|{d['a']}|{d['r']}"), btn("🏠 Menu", "home")]
    await show(u, c, render_inst(d["a"], i, eips, note), rows)


@route("ei", slow=True)
async def h_ei(u, c, p): await inst_view(u, c, p[1])


# ======================= power =======================
@route("eq")
async def h_eq(u, c, p):
    d = tok_data(u, p[1]); verb = {"stop": "⏹ Stop", "reboot": "🔄 Reboot"}[p[2]]
    await show(u, c, _crumb(d["a"], f"<code>{d['i']}</code>") + f"{verb} instance hii?",
               [[btn("✅ Ndiyo", f"ea|{p[1]}|{p[2]}"), btn("❌ Hapana", f"ei|{p[1]}")]])


@route("ea", slow=True)
async def h_ea(u, c, p):
    d, act, uid = tok_data(u, p[1]), p[2], u.effective_user.id
    await aws.run(ec2s.power, ec2c(u, d), d["i"], act)
    want = {"start": "running", "stop": "stopped", "terminate": "terminated"}.get(act)
    if want:
        store.add_watch(uid, "ec2", d["a"], f"{d['r']}|{d['i']}", {"want": want})
    if act in ("stop", "terminate"):
        mark_state(uid, d["a"], d["r"], d["i"], "stopping")         # our own action: no alert
    await inst_view(u, c, p[1], f"✅ {act} imetumwa. Nitakutaarifu hali ikibadilika.")


@route("ek")
async def h_ek(u, c, p):
    d = tok_data(u, p[1])
    await show(u, c, _crumb(d["a"], f"<code>{d['i']}</code>") + "🗑 <b>Terminate</b> inafuta instance <b>na data yake</b>. Endelea?",
               [[btn("⚠️ Endelea", f"ek2|{p[1]}"), btn("❌ Hapana", f"ei|{p[1]}")]])


@route("ek2")
async def h_ek2(u, c, p):
    d = tok_data(u, p[1])
    await show(u, c, _crumb(d["a"], f"<code>{d['i']}</code>") + "🚨 <b>Uthibitisho wa mwisho.</b> Hii HAIWEZI kurudishwa.",
               [[btn("🗑 TERMINATE", f"ea|{p[1]}|terminate")], [btn("❌ Ghairi", f"ei|{p[1]}")]])


# ======================= metrics / snapshot =======================
@route("em", slow=True)
async def h_em(u, c, p):
    d = tok_data(u, p[1])
    s = await aws.run(ms.ec2_series, ec2c(u, d, "cloudwatch"), d["i"], 1)
    cpu = s["cpu"]
    body = card(f"🔥 <b>CPU (saa 1)</b>\n<code>{spark(cpu) or '—'}</code>\n"
                f"avg <code>{(sum(cpu) / len(cpu)) if cpu else 0:.1f}%</code> · max <code>{max(cpu) if cpu else 0:.1f}%</code>\n\n"
                f"⬆️ NetworkOut: <code>{s['out'] / 1e6:,.1f} MB</code>\n⬇️ NetworkIn: <code>{s['in'] / 1e6:,.1f} MB</code>")
    await show(u, c, _crumb(d["a"], f"<code>{d['i']}</code>", "📊 Metrics") + body,
               [[btn("🔄 Refresh", f"em|{p[1]}")], nav(f"ei|{p[1]}")])


@route("sn", slow=True)
async def h_sn(u, c, p):
    d = tok_data(u, p[1])
    i, _ = await aws.run(ec2s.get_instance, ec2c(u, d), d["r"], d["i"])
    await show(u, c, _crumb(d["a"], f"<code>{d['i']}</code>", "📸 Snapshot") +
               f"Chukua snapshot ya volumes zote (<b>{len(i['vols'])}</b>)? Hulipiwa kwa GB.",
               [[btn("📸 Chukua", f"snx|{p[1]}"), btn("❌ Hapana", f"ei|{p[1]}")]])


@route("snx", slow=True)
async def h_snx(u, c, p):
    d = tok_data(u, p[1])
    ids = await aws.run(ec2s.snapshot, ec2c(u, d), d["i"])
    await inst_view(u, c, p[1], "📸 Snapshots: " + ", ".join(f"<code>{x}</code>" for x in ids))


# ======================= launch from template =======================
@route("lt", slow=True)
async def h_lt(u, c, p):
    acc, region, uid = p[1], p[2], u.effective_user.id
    lts = await aws.run(ec2s.launch_templates, aws.client(uid, acc, "ec2", region))
    rows = [[btn(f"🧩 {t['LaunchTemplateName']}"[:55], "lt2|" + store.ref(uid, {"a": acc, "r": region, "t": t["LaunchTemplateId"], "n": t["LaunchTemplateName"]}))]
            for t in lts[:10]]
    body = "Chagua Launch Template:" if lts else "Hakuna Launch Templates kwenye region hii. Tengeneza moja kwenye AWS Console kwanza."
    await show(u, c, _crumb(acc, f"➕ Launch · {region}") + body, rows + [nav(f"er|{acc}|{region}")])


@route("lt2")
async def h_lt2(u, c, p):
    d = tok_data(u, p[1])
    c.user_data["flow"] = {"t": "lname", "tok": p[1]}
    await show(u, c, _crumb(d["a"], "➕ Launch") + f"Template: <b>{esc(d['n'])}</b>\nTuma <b>jina</b> la instance (tag Name):",
               [[btn("⏭ Skip jina", f"lt3|{p[1]}")], [btn("✖️ Cancel", f"er|{d['a']}|{d['r']}")]])


async def _launch_confirm(u, c, d, name):
    tok = store.ref(u.effective_user.id, {**d, "nm": name})
    await show(u, c, _crumb(d["a"], "➕ Launch") + card(f"🧩 Template: <b>{esc(d['n'])}</b>\n📍 {d['r']}\n🏷 Name: <b>{esc(name) or '—'}</b>") +
               "\nZindua instance? Itaanza kulipiwa.",
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
    store.add_watch(uid, "ec2", d["a"], f"{d['r']}|{iid}", {"want": "running"})
    tok = itok(u, d["a"], d["r"], iid)
    await show(u, c, _crumb(d["a"], "➕ Launch") + f"✅ Imezinduliwa: <code>{iid}</code>\nNitakutaarifu ikiwa <b>running</b>.",
               [[btn("🖥 Open", f"ei|{tok}")], nav(f"er|{d['a']}|{d['r']}")])
