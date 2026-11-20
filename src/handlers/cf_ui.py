"""CloudFront: list, detail, enable/disable, create, origins & ports, settings, backup/restore, delete."""
import asyncio, io, json, logging, datetime as dt
import storage as store
from core import aws
from core.router import route, flow
from core.ui import show, btn, nav, head, card, esc, gb, pct
from handlers.common import edit_dist, prompt, tok_data, clean_domain, valid_port, back_accounts
from services import cloudfront as cfs, metrics as ms

log = logging.getLogger(__name__)
PER = 8


def _k(acc, did): return f"{acc}|{did}"
def _crumb(acc, did, *more): return head("🏠 Home", f"👤 {esc(acc)}", f"☁️ <code>{did}</code>", *more)
def _cl(u, acc, svc="cloudfront", region="us-east-1"): return aws.client(u.effective_user.id, acc, svc, region)


# ======================= list =======================
@route("dl", slow=True)
async def h_dl(u, c, p):
    acc, page = p[1], int(p[2]) if len(p) > 2 else 0
    ds = await aws.run(cfs.all_dists, _cl(u, acc))
    rows = []
    for d in ds[page * PER:(page + 1) * PER]:
        icon = "🔴" if not d["Enabled"] else ("🟢" if d["Status"] == "Deployed" else "🟡")
        rows.append([btn(f"{icon} {d['Id']} · {cfs.dlabel(d)}"[:60], f"d|{acc}|{d['Id']}")])
    pager = ([btn("◀️", f"dl|{acc}|{page - 1}")] if page > 0 else []) + \
            ([btn("▶️", f"dl|{acc}|{page + 1}")] if (page + 1) * PER < len(ds) else [])
    if pager: rows.append(pager)
    rows += [[btn("➕ Create", f"cr|{acc}"), btn("📊 Usage", f"ua|{acc}")], nav(back_accounts(u, "dl"))]
    body = f"Distributions: <b>{len(ds)}</b>   🟢 live · 🟡 deploying · 🔴 off" if ds else "Hakuna distribution bado. Bonyeza ➕ Create."
    await show(u, c, head("🏠 Home", f"👤 {esc(acc)}", "☁️ CloudFront") + body, rows)


# ======================= detail =======================
def render_detail(acc, did, D, gbv=None, rates=None, note=""):
    cfg = D["DistributionConfig"]; on = cfg["Enabled"]; st = D["Status"]
    icon = "🔴" if not on else ("🟢" if st == "Deployed" else "🟡")
    orgs = cfs.origins(cfg); lines = [f"🌐 <b>Domain:</b> <code>{esc(D['DomainName'])}</code>"]
    for i, o in enumerate(orgs):
        tag = f"Origin {i + 1}" if len(orgs) > 1 else "Origin"
        co = o.get("CustomOriginConfig")
        if co:
            lines.append(f"🎯 <b>{tag}:</b> <code>{esc(o['DomainName'])}</code>\n"
                         f"🔌 <b>Port:</b> <code>{cfs.eff_port(co)}</code> ({co.get('OriginProtocolPolicy')})")
        else:
            lines.append(f"🎯 <b>{tag}:</b> <code>{esc(o['DomainName'])}</code> (S3/other)")
    al = cfs.aliases(cfg)
    lines.append(f"🔗 <b>Custom:</b> {esc(', '.join(al)) if al else '—'}")
    if al: lines.append(f"📌 <b>DNS:</b> CNAME → <code>{esc(D['DomainName'])}</code> (DNS only)")
    stat = f"📊 <b>Mwezi huu:</b> <code>{gb(gbv)}</code>"
    if rates and rates.get("e5") is not None: stat += f" · 5xx(1h): <code>{pct(rates['e5'])}</code>"
    lines.append(stat)
    text = (_crumb(acc, did) + f"{icon} <b>{esc(cfg.get('Comment') or did)}</b> · <i>{st}</i>"
            + ("" if on else " · OFF") + "\n\n" + card("\n".join(lines)))
    if st != "Deployed": text += "\n⏳ <i>Mabadiliko yanasambazwa (3-10 dk). Nitakutaarifu likikamilika.</i>"
    if note: text += f"\n{note}"
    return text


