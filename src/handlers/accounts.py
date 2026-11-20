"""Link / remove AWS accounts (any number)."""
import re
import boto3
import storage as store
from core import aws
from core.router import route, flow
from core.ui import show, btn, nav, head, card, esc
from handlers.common import prompt


@route("acc")
async def h_acc(u, c, p):
    names = store.accounts(u.effective_user.id)
    rows = [[btn(f"👤 {n}", f"ai|{n}"), btn("🗑", f"ad|{n}")] for n in names]
    await show(u, c, head("🏠 Home", "👤 Accounts") + f"Zilizounganishwa: <b>{len(names)}</b> (hakuna kikomo).",
               rows + [[btn("➕ Add account", "aa")], nav()])


@route("aa")
async def h_aa(u, c, p):
    await prompt(u, c, {"t": "aname"}, head("🏠 Home", "👤 Accounts", "➕ New") +
                 "Tuma <b>jina fupi</b> la account (herufi/namba/_, max 12). Mfano: <code>acc1</code>", "acc")


def _perms(uid, acc):
    import datetime
    now = datetime.datetime.now(datetime.timezone.utc)
    checks = {
        "CloudFront": lambda: aws.client(uid, acc).list_distributions(MaxItems="1"),
        "ACM": lambda: aws.client(uid, acc, "acm").list_certificates(MaxItems=1),
        "CloudWatch": lambda: aws.client(uid, acc, "cloudwatch").get_metric_statistics(
            Namespace="AWS/CloudFront", MetricName="Requests", StartTime=now - datetime.timedelta(minutes=5),
            EndTime=now, Period=60, Statistics=["Sum"],
            Dimensions=[{"Name": "DistributionId", "Value": "X"}, {"Name": "Region", "Value": "Global"}]),
        "EC2": lambda: aws.client(uid, acc, "ec2").describe_regions(),
    }
    out = {}
    for k, fn in checks.items():
        try: fn(); out[k] = True
        except Exception: out[k] = False
    me = aws.client(uid, acc, "sts").get_caller_identity()["Account"]
    return me, out


@route("ai", slow=True)
async def h_ai(u, c, p):
    me, out = await aws.run(_perms, u.effective_user.id, p[1])
    lines = "\n".join(f"{'✅' if v else '❌'} {k}" for k, v in out.items())
    note = "" if all(out.values()) else "\n<i>❌ = ruhusa haipo (CloudFront/ACM/CloudWatch: iam/cloudfront.json, EC2: iam/ec2.json)</i>"
    await show(u, c, head("🏠 Home", "👤 Accounts", f"👤 {esc(p[1])}") +
               card(f"AWS ID: <code>{me}</code>\n{lines}") + note,
               [[btn("☁️ CloudFront", f"dl|{p[1]}|0"), btn("🖥 EC2", f"ec|{p[1]}")], nav("acc")])


@route("ad")
async def h_ad(u, c, p):
    await show(u, c, f"🗑 Futa account <b>{esc(p[1])}</b> kwenye bot? (AWS haiguswi)",
               [[btn("✅ Ndiyo", f"adok|{p[1]}"), btn("❌ Hapana", "acc")]])


@route("adok")
async def h_adok(u, c, p):
    uid = u.effective_user.id
    store.del_account(uid, p[1]); aws.forget(uid, p[1])
    await h_acc(u, c, p)


@flow("aname")
async def f_aname(u, c, f, txt):
    uid = u.effective_user.id
    if not re.fullmatch(r"\w{1,12}", txt) or store.has_account(uid, txt):
        return await prompt(u, c, f, "❌ Jina si sahihi au lipo tayari. Jaribu jingine:", "acc")
    await prompt(u, c, {"t": "aak", "name": txt},
                 "🔑 Tuma <b>Access Key ID</b>\n<i>Tumia IAM user mwenye ruhusa chache. Ujumbe wako utafutwa.</i>", "acc")


@flow("aak")
async def f_aak(u, c, f, txt):
    await prompt(u, c, {"t": "ask", "name": f["name"], "ak": txt},
                 "🔑 Tuma <b>Secret Access Key</b> (itafutwa baada ya kutumwa):", "acc")


@flow("ask")
async def f_ask(u, c, f, txt):
    uid = u.effective_user.id
    sts = boto3.client("sts", aws_access_key_id=f["ak"], aws_secret_access_key=txt, region_name="us-east-1")
    me = await aws.run(sts.get_caller_identity)
    store.add_account(uid, f["name"], f["ak"], txt)
    aws.forget(uid, f["name"])
    await show(u, c, f"✅ <b>{esc(f['name'])}</b> imeunganishwa (AWS <code>{me['Account']}</code>)",
               [[btn("🔎 Check permissions", f"ai|{f['name']}")], [btn("➕ Add nyingine", "aa")], nav("acc")])
