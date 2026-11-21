"""Background jobs (v4 Pro):
- watch_tick (30s): CF Deployed, ACM Cert ISSUED, EC2 state changes
- health_tick (15m): Smart 5xx Auto-Failover, 5xx alerts, Multi-Region EC2 down/CPU alerts,
  Free Tier expiry countdown alerts, Daily Morning Digest (EAT), Weekly Encrypted DB Auto-Backup
- usage_tick (30m): CloudFront Data & Requests threshold alerts + Live USD Bill Watchdog."""
import io, json, time, logging, datetime as dt
from botocore.exceptions import ClientError
from telegram.error import TelegramError
import config
import storage as store
from core import aws
from core.ui import btn, mk, esc, card, progress_bar, num, NOPREVIEW
from handlers.common import ec2map_key, monitored, scan_ec2_all_regions, mark_state
from handlers.ops import send_daily_digest
from services import cloudfront as cfs, ec2 as ec2s, metrics as ms

log = logging.getLogger(__name__)


async def notify(ctx, uid, text, rows=None):
    try:
        await ctx.bot.send_message(uid, text, parse_mode="HTML", reply_markup=mk(rows), link_preview_options=NOPREVIEW)
    except TelegramError as e:
        log.warning("notify failed: %s", e)


# ============ watchers (30s) ============
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
            state = "ON" if cfg["Enabled"] else "OFF"
            aws.invalidate(("cf_list", uid, acc))
            text = (
                "✅ <b>CloudFront Deployed!</b>\n"
                + card(f"╭ 👤 Account: <code>{esc(acc)}</code>\n├ 🆔 ID: <code>{ref}</code>\n╰ 📦 Hali: <b>Deployed ({state})</b>")
            )
            rows = [[btn("☁️ Open", f"d|{acc}|{ref}")]]
            if ex.get("then") == "delete" and not cfg["Enabled"]:
                text += "\nSasa unaweza kuifuta."; rows.insert(0, [btn("🗑 Futa sasa", f"rm|{acc}|{ref}")])
            await notify(ctx, uid, text, rows); store.del_watch(w["id"])
        elif kind == "cert":
            if age > 72 * 3600: return store.del_watch(w["id"])
            cert = (await aws.run(aws.client(uid, acc, "acm").describe_certificate, CertificateArn=ref))["Certificate"]
            st = cert["Status"]
            if st == "ISSUED":
                text = (
                    "🔐 <b>ACM SSL Certificate ISSUED!</b>\n"
                    + card(f"╭ 👤 Account: <code>{esc(acc)}</code>\n├ 🌍 Domain: <code>{esc(ex.get('dom', ''))}</code>\n╰ Sasa unaweza kuiunganisha kwenye Domains &amp; SSL.")
                )
                rows = [[btn("🌍 Domains & SSL", f"cd|{acc}|{ex.get('did', '')}")]] if ex.get("did") else None
                await notify(ctx, uid, text, rows); store.del_watch(w["id"])
            elif st in ("FAILED", "VALIDATION_TIMED_OUT", "REVOKED", "INACTIVE"):
                await notify(ctx, uid, f"❌ Cheti cha ACM <code>{esc(ex.get('dom', ''))}</code> kimeshindwa (<b>{st}</b>).")
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


# ============ health, smart 5xx auto-failover, FT expiry, digest & DB backup (15m) ============
async def health_tick(ctx):
    now_local = dt.datetime.now(config.LOCAL_TZ)
    today_local = now_local.strftime("%Y-%m-%d")
    tasks = []
    for uid in store.all_users():
        st = store.user_settings(uid)
        accs = store.accounts_meta(uid)
        for acc, info in accs.items():
            if st["alerts_on"]:
                tasks.append(_check_ft_expiry(ctx, uid, acc, info))
                tasks.append(_health_cf(ctx, uid, acc, st))
                tasks.append(_health_ec2(ctx, uid, acc, st))
        tasks.append(_user_schedule(ctx, uid, st, now_local, today_local))
    tasks.append(_run_ec2_schedules(ctx, now_local, today_local))
    if tasks:
        await aws.gather_limited(tasks, 4)