def detail_rows(acc, did, on):
    k = _k(acc, did)
    return [
        [btn("⏸ Disable" if on else "▶️ Enable", f"tg|{k}"), btn("🔄 Refresh", f"d|{k}")],
        [btn("🎯 Origin & EC2", f"or|{k}"), btn("⚙️ Settings", f"st|{k}")],
        [btn("🌍 Domains & SSL", f"cd|{k}"), btn("📊 Analytics", f"an|{k}")],
        [btn("🧹 Purge", f"pg|{k}"), btn("💾 Backup/Restore", f"bk|{k}")],
        [btn("🗑 Delete", f"rm|{k}"), btn("⬅️ Back", f"dl|{acc}|0")],
    ]


async def detail(u, c, acc, did, note=""):
    cl, cw = _cl(u, acc), _cl(u, acc, "cloudwatch")
    async def safe(fn, *a):
        try: return await aws.run(fn, *a)
        except Exception as e:
            log.info("stats unavailable: %s", e); return None
    (D, _), gbv, rates = await asyncio.gather(
        aws.run(cfs.get, cl, did), safe(ms.cf_month_gb, cw, did), safe(ms.cf_rates, cw, did, 60))
    await show(u, c, render_detail(acc, did, D, gbv, rates, note),
               detail_rows(acc, did, D["DistributionConfig"]["Enabled"]))


@route("d", slow=True)
async def h_d(u, c, p): await detail(u, c, p[1], p[2])


@route("tg", slow=True)
async def h_tg(u, c, p):
    await edit_dist(u, p[1], p[2], lambda cfg: cfg.__setitem__("Enabled", not cfg["Enabled"]))
    await detail(u, c, p[1], p[2], "✅ Imebadilishwa.")


# ======================= purge =======================
@route("pg")
async def h_pg(u, c, p):
    k = _k(p[1], p[2])
    await show(u, c, _crumb(p[1], p[2], "🧹 Purge") + "Futa cache yote (<code>/*</code>)?",
               [[btn("✅ Ndiyo", f"pgok|{k}"), btn("❌ Hapana", f"d|{k}")]])


@route("pgok", slow=True)
async def h_pgok(u, c, p):
    import time
    await aws.run(_cl(u, p[1]).create_invalidation, DistributionId=p[2],
                  InvalidationBatch={"Paths": {"Quantity": 1, "Items": ["/*"]}, "CallerReference": str(time.time())})
    await detail(u, c, p[1], p[2], "✅ Purge imetumwa.")


# ======================= delete =======================
@route("rm")
async def h_rm(u, c, p):
    k = _k(p[1], p[2])
    await show(u, c, _crumb(p[1], p[2], "🗑 Delete") + card(
        "Distribution lazima izimwe kwanza na iwe <b>Deployed</b> kabla ya kufutwa.\n"
        "Nitaizima sasa (ikiwa ipo ON) na kukutaarifu itakapokuwa tayari kufutwa."),
        [[btn("🗑 Endelea", f"rmok|{k}"), btn("❌ Hapana", f"d|{k}")]])


@route("rmok", slow=True)
async def h_rmok(u, c, p):
    acc, did, uid = p[1], p[2], u.effective_user.id
    cl = _cl(u, acc)
    D, etag = await aws.run(cfs.get, cl, did)
    if D["DistributionConfig"]["Enabled"]:
        await edit_dist(u, acc, did, lambda cfg: cfg.__setitem__("Enabled", False))
        store.add_watch(uid, "dist", acc, did, {"then": "delete"})
        return await show(u, c, _crumb(acc, did) + "⏳ Imezimwa. Nitakutaarifu itakapokuwa <b>Deployed</b> ili uifute.",
                          [[btn("🔄 Refresh", f"d|{_k(acc, did)}")], nav()])
    if D["Status"] != "Deployed":
        store.add_watch(uid, "dist", acc, did, {"then": "delete"})
        return await show(u, c, _crumb(acc, did) + "⏳ Bado <b>InProgress</b>. AWS haikubali kufuta sasa; nitakutaarifu ikiwa tayari.",
                          [[btn("🔄 Refresh", f"d|{_k(acc, did)}")], nav()])
    await aws.run(cl.delete_distribution, Id=did, IfMatch=etag)
    await show(u, c, f"✅ <code>{did}</code> imefutwa.", [[btn("⬅️ List", f"dl|{acc}|0")], nav()])


# ======================= create =======================
@route("cr")
async def h_cr(u, c, p):
    acc, uid = p[1], u.effective_user.id
    tok = store.ref(uid, {"m": "c", "a": acc})
    c.user_data["flow"] = {"t": "c1", "acc": acc}
    await show(u, c, head("🏠 Home", f"👤 {esc(acc)}", "➕ Create") + "1/3 Tuma <b>origin domain</b> (EC2 Public DNS):",
               [[btn("☁️ Pick from EC2", f"pe|{tok}")], [btn("✖️ Cancel", f"dl|{acc}|0")]])


