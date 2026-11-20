"""EC2 <-> CloudFront linking: set a CF origin from an EC2 instance, or pick an EC2 from CF."""
import storage as store
from core import aws
from core.router import route
from core.ui import show, btn, nav, head, card, esc
from handlers.common import tok_data, ec2c, edit_dist, prompt, region_buttons
from handlers import cf_ui
from services import cloudfront as cfs, ec2 as ec2s


def _h(*more): return head("🏠 Home", "🔗 Link EC2 ↔ CloudFront", *more)


# ======================= EC2 -> CF =======================
@route("lk", slow=True)
async def h_lk(u, c, p):
    d, uid = tok_data(u, p[1]), u.effective_user.id
    i, _ = await aws.run(ec2s.get_instance, ec2c(u, d), d["r"], d["i"])
    if not i["dns"]:
        raise ValueError("Instance haina Public DNS (imezimwa au haina public IP / DNS hostnames).")
    tok = store.ref(uid, {"dns": i["dns"], "n": i["name"] or i["id"], "back": p[1]})
    names = store.accounts(uid)
    if len(names) == 1: return await lkd(u, c, tok, names[0], 0)
    await show(u, c, _h("Chagua account ya CloudFront") + f"Origin mpya: <code>{esc(i['dns'])}</code>",
               [[btn(f"👤 {n}", f"lkd|{tok}|{n}|0")] for n in names] + [nav(f"ei|{p[1]}")])


async def lkd(u, c, tok, acc, page):
    d = tok_data(u, tok)
    ds = await aws.run(cfs.all_dists, aws.client(u.effective_user.id, acc))
    rows = [[btn(f"{'🔴' if not x['Enabled'] else '🟢'} {x['Id']} · {cfs.dlabel(x)}"[:60], f"lko|{tok}|{acc}|{x['Id']}")] for x in ds[:12]]
    await show(u, c, _h(f"👤 {esc(acc)}") + f"Origin mpya: <code>{esc(d['dns'])}</code>\nChagua distribution:" if ds else _h() + "Hakuna distributions.",
               rows + [nav(f"ei|{d['back']}")])


@route("lkd", slow=True)
async def h_lkd(u, c, p): await lkd(u, c, p[1], p[2], int(p[3]))


@route("lko", slow=True)
async def h_lko(u, c, p):
    tok, acc, did = p[1], p[2], p[3]
    cfg = await cf_ui._cfg(u, acc, did); orgs = cfs.origins(cfg)
    if len(orgs) == 1: return await lkc(u, c, tok, acc, did, 0)
    await show(u, c, _h(f"☁️ {did}") + "Chagua origin ya kubadilisha:",
               [[btn(f"{i + 1}. {o['DomainName']}"[:60], f"lkc|{tok}|{acc}|{did}|{i}")] for i, o in enumerate(orgs)] + [nav(f"lkd|{tok}|{acc}|0")])


async def lkc(u, c, tok, acc, did, i):
    d = tok_data(u, tok)
    cfg = await cf_ui._cfg(u, acc, did); old = cfs.origin_at(cfg, i)["DomainName"]
    await show(u, c, _h(f"☁️ {did}") + card(f"🎯 Origin {i + 1}\n❌ <code>{esc(old)}</code>\n✅ <code>{esc(d['dns'])}</code>") +
               "\nBadilisha? (backup ya config inahifadhiwa kwanza)",
               [[btn("✅ Badilisha", f"lkx|{tok}|{acc}|{did}|{i}"), btn("❌ Hapana", f"lkd|{tok}|{acc}|0")]])


@route("lkc", slow=True)
async def h_lkc(u, c, p): await lkc(u, c, p[1], p[2], p[3], int(p[4]))


@route("lkx", slow=True)
async def h_lkx(u, c, p):
    d, acc, did, i = tok_data(u, p[1]), p[2], p[3], int(p[4])
    def fn(cfg): cfs.origin_at(cfg, i)["DomainName"] = d["dns"]
    await edit_dist(u, acc, did, fn)
    await cf_ui.detail(u, c, acc, did, f"✅ Origin → <code>{esc(d['dns'])}</code>")


# ======================= CF -> EC2 (also used by Create) =======================
def _tg_back(tg):
    return f"cr|{tg['a']}" if tg["m"] == "c" else f"oe|{tg['a']}|{tg['d']}|{tg['o']}"


@route("pe")
async def h_pe(u, c, p):
    tg, uid = tok_data(u, p[1]), u.effective_user.id
    names = store.accounts(uid)
    if len(names) == 1: return await peg(u, c, p[1], names[0])
    await show(u, c, _h("☁️ Pick from EC2") + "Chagua account ya EC2:",
               [[btn(f"👤 {n}", f"peg|{p[1]}|{n}")] for n in names] + [nav(_tg_back(tg))])


async def peg(u, c, tokT, acc):
    tg = tok_data(u, tokT)
    await show(u, c, _h(f"👤 {esc(acc)}") + "Chagua region (⭐ = ina instances):",
               region_buttons(u.effective_user.id, acc, f"pel|{tokT}|{acc}|{{r}}") + [nav(_tg_back(tg))])


@route("peg")
async def h_peg(u, c, p): await peg(u, c, p[1], p[2])


@route("pel", slow=True)
async def h_pel(u, c, p):
    tokT, acc, region, uid = p[1], p[2], p[3], u.effective_user.id
    tg = tok_data(u, tokT)
    items = [i for i in await aws.run(ec2s.list_instances, aws.client(uid, acc, "ec2", region), region) if i["dns"] and i["state"] == "running"]
    rows = [[btn(f"{ec2s.icon(i['state'])} {i['name'] or i['id']} · {i['type']}"[:60],
                 "pi|" + store.ref(uid, {"tg": tg, "dns": i["dns"], "n": i["name"] or i["id"]}))] for i in items[:15]]
    await show(u, c, _h(f"📍 {ec2s.rname(region)}") + ("Chagua instance (running + ina Public DNS):" if items else "Hakuna instance ya running yenye Public DNS hapa."),
               rows + [nav(f"peg|{tokT}|{acc}")])


@route("pi", slow=True)
async def h_pi(u, c, p):
    d = tok_data(u, p[1]); tg = d["tg"]
    if tg["m"] == "c":
        return await prompt(u, c, {"t": "c2", "acc": tg["a"], "origin": d["dns"]},
                            f"✅ Origin: <code>{esc(d['dns'])}</code>\n2/3 Tuma <b>port</b> ya origin (mfano <code>8080</code>):", f"dl|{tg['a']}|0")
    cfg = await cf_ui._cfg(u, tg["a"], tg["d"]); old = cfs.origin_at(cfg, tg["o"])["DomainName"]
    await show(u, c, _h(f"☁️ {tg['d']}") + card(f"🎯 Origin {tg['o'] + 1}\n❌ <code>{esc(old)}</code>\n✅ <code>{esc(d['dns'])}</code> ({esc(d['n'])})") + "\nBadilisha?",
               [[btn("✅ Badilisha", f"pix|{p[1]}"), btn("❌ Hapana", f"oe|{tg['a']}|{tg['d']}|{tg['o']}")]])


@route("pix", slow=True)
async def h_pix(u, c, p):
    d = tok_data(u, p[1]); tg = d["tg"]
    def fn(cfg): cfs.origin_at(cfg, tg["o"])["DomainName"] = d["dns"]
    await edit_dist(u, tg["a"], tg["d"], fn)
    await cf_ui.origin_view(u, c, tg["a"], tg["d"], tg["o"], f"✅ Origin → <code>{esc(d['dns'])}</code>")
