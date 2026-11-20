"""Custom domain (alias) + ACM certificate management."""
import storage as store
from core import aws
from core.router import route, flow
from core.ui import show, btn, nav, card, esc
from handlers.common import edit_dist, prompt, tok_data, clean_domain
from handlers.cf_ui import detail, _crumb, _k
from services import cloudfront as cfs


def _acm(u, acc): return aws.client(u.effective_user.id, acc, "acm", "us-east-1")


@route("cd", slow=True)
async def h_cd(u, c, p):
    acc, did, uid = p[1], p[2], u.effective_user.id
    certs = await aws.run(cfs.issued_certs, _acm(u, acc))
    D, _ = await aws.run(cfs.get, aws.client(uid, acc), did)
    al = cfs.aliases(D["DistributionConfig"])
    rows = [[btn(f"🔐 {x['DomainName']}"[:55], "cc|" + store.ref(uid, {"a": acc, "d": did, "arn": x["CertificateArn"]}))]
            for x in certs[:8]]
    k = _k(acc, did)
    rows += [[btn("➕ Request cert", f"cn|{k}"), btn("❌ Remove domain", f"cdr|{k}")], nav(f"d|{k}")]
    body = card(f"🔗 Sasa: {esc(', '.join(al)) if al else '—'}") + "\nChagua cheti (ACM us-east-1, ISSUED) kinachofunika domain yako:"
    if not certs: body += "\n<i>Hakuna cheti ISSUED. Bonyeza ➕ Request cert.</i>"
    await show(u, c, _crumb(acc, did, "🌍 Domains & SSL") + body, rows)


@route("cc")
async def h_cc(u, c, p):
    d = tok_data(u, p[1])
    await prompt(u, c, {"t": "alias", "acc": d["a"], "did": d["d"], "arn": d["arn"]},
                 "🌍 Tuma domain(s) (zaidi ya moja tenganisha kwa koma). Mfano: <code>cdn.domain.com</code>",
                 f"cd|{_k(d['a'], d['d'])}")


@flow("alias")
async def f_alias(u, c, f, txt):
    import re
    doms = [clean_domain(x) for x in re.split(r"[,\s]+", txt) if x]
    if not doms or None in doms:
        return await prompt(u, c, f, "❌ Domain si sahihi. Jaribu tena:", f"cd|{_k(f['acc'], f['did'])}")
    await edit_dist(u, f["acc"], f["did"], lambda cfg: cfs.set_aliases(cfg, doms, f["arn"]))
    await detail(u, c, f["acc"], f["did"], "✅ Domain imewekwa. Weka CNAME kwenye DNS (DNS only).")


@route("cn")
async def h_cn(u, c, p):
    await prompt(u, c, {"t": "cert", "acc": p[1], "did": p[2]},
                 "➕ Tuma domain ya cheti (mfano <code>cdn.domain.com</code> au <code>*.domain.com</code>):",
                 f"cd|{_k(p[1], p[2])}")


@flow("cert")
async def f_cert(u, c, f, txt):
    dom = txt.lower().strip()
    if "." not in dom or " " in dom:
        return await prompt(u, c, f, "❌ Domain si sahihi. Jaribu tena:", f"cd|{_k(f['acc'], f['did'])}")
    arn, rec = await aws.run(cfs.request_cert, _acm(u, f["acc"]), dom)
    store.add_watch(u.effective_user.id, "cert", f["acc"], arn, {"did": f["did"], "dom": dom})
    body = ("Weka record hii kwenye DNS (<b>DNS only</b>):\n" +
            card(f"Type: <code>{rec['Type']}</code>\nName: <code>{esc(rec['Name'])}</code>\nValue: <code>{esc(rec['Value'])}</code>")
            if rec else "Record bado haijatoka. Fungua ACM baadaye.")
    await show(u, c, _crumb(f["acc"], f["did"], "🌍 Cheti") + f"✅ Cheti cha <code>{esc(dom)}</code> kimeombwa.\n\n{body}\n"
               "🔔 Nitakutaarifu kikiwa <b>ISSUED</b>.", [[btn("🌍 Domains & SSL", f"cd|{_k(f['acc'], f['did'])}")], nav()])


@route("cdr", slow=True)
async def h_cdr(u, c, p):
    await edit_dist(u, p[1], p[2], cfs.clear_aliases)
    await detail(u, c, p[1], p[2], "✅ Custom domain imeondolewa.")
