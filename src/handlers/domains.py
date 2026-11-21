"""Custom domain (alias) + ACM certificate management (Issued + Pending DNS CNAME viewer)."""
import re
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
    certs, pending = await aws.run(cfs.list_certs, _acm(u, acc))
    rows = [[btn(f"🔐 {x['DomainName']}"[:55], "cc|" + store.ref(uid, {"a": acc, "d": did, "arn": x["CertificateArn"]}))]
            for x in certs[:10]]
    for x in pending[:5]:
        rows.append([btn(f"⏳ Pending: {x['DomainName']}"[:55],
                         "cpv|" + store.ref(uid, {"a": acc, "d": did, "arn": x["CertificateArn"]}))])
    k = _k(acc, did)
    rows += [
        [btn("➕ Request Cert", f"cn|{k}"), btn("❌ Remove Custom", f"cdr|{k}")],
        [btn("⬅️ Back", f"d|{k}")],
    ]
    body = card(
        f"🔐 <b>Issued:</b> <code>{len(certs)}</code> · ⏳ <b>Pending:</b> <code>{len(pending)}</code>\n"
        "Chagua cheti kilichothibitishwa (🔐) au kagua kinachosubiri (⏳):"
    )
    await show(u, c, _crumb(acc, did, "🌍 <b>Domains &amp; SSL</b>") + body, rows)


@route("cpv", slow=True)
async def h_cpv(u, c, p):
    d = tok_data(u, p[1])
    cert = (await aws.run(_acm(u, d["a"]).describe_certificate, CertificateArn=d["arn"]))["Certificate"]
    dv = (cert.get("DomainValidationOptions") or [{}])[0]
    rec = dv.get("ResourceRecord")
    msg = f"⏳ <b>Cheti:</b> <code>{esc(cert.get('DomainName'))}</code>\nHali: <b>{cert.get('Status')}</b>\n\n"
    if rec:
        msg += (
            "Weka CNAME hii kwenye DNS (Cloudflare: <b>DNS only</b>):\n"
            + card(f"╭ Type: <code>{rec['Type']}</code>\n├ Name: <code>{esc(rec['Name'])}</code>\n╰ Value: <code>{esc(rec['Value'])}</code>")
        )
    else:
        msg += card("<i>Record bado inatengenezwa AWS. Jaribu tena baada ya sekunde chache.</i>")
    await show(u, c, msg, [[btn("⬅️ Back", f"cd|{_k(d['a'], d['d'])}")]])


@route("cc")
async def h_cc(u, c, p):
    d = tok_data(u, p[1])
    await prompt(u, c, {"t": "alias", "acc": d["a"], "did": d["d"], "arn": d["arn"]},
                 "🌍 Tuma custom domain(s) (tenganisha kwa koma), mfano: <code>cdn.domain.com</code>:",
                 f"cd|{_k(d['a'], d['d'])}")


@flow("alias")
async def f_alias(u, c, f, txt):
    doms = [clean_domain(x) for x in re.split(r"[,\s]+", txt) if x]
    if not doms or None in doms:
        return await prompt(u, c, f, "❌ Domain si sahihi. Jaribu tena:", f"cd|{_k(f['acc'], f['did'])}")
    await edit_dist(u, f["acc"], f["did"], lambda cfg: cfs.set_aliases(cfg, doms, f["arn"]),
                    audit_act="CF_ALIAS", audit_det=",".join(doms[:2]))
    await detail(u, c, f["acc"], f["did"], "✅ Domain imewekwa. Weka CNAME kwenye DNS (DNS only).")


@route("cn")
async def h_cn(u, c, p):
    await prompt(u, c, {"t": "cert", "acc": p[1], "did": p[2]},
                 "➕ Tuma domain ya cheti cha ACM (mfano <code>cdn.domain.com</code> au <code>*.domain.com</code>):",
                 f"cd|{_k(p[1], p[2])}")


@flow("cert")
async def f_cert(u, c, f, txt):
    dom = txt.lower().strip()
    if "." not in dom or " " in dom:
        return await prompt(u, c, f, "❌ Domain si sahihi. Jaribu tena:", f"cd|{_k(f['acc'], f['did'])}")
    arn, rec = await aws.run(cfs.request_cert, _acm(u, f["acc"]), dom)
    store.add_watch(u.effective_user.id, "cert", f["acc"], arn, {"did": f["did"], "dom": dom})
    store.audit(u.effective_user.id, f["acc"], "ACM_REQ", dom)
    body = (
        "Weka CNAME hii kwenye DNS (Cloudflare: <b>DNS only</b>):\n"
        + card(f"╭ Type: <code>{rec['Type']}</code>\n├ Name: <code>{esc(rec['Name'])}</code>\n╰ Value: <code>{esc(rec['Value'])}</code>")
        if rec else card("<i>Record bado inatengenezwa, fungua Domains &amp; SSL → Pending baada ya sekunde chache.</i>")
    )
    await show(u, c, _crumb(f["acc"], f["did"], "🌍 <b>Cheti</b>")
               + f"✅ Cheti cha <code>{esc(dom)}</code> kimeombwa!\n\n{body}\n🔔 Bot itakujulisha kikiwa <b>ISSUED</b>.",
               [[btn("🌍 Domains & SSL", f"cd|{_k(f['acc'], f['did'])}")], nav()])


@route("cdr", slow=True)
async def h_cdr(u, c, p):
    await edit_dist(u, p[1], p[2], cfs.clear_aliases, audit_act="CF_RM_ALIAS")
    await detail(u, c, p[1], p[2], "✅ Custom domain imeondolewa.")
