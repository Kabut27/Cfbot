"""EC2 / Security Group / Lock-to-CF / Elastic IP / Snapshots / Zombies / FreeTier 750h+EBS / Security Audit / SSM."""
import calendar, time, datetime as dt
from botocore.exceptions import ClientError
import config

REGIONS = {
    "us-east-1": "🇺🇸 N. Virginia", "us-east-2": "🇺🇸 Ohio", "us-west-1": "🇺🇸 N. California",
    "us-west-2": "🇺🇸 Oregon", "eu-west-1": "🇮🇪 Ireland", "eu-west-2": "🇬🇧 London",
    "eu-central-1": "🇩🇪 Frankfurt", "eu-north-1": "🇸🇪 Stockholm", "ap-southeast-1": "🇸🇬 Singapore",
    "ap-southeast-2": "🇦🇺 Sydney", "ap-south-1": "🇮🇳 Mumbai", "ap-northeast-1": "🇯🇵 Tokyo",
    "sa-east-1": "🇧🇷 São Paulo", "ca-central-1": "🇨🇦 Canada", "af-south-1": "🇿🇦 Cape Town",
    "me-south-1": "🇧🇭 Bahrain",
}
ICONS = {"running": "🟢", "stopped": "🔴", "pending": "🟡", "stopping": "🟠",
         "shutting-down": "🟠", "terminated": "⚫"}
LIVE = ["pending", "running", "stopping", "stopped", "shutting-down"]

# SSM quick presets: (label, command, needs_confirm)
PRESETS = {
    "df": ("💾 Disk (df -h)", "df -h", False),
    "fr": ("🧠 RAM (free -m)", "free -m", False),
    "up": ("⏱ Uptime & Load", "uptime && who", False),
    "tp": ("🔥 Top CPU", "ps aux --sort=-%cpu | head -8", False),
    "xs": ("ℹ️ x-ui Status", "systemctl status x-ui --no-pager -l | head -n 20", False),
    "xr": ("🔄 Restart x-ui", "systemctl restart x-ui && sleep 1 && systemctl is-active x-ui", True),
    "ns": ("🌐 Nginx Status", "systemctl status nginx --no-pager -l | head -n 20", False),
    "nr": ("🔄 Restart Nginx", "systemctl restart nginx && systemctl is-active nginx", True),
    "cs": ("🔒 Caddy Status", "systemctl status caddy --no-pager -l | head -n 20", False),
    "cr": ("🔄 Restart Caddy", "systemctl restart caddy && systemctl is-active caddy", True),
}

# Quick firewall presets: (label, proto, port, src_mode)
SG_QUICK_PRESETS = {
    "cf80": ("☁️ 80 TCP (CF)", "tcp", 80, "cf"),
    "cf443": ("☁️ 443 TCP (CF)", "tcp", 443, "cf"),
    "cf8080": ("☁️ 8080 TCP (CF)", "tcp", 8080, "cf"),
    "pub443": ("🌐 443 TCP (Pub)", "tcp", 443, "pub"),
    "pub80": ("🌐 80 TCP (Pub)", "tcp", 80, "pub"),
    "pub22": ("🔑 22 SSH (Pub)", "tcp", 22, "pub"),
}

# Risky ports for 1-Click Security Audit
RISKY_DB_PORTS = {
    3306: "MySQL", 5432: "PostgreSQL", 6379: "Redis", 27017: "MongoDB",
    1433: "MSSQL", 9200: "Elasticsearch", 11211: "Memcached", 3389: "RDP",
}
CF_LOCKABLE_PORTS = {80: "HTTP", 443: "HTTPS", 8080: "HTTP-Alt", 8443: "HTTPS-Alt", 2053: "Panel", 54321: "x-ui"}


def rname(r): return REGIONS.get(r, r)
def icon(state): return ICONS.get(state, "⚪")


def enabled_regions(cl):
    return sorted(r["RegionName"] for r in cl.describe_regions()["Regions"])


def _norm(i, region):
    tags = {t["Key"]: t["Value"] for t in i.get("Tags", [])}
    return {
        "id": i["InstanceId"], "name": tags.get("Name", ""), "state": i["State"]["Name"],
        "type": i["InstanceType"], "ip": i.get("PublicIpAddress"),
        "dns": i.get("PublicDnsName") or None, "pip": i.get("PrivateIpAddress"),
        "az": i.get("Placement", {}).get("AvailabilityZone"), "launch": i.get("LaunchTime"),
        "sgs": [(g["GroupId"], g["GroupName"]) for g in i.get("SecurityGroups", [])],
        "vols": [b["Ebs"]["VolumeId"] for b in i.get("BlockDeviceMappings", []) if "Ebs" in b],
        "region": region,
    }