@flow("c1")
async def f_c1(u, c, f, txt):
    dom = clean_domain(txt)
    if not dom: return await prompt(u, c, f, "❌ Domain si sahihi. Jaribu tena:", f"dl|{f['acc']}|0")
    await prompt(u, c, {"t": "c2", "acc": f["acc"], "origin": dom},
                 f"✅ Origin: <code>{esc(dom)}</code>\n2/3 Tuma <b>port</b> ya origin (mfano <code>8080</code>):", f"dl|{f['acc']}|0")


@flow("c2")
async def f_c2(u, c, f, txt):
    port = valid_port(txt)
    if not port: return await prompt(u, c, f, "❌ Port si sahihi. Jaribu tena:", f"dl|{f['acc']}|0")
    await prompt(u, c, {**f, "t": "c3", "port": port}, "3/3 Tuma <b>jina/label</b> ya distribution:", f"dl|{f['acc']}|0")


@flow("c3")
async def f_c3(u, c, f, txt):
    uid = u.effective_user.id
    r = await aws.run(_cl(u, f["acc"]).create_distribution,
                      DistributionConfig=cfs.build_cfg(f["origin"], f["port"], txt[:100]))
    did = r["Distribution"]["Id"]
    store.add_watch(uid, "dist", f["acc"], did)
    await detail(u, c, f["acc"], did, "✅ Imetengenezwa.")


# ======================= origins & ports =======================
async def _cfg(u, acc, did):
    return (await aws.run(_cl(u, acc).get_distribution_config, Id=did))["DistributionConfig"]


async def origin_view(u, c, acc, did, i, note=""):
    cfg = await _cfg(u, acc, did)
    o = cfs.origin_at(cfg, i); co = o.get("CustomOriginConfig"); k = f"{_k(acc, did)}|{i}"
    tok = store.ref(u.effective_user.id, {"m": "e", "a": acc, "d": did, "o": i})
    lines = [f"🎯 <b>Domain:</b> <code>{esc(o['DomainName'])}</code>"]
    rows = [[btn("✏️ Domain", f"od|{k}"), btn("☁️ Pick from EC2", f"pe|{tok}")]]
    if co:
        pol = co.get("OriginProtocolPolicy"); h_on = pol in ("http-only", "match-viewer"); s_on = pol in ("https-only", "match-viewer")
        lines += [f"🔒 <b>Protocol:</b> {pol}", f"🔌 <b>HTTP port:</b> <code>{co.get('HTTPPort')}</code>{' ✅' if h_on else ''}",
                  f"🔐 <b>HTTPS port:</b> <code>{co.get('HTTPSPort')}</code>{' ✅' if s_on else ''}"]
        rows += [[btn(f"🔌 HTTP {co.get('HTTPPort')}", f"ph|{k}"), btn(f"🔐 HTTPS {co.get('HTTPSPort')}", f"ps|{k}")],
                 [btn(f"🔒 Protocol: {pol} ▸", f"pp|{k}")]]
        lines.append("<i>✅ = port inayotumika kwa protocol ya sasa.</i>")
    else:
        lines.append("<i>S3/other origin: port/protocol hazihaririwi.</i>")
    rows.append(nav(f"or|{_k(acc, did)}" if len(cfs.origins(cfg)) > 1 else f"d|{_k(acc, did)}"))
    await show(u, c, _crumb(acc, did, f"🎯 Origin {i + 1}") + card("\n".join(lines)) + (f"\n{note}" if note else ""), rows)


@route("or", slow=True)
async def h_or(u, c, p):
    acc, did = p[1], p[2]
    cfg = await _cfg(u, acc, did); orgs = cfs.origins(cfg)
    if len(orgs) == 1: return await origin_view(u, c, acc, did, 0)
    rows = [[btn(f"{i + 1}. {o['DomainName']}"[:60], f"oe|{_k(acc, did)}|{i}")] for i, o in enumerate(orgs)]
    await show(u, c, _crumb(acc, did, "🎯 Origins") + f"Origins: <b>{len(orgs)}</b>. Chagua ya kuhariri:",
               rows + [nav(f"d|{_k(acc, did)}")])


@route("oe", slow=True)
async def h_oe(u, c, p): await origin_view(u, c, p[1], p[2], int(p[3]))


@route("od")
async def h_od(u, c, p):
    await prompt(u, c, {"t": "od", "acc": p[1], "did": p[2], "i": int(p[3])},
                 "🎯 Tuma <b>origin domain</b> mpya (DNS name):", f"oe|{_k(p[1], p[2])}|{p[3]}")


