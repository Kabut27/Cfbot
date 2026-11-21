"""Link / inspect / check permissions / remove AWS accounts (Fernet AES + Free Tier expiry badge)."""
import re, datetime as dt
import boto3
import storage as store
from core import aws
from core.router import route, flow
from core.ui import show, btn, nav, head, card, tree_card, esc
from handlers.common import prompt, cf_list_cached
from services import cloudfront as cfs


@route("acc")
async def h_acc(u, c, p):
    uid = u.effective_user.id
    accs = store.accounts_meta(uid)
    rows = []
    for name, info in accs.items():
        ex = cfs.calc_ft_expiry(info["opened_at"], info["ft_months"])
        tag = f" · {ex['badge']}{max(0, ex['days_left'])}d" if ex else ""
        rows.append([btn(f"👤 {name} ({info['region']}){tag}"[:45], f"ai|{name}"), btn("🗑", f"ad|{name}")])
    rows += [[btn("➕ Add Account", "aa")], nav()]
    await show(
        u, c,
        head("🏠 <b>Home</b>", f"👤 <b>Accounts ({len(accs)})</b>")
        + card("🔒 Keys zote zimesimbwa kwa <b>Fernet AES</b> ndani ya SQLite."),
        rows,
    )


@route("aa")
async def h_aa(u, c, p):
    await prompt(
        u, c, {"t": "aname"},
        "➕ <b>Account Mpya ya AWS</b>\n" + "━" * 20 + "\n"
        + card("Tuma jina fupi (herufi/namba/_, max 15).\nMfano: <code>aws_main</code>"),
        "acc",
    )


def _inspect_acc(uid, acc):
    now = dt.datetime.now(dt.timezone.utc)
    checks = {
        "CloudFront": lambda: aws.client(uid, acc).list_distributions(MaxItems="1"),
        "ACM": lambda: aws.client(uid, acc, "acm").list_certificates(MaxItems=1),
        "CloudWatch": lambda: aws.client(uid, acc, "cloudwatch").get_metric_statistics(
            Namespace="AWS/CloudFront", MetricName="Requests",
            StartTime=now - dt.timedelta(minutes=5), EndTime=now, Period=60, Statistics=["Sum"],
            Dimensions=[{"Name": "DistributionId", "Value": "X"}, {"Name": "Region", "Value": "Global"}],
        ),
        "EC2": lambda: aws.client(uid, acc, "ec2").describe_regions(),
    }
    out = {}
    for k, fn in checks.items():
        try: fn(); out[k] = True
        except Exception: out[k] = False
    me = aws.client(uid, acc, "sts").get_caller_identity()
    return me, out


@route("ai", slow=True)
async def h_ai(u, c, p):
    uid, acc = u.effective_user.id, p[1]
    me, perms = await aws.run(_inspect_acc, uid, acc)
    try:
        ds_cnt = len(await cf_list_cached(uid, acc))
    except Exception:
        ds_cnt = 0
    info = store.account_meta(uid, acc)
    ex = cfs.calc_ft_expiry(info["opened_at"], info["ft_months"])
    ft_str = (
        '"EXPIRED"  # ' + ex["end"] if ex["days_left"] <= 0 else f'{ex["days_left"]}d left  # → {ex["end"]}'
    ) if ex else '"haijawekwa"'
    tcard = tree_card([
        ("Account", f'"{acc}"'),
        ("AWS_ID", f'"{me["Account"]}"'),
        ("IAM_User", f'"{me["Arn"].split(":")[-1]}"'),
        ("CloudFront", f"{ds_cnt} distributions"),
        ("EC2_Reg", f'"{info["region"]}"'),
        ("FT_Expiry", ft_str),
    ])
    p_lines = " · ".join(f"{'✅' if v else '❌'} {k}" for k, v in perms.items())
    note = "" if all(perms.values()) else "\n<i>❌ = ruhusa haipo (angalia iam/cloudfront.json &amp; iam/ec2.json)</i>"
    await show(
        u, c,
        head("🏠 <b>Home</b>", f"👤 <b>{esc(acc)}</b>") + tcard + "\n\n" + card(f"🔎 <b>IAM Permissions:</b>\n{p_lines}") + note,
        [
            [btn("☁️ CloudFront", f"dl|{acc}|0"), btn("🖥 EC2 Servers", f"ec|{acc}")],
            [btn("🛡 Free Tier & Muda", f"ft|{acc}"), btn("🔎 Scan EC2", f"es|{acc}")],
            [btn("⬅️ Accounts", "acc"), btn("🏠 Menu Kuu", "home")],
        ],
    )


@route("ad")
async def h_ad(u, c, p):
    await show(
        u, c,
        f"🗑 <b>Futa account <code>{esc(p[1])}</code> kwenye bot?</b>\n"
        + card("ℹ️ Akaunti yako ya AWS haiguswi — keys zinaondolewa kwenye bot pekee."),
        [[btn("✅ Ndiyo, Futa", f"adok|{p[1]}"), btn("❌ Hapana", "acc")]],
    )


@route("adok")
async def h_adok(u, c, p):
    uid, acc = u.effective_user.id, p[1]
    store.del_account(uid, acc)
    aws.forget(uid, acc)
    store.audit(uid, acc, "ACCOUNT_DEL", "Removed from bot")
    await h_acc(u, c, p)


@flow("aname")
async def f_aname(u, c, f, txt):
    uid = u.effective_user.id
    if not re.fullmatch(r"\w{1,15}", txt) or store.has_account(uid, txt):
        return await prompt(u, c, f, "❌ Jina si sahihi au lipo tayari (herufi/namba/_, max 15). Jaribu jingine:", "acc")
    await prompt(u, c, {"t": "aak", "name": txt},
                 "🔑 Tuma <b>AWS Access Key ID</b> (ujumbe utafutwa mara moja):", "acc")


@flow("aak")
async def f_aak(u, c, f, txt):
    await prompt(u, c, {"t": "ask", "name": f["name"], "ak": txt},
                 "🔑 Tuma <b>AWS Secret Access Key</b> (ujumbe utafutwa mara moja):", "acc")


@flow("ask")
async def f_ask(u, c, f, txt):
    uid = u.effective_user.id
    sts = boto3.client("sts", aws_access_key_id=f["ak"], aws_secret_access_key=txt, region_name="us-east-1")
    me = await aws.run(sts.get_caller_identity)
    auto_opened = await aws.run(cfs.try_account_created_date, f["ak"], txt)
    store.add_account(uid, f["name"], f["ak"], txt, opened_at=auto_opened)
    aws.forget(uid, f["name"])
    store.audit(uid, f["name"], "ACCOUNT_ADD", f"AWS {me['Account']}")
    extra = f"\n📅 Ilifunguliwa: <code>{auto_opened}</code>" if auto_opened else ""
    await show(
        u, c,
        f"✅ <b>Account <code>{esc(f['name'])}</code> imeunganishwa!</b>\n"
        + card(f"🆔 AWS ID: <code>{me['Account']}</code>{extra}"),
        [
            [btn("☁️ CloudFront", f"dl|{f['name']}|0"), btn("🖥 EC2", f"ec|{f['name']}")],
            [btn("🔎 Check Permissions", f"ai|{f['name']}"), btn("🛡 Free Tier", f"ft|{f['name']}")],
            nav(),
        ],
    )
