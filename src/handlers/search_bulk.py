"""v5 Ultra: Quick Search (/find <keyword>), Safe Bulk Actions, and CSV Inventory Export."""
import asyncio, csv, io, time, datetime as dt
import config
import storage as store
from core import aws
from core.router import route, flow
from core.ui import show, btn, nav, head, card, stamp, esc, set_theme
from handlers.common import prompt, itok, cf_list_cached, scan_ec2_all_regions, back_accounts, mark_state
from services import cloudfront as cfs, ec2 as ec2s


# ======================= Quick Search (/find & Button) =======================
async def _run_search(u, c, kw):
    uid = u.effective_user.id
    q = kw.strip().lower()
    if not q:
        return await prompt(u, c, {"t": "qfind"},
                            "🔍 <b>Quick Search</b>\n\nTuma neno la kutafuta (ID, Domain, Label, Jina la EC2, au IP):", "home")
    accs = store.accounts(uid)
    if not accs:
        return await show(u, c, "⚠️ Hakuna AWS account iliyounganishwa.", [[btn("➕ Add Account", "aa")], nav()])

    async def _search_acc(acc):
        cf_hits, ec2_hits = [], []
        try:
            ds = await cf_list_cached(uid, acc)
            for d in ds:
                origins = [o.get("DomainName", "") for o in (d.get("Origins", {}).get("Items") or [])]
                aliases = d.get("Aliases", {}).get("Items") or []
                hay = " ".join([d["Id"], d.get("DomainName", ""), d.get("Comment", "")] + aliases + origins).lower()
                if q in hay:
                    cf_hits.append((acc, d))
        except Exception:
            pass
        try:
            scan = await scan_ec2_all_regions(uid, acc)
            for reg, insts, _ in scan:
                for i in insts:
                    hay = " ".join([i["id"], i["name"], i["ip"] or "", i["pip"] or "", i["dns"] or "", reg, i["type"]]).lower()
                    if q in hay:
                        ec2_hits.append((acc, reg, i))
        except Exception:
            pass
        return cf_hits, ec2_hits

    chunks = await asyncio.gather(*(_search_acc(a) for a in accs))
    cf_all = [x for ch in chunks for x in ch[0]]
    ec2_all = [x for ch in chunks for x in ch[1]]

    rows = []
    for acc, d in cf_all[:8]:
        dot = "🟢" if d["Enabled"] else "🔴"
        rows.append([btn(f"☁️ {dot} [{acc}] {d['Id']} · {cfs.dlabel(d)}"[:56], f"d|{acc}|{d['Id']}")])
    for acc, reg, i in ec2_all[:8]:
        rows.append([btn(f"🖥 {ec2s.icon(i['state'])} [{acc}] {i['name'] or i['id']} · {i['ip'] or reg}"[:56],
                         f"ei|{itok(u, acc, reg, i['id'])}")])
    rows += [[btn("🔍 Tafuta Nyingine", "find")], nav()]

    total = len(cf_all) + len(ec2_all)
    body = card(
        f"🔎 Neno: <code>{esc(kw)}</code>\n"
        f"☁️ CloudFront: <b>{len(cf_all)}</b> · 🖥 EC2: <b>{len(ec2_all)}</b>\n"
        + ("Bonyeza matokeo kufungua moja kwa moja:" if total else "<i>Hakuna kilichopatikanwa kwenye accounts zako zote.</i>")
    )
    await show(u, c, head("🏠 <b>Home</b>", "🔍 <b>Quick Search</b>") + body + stamp(), rows)


async def cmd_find(u, c):
    uid = u.effective_user.id
    if not store.is_admin(uid):
        return
    set_theme(store.get_setting(uid, "ui_theme", "classic"))
    kw = " ".join(c.args).strip() if c.args else ""
    c.user_data.pop("flow", None)
    if not kw:
        return await prompt(u, c, {"t": "qfind"},
                            "🔍 <b>Quick Search</b>\n\nTuma neno la kutafuta (mfano <code>nginx</code>, <code>13.24</code>, <code>E1A2B</code>):", "home")
    await _run_search(u, c, kw)


@route("find")
async def h_find(u, c, p):
    await prompt(u, c, {"t": "qfind"},
                 "🔍 <b>Quick Search</b>\n" + card("Tuma jina, ID, domain au IP kutafuta kwenye CloudFront &amp; EC2 za accounts zote:"),
                 "home")


