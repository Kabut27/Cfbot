"""CloudFront, ACM, WAF & FreeTier/Account service helpers (blocking boto3 calls)."""
import os, re, json, time, glob, datetime as dt
import boto3
from botocore.exceptions import ClientError
import config

PROTOS = ["http-only", "https-only", "match-viewer"]
VIEWER = {"r": "redirect-to-https", "h": "https-only", "a": "allow-all"}
PRICES = ["PriceClass_All", "PriceClass_200", "PriceClass_100"]
HTTPV = ["http2and3", "http2", "http1.1"]
METHODS = ["GET", "HEAD", "OPTIONS", "PUT", "POST", "PATCH", "DELETE"]


# ---------- reads ----------
def all_dists(cl):
    out = []
    for pg in cl.get_paginator("list_distributions").paginate():
        out += pg.get("DistributionList", {}).get("Items", []) or []
    return out


def get(cl, did):
    r = cl.get_distribution(Id=did)
    return r["Distribution"], r["ETag"]


def dlabel(d):
    return d.get("Comment") or (d.get("Aliases", {}).get("Items") or [d["DomainName"]])[0]


# ---------- backups (last N per distribution, chmod 600) ----------
def _backup(did, cfg):
    os.makedirs(config.BACKUPS, mode=0o700, exist_ok=True)
    path = os.path.join(config.BACKUPS, f"{did}-{int(time.time() * 1000)}.json")
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(cfg, f, indent=2, default=str)
    for old in sorted(glob.glob(os.path.join(config.BACKUPS, f"{did}-*.json")))[:-config.KEEP_BACKUPS]:
        try: os.remove(old)
        except OSError: pass
    return path


def list_backups(did):
    files = sorted(glob.glob(os.path.join(config.BACKUPS, f"{did}-*.json")), reverse=True)
    return [(int(re.search(r"-(\d+)\.json$", f).group(1)) / 1000, f) for f in files]


def mutate(cl, did, fn):
    """get config -> backup -> fn(cfg) -> update (optimistic concurrency via ETag)."""
    r = cl.get_distribution_config(Id=did)
    cfg = r["DistributionConfig"]
    _backup(did, cfg)
    fn(cfg)
    return cl.update_distribution(Id=did, IfMatch=r["ETag"], DistributionConfig=cfg)["Distribution"]


def restore(cl, did, path):
    real = os.path.realpath(path)
    if not real.startswith(os.path.realpath(config.BACKUPS) + os.sep) or not os.path.basename(real).startswith(did + "-"):
        raise ValueError("Backup si sahihi kwa distribution hii.")
    with open(real) as f:
        old = json.load(f)
    mutate(cl, did, lambda cfg: (cfg.clear(), cfg.update(old)))


def clone_dist(cl, did, new_label):
    cfg = cl.get_distribution_config(Id=did)["DistributionConfig"]
    cfg["CallerReference"] = str(time.time())
    cfg["Comment"] = (new_label or did)[:128]
    cfg["Aliases"] = {"Quantity": 0}
    cfg["ViewerCertificate"] = {"CloudFrontDefaultCertificate": True, "MinimumProtocolVersion": "TLSv1"}
    return cl.create_distribution(DistributionConfig=cfg)["Distribution"]["Id"]


def month_invalidations(cl, did):
    r = cl.list_invalidations(DistributionId=did, MaxItems="50")
    items = r.get("InvalidationList", {}).get("Items", []) or []
    now = dt.datetime.now(dt.timezone.utc)
    start_month = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    m_cnt = sum(1 for i in items if i["CreateTime"] >= start_month)
    return m_cnt, items[:5]


# ---------- config helpers ----------
def origins(cfg): return cfg["Origins"]["Items"]

def active_origin(cfg):
    items = origins(cfg)
    active_oid = cfg.get("DefaultCacheBehavior", {}).get("TargetOriginId", "")
    return next((x for x in items if x["Id"] == active_oid), items[0])

def origin_at(cfg, i):
    items = origins(cfg)
    if i < 0: i = len(items) + i
    if not 0 <= i < len(items):
        raise ValueError("Origin hiyo haipo tena.")
    return items[i]

def custom_of(o):
    co = o.get("CustomOriginConfig")
    if not co:
        raise ValueError("Origin hii ni S3/nyingine: port na protocol hazihaririwi hapa.")
    return co

