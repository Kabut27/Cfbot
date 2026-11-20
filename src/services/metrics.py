"""CloudWatch stats. Uses get_metric_statistics (covered by CloudWatch's free API tier,
unlike get_metric_data which is billed per metric)."""
import datetime as dt

CF = "AWS/CloudFront"


def _now(): return dt.datetime.now(dt.timezone.utc)

def month_start():
    return _now().replace(day=1, hour=0, minute=0, second=0, microsecond=0)

def _points(cw, ns, metric, dims, start, end, period, stat):
    r = cw.get_metric_statistics(Namespace=ns, MetricName=metric, Dimensions=dims,
                                 StartTime=start, EndTime=end, Period=period, Statistics=[stat])
    return sorted(r["Datapoints"], key=lambda p: p["Timestamp"])

def _cf_dims(did):
    return [{"Name": "DistributionId", "Value": did}, {"Name": "Region", "Value": "Global"}]

GIB = 1024 ** 3


def cf_month_gb(cw, did):
    pts = _points(cw, CF, "BytesDownloaded", _cf_dims(did), month_start(), _now(), 86400, "Sum")
    return sum(p["Sum"] for p in pts) / GIB

def cf_month_detail(cw, did):
    """-> {'gb', 'req', 'daily': [gb per day]}"""
    start, end = month_start(), _now()
    b = _points(cw, CF, "BytesDownloaded", _cf_dims(did), start, end, 86400, "Sum")
    r = _points(cw, CF, "Requests", _cf_dims(did), start, end, 86400, "Sum")
    return {"gb": sum(p["Sum"] for p in b) / GIB, "req": sum(p["Sum"] for p in r),
            "daily": [p["Sum"] / GIB for p in b]}

def cf_rates(cw, did, minutes=60):
    """-> {'e4', 'e5' (percent, None if no traffic), 'req'}"""
    end = _now(); start = end - dt.timedelta(minutes=minutes); period = max(60, minutes * 60)
    def avg(metric):
        pts = _points(cw, CF, metric, _cf_dims(did), start, end, period, "Average")
        return sum(p["Average"] for p in pts) / len(pts) if pts else None
    req = _points(cw, CF, "Requests", _cf_dims(did), start, end, period, "Sum")
    return {"e4": avg("4xxErrorRate"), "e5": avg("5xxErrorRate"), "req": sum(p["Sum"] for p in req)}

def cf_health(cw, did, minutes=15):
    """Cheaper check for the alert job: 2 API calls."""
    end = _now(); start = end - dt.timedelta(minutes=minutes); period = minutes * 60
    e5 = _points(cw, CF, "5xxErrorRate", _cf_dims(did), start, end, period, "Average")
    rq = _points(cw, CF, "Requests", _cf_dims(did), start, end, period, "Sum")
    return (sum(p["Average"] for p in e5) / len(e5) if e5 else None), sum(p["Sum"] for p in rq)


def ec2_series(cw, iid, hours=1):
    end = _now(); start = end - dt.timedelta(hours=hours)
    d = [{"Name": "InstanceId", "Value": iid}]
    cpu = _points(cw, "AWS/EC2", "CPUUtilization", d, start, end, 300, "Average")
    out = _points(cw, "AWS/EC2", "NetworkOut", d, start, end, 300, "Sum")
    inn = _points(cw, "AWS/EC2", "NetworkIn", d, start, end, 300, "Sum")
    return {"cpu": [p["Average"] for p in cpu], "out": sum(p["Sum"] for p in out),
            "in": sum(p["Sum"] for p in inn)}

def ec2_cpu_now(cw, iid):
    end = _now(); start = end - dt.timedelta(minutes=10)
    pts = _points(cw, "AWS/EC2", "CPUUtilization", [{"Name": "InstanceId", "Value": iid}], start, end, 300, "Average")
    return pts[-1]["Average"] if pts else None
