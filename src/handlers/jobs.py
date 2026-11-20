"""Background jobs: deploy/cert/EC2 watchers (30s) and proactive alerts (15 min / 1 h)."""
import json, time, logging, datetime as dt
from botocore.exceptions import ClientError
from telegram.error import TelegramError
import config
import storage as store
from core import aws
from core.ui import btn, mk, esc, bar, NOPREVIEW, pct
from handlers.common import ec2map_key, monitored
from services import cloudfront as cfs, ec2 as ec2s, metrics as ms

log = logging.getLogger(__name__)


async def notify(ctx, uid, text, rows=None):
    try:
        await ctx.bot.send_message(uid, text, parse_mode="HTML", reply_markup=mk(rows), link_preview_options=NOPREVIEW)
    except TelegramError as e:
        log.warning("notify failed: %s", e)


def _on(uid, key): return store.get_setting(uid, key, "1") == "1"


# ============ watchers (user-initiated operations) ============
async def watch_tick(ctx):
    ws = store.list_watches()
    if ws:
        await aws.gather_limited([_check(ctx, w) for w in ws], 8)


async def _check(ctx, w):
    uid, acc, kind, ref, ex = w["uid"], w["acc"], w["kind"], w["ref"], w["extra"]
    age = time.time() - w["ts"]
    try:
        if not store.has_account(uid, acc):
            return store.del_watch(w["id"])
        if kind == "dist":
            if age > 2 * 3600: return store.del_watch(w["id"])
            D, _ = await aws.run(cfs.get, aws.client(uid, acc), ref)
            if D["Status"] != "Deployed": return
            cfg = D["DistributionConfig"]
            text = f"✅ <b>{esc(cfg.get('Comment') or ref)}</b> (<code>{ref}</code>) imekamilika: <b>Deployed</b>."
            rows = [[btn("☁️ Open", f"d|{acc}|{ref}")]]
            if ex.get("then") == "delete" and not cfg["Enabled"]:
                text += "\nSasa unaweza kuifuta."; rows.insert(0, [btn("🗑 Futa sasa", f"rm|{acc}|{ref}")])
            await notify(ctx, uid, text, rows); store.del_watch(w["id"])
        elif kind == "cert":
            if age > 72 * 3600: return store.del_watch(w["id"])
            cert = (await aws.run(aws.client(uid, acc, "acm").describe_certificate, CertificateArn=ref))["Certificate"]
            st = cert["Status"]
            if st == "ISSUED":
                await notify(ctx, uid, f"🔐 Cheti cha <code>{esc(ex.get('dom', ''))}</code> sasa ni <b>ISSUED</b>. Unaweza kukiweka kama custom domain.",
                             [[btn("🌍 Domains & SSL", f"cd|{acc}|{ex.get('did', '')}")]] if ex.get("did") else None)
                store.del_watch(w["id"])
            elif st in ("FAILED", "VALIDATION_TIMED_OUT", "REVOKED", "INACTIVE"):
                await notify(ctx, uid, f"❌ Cheti cha <code>{esc(ex.get('dom', ''))}</code>: <b>{st}</b>. Omba upya.")
                store.del_watch(w["id"])
        elif kind == "ec2":
            if age > 1800: return store.del_watch(w["id"])
            region, iid = ref.split("|")
            inst, _ = await aws.run(ec2s.get_instance, aws.client(uid, acc, "ec2", region), region, iid)
            if inst["state"] != ex.get("want"): return
            tok = store.ref(uid, {"a": acc, "r": region, "i": iid})
            text = f"{ec2s.icon(inst['state'])} <b>{esc(inst['name'] or iid)}</b> sasa ni <b>{inst['state']}</b>."
            rows = [[btn("🖥 Open", f"ei|{tok}")]]
            if inst["state"] == "running" and inst["dns"]:
                text += f"\n🔗 <code>{inst['dns']}</code>\n<i>Public DNS inaweza kuwa imebadilika (bila Elastic IP).</i>"
                rows.insert(0, [btn("☁️ Set as CF origin", f"lk|{tok}")])
            await notify(ctx, uid, text, rows); store.del_watch(w["id"])
    except ClientError as e:
        if e.response["Error"]["Code"] in ("NoSuchDistribution", "InvalidInstanceID.NotFound", "ResourceNotFoundException"):
            store.del_watch(w["id"])
        else:
            log.warning("watch %s failed: %s", w["id"], e)
    except Exception:
        log.exception("watch %s failed", w["id"])


# ============ proactive alerts ============
async def health_tick(ctx):
    tasks = []
    for uid in store.all_users():
        for acc in store.accounts(uid):
            if _on(uid, "al_5xx"): tasks.append(_health_cf(ctx, uid, acc))
            if _on(uid, "al_ec2"): tasks.append(_health_ec2(ctx, uid, acc))
    if tasks: await aws.gather_limited(tasks, 4)


