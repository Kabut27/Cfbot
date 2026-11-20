"""Shared handler helpers."""
import json
import storage as store
from core import aws
from core.ui import show, btn
from services import cloudfront as cfs

STALE = "Kitufe hiki ni cha zamani. Fungua tena kutoka Menu."


def tok_data(u, tok):
    d = store.deref(u.effective_user.id, tok)
    if d is None:
        raise ValueError(STALE)
    return d


async def edit_dist(u, acc, did, fn):
    """Apply fn(cfg) to a distribution (backup + ETag) and watch it until Deployed."""
    uid = u.effective_user.id
    async with aws.lock(("dist", uid, did)):
        await aws.run(cfs.mutate, aws.client(uid, acc), did, fn)
    store.add_watch(uid, "dist", acc, did)


async def prompt(u, c, flow_data, text, back):
    c.user_data["flow"] = flow_data
    await show(u, c, text, [[btn("✖️ Cancel", back)]])


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
    """Tell the alert job a state change was made by us (so it doesn't alert)."""
    k = ec2map_key(uid, acc, region)
    try: m = json.loads(store.get_state(k, "{}"))
    except ValueError: m = {}
    m[iid] = state
    store.set_state(k, json.dumps(m))


import re

def clean_domain(s):
    s = re.sub(r"^https?://", "", s.strip().lower()).split("/")[0].split(":")[0]
    return s if re.fullmatch(r"[a-z0-9]([a-z0-9.-]*[a-z0-9])?", s) and "." in s else None

def valid_port(s):
    return int(s) if s.isdigit() and 1 <= int(s) <= 65535 else None

def back_accounts(u, purpose):
    """Back target for account-level screens: picker if several accounts, else home."""
    return f"pk|{purpose}" if len(store.accounts(u.effective_user.id)) > 1 else None


from core.ui import grid
from services import ec2 as ec2s

def itok(u, acc, region, iid):
    return store.ref(u.effective_user.id, {"a": acc, "r": region, "i": iid})

def ec2c(u, d, svc="ec2"):
    return aws.client(u.effective_user.id, d["a"], svc, d["r"])

def region_buttons(uid, acc, fmt):
    mon = monitored(uid, acc)
    regs = sorted(ec2s.REGIONS, key=lambda r: (r not in mon, r))
    return grid([btn(("⭐ " if r in mon else "") + ec2s.rname(r), fmt.format(r=r)) for r in regs], 2)
