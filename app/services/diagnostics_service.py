def infer_phase(latest_metric, vocab):
    """Determine phase from latest MetricEntry (or None → player)."""
    if latest_metric is None:
        return "player"
    thresholds = vocab["phase_thresholds"]
    avg_impr = latest_metric.avg_impressions or 0
    followers_delta = latest_metric.followers_delta_per_day or 0
    if (avg_impr >= thresholds["monetize_ready"]["avg_impressions"] and
            followers_delta * 30 >= thresholds["monetize_ready"]["followers"]):
        return "monetize"
    elif avg_impr >= thresholds["early"]["min_avg_impressions"]:
        return "early"
    return "player"


def funnel_diagnostics(latest_metric, vocab):
    """Return list of diagnostic dicts for each funnel metric."""
    if latest_metric is None:
        return []
    results = []
    for diag in vocab["funnel_diagnostics"]:
        val = getattr(latest_metric, diag["metric"], None)
        if val is None:
            continue
        status = "ok" if float(val) >= diag["target"] else "warning"
        results.append({
            "label": diag["label"],
            "value": val,
            "target": diag["target"],
            "status": status,
            "cause": diag["cause"],
            "action": diag["action"],
        })
    return results


PHASE_LABELS = {
    "player": "プレイヤー",
    "early": "アーリー",
    "monetize": "マネタイズ",
}
