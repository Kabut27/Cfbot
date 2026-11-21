"""CloudWatch stats (free get_metric_statistics API) + AWS/Billing USD + Forecasting + TCP probe."""
import asyncio, calendar, time, datetime as dt

CF = "AWS/CloudFront"
GIB = 1024 ** 3


def _now(): return dt.datetime.now(dt.timezone.utc)

def month_start():
    return _now().replace(day=1, hour=0, minute=0, second=0, microsecond=0)

def _points(cw, ns, metric, dims, start, end, period, stat):
    r = cw.get_metric_statistics(Namespace=ns, MetricName=metric, Dimensions=dims,
                                 StartTime=start, EndTime=end, Period=period, Statistics=[stat])
    return sorted(r.get("Datapoints", []), key=lambda p: p["Timestamp"])

def _cf_dims(did):
    return [{"Name": "DistributionId", "Value": did}, {"Name": "Region", "Value": "Global"}]


def cf_month_usage(cw, did):
    start, end = month_start(), _now()
    b = _points(cw, CF, "BytesDownloaded", _cf_dims(did), start, end, 86400, "Sum")
    r = _points(cw, CF, "Requests", _cf_dims(did), start, end, 86400, "Sum")
    return sum(p["Sum"] for p in b) / GIB, float(sum(p["Sum"] for p in r))


def cf_month_gb(cw, did):
    pts = _points(cw, CF, "BytesDownloaded", _cf_dims(did), month_start(), _now(), 86400, "Sum")
    return sum(p["Sum"] for p in pts) / GIB


def cf_full_metrics(cw, did):
    now = _now()
    start_mo = month_start()
    start_7d = (now - dt.timedelta(days=6)).replace(hour=0, minute=0, second=0, microsecond=0)
    start_q = min(start_mo, start_7d)
    start_24h = now - dt.timedelta(hours=24)
    dims = _cf_dims(did)

    def _daily(metric):
        pts = _points(cw, CF, metric, dims, start_q, now, 86400, "Sum")
        by_day = {}
        mo_sum = 0.0
        for p in pts:
            ts = p["Timestamp"]
            v = float(p["Sum"])
            dkey = ts.strftime("%Y-%m-%d")
            by_day[dkey] = by_day.get(dkey, 0.0) + v
            if ts >= start_mo:
                mo_sum += v
        trend = [by_day.get((now - dt.timedelta(days=i)).strftime("%Y-%m-%d"), 0.0) for i in range(6, -1, -1)]
        return mo_sum, trend

    def _sum_24h(metric):
        pts = _points(cw, CF, metric, dims, start_24h, now, 3600, "Sum")
        return float(sum(p["Sum"] for p in pts))

    def _avg_24h(metric):
        pts = _points(cw, CF, metric, dims, start_24h, now, 3600, "Average")
        return float(sum(p["Average"] for p in pts) / len(pts)) if pts else 0.0

    b_mo, b_7d = _daily("BytesDownloaded")
    r_mo, r_7d = _daily("Requests")
    return {
        "gb": b_mo / GIB,
        "gb_24h": _sum_24h("BytesDownloaded") / GIB,
        "gb_7d": [x / GIB for x in b_7d],
        "req": r_mo,
        "req_24h": _sum_24h("Requests"),
        "req_7d": r_7d,
        "e4": _avg_24h("4xxErrorRate"),
        "e5": _avg_24h("5xxErrorRate"),
    }


