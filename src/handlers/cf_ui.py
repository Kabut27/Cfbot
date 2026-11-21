"""CloudFront v4 Pro UI: Tree Cards, Latency Tester, 1-Click Clone, Multi-Origin Failover,
Read Timeout, Custom Headers, Geo Restriction, WAF WebACL, Purge (Quota + Custom Paths), Backup/Restore."""
import asyncio, io, json, logging, re, time, datetime as dt
import config
import storage as store
from core import aws
from core.router import route, flow
from core.ui import show, loading, btn, nav, head, card, tree_card, progress_bar, spark, health_badge, stamp, esc, num
from handlers.common import edit_dist, prompt, tok_data, clean_domain, valid_port, back_accounts, cf_list_cached
from services import cloudfront as cfs, metrics as ms

log = logging.getLogger(__name__)
PER = 8


def _k(acc, did): return f"{acc}|{did}"
def _crumb(acc, did, *more): return head("🏠 <b>Home</b>", f"👤 <b>{esc(acc)}</b>", f"☁️ <code>{did}</code>", *more)
def _cl(u, acc, svc="cloudfront", region="us-east-1"): return aws.client(u.effective_user.id, acc, svc, region)


# ======================= list =======================
@route("dl", slow=True)
async def h_dl(u, c, p):
    uid, acc = u.effective_user.id, p[1]
    page = int(p[2]) if len(p) > 2 else 0
    force = (len(p) > 3 and p[3] == "1")
    ds = await cf_list_cached(uid, acc, force=force)
    on_cnt = sum(1 for d in ds if d["Enabled"])
    dep_cnt = sum(1 for d in ds if d["Status"] != "Deployed")

    rows = []
    for d in ds[page * PER:(page + 1) * PER]:
        dot = "🟢" if d["Enabled"] and d["Status"] == "Deployed" else ("🟡" if d["Status"] != "Deployed" else "🔴")
        rows.append([btn(f"{dot} {d['Id']} · {cfs.dlabel(d)}"[:55], f"d|{acc}|{d['Id']}")])
    pager = ([btn("◀️ Nyuma", f"dl|{acc}|{page - 1}")] if page > 0 else []) +             ([btn("Mbele ▶️", f"dl|{acc}|{page + 1}")] if (page + 1) * PER < len(ds) else [])
    if pager: rows.append(pager)
    rows += [
        [btn("➕ Create CF", f"cr|{acc}"), btn("📊 Data & Reqs", f"ua|{acc}")],
        [btn("🔄 Refresh", f"dl|{acc}|{page}|1"), btn("🏠 Menu Kuu", "home")],
    ]
    summary = card(
        f"╭ ☁️ <b>Jumla:</b> <code>{len(ds)}</code> (Ukurasa {page + 1})\n"
        f"├ 🟢 <b>Active:</b> <code>{on_cnt}</code> · 🔴 <b>Off:</b> <code>{len(ds) - on_cnt}</code>\n"
        f"╰ 🟡 <b>Deploying:</b> <code>{dep_cnt}</code>"
    )
    age = aws.cache_age(("cf_list", uid, acc))
    await show(u, c, head("🏠 <b>Home</b>", f"👤 <b>{esc(acc)}</b>", "☁️ <b>CloudFront</b>") + summary + stamp(age), rows)


# ======================= detail =======================
def render_detail(acc, did, D, metrics=None, note=""):
    cfg = D["DistributionConfig"]
    origins = cfs.origins(cfg)
    o = cfs.active_origin(cfg)
    co = o.get("CustomOriginConfig") or {}
    al = cfs.aliases(cfg)
    vc = cfg.get("ViewerCertificate", {})
    on = cfg["Enabled"]
    status = D["Status"]
    dot = "🟢" if (on and status == "Deployed") else ("🟡" if status != "Deployed" else "🔴")

    proto = co.get("OriginProtocolPolicy", "S3")
    if proto == "http-only":
        port_val = f"{co.get('HTTPPort', '-')}  # http-only"
    elif proto == "https-only":
        port_val = f"{co.get('HTTPSPort', '-')}  # https-only"
    elif proto == "match-viewer":
        port_val = f"{co.get('HTTPPort', '-')}/{co.get('HTTPSPort', '-')}  # match"
    else:
        port_val = '"S3 Origin"'

    http_v = cfg.get("HttpVersion", "http2").upper().replace("HTTP2AND3", "HTTP/3")
    ck = cfs.cache_kind(cfg)
    cache_lbl = {"d": "Disabled", "o": "Optimized"}.get(ck, "Custom")
    price_short = cfg.get("PriceClass", "-").replace("PriceClass_", "")

    cf_score = 100
    if not on: cf_score -= 20
    if status != "Deployed": cf_score -= 10
    if cfg.get("DefaultCacheBehavior", {}).get("ViewerProtocolPolicy") == "allow-all": cf_score -= 15
    if metrics:
        if metrics["e5"] >= 10: cf_score -= 35
        elif metrics["e5"] >= 2: cf_score -= 15
        if metrics["gb"] / config.FREE_TIER_GB >= 0.85: cf_score -= 15
    y_rows = [
        ("Health", f'"{health_badge(cf_score)}"'),
        ("Status", f'"{status} ({"ON" if on else "OFF"})"'),
        ("Port", port_val),
        ("HTTP", f'"{http_v}"  # {price_short} · IPv6={"on" if cfg.get("IsIPV6Enabled") else "off"}'),
        ("Cache", f'"{cache_lbl}"  # Cert={"ACM" if vc.get("ACMCertificateArn") else "Default"}'),
    ]
    if metrics:
        gb_pct = metrics["gb"] / config.FREE_TIER_GB * 100
        req_pct = metrics["req"] / config.FREE_TIER_REQ * 100
        y_rows += [
            ("Data_7d", f"{spark(metrics['gb_7d'], 7)}  # {metrics['gb']:.2f} GB ({gb_pct:.1f}%)"),
            ("Reqs_7d", f"{spark(metrics['req_7d'], 7)}  # {num(metrics['req'])} ({req_pct:.1f}%)"),
            ("Last_24h", f"{num(metrics['req_24h'])} reqs · {metrics['gb_24h']:.2f} GB"),
            ("Err_24h", f"4xx {metrics['e4']:.1f}% · 5xx {metrics['e5']:.1f}%"),
        ]

    copy_lines = [
        f"╭ 🆔 <b>ID:</b> <code>{did}</code>",
        f"├ 🎯 <b>Origin ({len(origins)}):</b> <code>{esc(o['DomainName'])}</code>",
        f"├ 🌐 <b>Edge:</b> <code>{D['DomainName']}</code>" if al else f"╰ 🌐 <b>Edge:</b> <code>{D['DomainName']}</code>",
    ]
    if al:
        copy_lines.append(f"╰ 🔗 <b>Custom:</b> <code>{esc(', '.join(al))}</code>")

    title = esc(cfg.get("Comment") or did)
    text = (
        _crumb(acc, did)
        + f"{dot} <b>{title}</b>\n"
        + tree_card(y_rows) + "\n\n"
        + card("\n".join(copy_lines))
    )
    if al:
        text += f"\n📌 <i>DNS: CNAME → <code>{D['DomainName']}</code> (DNS only)</i>"
    if status != "Deployed":
        text += "\n⏳ <i>Mabadiliko yanasambazwa (3–10 dk). Bot itakutaarifu yakikamilika.</i>"
    if note:
        text += f"\n{note}"
    return text + stamp()