def eff_port(co):
    p = co.get("OriginProtocolPolicy", "http-only")
    if p == "http-only": return co.get("HTTPPort", 80)
    if p == "https-only": return co.get("HTTPSPort", 443)
    return f"{co.get('HTTPPort', 80)}/{co.get('HTTPSPort', 443)}"

def add_backup_origin(cfg, domain, http_port, https_port=443, proto="http-only"):
    oid = f"origin-{int(time.time()) % 100000}"
    cfg["Origins"]["Items"].append({
        "Id": oid,
        "DomainName": domain,
        "CustomHeaders": {"Quantity": 0},
        "CustomOriginConfig": {
            "HTTPPort": int(http_port),
            "HTTPSPort": int(https_port),
            "OriginProtocolPolicy": proto,
            "OriginSslProtocols": {"Quantity": 1, "Items": ["TLSv1.2"]},
            "OriginReadTimeout": 60,
            "OriginKeepaliveTimeout": 60,
        },
    })
    cfg["Origins"]["Quantity"] = len(cfg["Origins"]["Items"])

def del_origin(cfg, i):
    items = cfg["Origins"]["Items"]
    if len(items) <= 1:
        raise ValueError("Huwezi kufuta origin ya pekee.")
    if items[i]["Id"] == cfg["DefaultCacheBehavior"]["TargetOriginId"]:
        raise ValueError("Huwezi kufuta origin ambayo ni ACTIVE. Badili active kwanza.")
    items.pop(i)
    cfg["Origins"]["Quantity"] = len(items)

def set_active_origin(cfg, i):
    target = origin_at(cfg, i)
    cfg["DefaultCacheBehavior"]["TargetOriginId"] = target["Id"]

def set_header(cfg, i, name, value):
    o = origin_at(cfg, i)
    ch = o.setdefault("CustomHeaders", {"Quantity": 0, "Items": []})
    items = [x for x in ch.get("Items", []) if x["HeaderName"].lower() != name.lower()]
    items.append({"HeaderName": name, "HeaderValue": value})
    o["CustomHeaders"] = {"Quantity": len(items), "Items": items}

def clear_headers(cfg, i):
    origin_at(cfg, i)["CustomHeaders"] = {"Quantity": 0}

def behaviors(cfg):
    return [cfg["DefaultCacheBehavior"]] + (cfg.get("CacheBehaviors", {}).get("Items") or [])

def set_viewer(cfg, code):
    for b in behaviors(cfg): b["ViewerProtocolPolicy"] = VIEWER[code]

def viewer_code(cfg):
    inv = {v: k for k, v in VIEWER.items()}
    return inv.get(cfg["DefaultCacheBehavior"].get("ViewerProtocolPolicy"), "r")

def cache_kind(cfg):
    ids = {b.get("CachePolicyId") for b in behaviors(cfg)}
    if ids == {config.CACHING_DISABLED}: return "d"
    if ids == {config.CACHING_OPTIMIZED}: return "o"
    return "x"

def set_cache(cfg, kind):
    pid = config.CACHING_DISABLED if kind == "d" else config.CACHING_OPTIMIZED
    for b in behaviors(cfg):
        b["CachePolicyId"] = pid
        for k in ("ForwardedValues", "MinTTL", "DefaultTTL", "MaxTTL"):
            b.pop(k, None)
        if kind == "d":
            b["Compress"] = False
            b.setdefault("OriginRequestPolicyId", config.ALL_VIEWER)

def set_compress(cfg, on):
    if on and cache_kind(cfg) != "o":
        raise ValueError("Compression inahitaji cache policy ya CachingOptimized (Settings → Cache).")
    for b in behaviors(cfg): b["Compress"] = on

def set_geo(cfg, mode, codes=None):
    if mode == "none" or not codes:
        cfg["Restrictions"] = {"GeoRestriction": {"RestrictionType": "none", "Quantity": 0}}
    else:
        cfg["Restrictions"] = {"GeoRestriction": {"RestrictionType": mode, "Quantity": len(codes), "Items": codes}}

def aliases(cfg): return cfg.get("Aliases", {}).get("Items") or []

def set_aliases(cfg, doms, arn):
    cfg["Aliases"] = {"Quantity": len(doms), "Items": doms}
    cfg["ViewerCertificate"] = {"ACMCertificateArn": arn, "SSLSupportMethod": "sni-only",
                                "MinimumProtocolVersion": "TLSv1.2_2021"}

def clear_aliases(cfg):
    cfg["Aliases"] = {"Quantity": 0}
    cfg["ViewerCertificate"] = {"CloudFrontDefaultCertificate": True, "MinimumProtocolVersion": "TLSv1"}