async def _health_cf(ctx, uid, acc):
    try:
        cw = aws.client(uid, acc, "cloudwatch")
        ds = [d for d in await aws.run(cfs.all_dists, aws.client(uid, acc)) if d["Enabled"] and d["Status"] == "Deployed"]
        for d in ds:
            e5, req = await aws.run(ms.cf_health, cw, d["Id"], 15)
            bad = e5 is not None and e5 >= 20 and req >= 20
            key = f"h5xx:{uid}:{acc}:{d['Id']}"
            was = store.get_state(key) == "bad"
            if bad and not was:
                store.set_state(key, "bad")
                await notify(ctx, uid, f"🚨 <b>5xx ni juu</b> kwenye <code>{d['Id']}</code> ({esc(cfs.dlabel(d))}): <b>{pct(e5)}</b> ya {int(req)} requests (dk 15).\n"
                                       "Backend (EC2/x-ui) huenda imekufa, au origin/port si sahihi.",
                             [[btn("☁️ Open", f"d|{acc}|{d['Id']}"), btn("📊 Analytics", f"an|{acc}|{d['Id']}")]])
            elif not bad and was:
                store.set_state(key, "ok")
                await notify(ctx, uid, f"✅ <code>{d['Id']}</code> imerudi kawaida (5xx chini).")
    except Exception:
        log.exception("cf health failed for %s", acc)


async def _health_ec2(ctx, uid, acc):
    for region in monitored(uid, acc):
        try:
            cl = aws.client(uid, acc, "ec2", region)
            items = await aws.run(ec2s.list_instances, cl, region)
            cur = {i["id"]: i["state"] for i in items}
            key = ec2map_key(uid, acc, region)
            try: prev = json.loads(store.get_state(key, "{}"))
            except ValueError: prev = {}
            names = {i["id"]: i["name"] or i["id"] for i in items}
            for iid, st in prev.items():
                now = cur.get(iid, "terminated")
                if st == "running" and now != "running":
                    tok = store.ref(uid, {"a": acc, "r": region, "i": iid})
                    await notify(ctx, uid, f"🔴 <b>EC2 imezimika:</b> {esc(names.get(iid, iid))} (<code>{iid}</code>, {region}) → <b>{now}</b>.",
                                 [[btn("🖥 Open", f"ei|{tok}")]] if now != "terminated" else None)
            store.set_state(key, json.dumps(cur))
            cw = aws.client(uid, acc, "cloudwatch", region)
            for i in items:
                if i["state"] != "running": continue
                cpu = await aws.run(ms.ec2_cpu_now, cw, i["id"])
                ck = f"cpu:{uid}:{acc}:{i['id']}"
                if cpu is not None and cpu >= 90 and not store.get_state(ck):
                    store.set_state(ck, "1")
                    tok = store.ref(uid, {"a": acc, "r": region, "i": i["id"]})
                    await notify(ctx, uid, f"🔥 <b>CPU juu:</b> {esc(i['name'] or i['id'])} <b>{cpu:.0f}%</b>.", [[btn("📊 Metrics", f"em|{tok}")]])
                elif cpu is not None and cpu < 80 and store.get_state(ck):
                    store.del_state(ck)
        except Exception:
            log.exception("ec2 health failed for %s/%s", acc, region)


async def usage_tick(ctx):
    month = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m")
    for uid in store.all_users():
        if not _on(uid, "al_usage"): continue
        for acc in store.accounts(uid):
            try:
                cw = aws.client(uid, acc, "cloudwatch")
                ds = await aws.run(cfs.all_dists, aws.client(uid, acc))
                async def one(d):
                    try: return await aws.run(ms.cf_month_gb, cw, d["Id"])
                    except Exception: return 0.0
                tot = sum(await aws.gather_limited([one(d) for d in ds], 8))
                pc = tot / config.FREE_TIER_GB * 100
                for th in sorted(config.USAGE_ALERT_PCTS, reverse=True):
                    if pc >= th:
                        key = f"usage{th}:{uid}:{acc}:{month}"
                        if not store.get_state(key):
                            for t2 in config.USAGE_ALERT_PCTS:
                                if t2 <= th: store.set_state(f"usage{t2}:{uid}:{acc}:{month}", "1")
                            await notify(ctx, uid, f"📈 <b>{esc(acc)}</b>: matumizi <b>{pc:.0f}%</b> ya free tier\n<code>{bar(pc)}</code> {tot:,.0f} / {config.FREE_TIER_GB} GB\n"
                                                   "Gawa trafiki kwenye account/CDN nyingine kabla haijaisha.",
                                         [[btn("📊 Usage", f"ua|{acc}")]])
                        break
            except Exception:
                log.exception("usage check failed for %s", acc)