def detail_rows(acc, did, on):
    k = _k(acc, did)
    return [
        [btn("⏸ Disable" if on else "▶️ Enable", f"tg|{k}"), btn("🔄 Refresh", f"d|{k}")],
        [btn("🎯 Origin & Failover", f"or|{k}"), btn("⚙️ Settings", f"st|{k}")],
        [btn("🩺 Test Origin", f"cf_tst|{k}"), btn("📋 Clone CF", f"cf_cln|{k}")],
        [btn("🌍 Domains & SSL", f"cd|{k}"), btn("📊 Data & Reqs", f"an|{k}")],
        [btn("🧹 Purge", f"pg|{k}"), btn("💾 Backup / Restore", f"bk|{k}")],
        [btn("🗑 Delete", f"rm|{k}"), btn("⬅️ Back", f"dl|{acc}|0")],
    ]


async def detail(u, c, acc, did, note="", with_metrics=True):
    cl, cw = _cl(u, acc), _cl(u, acc, "cloudwatch")
    async def safe_met():
        if not with_metrics:
            return None
        try: return await aws.run(ms.cf_full_metrics, cw, did)
        except Exception as e:
            log.info("metrics unavailable: %s", e); return None
    (D, _), metrics = await asyncio.gather(aws.run(cfs.get, cl, did), safe_met())
    await show(u, c, render_detail(acc, did, D, metrics, note),
               detail_rows(acc, did, D["DistributionConfig"]["Enabled"]))


@route("d", slow=True)
async def h_d(u, c, p): await detail(u, c, p[1], p[2])


@route("tg", slow=True)
async def h_tg(u, c, p):
    await edit_dist(u, p[1], p[2], lambda cfg: cfg.__setitem__("Enabled", not cfg["Enabled"]), audit_act="CF_TOGGLE")
    await detail(u, c, p[1], p[2], "✅ Hali imebadilishwa.")


# ======================= latency tester =======================
@route("cf_tst", slow=True)
async def h_cf_tst(u, c, p):
    acc, did = p[1], p[2]
    D, _ = await aws.run(cfs.get, _cl(u, acc), did)
    cfg = D["DistributionConfig"]
    o = cfs.active_origin(cfg)
    co = o.get("CustomOriginConfig") or {}
    proto = co.get("OriginProtocolPolicy", "http-only")
    port = int(co.get("HTTPSPort", 443) if proto == "https-only" else co.get("HTTPPort", 80))
    orig_host, edge_host = o["DomainName"], D["DomainName"]

    (o_ok, o_ms, o_msg), (e_ok, e_ms, e_msg) = await asyncio.gather(
        ms.tcp_probe(orig_host, port, 5.0),
        ms.tcp_probe(edge_host, 443, 5.0),
    )
    tcard = tree_card([
        ("Origin", f'"{o_msg}"  # {o_ms:.0f}ms (port {port})'),
        ("Edge_443", f'"{e_msg}"  # {e_ms:.0f}ms'),
        ("Protocol", f'"{proto}"'),
    ])
    diag = (
        "🟢 <b>Origin inapokea muunganisho vizuri!</b>"
        if o_ok
        else (
            f"🔴 <b>Origin haikujibu kwenye port <code>{port}</code> ({esc(o_msg)}).</b>\n"
            "• Ikiwa umefunga EC2 kwa <i>CloudFront Only</i>, probe ya moja kwa moja kutoka VPS inaweza kuzuiliwa (ishara firewall inafanya kazi).\n"
            "• Kama unapata 502/504, kagua Nginx/Caddy/x-ui na Security Group ya EC2."
        )
    )
    k = _k(acc, did)
    text = (
        _crumb(acc, did, "🩺 <b>Health &amp; Latency Test</b>")
        + tcard + "\n\n"
        + card(f"🎯 <b>Origin:</b> <code>{esc(orig_host)}:{port}</code>\n🌐 <b>Edge:</b> <code>{edge_host}:443</code>\n\n{diag}")
    )
    await show(u, c, text, [[btn("🔄 Test Tena", f"cf_tst|{k}")], [btn("⬅️ Back", f"d|{k}")]])