@flow("od")
async def f_od(u, c, f, txt):
    dom = clean_domain(txt)
    if not dom: return await prompt(u, c, f, "❌ Domain si sahihi. Jaribu tena:", f"oe|{_k(f['acc'], f['did'])}|{f['i']}")
    def fn(cfg): cfs.origin_at(cfg, f["i"])["DomainName"] = dom
    await edit_dist(u, f["acc"], f["did"], fn)
    await origin_view(u, c, f["acc"], f["did"], f["i"], "✅ Origin imebadilishwa.")


async def _port_prompt(u, c, p, field, label):
    await prompt(u, c, {"t": "port", "acc": p[1], "did": p[2], "i": int(p[3]), "field": field},
                 f"🔌 Tuma <b>{label} port</b> ya origin (mfano <code>{'8080' if field == 'HTTPPort' else '443'}</code>):",
                 f"oe|{_k(p[1], p[2])}|{p[3]}")


@route("ph")
async def h_ph(u, c, p): await _port_prompt(u, c, p, "HTTPPort", "HTTP")

@route("ps")
async def h_ps(u, c, p): await _port_prompt(u, c, p, "HTTPSPort", "HTTPS")


@flow("port")
async def f_port(u, c, f, txt):
    port = valid_port(txt)
    if not port: return await prompt(u, c, f, "❌ Port si sahihi. Jaribu tena:", f"oe|{_k(f['acc'], f['did'])}|{f['i']}")
    def fn(cfg): cfs.custom_of(cfs.origin_at(cfg, f["i"]))[f["field"]] = port
    await edit_dist(u, f["acc"], f["did"], fn)
    await origin_view(u, c, f["acc"], f["did"], f["i"], f"✅ {f['field']} = {port}")


@route("pp", slow=True)
async def h_pp(u, c, p):
    i = int(p[3])
    def fn(cfg):
        co = cfs.custom_of(cfs.origin_at(cfg, i))
        co["OriginProtocolPolicy"] = cfs.PROTOS[(cfs.PROTOS.index(co.get("OriginProtocolPolicy", "http-only")) + 1) % 3]
    await edit_dist(u, p[1], p[2], fn)
    await origin_view(u, c, p[1], p[2], i, "✅ Protocol imebadilishwa.")


# ======================= settings =======================
async def settings_view(u, c, acc, did, note=""):
    cfg = await _cfg(u, acc, did); k = _k(acc, did)
    vc, ck = cfs.viewer_code(cfg), cfs.cache_kind(cfg)
    comp = all(b.get("Compress") for b in cfs.behaviors(cfg))
    v6, hv, pr = cfg.get("IsIPV6Enabled"), cfg.get("HttpVersion", "http2"), cfg.get("PriceClass", "PriceClass_All")
    n = len(cfs.behaviors(cfg))
    text = (_crumb(acc, did, "⚙️ Settings") + card(
        f"🔒 Viewer: <code>{cfs.VIEWER[vc]}</code>\n⚡ Cache: <code>{ {'d': 'CachingDisabled', 'o': 'CachingOptimized'}.get(ck, 'custom') }</code>\n"
        f"🚀 HTTP: <code>{hv}</code> · 🗜 Compress: <code>{'on' if comp else 'off'}</code>\n"
        f"🌐 IPv6: <code>{'on' if v6 else 'off'}</code> · 💲 <code>{pr}</code>\n"
        f"<i>Viewer/Cache/Compress zinatumika kwa behaviors zote ({n}).</i>") + (f"\n{note}" if note else ""))
    mark = lambda on, t: ("✅ " if on else "") + t
    rows = [
        [btn(mark(vc == "h", "🔒 HTTPS only"), f"vp|{k}|h"), btn(mark(vc == "r", "↪️ Redirect"), f"vp|{k}|r"), btn(mark(vc == "a", "🔓 All"), f"vp|{k}|a")],
        [btn(mark(ck == "d", "⛔ No cache"), f"cp|{k}|d"), btn(mark(ck == "o", "⚡ Optimized"), f"cp|{k}|o")],
        [btn(f"🚀 HTTP: {hv} ▸", f"hv|{k}"), btn(f"🗜 Compress: {'on' if comp else 'off'}", f"cz|{k}")],
        [btn(f"🌐 IPv6: {'on' if v6 else 'off'}", f"v6|{k}"), btn(f"💲 {pr.replace('PriceClass_', '')} ▸", f"pc|{k}")],
        nav(f"d|{k}"),
    ]
    await show(u, c, text, rows)