@flow("qfind")
async def f_qfind(u, c, f, txt):
    await _run_search(u, c, txt)


# ======================= Safe Bulk Actions =======================
@route("bulk")
async def h_bulk(u, c, p):
    acc = p[1]
    rows = [
        [btn("🧹 Purge All Active CF (/*)", f"blk_pg|{acc}")],
        [btn("⏹ Stop All Running EC2", f"blk_st1|{acc}")],
        [btn("▶️ Start All Stopped EC2", f"blk_up|{acc}")],
        [btn("📥 Export Inventory (CSV)", "exp_csv")],
        nav(back_accounts(u, "bulk")),
    ]
    await show(
        u, c,
        head("🏠 <b>Home</b>", f"👤 <b>{esc(acc)}</b>", "⚡ <b>Bulk Actions</b>")
        + card("Tekeleza vitendo vya pamoja kwa usalama kwenye account hii.\n<i>Kila kitendo kinahitaji uthibitisho kabla hakijafanyika.</i>"),
        rows,
    )


@route("blk_pg")
async def h_blk_pg(u, c, p):
    acc = p[1]
    await show(
        u, c,
        f"🧹 <b>Purge ALL Active CloudFront (<code>/*</code>) kwenye {esc(acc)}?</b>\n"
        + card("Itatuma invalidation <code>/*</code> kwa kila distribution iliyo <b>Enabled</b>."),
        [[btn("✅ Ndiyo, Purge Zote", f"blk_pgok|{acc}"), btn("❌ Hapana", f"bulk|{acc}")]],
    )


@route("blk_pgok", slow=True)
async def h_blk_pgok(u, c, p):
    uid, acc = u.effective_user.id, p[1]
    cf = aws.client(uid, acc)
    ds = [d for d in await cf_list_cached(uid, acc, force=True) if d["Enabled"]]
    async def _one(did):
        try:
            await aws.run(
                cf.create_invalidation,
                DistributionId=did,
                InvalidationBatch={"Paths": {"Quantity": 1, "Items": ["/*"]}, "CallerReference": f"blk-{time.time()}-{did}"},
            )
            return True
        except Exception:
            return False
    res = await aws.gather_limited([_one(d["Id"]) for d in ds], 6)
    ok_n = sum(1 for x in res if x)
    store.audit(uid, acc, "BULK_PURGE", f"{ok_n}/{len(ds)} CF")
    await show(u, c, f"✅ <b>Bulk Purge Imekamilika!</b>\n" + card(f"Distributions zilizofanyiwa purge: <b>{ok_n} / {len(ds)}</b>"),
               [[btn("⬅️ Bulk Actions", f"bulk|{acc}")], nav()])


@route("blk_st1")
async def h_blk_st1(u, c, p):
    acc = p[1]
    await show(
        u, c,
        f"⚠️ <b>HATUA 1/2: Zima (STOP) EC2 ZOTE zinazowaka kwenye {esc(acc)}?</b>\n"
        + card("Server zote zilizo <b>running</b> kwenye regions zote za account hii zitazimwa."),
        [[btn("🟠 Ndiyo, Endelea (1/2)", f"blk_st2|{acc}"), btn("❌ Hapana", f"bulk|{acc}")]],
    )


@route("blk_st2")
async def h_blk_st2(u, c, p):
    acc = p[1]
    await show(
        u, c,
        f"🚨 <b>UTHIBITISHO WA MWISHO (2/2)</b>\nBonyeza chini kuzima EC2 zote zinazowaka za <b>{esc(acc)}</b>:",
        [[btn("⏹ STOP ALL RUNNING EC2", f"blk_stok|{acc}"), btn("❌ Ghairi", f"bulk|{acc}")]],
    )


@route("blk_stok", slow=True)
async def h_blk_stok(u, c, p):
    uid, acc = u.effective_user.id, p[1]
    scan = await scan_ec2_all_regions(uid, acc, force=True)
    stopped_n = 0
    for reg, insts, _ in scan:
        ids = [i["id"] for i in insts if i["state"] == "running"]
        if not ids:
            continue
        cl = aws.client(uid, acc, "ec2", reg)
        await aws.run(cl.stop_instances, InstanceIds=ids)
        for iid in ids:
            mark_state(uid, acc, reg, iid, "stopping")
        stopped_n += len(ids)
    aws.invalidate(("ec2_list", uid, acc))
    aws.invalidate(("ec2_scan", uid, acc))
    store.audit(uid, acc, "BULK_EC2_STOP", f"{stopped_n} instances")
    await show(u, c, f"✅ <b>Bulk Stop Imekamilika!</b>\n" + card(f"EC2 zilizoagizwa kuzima: <b>{stopped_n}</b>"),
               [[btn("⬅️ Bulk Actions", f"bulk|{acc}")], nav()])