# ======================= 1-Click Clone =======================
@route("cf_cln")
async def h_cf_cln(u, c, p):
    acc, did = p[1], p[2]
    await prompt(
        u, c, {"t": "cf_cln", "acc": acc, "did": did},
        f"📋 <b>Clone <code>{did}</code></b>\n\nTuma <b>jina/label mpya</b> kwa distribution hii mpya:",
        f"d|{_k(acc, did)}",
    )


@flow("cf_cln")
async def f_cf_cln(u, c, f, txt):
    uid, acc, did = u.effective_user.id, f["acc"], f["did"]
    new_id = await aws.run(cfs.clone_dist, _cl(u, acc), did, txt)
    aws.invalidate(("cf_list", uid, acc))
    store.add_watch(uid, "dist", acc, new_id)
    store.audit(uid, acc, "CF_CLONE", f"{did} -> {new_id}")
    await detail(u, c, acc, new_id, f"✅ Clone imekamilika kutoka <code>{did}</code>!", with_metrics=False)


# ======================= purge (quota + /* + custom paths) =======================
@route("pg", slow=True)
async def h_pg(u, c, p):
    acc, did = p[1], p[2]
    k = _k(acc, did)
    try:
        m_cnt, recent = await aws.run(cfs.month_invalidations, _cl(u, acc), did)
    except Exception:
        m_cnt, recent = 0, []
    lines = [
        f"• <code>{i['Id']}</code> · <b>{i['Status']}</b> ({i['CreateTime'].strftime('%d/%m %H:%M')})"
        for i in recent
    ]
    body = card(
        f"📊 <b>Mwezi huu:</b> <code>{m_cnt} / {config.FREE_TIER_INV}</code>\n"
        f"{progress_bar(m_cnt, config.FREE_TIER_INV)}\n\n"
        + ("\n".join(lines) if lines else "<i>Hakuna invalidations za hivi karibuni.</i>")
    )
    await show(u, c, _crumb(acc, did, "🧹 <b>Purge / Invalidation</b>") + body,
               [[btn("🧹 Purge All (/*)", f"pgok|{k}"), btn("🎯 Custom Paths", f"pgcus|{k}")],
                [btn("⬅️ Back", f"d|{k}")]])


@route("pgok", slow=True)
async def h_pgok(u, c, p):
    acc, did = p[1], p[2]
    await aws.run(
        _cl(u, acc).create_invalidation,
        DistributionId=did,
        InvalidationBatch={"Paths": {"Quantity": 1, "Items": ["/*"]}, "CallerReference": str(time.time())},
    )
    store.audit(u.effective_user.id, acc, "CF_PURGE", f"{did} /*")
    await show(u, c, f"✅ Purge ya <code>/*</code> imetumwa kwenye <code>{did}</code>.",
               [[btn("⬅️ Back", f"pg|{_k(acc, did)}")]])


@route("pgcus")
async def h_pgcus(u, c, p):
    acc, did = p[1], p[2]
    await prompt(
        u, c, {"t": "pg_cus", "acc": acc, "did": did},
        "🎯 Tuma paths za ku-purge (tenganisha kwa koma), mfano: <code>/index.html, /assets/*</code>:",
        f"pg|{_k(acc, did)}",
    )


@flow("pg_cus")
async def f_pg_cus(u, c, f, txt):
    paths = [x.strip() for x in re.split(r"[,\s]+", txt) if x.strip()]
    if not paths:
        return await prompt(u, c, f, "❌ Tuma angalau path moja (mfano <code>/index.html</code>):", f"pg|{_k(f['acc'], f['did'])}")
    paths = [p if p.startswith("/") else "/" + p for p in paths]
    await aws.run(
        _cl(u, f["acc"]).create_invalidation,
        DistributionId=f["did"],
        InvalidationBatch={"Paths": {"Quantity": len(paths), "Items": paths}, "CallerReference": str(time.time())},
    )
    store.audit(u.effective_user.id, f["acc"], "CF_PURGE", f"{f['did']} {','.join(paths[:3])}")
    await show(u, c, f"✅ Purge imetumwa kwa paths: <code>{esc(', '.join(paths))}</code>",
               [[btn("⬅️ Back", f"pg|{_k(f['acc'], f['did'])}")]])


# ======================= delete =======================
@route("rm")
async def h_rm(u, c, p):
    k = _k(p[1], p[2])
    await show(
        u, c,
        f"⚠️ <b>Futa Distribution <code>{p[2]}</code>?</b>\n\n"
        + card("Ikiwa bado iko ON, itazimwa kwanza na utapata taarifa ikishakuwa <b>Deployed</b> ili ufute moja kwa moja."),
        [[btn("🗑 Ndiyo, Endelea", f"rmok|{k}"), btn("❌ Hapana", f"d|{k}")]],
    )