def build_cfg(origin, port, label):
    return {
        "CallerReference": str(time.time()), "Comment": label[:128], "Enabled": True,
        "Origins": {"Quantity": 1, "Items": [{
            "Id": "origin-1", "DomainName": origin,
            "CustomHeaders": {"Quantity": 0},
            "CustomOriginConfig": {
                "HTTPPort": port, "HTTPSPort": 443, "OriginProtocolPolicy": "http-only",
                "OriginSslProtocols": {"Quantity": 1, "Items": ["TLSv1.2"]},
                "OriginReadTimeout": 60, "OriginKeepaliveTimeout": 60}}]},
        "DefaultCacheBehavior": {
            "TargetOriginId": "origin-1", "ViewerProtocolPolicy": "redirect-to-https",
            "AllowedMethods": {"Quantity": 7, "Items": METHODS,
                               "CachedMethods": {"Quantity": 2, "Items": ["GET", "HEAD"]}},
            "CachePolicyId": config.CACHING_DISABLED, "OriginRequestPolicyId": config.ALL_VIEWER,
            "Compress": False},
        "HttpVersion": "http2and3", "IsIPV6Enabled": True, "PriceClass": "PriceClass_All"}


# ---------- ACM & WAF ----------
def list_certs(acm):
    issued = acm.list_certificates(CertificateStatuses=["ISSUED"]).get("CertificateSummaryList", [])
    pending = acm.list_certificates(CertificateStatuses=["PENDING_VALIDATION"]).get("CertificateSummaryList", [])
    return issued, pending

def request_cert(acm, domain):
    arn = acm.request_certificate(DomainName=domain, ValidationMethod="DNS")["CertificateArn"]
    rec = None
    for _ in range(6):
        time.sleep(2)
        opts = acm.describe_certificate(CertificateArn=arn)["Certificate"].get("DomainValidationOptions") or [{}]
        rec = opts[0].get("ResourceRecord")
        if rec: break
    return arn, rec

def list_waf_acls(waf_cl):
    return waf_cl.list_web_acls(Scope="CLOUDFRONT").get("WebACLs", [])


# ---------- Account Creation Date & AWS Free Tier Billing API ----------
def calc_ft_expiry(opened_at, ft_months=12):
    if not opened_at:
        return None
    try:
        start_dt = dt.datetime.strptime(opened_at[:10], "%Y-%m-%d").replace(tzinfo=dt.timezone.utc)
    except ValueError:
        return None
    total_days = 365 if ft_months == 12 else int(round(ft_months * 30.4375))
    end_dt = start_dt + dt.timedelta(days=total_days)
    now = dt.datetime.now(dt.timezone.utc)
    days_left = (end_dt.date() - now.date()).days
    days_used = max(0, (now.date() - start_dt.date()).days)
    if days_left <= 0:
        badge, status = "🔴", "IMEKWISHA"
    elif days_left <= 30:
        badge, status = "🟡", "KARIBU KUISHA"
    else:
        badge, status = "🟢", "ACTIVE"
    return {
        "start": start_dt.strftime("%Y-%m-%d"),
        "end": end_dt.strftime("%Y-%m-%d"),
        "total_days": total_days,
        "days_used": min(total_days, days_used),
        "days_left": days_left,
        "months": ft_months,
        "badge": badge,
        "status": status,
    }


def try_account_created_date(ak, sk):
    try:
        cl = boto3.client("account", aws_access_key_id=ak, aws_secret_access_key=sk, region_name="us-east-1")
        r = cl.get_account_information()
        d = r.get("AccountCreatedDate")
        if isinstance(d, dt.datetime):
            return d.strftime("%Y-%m-%d")
        if isinstance(d, str) and len(d) >= 10:
            return d[:10]
    except Exception:
        pass
    return ""


def try_freetier_api(ak, sk):
    try:
        cl = boto3.client("freetier", aws_access_key_id=ak, aws_secret_access_key=sk, region_name="us-east-1")
        items, tok = [], None
        for _ in range(3):
            kw = {"nextToken": tok} if tok else {}
            r = cl.get_free_tier_usage(**kw)
            items.extend(r.get("freeTierUsages", []) or [])
            tok = r.get("nextToken")
            if not tok:
                break
        return items, ""
    except ClientError as e:
        code = e.response["Error"]["Code"]
        return (None, "denied") if code in ("AccessDeniedException", "UnauthorizedOperation") else (None, code)
    except Exception as e:
        return None, type(e).__name__