def list_instances(cl, region):
    out = []
    flt = [{"Name": "instance-state-name", "Values": LIVE}]
    for pg in cl.get_paginator("describe_instances").paginate(Filters=flt):
        for r in pg["Reservations"]:
            out += [_norm(i, region) for i in r["Instances"]]
    return sorted(out, key=lambda x: (x["name"].lower(), x["id"]))


def get_instance(cl, region, iid):
    r = cl.describe_instances(InstanceIds=[iid])
    inst = _norm(r["Reservations"][0]["Instances"][0], region)
    eips = cl.describe_addresses(Filters=[{"Name": "instance-id", "Values": [iid]}]).get("Addresses", [])
    return inst, eips


def power(cl, iid, act):
    fn = {"start": cl.start_instances, "stop": cl.stop_instances,
          "reboot": cl.reboot_instances, "terminate": cl.terminate_instances}[act]
    fn(InstanceIds=[iid])


# ---------- Built-in EC2 750h & EBS 30GB Free Tier collector ----------
def freetier_region_stats(cl, region):
    """Returns running hours this month, projected hours, running count, and total EBS GB in a region."""
    now = dt.datetime.now(dt.timezone.utc)
    ms = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    days_in_mo = calendar.monthrange(now.year, now.month)[1]
    mo_end = ms + dt.timedelta(days=days_in_mo)
    hrs_remaining = max(0.0, (mo_end - now).total_seconds() / 3600.0)

    insts = list_instances(cl, region)
    hrs_used = 0.0
    running = []
    for i in insts:
        if i["state"] == "running":
            running.append(i)
            lt = i["launch"]
            if lt:
                if lt.tzinfo is None:
                    lt = lt.replace(tzinfo=dt.timezone.utc)
                start_t = max(lt, ms)
                hrs_used += max(0.0, (now - start_t).total_seconds() / 3600.0)
    proj_hrs = hrs_used + len(running) * hrs_remaining

    ebs_gb = 0
    try:
        for pg in cl.get_paginator("describe_volumes").paginate():
            for v in pg.get("Volumes", []):
                ebs_gb += int(v.get("Size", 0))
    except Exception:
        pass

    return {
        "region": region,
        "hours": hrs_used,
        "proj_hours": proj_hrs,
        "running": running,
        "total": len(insts),
        "ebs_gb": ebs_gb,
    }


# ---------- security groups & 1-Click Security Audit ----------
def cf_prefix_list(cl):
    r = cl.describe_managed_prefix_lists(Filters=[{"Name": "prefix-list-name", "Values": [config.CF_PREFIX_LIST_NAME]}])
    pls = r.get("PrefixLists", [])
    if not pls: raise ValueError("CloudFront Managed Prefix List haipatikani kwenye region hii.")
    return pls[0]["PrefixListId"]


def sg_summary(cl, sg_ids):
    if not sg_ids:
        return "Hakuna SG"
    sgs = cl.describe_security_groups(GroupIds=sg_ids).get("SecurityGroups", [])
    parts = []
    for sg in sgs:
        for perm in sg.get("IpPermissions", []):
            fp, tp, proto = perm.get("FromPort"), perm.get("ToPort"), perm.get("IpProtocol")
            p_str = "ALL" if proto == "-1" else (f"{fp}" if fp == tp else f"{fp}-{tp}")
            p_tag = "/u" if proto == "udp" else ""
            if perm.get("PrefixListIds"):
                parts.append(f"{p_str}{p_tag}(CF)")
            elif any(x.get("CidrIp") == "0.0.0.0/0" for x in perm.get("IpRanges", [])):
                parts.append(f"{p_str}{p_tag}(Pub)")
            else:
                parts.append(f"{p_str}{p_tag}(Priv)")
    return ", ".join(dict.fromkeys(parts)) or "Hakuna ports wazi"


def sg_rules(cl, sgid):
    g = cl.describe_security_groups(GroupIds=[sgid])["SecurityGroups"][0]
    rules = []
    for p in g.get("IpPermissions", []):
        base = {"proto": p["IpProtocol"], "fp": p.get("FromPort"), "tp": p.get("ToPort")}
        for r in p.get("IpRanges", []): rules.append({**base, "kind": "cidr", "src": r["CidrIp"]})
        for r in p.get("Ipv6Ranges", []): rules.append({**base, "kind": "cidr6", "src": r["CidrIpv6"]})
        for r in p.get("PrefixListIds", []): rules.append({**base, "kind": "pl", "src": r["PrefixListId"]})
        for r in p.get("UserIdGroupPairs", []): rules.append({**base, "kind": "sg", "src": r["GroupId"]})
    return g["GroupName"], rules