@route("rmok", slow=True)
async def h_rmok(u, c, p):
    acc, did, uid = p[1], p[2], u.effective_user.id
    cl = _cl(u, acc)
    D, etag = await aws.run(cfs.get, cl, did)
    if D["DistributionConfig"]["Enabled"]:
        await edit_dist(u, acc, did, lambda cfg: cfg.__setitem__("Enabled", False),
                        audit_act="CF_DISABLE", audit_det="(for delete)")
        store.add_watch(uid, "dist", acc, did, {"then": "delete"})
        return await show(u, c, _crumb(acc, did) + "⏳ Imezimwa. Nitakutaarifu itakapokuwa <b>Deployed</b> ili uifute.",
                          [[btn("🔄 Refresh", f"d|{_k(acc, did)}")], nav()])
    if D["Status"] != "Deployed":
        store.add_watch(uid, "dist", acc, did, {"then": "delete"})
        return await show(u, c, _crumb(acc, did) + "⏳ Bado <b>InProgress</b>. Nitakutaarifu ikiwa <b>Deployed</b>.",
                          [[btn("🔄 Refresh", f"d|{_k(acc, did)}")], nav()])
    await aws.run(cl.delete_distribution, Id=did, IfMatch=etag)
    aws.invalidate(("cf_list", uid, acc))
    store.audit(uid, acc, "CF_DELETE", did)
    await show(u, c, f"✅ Distribution <code>{did}</code> imefutwa kabisa.", [[btn("⬅️ List", f"dl|{acc}|0|1")], nav()])


# ======================= create =======================
@route("cr")
async def h_cr(u, c, p):
    acc, uid = p[1], u.effective_user.id
    tok = store.ref(uid, {"m": "c", "a": acc})
    c.user_data["flow"] = {"t": "c1", "acc": acc}
    await show(
        u, c,
        f"➕ <b>Create CloudFront · {esc(acc)}</b>\n{'━' * 20}\n"
        + card("<b>Hatua 1/3:</b> Tuma <b>Origin Domain</b>\nMfano: <code>ec2-1-2-3-4.compute-1.amazonaws.com</code>"),
        [[btn("🖥 Pick from EC2", f"pe|{tok}")], [btn("✖️ Ghairi", f"dl|{acc}|0")]],
    )


@flow("c1")
async def f_c1(u, c, f, txt):
    dom = clean_domain(txt)
    if not dom: return await prompt(u, c, f, "❌ Domain si sahihi. Jaribu tena:", f"dl|{f['acc']}|0")
    await prompt(u, c, {"t": "c2", "acc": f["acc"], "origin": dom},
                 card(f"✅ Origin: <code>{esc(dom)}</code>\n<b>Hatua 2/3:</b> Tuma <b>Port</b> ya origin (mfano <code>80</code> au <code>8080</code>):"),
                 f"dl|{f['acc']}|0")


@flow("c2")
async def f_c2(u, c, f, txt):
    port = valid_port(txt)
    if not port: return await prompt(u, c, f, "❌ Port si sahihi (1–65535):", f"dl|{f['acc']}|0")
    await prompt(u, c, {**f, "t": "c3", "port": port},
                 card("<b>Hatua 3/3:</b> Tuma <b>Jina / Label</b> ya distribution:"), f"dl|{f['acc']}|0")


@flow("c3")
async def f_c3(u, c, f, txt):
    uid = u.effective_user.id
    r = await aws.run(_cl(u, f["acc"]).create_distribution,
                      DistributionConfig=cfs.build_cfg(f["origin"], f["port"], txt[:128]))
    did = r["Distribution"]["Id"]
    aws.invalidate(("cf_list", uid, f["acc"]))
    store.add_watch(uid, "dist", f["acc"], did)
    store.audit(uid, f["acc"], "CF_CREATE", f"{did} ({f['origin']}:{f['port']})")
    await detail(u, c, f["acc"], did, "✅ Imetengenezwa.", with_metrics=False)


# ======================= origins, failover, timeout & headers =======================
async def _cfg(u, acc, did):
    return (await aws.run(_cl(u, acc).get_distribution_config, Id=did))["DistributionConfig"]