def forecast_usage(used, limit, unit="GB", now=None):
    """Calculates daily burn rate, end-of-month projection, and days until hitting limit."""
    now = now or _now()
    days_in_mo = calendar.monthrange(now.year, now.month)[1]
    ms = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    elapsed_days = max(0.25, (now - ms).total_seconds() / 86400.0)
    days_left_mo = max(0.0, days_in_mo - elapsed_days)

    burn_daily = float(used) / elapsed_days
    projected = burn_daily * days_in_mo
    proj_pct = (projected / float(limit) * 100.0) if limit > 0 else 0.0

    if used >= limit:
        days_to_limit = 0
        summary_sw = f"🚨 Kikomo cha {limit:,.0f} {unit} tayari kimevukwa!"
    elif burn_daily <= 0:
        days_to_limit = None
        summary_sw = f"🟢 Hakuna matumizi bado ({unit})."
    else:
        rem = float(limit) - float(used)
        days_to_limit = rem / burn_daily
        if days_to_limit <= days_left_mo:
            summary_sw = f"⚠️ Kwa kasi ya sasa: utafikia {limit:,.0f} {unit} ndani ya siku {max(1, int(round(days_to_limit)))}!"
        else:
            summary_sw = f"🟢 Salama: makadirio mwisho wa mwezi ni {projected:,.1f} {unit} ({proj_pct:.0f}%)."

    return {
        "burn_daily": burn_daily,
        "projected": projected,
        "proj_pct": proj_pct,
        "days_to_limit": days_to_limit,
        "days_in_mo": days_in_mo,
        "summary_sw": summary_sw,
    }


def cf_health(cw, did, minutes=10):
    end = _now(); start = end - dt.timedelta(minutes=minutes)
    period = 300 if minutes <= 15 else minutes * 60
    e5 = _points(cw, CF, "5xxErrorRate", _cf_dims(did), start, end, period, "Average")
    rq = _points(cw, CF, "Requests", _cf_dims(did), start, end, period, "Sum")
    return (sum(p["Average"] for p in e5) / len(e5) if e5 else 0.0), float(sum(p["Sum"] for p in rq))


def billing_usd(cw):
    try:
        end = _now(); start = end - dt.timedelta(hours=24)
        pts = _points(cw, "AWS/Billing", "EstimatedCharges",
                      [{"Name": "Currency", "Value": "USD"}], start, end, 21600, "Maximum")
        return float(pts[-1]["Maximum"]) if pts else None
    except Exception:
        return None


def ec2_metrics(cw, iid):
    end = _now()
    d = [{"Name": "InstanceId", "Value": iid}]
    cpu = _points(cw, "AWS/EC2", "CPUUtilization", d, end - dt.timedelta(hours=1), end, 300, "Average")
    out = _points(cw, "AWS/EC2", "NetworkOut", d, end - dt.timedelta(hours=24), end, 3600, "Sum")
    inn = _points(cw, "AWS/EC2", "NetworkIn", d, end - dt.timedelta(hours=24), end, 3600, "Sum")
    cpu_vals = [float(p["Average"]) for p in cpu]
    return {
        "cpu": cpu_vals[-1] if cpu_vals else 0.0,
        "cpu_vals": cpu_vals,
        "net_out_gb": float(sum(p["Sum"] for p in out)) / GIB,
        "net_in_gb": float(sum(p["Sum"] for p in inn)) / GIB,
    }


def ec2_cpu_now(cw, iid):
    end = _now(); start = end - dt.timedelta(minutes=10)
    pts = _points(cw, "AWS/EC2", "CPUUtilization", [{"Name": "InstanceId", "Value": iid}], start, end, 300, "Average")
    return float(pts[-1]["Average"]) if pts else None


async def tcp_probe(host, port, timeout=5.0):
    t0 = time.monotonic()
    try:
        _, writer = await asyncio.wait_for(asyncio.open_connection(host, int(port)), timeout=timeout)
        ms = (time.monotonic() - t0) * 1000.0
        writer.close()
        try: await writer.wait_closed()
        except Exception: pass
        return True, ms, "OPEN"
    except asyncio.TimeoutError:
        return False, timeout * 1000.0, "TIMEOUT"
    except OSError as e:
        ms = (time.monotonic() - t0) * 1000.0
        return False, ms, e.strerror or type(e).__name__