def fmt_rule(r, cfpl=None):
    port = "ALL" if r["proto"] == "-1" else (f"{r['proto'].upper()} {r['fp']}" if r["fp"] == r["tp"]
                                            else f"{r['proto'].upper()} {r['fp']}-{r['tp']}")
    src = r["src"]
    if r["kind"] == "pl": src = "☁️ CloudFront" if src == cfpl else f"☁️ {src}"
    elif src in ("0.0.0.0/0", "::/0"): src = "🌐 0.0.0.0/0"
    else: src = f"🔒 {src}"
    return f"{port} ← {src}"


def _perm(r):
    p = {"IpProtocol": r["proto"]}
    if r["fp"] is not None: p["FromPort"], p["ToPort"] = r["fp"], r["tp"]
    key = {"cidr": ("IpRanges", "CidrIp"), "cidr6": ("Ipv6Ranges", "CidrIpv6"),
           "pl": ("PrefixListIds", "PrefixListId"), "sg": ("UserIdGroupPairs", "GroupId")}[r["kind"]]
    p[key[0]] = [{key[1]: r["src"]}]
    return p


def revoke(cl, sgid, rule):
    cl.revoke_security_group_ingress(GroupId=sgid, IpPermissions=[_perm(rule)])


def authorize(cl, sgid, port, src_kind, src, proto="tcp"):
    p = {"IpProtocol": proto, "FromPort": int(port), "ToPort": int(port)}
    if src_kind == "pl":
        p["PrefixListIds"] = [{"PrefixListId": src, "Description": "CloudFront Only (Bot)"}]
    else:
        p["IpRanges"] = [{"CidrIp": src, "Description": "Bot Firewall"}]
    cl.authorize_security_group_ingress(GroupId=sgid, IpPermissions=[p])


def lock_port_to_cf(cl, sgid, port):
    """Zero-downtime 1-Click Lock: authorizes CloudFront Prefix List FIRST, then revokes 0.0.0.0/0."""
    pl = cf_prefix_list(cl)
    try:
        authorize(cl, sgid, int(port), "pl", pl, proto="tcp")
    except ClientError as e:
        if e.response["Error"]["Code"] != "InvalidPermission.Duplicate":
            raise
    cl.revoke_security_group_ingress(
        GroupId=sgid,
        IpPermissions=[{"IpProtocol": "tcp", "FromPort": int(port), "ToPort": int(port),
                        "IpRanges": [{"CidrIp": "0.0.0.0/0"}]}],
    )


def audit_region_sgs(cl, region):
    """Scans Security Groups attached to live instances for risky 0.0.0.0/0 exposures."""
    insts = list_instances(cl, region)
    sg_to_insts = {}
    for i in insts:
        for sgid, sgname in i["sgs"]:
            sg_to_insts.setdefault(sgid, []).append(i["name"] or i["id"])
    if not sg_to_insts:
        return []
    findings = []
    sgs = cl.describe_security_groups(GroupIds=list(sg_to_insts.keys())).get("SecurityGroups", [])
    for sg in sgs:
        sgid, sgname = sg["GroupId"], sg["GroupName"]
        attached = ", ".join(sg_to_insts.get(sgid, [])[:2])
        for p in sg.get("IpPermissions", []):
            proto = p.get("IpProtocol")
            fp, tp = p.get("FromPort"), p.get("ToPort")
            is_pub = any(r.get("CidrIp") == "0.0.0.0/0" for r in p.get("IpRanges", []))
            if not is_pub:
                continue
            rule_obj = {"proto": proto, "fp": fp, "tp": tp, "kind": "cidr", "src": "0.0.0.0/0"}
            if proto == "-1":
                findings.append({
                    "sev": "CRITICAL", "score_pen": 30, "reg": region, "sg": sgid, "sgname": sgname,
                    "inst": attached, "port": None, "fix": "close", "rule": rule_obj,
                    "msg": f"ALL Traffic wazi (0.0.0.0/0) kwenye {sgid} ({attached})",
                })
                continue
            if fp is None or tp is None:
                continue
            if fp <= 22 <= tp and proto == "tcp":
                findings.append({
                    "sev": "HIGH", "score_pen": 15, "reg": region, "sg": sgid, "sgname": sgname,
                    "inst": attached, "port": 22, "fix": "warn_only", "rule": rule_obj,
                    "msg": f"SSH (22) iko wazi kwa dunia nzima ({sgid} · {attached})",
                })
            for dport, dname in RISKY_DB_PORTS.items():
                if fp <= dport <= tp:
                    findings.append({
                        "sev": "CRITICAL", "score_pen": 25, "reg": region, "sg": sgid, "sgname": sgname,
                        "inst": attached, "port": dport, "fix": "close", "rule": rule_obj,
                        "msg": f"{dname} ({dport}) wazi kwa 0.0.0.0/0 ({sgid} · {attached})",
                    })
            if proto == "tcp" and fp == tp and int(fp) in CF_LOCKABLE_PORTS:
                findings.append({
                    "sev": "MEDIUM", "score_pen": 8, "reg": region, "sg": sgid, "sgname": sgname,
                    "inst": attached, "port": int(fp), "fix": "lock_cf", "rule": rule_obj,
                    "msg": f"Port {fp} ({CF_LOCKABLE_PORTS[int(fp)]}) wazi Public — funga iwe CF Only ({sgid})",
                })
    return findings