async def origin_view(u, c, acc, did, i=0, note=""):
    uid = u.effective_user.id
    cfg = await _cfg(u, acc, did)
    orgs = cfs.origins(cfg)
    if i < 0: i = len(orgs) - 1
    elif i >= len(orgs): i = 0
    o = orgs[i]
    active_oid = cfg.get("DefaultCacheBehavior", {}).get("TargetOriginId", "")
    is_active = (o["Id"] == active_oid)
    co = o.get("CustomOriginConfig") or {}
    ch = o.get("CustomHeaders", {}).get("Items") or []
    hdr_txt = ", ".join(h["HeaderName"] for h in ch) if ch else "none"
    k = f"{_k(acc, did)}|{i}"
    tok = store.ref(uid, {"m": "e", "a": acc, "d": did, "o": i})

    tcard = tree_card([
        ("Origin_ID", f'"{o["Id"]}"  # {"ACTIVE" if is_active else "BACKUP"}'),
        ("HTTP_Port", f"{co.get('HTTPPort', '-')}"),
        ("HTTPS_Pt", f"{co.get('HTTPSPort', '-')}"),
        ("Protocol", f'"{co.get("OriginProtocolPolicy", "S3")}"'),
        ("Timeout", f"{co.get('OriginReadTimeout', '-')}s"),
        ("Headers", f'"{hdr_txt}"'),
    ])
    badge = "🟢 ACTIVE" if is_active else "🔵 BACKUP"
    text = (
        _crumb(acc, did, f"🎯 <b>Origin #{i + 1}/{len(orgs)} ({badge})</b>")
        + tcard + "\n\n"
        + card(f"🎯 <b>Domain:</b> <code>{esc(o['DomainName'])}</code>")
        + (f"\n{note}" if note else "")
    )
    rows = []
    if len(orgs) > 1:
        nxt = (i + 1) % len(orgs)
        nav_row = [btn(f"🔀 Tazama Origin ({i + 1}/{len(orgs)})", f"oe|{_k(acc, did)}|{nxt}")]
        if not is_active:
            nav_row.append(btn("⚡ Fanya ACTIVE", f"or_sw|{k}"))
        rows.append(nav_row)

    rows += [[btn("✏️ Edit Domain", f"od|{k}"), btn("🖥 Pick from EC2", f"pe|{tok}")]]
    if co:
        rows += [
            [btn(f"🔌 HTTP Port ({co.get('HTTPPort')})", f"ph|{k}"), btn(f"🔒 HTTPS Port ({co.get('HTTPSPort')})", f"ps|{k}")],
            [btn("🔄 Origin Protocol", f"pp|{k}"), btn("⏱ Read Timeout", f"to_ed|{k}")],
        ]
    rows += [
        [btn("🛡 Set Header", f"ch_ed|{k}"), btn("🗑 Clear Headers", f"ch_cl|{k}")],
    ]
    fo_row = [btn("➕ Add Backup Origin", f"or_add|{_k(acc, did)}")]
    if len(orgs) > 1 and not is_active:
        fo_row.append(btn("🗑 Futa Backup Hii", f"or_del|{k}"))
    rows.append(fo_row)
    rows.append([btn("⬅️ Back to Distribution", f"d|{_k(acc, did)}")])
    await show(u, c, text, rows)


@route("or", slow=True)
async def h_or(u, c, p): await origin_view(u, c, p[1], p[2], 0)

@route("oe", slow=True)
async def h_oe(u, c, p): await origin_view(u, c, p[1], p[2], int(p[3]))

@route("or_sw", slow=True)
async def h_or_sw(u, c, p):
    i = int(p[3])
    await edit_dist(u, p[1], p[2], lambda cfg: cfs.set_active_origin(cfg, i),
                    audit_act="CF_FAILOVER", audit_det=f"-> origin#{i + 1}")
    await origin_view(u, c, p[1], p[2], i, "✅ Active origin imebadilishwa (Failover).")

@route("or_del", slow=True)
async def h_or_del(u, c, p):
    i = int(p[3])
    await edit_dist(u, p[1], p[2], lambda cfg: cfs.del_origin(cfg, i),
                    audit_act="CF_ORIGIN_DEL", audit_det=f"origin#{i + 1}")
    await origin_view(u, c, p[1], p[2], 0, "✅ Backup origin imefutwa.")

@route("or_add")
async def h_or_add(u, c, p):
    await prompt(u, c, {"t": "or_add1", "acc": p[1], "did": p[2]},
                 "➕ <b>Ongeza Backup Origin</b>\n\nTuma <b>Domain / EC2 DNS</b> ya backup origin:",
                 f"or|{_k(p[1], p[2])}")

@flow("or_add1")
async def f_or_add1(u, c, f, txt):
    dom = clean_domain(txt)
    if not dom: return await prompt(u, c, f, "❌ Domain si sahihi. Jaribu tena:", f"or|{_k(f['acc'], f['did'])}")
    await prompt(u, c, {**f, "t": "or_add2", "dom": dom},
                 "🔌 Tuma <b>HTTP Port</b> ya backup origin (mfano <code>80</code> au <code>8080</code>):",
                 f"or|{_k(f['acc'], f['did'])}")

@flow("or_add2")
async def f_or_add2(u, c, f, txt):
    port = valid_port(txt)
    if not port: return await prompt(u, c, f, "❌ Port si sahihi (1–65535):", f"or|{_k(f['acc'], f['did'])}")
    await edit_dist(u, f["acc"], f["did"], lambda cfg: cfs.add_backup_origin(cfg, f["dom"], port),
                    audit_act="CF_ORIGIN_ADD", audit_det=f"{f['dom']}:{port}")
    await origin_view(u, c, f["acc"], f["did"], -1, "✅ Backup origin imeongezwa.")

@route("od")
async def h_od(u, c, p):
    await prompt(u, c, {"t": "od", "acc": p[1], "did": p[2], "i": int(p[3])},
                 "🎯 Tuma <b>Origin Domain</b> mpya (DNS name):", f"oe|{_k(p[1], p[2])}|{p[3]}")

@flow("od")
async def f_od(u, c, f, txt):
    dom = clean_domain(txt)
    if not dom: return await prompt(u, c, f, "❌ Domain si sahihi. Jaribu tena:", f"oe|{_k(f['acc'], f['did'])}|{f['i']}")
    await edit_dist(u, f["acc"], f["did"], lambda cfg: cfs.origin_at(cfg, f["i"]).__setitem__("DomainName", dom),
                    audit_act="CF_ORIGIN", audit_det=dom[:40])
    await origin_view(u, c, f["acc"], f["did"], f["i"], "✅ Origin imebadilishwa.")

