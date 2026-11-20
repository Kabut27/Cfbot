"""EC2 / Security group / Elastic IP / SSM logic (blocking boto3 calls)."""
import time
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
ICONS = {"running": "🟢", "stopped": "🔴", "pending": "🟡", "stopping": "🟡",
         "shutting-down": "🟡", "terminated": "⚫"}
LIVE = ["pending", "running", "stopping", "stopped", "shutting-down"]

# Safe presets only (no free-form shell from Telegram). (label, command, needs_confirm)
PRESETS = {
    "xr": ("🔄 Restart x-ui", "systemctl restart x-ui && sleep 1 && systemctl is-active x-ui", True),
    "xs": ("ℹ️ x-ui status", "systemctl status x-ui --no-pager | head -15", False),
    "ng": ("🔄 Restart nginx", "systemctl restart nginx && systemctl is-active nginx", True),
    "df": ("💽 Disk (df -h)", "df -h", False),
    "fr": ("🧠 RAM (free -m)", "free -m", False),
    "up": ("⏱ Uptime/load", "uptime", False),
    "tp": ("🔥 Top CPU", "ps aux --sort=-%cpu | head -8", False),
}


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


# ---------- security groups ----------
def cf_prefix_list(cl):
    r = cl.describe_managed_prefix_lists(Filters=[{"Name": "prefix-list-name", "Values": [config.CF_PREFIX_LIST_NAME]}])
    pls = r.get("PrefixLists", [])
    if not pls: raise ValueError("CloudFront prefix list haipatikani kwenye region hii.")
    return pls[0]["PrefixListId"]


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
    port = "all" if r["proto"] == "-1" else (f"{r['proto']} {r['fp']}" if r["fp"] == r["tp"]
                                            else f"{r['proto']} {r['fp']}-{r['tp']}")
    src = r["src"]
    if r["kind"] == "pl": src = "☁️ CloudFront" if src == cfpl else f"🧩 {src}"
    elif src in ("0.0.0.0/0", "::/0"): src = "🌍 Anywhere"
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


def authorize(cl, sgid, port, src_kind, src):
    p = {"IpProtocol": "tcp", "FromPort": port, "ToPort": port}
    if src_kind == "pl": p["PrefixListIds"] = [{"PrefixListId": src, "Description": "cfbot CloudFront"}]
    else: p["IpRanges"] = [{"CidrIp": src, "Description": "cfbot"}]
    cl.authorize_security_group_ingress(GroupId=sgid, IpPermissions=[p])


# ---------- Elastic IP / snapshots / launch ----------
def eip_attach(cl, iid):
    a = cl.allocate_address(Domain="vpc")
    try:
        cl.associate_address(AllocationId=a["AllocationId"], InstanceId=iid)
    except Exception:
        cl.release_address(AllocationId=a["AllocationId"])   # don't leak a billed IP
        raise
    return a["PublicIp"]


def eip_release(cl, addr):
    if addr.get("AssociationId"): cl.disassociate_address(AssociationId=addr["AssociationId"])
    cl.release_address(AllocationId=addr["AllocationId"])


def snapshot(cl, iid):
    r = cl.create_snapshots(InstanceSpecification={"InstanceId": iid, "ExcludeBootVolume": False},
                            Description=f"cfbot {iid}",
                            TagSpecifications=[{"ResourceType": "snapshot", "Tags": [{"Key": "cfbot", "Value": "1"}]}])
    return [s["SnapshotId"] for s in r["Snapshots"]]


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