async def _check_ft_expiry(ctx, uid, acc, info):
    ex = cfs.calc_ft_expiry(info["opened_at"], info["ft_months"])
    if not ex:
        return
    dl = ex["days_left"]
    for milestone in (30, 14, 7, 3, 1, 0):
        if dl <= milestone:
            akey = f"ftexp:{uid}:{acc}:{ex['end']}:{milestone}"
            if not store.alert_recently_sent(akey, cooldown_sec=30 * 86400):
                title = "🚨 <b>AWS Free Tier IMEKWISHA!</b>" if dl <= 0 else f"⏳ <b>AWS Free Tier Inakaribia Kuisha ({dl}d)!</b>"
                await notify(ctx, uid, f"{title}\n" + card(
                    f"╭ 👤 Account: <code>{esc(acc)}</code>\n"
                    f"├ 🏁 Tarehe ya Kuisha: <code>{ex['end']}</code>\n"
                    "╰ 💡 Kagua EC2 zako ili zisikuletee gharama za ziada."
                ), [[btn("🛡 Free Tier", f"ft|{acc}")]])
            break


async def _health_cf(ctx, uid, acc, st):
    try:
        cf = aws.client(uid, acc)
        cw = aws.client(uid, acc, "cloudwatch", "us-east-1")
        ds = [d for d in await aws.run(cfs.all_dists, cf) if d["Enabled"] and d["Status"] == "Deployed"]
        for d in ds:
            did = d["Id"]
            e5, req = await aws.run(ms.cf_health, cw, did, 10)
            origins_items = d.get("Origins", {}).get("Items") or []

            # Smart 5xx Auto-Failover for multi-origin distributions
            if st["auto_failover"] and len(origins_items) >= 2 and req >= 10 and e5 >= 50.0:
                if not store.alert_recently_sent(f"autofail:{uid}:{acc}:{did}", cooldown_sec=1800):
                    switched = ["", ""]
                    def _auto_sw(cfg):
                        items = cfg["Origins"]["Items"]
                        if len(items) < 2: return
                        cur_id = cfg["DefaultCacheBehavior"]["TargetOriginId"]
                        cur_idx = next((i for i, x in enumerate(items) if x["Id"] == cur_id), 0)
                        nxt_item = items[(cur_idx + 1) % len(items)]
                        switched[0], switched[1] = items[cur_idx]["DomainName"], nxt_item["DomainName"]
                        cfg["DefaultCacheBehavior"]["TargetOriginId"] = nxt_item["Id"]
                    await aws.run(cfs.mutate, cf, did, _auto_sw)
                    aws.invalidate(("cf_list", uid, acc))
                    store.add_watch(uid, "dist", acc, did)
                    store.audit(uid, acc, "CF_AUTO_FAILOVER", f"{did} 5xx={e5:.0f}% -> {switched[1][:30]}")
                    await notify(ctx, uid, "🤖 <b>Smart Auto-Failover Imetekelezwa!</b>\n" + card(
                        f"╭ ☁️ Dist: <code>{did}</code> ({esc(acc)})\n"
                        f"├ 🔥 Sababu: 5xx <b>{e5:.1f}%</b> ({int(req)} reqs / 10m)\n"
                        f"├ ❌ Kutoka: <code>{esc(switched[0])}</code>\n"
                        f"╰ ✅ Kwenda: <code>{esc(switched[1])}</code>"
                    ), [[btn("☁️ Open", f"d|{acc}|{did}")]])
                continue

            # Standard 5xx alert
            bad = e5 >= 20.0 and req >= 20
            key = f"h5xx:{uid}:{acc}:{did}"
            was = store.get_state(key) == "bad"
            if bad and not was:
                store.set_state(key, "bad")
                await notify(ctx, uid, f"🚨 <b>5xx ni juu</b> kwenye <code>{did}</code> ({esc(cfs.dlabel(d))}): <b>{e5:.1f}%</b> ya {int(req)} reqs.\n"
                             "Backend (EC2/x-ui/nginx) huenda imekufa, au origin/port si sahihi.",
                             [[btn("☁️ Open", f"d|{acc}|{did}"), btn("🩺 Test Origin", f"cf_tst|{acc}|{did}")]])
            elif not bad and was:
                store.set_state(key, "ok")
                await notify(ctx, uid, f"✅ <code>{did}</code> imerudi kawaida (5xx chini).")
    except Exception:
        log.exception("cf health failed for %s", acc)


