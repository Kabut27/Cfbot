"""boto3 client factory (cached), thread offloading, locks, TTL cache."""
import time, asyncio
import boto3
from botocore.config import Config
import storage

_CFG = Config(retries={"max_attempts": 4, "mode": "standard"}, connect_timeout=5, read_timeout=25)
_clients = {}
_locks = {}
_ttl = {}


def client(uid, acc, svc="cloudfront", region="us-east-1"):
    key = (uid, acc, svc, region)
    c = _clients.get(key)
    if c is None:
        ak, sk = storage.creds(uid, acc)
        c = boto3.client(svc, aws_access_key_id=ak, aws_secret_access_key=sk,
                         region_name=region, config=_CFG)
        _clients[key] = c
    return c


def forget(uid, acc):
    for k in [k for k in _clients if k[0] == uid and k[1] == acc]:
        _clients.pop(k, None)


async def run(fn, *a, **k):
    return await asyncio.to_thread(fn, *a, **k)


def lock(key):
    return _locks.setdefault(key, asyncio.Lock())


async def gather_limited(coros, limit=8):
    sem = asyncio.Semaphore(limit)
    async def one(co):
        async with sem:
            return await co
    return await asyncio.gather(*(one(c) for c in coros))


async def cached(key, ttl, factory):
    v = _ttl.get(key)
    if v and time.time() - v[0] < ttl:
        return v[1]
    val = await factory()
    _ttl[key] = (time.time(), val)
    return val


def invalidate(prefix):
    for k in [k for k in _ttl if k[:len(prefix)] == prefix]:
        _ttl.pop(k, None)