async def _port_prompt(u, c, p, field, label):
    await prompt(u, c, {"t": "port", "acc": p[1], "did": p[2], "i": int(p[3]), "field": field},
                 f"🔌 Tuma <b>{label} Port</b> mpya (mfano <code>{'8080' if field == 'HTTPPort' else '443'}</code>):",
                 f"oe|{_k(p[1], p[2])}|{p[3]}")

@route("ph")
async def h_ph(u, c, p): await _port_prompt(u, c, p, "HTTPPort", "HTTP")

@route("ps")
async def h_ps(u, c, p): await _port_prompt(u, c, p, "HTTPSPort", "HTTPS")

@flow("port")
async def f_port(u, c, f, txt):
    port = valid_port(txt)
    if not port: return await prompt(u, c, f, "❌ Port si sahihi (1–65535):", f"oe|{_k(f['acc'], f['did'])}|{f['i']}")
    await edit_dist(u, f["acc"], f["did"],
                    lambda cfg: cfs.custom_of(cfs.origin_at(cfg, f["i"])).__setitem__(f["field"], port),
                    audit_act="CF_PORT", audit_det=f"{f['field']}={port}")
    await origin_view(u, c, f["acc"], f["did"], f["i"], f"✅ {f['field']} = {port}")

@route("pp", slow=True)
async def h_pp(u, c, p):
    i = int(p[3])
    def fn(cfg):
        co = cfs.custom_of(cfs.origin_at(cfg, i))
        co["OriginProtocolPolicy"] = cfs.PROTOS[(cfs.PROTOS.index(co.get("OriginProtocolPolicy", "http-only")) + 1) % 3]
    await edit_dist(u, p[1], p[2], fn, audit_act="CF_PROTO")
    await origin_view(u, c, p[1], p[2], i, "✅ Origin Protocol imebadilishwa.")

@route("to_ed")
async def h_to_ed(u, c, p):
    await prompt(u, c, {"t": "to_ed", "acc": p[1], "did": p[2], "i": int(p[3])},
                 "⏱ Tuma <b>Origin Read Timeout</b> kwa sekunde (1–60):", f"oe|{_k(p[1], p[2])}|{p[3]}")

@flow("to_ed")
async def f_to_ed(u, c, f, txt):
    if not txt.isdigit() or not 1 <= int(txt) <= 60:
        return await prompt(u, c, f, "❌ Timeout lazima iwe kati ya sekunde 1 na 60:", f"oe|{_k(f['acc'], f['did'])}|{f['i']}")
    await edit_dist(u, f["acc"], f["did"],
                    lambda cfg: cfs.custom_of(cfs.origin_at(cfg, f["i"])).__setitem__("OriginReadTimeout", int(txt)),
                    audit_act="CF_TIMEOUT", audit_det=f"{txt}s")
    await origin_view(u, c, f["acc"], f["did"], f["i"], f"✅ Read Timeout = {txt}s")

@route("ch_ed")
async def h_ch_ed(u, c, p):
    await prompt(u, c, {"t": "ch_ed", "acc": p[1], "did": p[2], "i": int(p[3])},
                 "🛡 Tuma Custom Header kwa muundo <code>Header-Name: Value</code>\nMfano: <code>X-Origin-Secret: siri123</code>",
                 f"oe|{_k(p[1], p[2])}|{p[3]}")

@flow("ch_ed")
async def f_ch_ed(u, c, f, txt):
    if ":" not in txt:
        return await prompt(u, c, f, "❌ Tumia muundo <code>Header-Name: Value</code>:", f"oe|{_k(f['acc'], f['did'])}|{f['i']}")
    hn, hv = [x.strip() for x in txt.split(":", 1)]
    if not hn or not hv:
        return await prompt(u, c, f, "❌ Header na thamani haziwezi kuwa tupu:", f"oe|{_k(f['acc'], f['did'])}|{f['i']}")
    await edit_dist(u, f["acc"], f["did"], lambda cfg: cfs.set_header(cfg, f["i"], hn, hv),
                    audit_act="CF_HEADER", audit_det=hn)
    await origin_view(u, c, f["acc"], f["did"], f["i"], f"✅ Header <code>{esc(hn)}</code> imewekwa.")

@route("ch_cl", slow=True)
async def h_ch_cl(u, c, p):
    i = int(p[3])
    await edit_dist(u, p[1], p[2], lambda cfg: cfs.clear_headers(cfg, i), audit_act="CF_CLR_HDR")
    await origin_view(u, c, p[1], p[2], i, "✅ Custom Headers zimesafishwa.")