async def _health_ec2(ctx, uid, acc, st):
    regs = monitored(uid, acc)
    if not regs:
        try:
            res = await scan_ec2_all_regions(uid, acc)
            regs = [r for r, it, _ in res if it]
        except Exception:
            regs = []
    for region in regs:
        try:
            cl = aws.client(uid, acc, "ec2", region)
            items = await aws.run(ec2s.list_instances, cl, region)
            cur = {i["id"]: i["state"] for i in items}
            key = ec2map_key(uid, acc, region)
            try: prev = json.loads(store.get_state(key, "{}"))
            except ValueError: prev = {}
            names = {i["id"]: i["name"] or i["id"] for i in items}
            for iid, old_st in prev.items():
                now_st = cur.get(iid, "terminated")
                if old_st == "running" and now_st != "running":
                    tok = store.ref(uid, {"a": acc, "r": region, "i": iid})
                    await notify(ctx, uid, f"🔴 <b>EC2 imezimika:</b> {esc(names.get(iid, iid))} (<code>{iid}</code>, {region}) → <b>{now_st}</b>.",
                                 [[btn("🖥 Open", f"ei|{tok}")]] if now_st != "terminated" else None)
            store.set_state(key, json.dumps(cur))
            cw = aws.client(uid, acc, "cloudwatch", region)
            for i in items:
                if i["state"] != "running": continue
                cpu = await aws.run(ms.ec2_cpu_now, cw, i["id"])
                if cpu is not None and cpu >= st["alert_cpu_pct"]:
                    if not store.alert_recently_sent(f"cpu:{uid}:{acc}:{i['id']}", cooldown_sec=3600):
                        tok = store.ref(uid, {"a": acc, "r": region, "i": i["id"]})
                        await notify(ctx, uid, f"🔥 <b>EC2 High CPU Alert ({esc(acc)} · {region})</b>\n" + card(
                            f"🖥 Server: <b>{esc(i['name'] or i['id'])}</b> (<code>{i['id']}</code>)\n"
                            f"⚡ CPU: <b>{cpu:.1f}%</b>\n{progress_bar(cpu, 100.0)}"
                        ), [[btn("📊 Metrics", f"em|{tok}")]])
        except Exception:
            log.exception("ec2 health failed for %s/%s", acc, region)


async def _user_schedule(ctx, uid, st, now_local, today_local):
    try:
        if st["digest_on"] and now_local.hour == st["digest_hour"]:
            if not store.alert_recently_sent(f"digest:{uid}:{today_local}", cooldown_sec=20 * 3600):
                await send_daily_digest(ctx.bot, uid)
        if st["auto_backup_db"]:
            if not store.alert_recently_sent(f"dbbak:{uid}", cooldown_sec=7 * 86400):
                raw = await aws.run(store.backup_bytes)
                buf = io.BytesIO(raw)
                await ctx.bot.send_document(
                    chat_id=uid,
                    document=buf,
                    filename=f"cfbot_{now_local.strftime('%Y%m%d')}.db",
                    caption="📦 <b>Weekly Auto-Backup (cfbot.db)</b>\n<i>Keys zimesimbwa kwa Fernet. Hifadhi SECRET_KEY yako salama.</i>",
                    parse_mode="HTML",
                )
    except Exception as e:
        log.warning("Digest/DB backup error for %s: %s", uid, e)


