"""Shared handler helpers, smart caches, validation, and EC2 scan helpers."""
import json, re
import config
import storage as store
from core import aws
from core.ui import show, btn, grid
from services import cloudfront as cfs, ec2 as ec2s

STALE = "⌛ Kitufe hiki ni cha zamani. Fungua tena kutoka Menu Kuu."


def tok_data(u, tok):
    d = store.deref(u.effective_user.id, tok)
    if d is None:
        raise ValueError(STALE)
    return d


async def cf_list_cached(uid, acc, force=False):
    return await aws.cached(
        ("cf_list", uid, acc),
        config.CACHE_TTL_SEC,
        lambda: aws.run(cfs.all_dists, aws.client(uid, acc)),
        force=force,
    )


async def ec2_list_cached(uid, acc, region, force=False):
    return await aws.cached(
        ("ec2_list", uid, acc, region),
        config.CACHE_TTL_SEC,
        lambda: aws.run(ec2s.list_instances, aws.client(uid, acc, "ec2", region), region),
        force=force,
    )


async def scan_ec2_all_regions(uid, acc, force=False):
    async def _scan():
        try:
            regs = await aws.run(ec2s.enabled_regions, aws.client(uid, acc, "ec2", "us-east-1"))
        except Exception:
            regs = list(ec2s.REGIONS.keys())

        async def _one(r):
            try:
                insts = await aws.run(ec2s.list_instances, aws.client(uid, acc, "ec2", r), r)
                return r, insts, None
            except Exception as e:
                return r, [], e

        res = await aws.gather_limited([_one(r) for r in regs], 10)
        return res

    return await aws.cached(("ec2_scan", uid, acc), config.CACHE_TTL_SEC, _scan, force=force)


async def edit_dist(u, acc, did, fn, audit_act="CF_EDIT", audit_det=""):
    """Apply fn(cfg) to a distribution (backup + ETag), invalidate cache, audit, and watch deploy."""
    uid = u.effective_user.id
    async with aws.lock(("dist", uid, did)):
        await aws.run(cfs.mutate, aws.client(uid, acc), did, fn)
    aws.invalidate(("cf_list", uid, acc))
    store.add_watch(uid, "dist", acc, did)
    store.audit(uid, acc, audit_act, f"{did} {audit_det}".strip())


async def prompt(u, c, flow_data, text, back):
    c.user_data["flow"] = flow_data
    await show(u, c, text, [[btn("✖️ Ghairi", back)]])


# ---- monitored EC2 regions + last-known instance states (used by alert jobs) ----
def monitored(uid, acc):
    try: return json.loads(store.get_setting(uid, f"regions:{acc}", "[]"))
    except ValueError: return []

def add_monitored(uid, acc, region):
    r = monitored(uid, acc)
    if region not in r:
        store.set_setting(uid, f"regions:{acc}", json.dumps(sorted(r + [region])))

def set_monitored(uid, acc, regions):
    store.set_setting(uid, f"regions:{acc}", json.dumps(sorted(regions)))

def ec2map_key(uid, acc, region): return f"ec2map:{uid}:{acc}:{region}"

def mark_state(uid, acc, region, iid, state):
    k = ec2map_key(uid, acc, region)
    try: m = json.loads(store.get_state(k, "{}"))
    except ValueError: m = {}
    m[iid] = state
    store.set_state(k, json.dumps(m))


def clean_domain(s):
    s = re.sub(r"^https?://", "", s.strip().lower()).split("/")[0].split(":")[0]
    return s if re.fullmatch(r"[a-z0-9]([a-z0-9.-]*[a-z0-9])?", s) and "." in s else None

def valid_port(s):
    return int(s) if s.isdigit() and 1 <= int(s) <= 65535 else None

def back_accounts(u, purpose):
    return f"pk|{purpose}" if len(store.accounts(u.effective_user.id)) > 1 else None

def itok(u, acc, region, iid):
    return store.ref(u.effective_user.id, {"a": acc, "r": region, "i": iid})

def ec2c(u, d, svc="ec2"):
    return aws.client(u.effective_user.id, d["a"], svc, d["r"])

def region_buttons(uid, acc, fmt):
    mon = monitored(uid, acc)
    def_reg = store.get_setting(uid, f"region:{acc}", "us-east-1")
    regs = sorted(ec2s.REGIONS, key=lambda r: (r != def_reg, r not in mon, r))
    return grid([btn(("⭐ " if (r == def_reg or r in mon) else "") + ec2s.rname(r), fmt.format(r=r)) for r in regs], 2)