# ======================= settings (Cache, Viewer, HTTP, Compress, IPv6, Price, Geo, WAF, Label) =======================
async def settings_view(u, c, acc, did, note=""):
    cfg = await _cfg(u, acc, did); k = _k(acc, did)
    vc, ck = cfs.viewer_code(cfg), cfs.cache_kind(cfg)
    comp = all(b.get("Compress") for b in cfs.behaviors(cfg))
    v6, hv, pr = cfg.get("IsIPV6Enabled"), cfg.get("HttpVersion", "http2"), cfg.get("PriceClass", "PriceClass_All")
    geo = cfg.get("Restrictions", {}).get("GeoRestriction", {})
    geo_type = geo.get("RestrictionType", "none")
    geo_items = geo.get("Items") or []
    geo_str = f'"{geo_type}"' + (f"  # {','.join(geo_items[:6])}" if geo_items else "")
    waf = cfg.get("WebACLId") or "none"
    cache_lbl = {"d": "Disabled", "o": "Optimized"}.get(ck, "Custom")

    tcard = tree_card([
        ("Cache", f'"{cache_lbl}"'),
        ("Viewer", f'"{cfs.VIEWER[vc]}"'),
        ("HTTP", f'"{hv}"'),
        ("Compress", "true" if comp else "false"),
        ("IPv6", "true" if v6 else "false"),
        ("Price", f'"{pr.replace("PriceClass_", "")}"'),
        ("Geo", geo_str),
        ("WAF", f'"{waf.split("/")[-1] if "/" in waf else waf}"'),
    ])
    rows = [
        [btn("⚡ Toggle Cache", f"cp|{k}|{'o' if ck == 'd' else 'd'}"), btn("🔐 Viewer Proto", f"vp|{k}")],
        [btn("🚀 HTTP Version", f"hv|{k}"), btn("📦 Compress", f"cz|{k}")],
        [btn("🌐 Toggle IPv6", f"v6|{k}"), btn("💲 Price Class", f"pc|{k}")],
        [btn("🌍 Geo Restriction", f"s_geo|{k}"), btn("🛡 WAF WebACL", f"s_waf|{k}")],
        [btn("📝 Edit Label", f"s_lbl|{k}"), btn("⬅️ Back", f"d|{k}")],
    ]
    await show(u, c, _crumb(acc, did, "⚙️ <b>Settings</b>") + tcard + (f"\n{note}" if note else ""), rows)


@route("st", slow=True)
async def h_st(u, c, p): await settings_view(u, c, p[1], p[2])

async def _apply(u, c, p, fn, note, act="CF_SET"):
    await edit_dist(u, p[1], p[2], fn, audit_act=act)
    await settings_view(u, c, p[1], p[2], note)

@route("vp", slow=True)
async def h_vp(u, c, p):
    order = ["r", "h", "a"]
    def fn(cfg):
        nxt = order[(order.index(cfs.viewer_code(cfg)) + 1) % 3]
        cfs.set_viewer(cfg, nxt)
    await _apply(u, c, p, fn, "✅ Viewer protocol imebadilishwa.", "CF_VPROTO")

@route("cp", slow=True)
async def h_cp(u, c, p):
    await _apply(u, c, p, lambda cfg: cfs.set_cache(cfg, p[3]), "✅ Cache policy imebadilishwa.", "CF_CACHE")

@route("hv", slow=True)
async def h_hv(u, c, p):
    def fn(cfg):
        cur = cfg.get("HttpVersion", "http2")
        cfg["HttpVersion"] = cfs.HTTPV[(cfs.HTTPV.index(cur) + 1) % len(cfs.HTTPV)] if cur in cfs.HTTPV else "http2and3"
    await _apply(u, c, p, fn, "✅ HTTP version imebadilishwa.", "CF_HTTP")

@route("cz", slow=True)
async def h_cz(u, c, p):
    def fn(cfg): cfs.set_compress(cfg, not all(b.get("Compress") for b in cfs.behaviors(cfg)))
    await _apply(u, c, p, fn, "✅ Compression imebadilishwa.", "CF_COMPRESS")

@route("v6", slow=True)
async def h_v6(u, c, p):
    await _apply(u, c, p, lambda cfg: cfg.__setitem__("IsIPV6Enabled", not cfg.get("IsIPV6Enabled")), "✅ IPv6 imebadilishwa.", "CF_IPV6")

@route("pc", slow=True)
async def h_pc(u, c, p):
    def fn(cfg): cfg["PriceClass"] = cfs.PRICES[(cfs.PRICES.index(cfg.get("PriceClass", cfs.PRICES[0])) + 1) % 3]
    await _apply(u, c, p, fn, "✅ Price class imebadilishwa.", "CF_PRICE")

@route("s_lbl")
async def h_s_lbl(u, c, p):
    await prompt(u, c, {"t": "s_lbl", "acc": p[1], "did": p[2]},
                 "📝 Tuma <b>Label / Comment</b> mpya:", f"st|{_k(p[1], p[2])}")

@flow("s_lbl")
async def f_s_lbl(u, c, f, txt):
    await edit_dist(u, f["acc"], f["did"], lambda cfg: cfg.__setitem__("Comment", txt[:128]),
                    audit_act="CF_LABEL", audit_det=txt[:30])
    await settings_view(u, c, f["acc"], f["did"], "✅ Label imebadilishwa.")

@route("s_geo")
async def h_s_geo(u, c, p):
    k = _k(p[1], p[2])
    await show(u, c, _crumb(p[1], p[2], "🌍 <b>Geo Restriction</b>") + "Chagua aina ya kizuizi cha kijiografia:", [
        [btn("🔓 Ondoa Geo-Restriction (None)", f"s_geon|{k}")],
        [btn("✅ Whitelist (Ruhusu Nchi)", f"s_geoa|{k}|whitelist"), btn("🚫 Blacklist (Zuia Nchi)", f"s_geoa|{k}|blacklist")],
        [btn("⬅️ Back", f"st|{k}")],
    ])