# ============ usage & Live USD Bill Watchdog (30m) ============
async def usage_tick(ctx):
    ym = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m")
    for uid in store.all_users():
        st = store.user_settings(uid)
        if not st["alerts_on"]:
            continue
        for acc in store.accounts(uid):
            try:
                cw = aws.client(uid, acc, "cloudwatch", "us-east-1")
                ds = await aws.run(cfs.all_dists, aws.client(uid, acc))
                async def _one(d):
                    try: return await aws.run(ms.cf_month_usage, cw, d["Id"])
                    except Exception: return (0.0, 0.0)
                usage = await aws.gather_limited([_one(d) for d in ds[:25]], 8)
                tot_gb = sum(x[0] for x in usage)
                tot_req = sum(x[1] for x in usage)
                gb_pct = tot_gb / config.FREE_TIER_GB * 100
                req_pct = tot_req / config.FREE_TIER_REQ * 100

                if gb_pct >= st["alert_cf_pct"] or req_pct >= st["alert_cf_pct"]:
                    if not store.alert_recently_sent(f"cf:{uid}:{acc}:{ym}:{st['alert_cf_pct']}", cooldown_sec=7 * 86400):
                        await notify(ctx, uid, f"🚨 <b>CloudFront Free Tier Alert ({esc(acc)})</b>\n" + card(
                            f"📦 <b>Data:</b> {tot_gb:.1f} / {config.FREE_TIER_GB:.0f} GB ({gb_pct:.0f}%)\n"
                            f"{progress_bar(tot_gb, config.FREE_TIER_GB)}\n\n"
                            f"📈 <b>Requests:</b> {num(tot_req)} / 10M ({req_pct:.0f}%)\n"
                            f"{progress_bar(tot_req, config.FREE_TIER_REQ)}"
                        ), [[btn("📊 Analytics", f"ua|{acc}")]])

                # Live Dollar Watchdog (CloudWatch AWS/Billing — FREE)
                bill_usd = await aws.run(ms.billing_usd, cw)
                if bill_usd is not None and bill_usd >= st["bill_alert_usd"]:
                    if not store.alert_recently_sent(f"bill:{uid}:{acc}:{ym}:{int(bill_usd)}", cooldown_sec=86400):
                        await notify(ctx, uid, f"💵 <b>AWS Billing Watchdog ({esc(acc)})</b>\n" + card(
                            f"🚨 Makadirio ya bili yamefikia: <b>${bill_usd:.2f} USD</b>\n"
                            f"🎯 Kikomo chako: <code>${st['bill_alert_usd']:.2f} USD</code>\n"
                            "💡 Kagua <b>🛡 Free Tier</b> au <b>🧹 Hunt Zombies</b> mara moja."
                        ), [[btn("🛡 Free Tier", f"ft|{acc}"), btn("🧹 Hunt Zombies", f"zomb|{acc}")]])
            except Exception:
                log.exception("usage/bill check failed for %s", acc)



async def _run_ec2_schedules(ctx, now_local, today_local):
    """Executes daily EC2 Sleep/Wake schedules (EAT) safely once per scheduled hour."""
    hr = now_local.hour
    for sc in store.list_schedules():
        uid, acc, reg, iid = sc["uid"], sc["acc"], sc["region"], sc["iid"]
        if not store.has_account(uid, acc):
            continue
        act = None
        if sc["stop_hr"] == hr:
            act = "stop"
        elif sc["start_hr"] == hr:
            act = "start"
        if not act:
            continue
        skey = f"sched:{uid}:{acc}:{reg}:{iid}:{act}:{today_local}:{hr}"
        if store.alert_recently_sent(skey, cooldown_sec=20 * 3600):
            continue
        try:
            cl = aws.client(uid, acc, "ec2", reg)
            inst, _ = await aws.run(ec2s.get_instance, cl, reg, iid)
            if act == "stop" and inst["state"] == "running":
                await aws.run(ec2s.power, cl, iid, "stop")
                mark_state(uid, acc, reg, iid, "stopping")
                aws.invalidate(("ec2_list", uid, acc))
                aws.invalidate(("ec2_scan", uid, acc))
                store.audit(uid, acc, "SCHED_EC2_STOP", f"{iid} ({reg}) @{hr:02d}:00")
                await notify(ctx, uid, f"🌙 <b>Auto-Schedule: EC2 Imezimwa</b>\n" + card(
                    f"🖥 Server: <b>{esc(inst['name'] or iid)}</b> (<code>{iid}</code>)\n"
                    f"📍 Region: <code>{reg}</code> · ⏰ <code>{hr:02d}:00 EAT</code>"
                ))
            elif act == "start" and inst["state"] == "stopped":
                await aws.run(ec2s.power, cl, iid, "start")
                aws.invalidate(("ec2_list", uid, acc))
                aws.invalidate(("ec2_scan", uid, acc))
                store.add_watch(uid, "ec2", acc, f"{reg}|{iid}", {"want": "running"})
                store.audit(uid, acc, "SCHED_EC2_START", f"{iid} ({reg}) @{hr:02d}:00")
                await notify(ctx, uid, f"🌅 <b>Auto-Schedule: EC2 Imewashwa</b>\n" + card(
                    f"🖥 Server: <b>{esc(inst['name'] or iid)}</b> (<code>{iid}</code>)\n"
                    f"📍 Region: <code>{reg}</code> · ⏰ <code>{hr:02d}:00 EAT</code>"
                ))
        except Exception as e:
            log.warning("EC2 schedule failed for %s/%s: %s", acc, iid, e)
