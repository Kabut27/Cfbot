"""CloudFront + ACM logic (blocking boto3 calls; run via core.aws.run)."""
import os, re, json, time, glob
import config

PROTOS = ["http-only", "https-only", "match-viewer"]
VIEWER = {"r": "redirect-to-https", "h": "https-only", "a": "allow-all"}
PRICES = ["PriceClass_All", "PriceClass_200", "PriceClass_100"]
HTTPV = ["http2", "http2and3"]
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
    return (d.get("Aliases", {}).get("Items") or [d["DomainName"]])[0]


# ---------- backups (last N per distribution, chmod 600) ----------
def _backup(did, cfg):
    os.makedirs(config.BACKUPS, mode=0o700, exist_ok=True)
    path = os.path.join(config.BACKUPS, f"{did}-{int(time.time() * 1000)}.json")
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(cfg, f, default=str)
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
    cl.update_distribution(Id=did, IfMatch=r["ETag"], DistributionConfig=cfg)


def restore(cl, did, path):
    real = os.path.realpath(path)
    if not real.startswith(os.path.realpath(config.BACKUPS) + os.sep) or not os.path.basename(real).startswith(did + "-"):
        raise ValueError("Backup si sahihi kwa distribution hii.")
    with open(real) as f:
        old = json.load(f)
    mutate(cl, did, lambda cfg: (cfg.clear(), cfg.update(old)))


# ---------- config helpers ----------
def origins(cfg): return cfg["Origins"]["Items"]

def origin_at(cfg, i):
    items = origins(cfg)
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
            b.pop(k, None)                       # legacy settings conflict with cache policies
        if kind == "d":
            b["Compress"] = False
            b.setdefault("OriginRequestPolicyId", config.ALL_VIEWER)

def set_compress(cfg, on):
    if on and cache_kind(cfg) != "o":
        raise ValueError("Compression inahitaji cache policy ya CachingOptimized (Settings → Cache).")
    for b in behaviors(cfg): b["Compress"] = on

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
        "CallerReference": str(time.time()), "Comment": label, "Enabled": True,
        "Origins": {"Quantity": 1, "Items": [{
            "Id": "origin-1", "DomainName": origin,
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
        "HttpVersion": "http2", "IsIPV6Enabled": True, "PriceClass": "PriceClass_All"}


# ---------- ACM (must be us-east-1 for CloudFront) ----------
def issued_certs(acm):
    r = acm.list_certificates(CertificateStatuses=["ISSUED"])
    return r.get("CertificateSummaryList", [])

def request_cert(acm, domain):
    arn = acm.request_certificate(DomainName=domain, ValidationMethod="DNS")["CertificateArn"]
    rec = None
    for _ in range(6):
        time.sleep(2)
        opts = acm.describe_certificate(CertificateArn=arn)["Certificate"].get("DomainValidationOptions") or [{}]
        rec = opts[0].get("ResourceRecord")
        if rec: break
    return arn, rec