@route("st", slow=True)
async def h_st(u, c, p): await settings_view(u, c, p[1], p[2])


async def _apply(u, c, p, fn, note):
    await edit_dist(u, p[1], p[2], fn)
    await settings_view(u, c, p[1], p[2], note)


@route("vp", slow=True)
async def h_vp(u, c, p): await _apply(u, c, p, lambda cfg: cfs.set_viewer(cfg, p[3]), "✅ Viewer protocol imebadilishwa.")

@route("cp", slow=True)
async def h_cp(u, c, p): await _apply(u, c, p, lambda cfg: cfs.set_cache(cfg, p[3]), "✅ Cache policy imebadilishwa.")

@route("hv", slow=True)
async def h_hv(u, c, p):
    def fn(cfg):
        cur = cfg.get("HttpVersion", "http2")
        cfg["HttpVersion"] = cfs.HTTPV[(cfs.HTTPV.index(cur) + 1) % 2] if cur in cfs.HTTPV else "http2"
    await _apply(u, c, p, fn, "✅ HTTP version imebadilishwa.")

@route("cz", slow=True)
async def h_cz(u, c, p):
    def fn(cfg): cfs.set_compress(cfg, not all(b.get("Compress") for b in cfs.behaviors(cfg)))
    await _apply(u, c, p, fn, "✅ Compression imebadilishwa.")

@route("v6", slow=True)
async def h_v6(u, c, p): await _apply(u, c, p, lambda cfg: cfg.__setitem__("IsIPV6Enabled", not cfg.get("IsIPV6Enabled")), "✅ IPv6 imebadilishwa.")

@route("pc", slow=True)
async def h_pc(u, c, p):
    def fn(cfg): cfg["PriceClass"] = cfs.PRICES[(cfs.PRICES.index(cfg.get("PriceClass", cfs.PRICES[0])) + 1) % 3]
    await _apply(u, c, p, fn, "✅ Price class imebadilishwa.")


# ======================= backup / restore =======================
@route("bk")
async def h_bk(u, c, p):
    k = _k(p[1], p[2]); n = len(cfs.list_backups(p[2]))
    await show(u, c, _crumb(p[1], p[2], "💾 Backup") + card(
        f"Nakala za kiotomatiki: <b>{n}</b> (mwisho {cfs.config.KEEP_BACKUPS} zinahifadhiwa; zinatengenezwa kabla ya kila mabadiliko)."),
        [[btn("📤 Export JSON", f"bx|{k}"), btn("♻️ Restore", f"br|{k}")], nav(f"d|{k}")])


@route("bx", slow=True)
async def h_bx(u, c, p):
    cfg = await _cfg(u, p[1], p[2])
    f = io.BytesIO(json.dumps(cfg, indent=2, default=str).encode())
    await u.effective_chat.send_document(document=f, filename=f"{p[2]}.json", caption=f"💾 Config ya {p[2]}")
    await h_bk(u, c, p)


@route("br")
async def h_br(u, c, p):
    uid, k = u.effective_user.id, _k(p[1], p[2])
    items = cfs.list_backups(p[2])[:8]
    rows = [[btn("♻️ " + dt.datetime.fromtimestamp(ts, dt.timezone.utc).strftime("%m-%d %H:%M UTC"),
                 "brc|" + store.ref(uid, {"a": p[1], "d": p[2], "f": f}))] for ts, f in items]
    await show(u, c, _crumb(p[1], p[2], "♻️ Restore") + ("Chagua nakala ya kurudisha:" if items else "Hakuna backups bado."),
               rows + [nav(f"bk|{k}")])


@route("brc")
async def h_brc(u, c, p):
    d = tok_data(u, p[1])
    await show(u, c, _crumb(d["a"], d["d"], "♻️ Restore") + "⚠️ Config ya sasa itabadilishwa na nakala hii (config ya sasa itahifadhiwa kwanza). Endelea?",
               [[btn("✅ Restore", f"bro|{p[1]}"), btn("❌ Hapana", f"br|{_k(d['a'], d['d'])}")]])


@route("bro", slow=True)
async def h_bro(u, c, p):
    d = tok_data(u, p[1])
    async with aws.lock(("dist", u.effective_user.id, d["d"])):
        await aws.run(cfs.restore, _cl(u, d["a"]), d["d"], d["f"])
    store.add_watch(u.effective_user.id, "dist", d["a"], d["d"])
    await detail(u, c, d["a"], d["d"], "✅ Config imerudishwa.")
