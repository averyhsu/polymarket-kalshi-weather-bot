"""Calibration analytics for forecast quality."""

from __future__ import annotations

from collections import defaultdict
from typing import Dict, List

from db.models import Database


def summarize_calibration(database: Database) -> Dict[str, object]:
    """Compute Brier score and a simple calibration table."""

    rows = [row for row in database.recent_calibration_rows() if row["settled_value"] is not None]
    if not rows:
        return {"count": 0, "brier_score": None, "buckets": []}

    brier_score = sum(float(row["brier_component"]) for row in rows if row["brier_component"] is not None) / len(rows)
    buckets = defaultdict(lambda: {"count": 0, "predicted_sum": 0.0, "actual_sum": 0.0})
    for row in rows:
        probability = float(row["predicted_probability"])
        bucket_start = int(probability * 10) * 10
        bucket_label = f"{bucket_start:02d}-{bucket_start + 9:02d}%"
        buckets[bucket_label]["count"] += 1
        buckets[bucket_label]["predicted_sum"] += probability
        buckets[bucket_label]["actual_sum"] += float(row["settled_value"])

    bucket_rows: List[Dict[str, object]] = []
    for label in sorted(buckets):
        data = buckets[label]
        bucket_rows.append(
            {
                "bucket": label,
                "count": data["count"],
                "avg_predicted": data["predicted_sum"] / data["count"],
                "avg_actual": data["actual_sum"] / data["count"],
            }
        )
    return {"count": len(rows), "brier_score": brier_score, "buckets": bucket_rows}