# ---------- Elastic IP / snapshots / launch / zombies ----------
def eip_attach(cl, iid):
    a = cl.allocate_address(Domain="vpc")
    try:
        cl.associate_address(AllocationId=a["AllocationId"], InstanceId=iid)
    except Exception:
        cl.release_address(AllocationId=a["AllocationId"])
        raise
    return a["PublicIp"]


def eip_release(cl, addr):
    if addr.get("AssociationId"): cl.disassociate_address(AssociationId=addr["AssociationId"])
    cl.release_address(AllocationId=addr["AllocationId"])


def list_instance_snapshots(cl, vols):
    if not vols:
        return []
    r = cl.describe_snapshots(Filters=[{"Name": "volume-id", "Values": vols}], OwnerIds=["self"])
    return sorted(r.get("Snapshots", []), key=lambda x: x["StartTime"], reverse=True)[:5]


def create_volume_snapshot(cl, vid, iid):
    desc = f"Bot backup of {iid} ({dt.datetime.now(dt.timezone.utc).strftime('%Y-%m-%d %H:%M UTC')})"
    return cl.create_snapshot(VolumeId=vid, Description=desc)["SnapshotId"]


def delete_snapshot(cl, sid):
    cl.delete_snapshot(SnapshotId=sid)


def zombie_scan_region(cl, reg):
    res = {"vols": [], "eips": [], "stopped_eips": [], "snaps": []}
    for pg in cl.get_paginator("describe_volumes").paginate(Filters=[{"Name": "status", "Values": ["available"]}]):
        for v in pg.get("Volumes", []):
            res["vols"].append({"reg": reg, "id": v["VolumeId"], "gb": v.get("Size", 0), "type": v.get("VolumeType", "gp3")})
    stopped_ids = {i["id"] for i in list_instances(cl, reg) if i["state"] == "stopped"}
    for a in cl.describe_addresses().get("Addresses", []):
        if not a.get("AssociationId"):
            res["eips"].append({"reg": reg, "aid": a["AllocationId"], "ip": a.get("PublicIp", "?")})
        elif a.get("InstanceId") in stopped_ids:
            res["stopped_eips"].append({"reg": reg, "aid": a["AllocationId"], "ip": a.get("PublicIp", "?"), "iid": a.get("InstanceId")})
    cutoff = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=30)
    for s in cl.describe_snapshots(OwnerIds=["self"]).get("Snapshots", []):
        if s["StartTime"] < cutoff:
            age_d = (dt.datetime.now(dt.timezone.utc) - s["StartTime"]).days
            res["snaps"].append({"reg": reg, "id": s["SnapshotId"], "gb": s.get("VolumeSize", 0), "age": age_d})
    return res


def launch_templates(cl):
    return cl.describe_launch_templates().get("LaunchTemplates", [])


def launch(cl, lt_id, name):
    kw = {}
    if name: kw["TagSpecifications"] = [{"ResourceType": "instance", "Tags": [{"Key": "Name", "Value": name}]}]
    r = cl.run_instances(LaunchTemplate={"LaunchTemplateId": lt_id, "Version": "$Default"},
                         MinCount=1, MaxCount=1, **kw)
    return r["Instances"][0]["InstanceId"]


# ---------- SSM ----------
def ssm_online(ssm, iid):
    info = ssm.describe_instance_information(Filters=[{"Key": "InstanceIds", "Values": [iid]}])["InstanceInformationList"]
    return bool(info) and info[0].get("PingStatus") == "Online"


def ssm_run(ssm, iid, cmd, timeout=25):
    cid = ssm.send_command(InstanceIds=[iid], DocumentName="AWS-RunShellScript",
                           Parameters={"commands": [cmd]}, TimeoutSeconds=30)["Command"]["CommandId"]
    t0 = time.time()
    while time.time() - t0 < timeout:
        time.sleep(1.5)
        try:
            inv = ssm.get_command_invocation(CommandId=cid, InstanceId=iid)
        except ClientError as e:
            if e.response["Error"]["Code"] == "InvocationDoesNotExist": continue
            raise
        if inv["Status"] in ("Success", "Failed", "Cancelled", "TimedOut"):
            return inv["Status"], (inv.get("StandardOutputContent") or "") + (inv.get("StandardErrorContent") or "")
    return "InProgress", ""