@route("blk_up")
async def h_blk_up(u, c, p):
    acc = p[1]
    await show(
        u, c,
        f"▶️ <b>Washa (START) EC2 ZOTE zilizozimwa kwenye {esc(acc)}?</b>",
        [[btn("✅ Ndiyo, Washa Zote", f"blk_upok|{acc}"), btn("❌ Hapana", f"bulk|{acc}")]],
    )


@route("blk_upok", slow=True)
async def h_blk_upok(u, c, p):
    uid, acc = u.effective_user.id, p[1]
    scan = await scan_ec2_all_regions(uid, acc, force=True)
    started_n = 0
    for reg, insts, _ in scan:
        ids = [i["id"] for i in insts if i["state"] == "stopped"]
        if not ids:
            continue
        cl = aws.client(uid, acc, "ec2", reg)
        await aws.run(cl.start_instances, InstanceIds=ids)
        started_n += len(ids)
    aws.invalidate(("ec2_list", uid, acc))
    aws.invalidate(("ec2_scan", uid, acc))
    store.audit(uid, acc, "BULK_EC2_START", f"{started_n} instances")
    await show(u, c, f"✅ <b>Bulk Start Imekamilika!</b>\n" + card(f"EC2 zilizoagizwa kuwaka: <b>{started_n}</b>"),
               [[btn("⬅️ Bulk Actions", f"bulk|{acc}")], nav()])


# ======================= CSV Inventory Export =======================
@route("exp_csv", slow=True)
async def h_exp_csv(u, c, p):
    uid = u.effective_user.id
    accs = store.accounts(uid)
    if not accs:
        return await show(u, c, "⚠️ Hakuna AWS account iliyounganishwa.", [[btn("➕ Add Account", "aa")], nav()])

    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["Account", "Service", "Region", "ID", "Name_or_Label", "Status", "Address_or_Domain", "Origin_or_Type", "Extra"])
    cf_cnt, ec2_cnt = 0, 0

    for acc in accs:
        try:
            ds = await cf_list_cached(uid, acc, force=True)
            for d in ds:
                cf_cnt += 1
                origins = d.get("Origins", {}).get("Items") or []
                orig = origins[0]["DomainName"] if origins else ""
                al = ",".join(d.get("Aliases", {}).get("Items") or [])
                st = f"{d['Status']} ({'ON' if d['Enabled'] else 'OFF'})"
                w.writerow([acc, "CloudFront", "global", d["Id"], d.get("Comment") or "", st, d["DomainName"], orig, al])
        except Exception:
            pass
        try:
            scan = await scan_ec2_all_regions(uid, acc, force=True)
            for reg, insts, _ in scan:
                for i in insts:
                    ec2_cnt += 1
                    w.writerow([acc, "EC2", reg, i["id"], i["name"], i["state"], i["ip"] or "", i["type"], i["dns"] or ""])
        except Exception:
            pass

    raw = buf.getvalue().encode("utf-8-sig")
    fname = f"aws_inventory_{dt.datetime.now(config.LOCAL_TZ).strftime('%Y%m%d_%H%M')}.csv"
    await u.effective_chat.send_document(
        document=io.BytesIO(raw),
        filename=fname,
        caption=f"📥 <b>AWS Inventory Export</b>\n☁️ CloudFront: <b>{cf_cnt}</b> · 🖥 EC2: <b>{ec2_cnt}</b> · 👤 Accounts: <b>{len(accs)}</b>",
        parse_mode="HTML",
    )
    store.audit(uid, "all", "INVENTORY_EXPORT", f"{cf_cnt} CF, {ec2_cnt} EC2")
    await show(
        u, c,
        f"✅ <b>Inventory CSV imetumwa!</b>\n"
        + card(f"📄 Faili: <code>{fname}</code>\n☁️ CloudFront: <b>{cf_cnt}</b>\n🖥 EC2: <b>{ec2_cnt}</b>"),
        [nav()],
    )