@route("s_geon", slow=True)
async def h_s_geon(u, c, p):
    await edit_dist(u, p[1], p[2], lambda cfg: cfs.set_geo(cfg, "none"), audit_act="CF_GEO", audit_det="none")
    await settings_view(u, c, p[1], p[2], "✅ Geo Restriction imeondolewa.")

@route("s_geoa")
async def h_s_geoa(u, c, p):
    await prompt(u, c, {"t": "s_geo", "acc": p[1], "did": p[2], "mode": p[3]},
                 f"🌍 Tuma misimbo ya nchi ({p[3]}), mfano: <code>TZ, KE, UG</code>:", f"s_geo|{_k(p[1], p[2])}")

@flow("s_geo")
async def f_s_geo(u, c, f, txt):
    codes = [x.strip().upper() for x in re.split(r"[,\s]+", txt) if x.strip()]
    if not codes or any(not re.fullmatch(r"[A-Z]{2}", x) for x in codes):
        return await prompt(u, c, f, "❌ Tumia misimbo ya nchi ya herufi 2 (ISO), mfano: <code>TZ, KE, UG</code>",
                            f"s_geo|{_k(f['acc'], f['did'])}")
    await edit_dist(u, f["acc"], f["did"], lambda cfg: cfs.set_geo(cfg, f["mode"], codes),
                    audit_act="CF_GEO", audit_det=f"{f['mode']}:{','.join(codes[:5])}")
    await settings_view(u, c, f["acc"], f["did"], "✅ Geo Restriction imewekwa.")

@route("s_waf", slow=True)
async def h_s_waf(u, c, p):
    uid, acc, did = u.effective_user.id, p[1], p[2]
    k = _k(acc, did)
    acls = await aws.run(cfs.list_waf_acls, _cl(u, acc, "wafv2", "us-east-1"))
    rows = [[btn(f"🛡 {a['Name']}"[:55], "s_wafs|" + store.ref(uid, {"a": acc, "d": did, "arn": a["ARN"]}))] for a in acls[:10]]
    rows += [
        [btn("❌ Ondoa WAF", "s_wafs|" + store.ref(uid, {"a": acc, "d": did, "arn": ""}))],
        [btn("⬅️ Back", f"st|{k}")],
    ]
    body = "Chagua WebACL ya kuunganisha:" if acls else card("<i>Hakuna CloudFront WAF WebACL iliyopatikana.</i>")
    await show(u, c, _crumb(acc, did, "🛡 <b>AWS WAF</b>") + body, rows)

@route("s_wafs", slow=True)
async def h_s_wafs(u, c, p):
    d = tok_data(u, p[1])
    arn = d["arn"]
    await edit_dist(u, d["a"], d["d"], lambda cfg: cfg.__setitem__("WebACLId", arn),
                    audit_act="CF_WAF", audit_det=arn.split("/")[-1] if arn else "removed")
    await settings_view(u, c, d["a"], d["d"], "✅ WAF imesasishwa.")


# ======================= backup / restore =======================
@route("bk")
async def h_bk(u, c, p):
    uid, acc, did = u.effective_user.id, p[1], p[2]
    k = _k(acc, did)
    items = cfs.list_backups(did)
    rows = [[btn("📥 Download Current JSON", f"bx|{k}")]]
    for ts, fn in items[:6]:
        dt_str = dt.datetime.fromtimestamp(ts, tz=config.LOCAL_TZ).strftime("%d/%m %H:%M EAT")
        rows.append([btn(f"♻️ Restore: {dt_str}", "brc|" + store.ref(uid, {"a": acc, "d": did, "f": fn}))])
    rows.append([btn("⬅️ Back", f"d|{k}")])
    await show(
        u, c,
        _crumb(acc, did, "💾 <b>Backup &amp; Restore</b>")
        + card(f"💾 Backups zilizopo: <b>{len(items)}</b> / {config.KEEP_BACKUPS}\nChagua backup ya kurudisha au pakua ya sasa:"),
        rows,
    )

@route("bx", slow=True)
async def h_bx(u, c, p):
    cfg = await _cfg(u, p[1], p[2])
    f = io.BytesIO(json.dumps(cfg, indent=2, default=str).encode())
    await u.effective_chat.send_document(document=f, filename=f"{p[2]}-backup.json",
                                         caption=f"💾 CloudFront Config Backup: <code>{p[2]}</code>", parse_mode="HTML")
    await h_bk(u, c, p)

@route("brc")
async def h_brc(u, c, p):
    d = tok_data(u, p[1])
    await show(u, c, _crumb(d["a"], d["d"], "♻️ <b>Restore</b>")
               + card("⚠️ Config ya sasa itabadilishwa na nakala hii (config ya sasa itahifadhiwa kwanza). Endelea?"),
               [[btn("✅ Ndiyo, Restore", f"bro|{p[1]}"), btn("❌ Hapana", f"bk|{_k(d['a'], d['d'])}")]])

@route("bro", slow=True)
async def h_bro(u, c, p):
    d = tok_data(u, p[1])
    uid = u.effective_user.id
    async with aws.lock(("dist", uid, d["d"])):
        await aws.run(cfs.restore, _cl(u, d["a"]), d["d"], d["f"])
    aws.invalidate(("cf_list", uid, d["a"]))
    store.add_watch(uid, "dist", d["a"], d["d"])
    store.audit(uid, d["a"], "CF_RESTORE", d["d"])
    await detail(u, c, d["a"], d["d"], "✅ Config imerudishwa.")
