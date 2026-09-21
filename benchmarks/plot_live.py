"""Render a self-contained SVG from a benchmarks.live result directory."""

import argparse
import html
import json
import math
import statistics
from pathlib import Path
from typing import Any


def text(x: float, y: float, value: str, size: int = 24, weight: int = 400, fill: str = "#17231d") -> str:
    return (
        f'<text x="{x}" y="{y}" font-family="Inter,ui-sans-serif,system-ui,sans-serif" '
        f'font-size="{size}" font-weight="{weight}" fill="{fill}">{html.escape(value)}</text>'
    )


def load_trials(result_dir: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in (result_dir / "trials.jsonl").read_text().splitlines() if line.strip()]


def render(result_dir: Path, output: Path, scale_result_dir: Path) -> None:
    trials = load_trials(result_dir)
    configs = [(1, 1), (1, 10), (25, 1), (25, 10), (100, 10)]
    rows: list[dict[str, float | int | str]] = []
    for batch, concurrency in configs:
        group = [
            trial
            for trial in trials
            if trial["mode"] == "stream"
            and trial["trial"].startswith("matrix-")
            and trial["batch"] == batch
            and trial["concurrency"] == concurrency
            and trial["status"] == "ok"
        ]
        if not group:
            raise ValueError(f"Missing stream trials for batch={batch}, concurrency={concurrency}")
        latency = statistics.median(float(trial["query_seconds"]) for trial in group)
        rows.append(
            {
                "label": f"batch {batch} · concurrency {concurrency}",
                "batch": batch,
                "concurrency": concurrency,
                "latency": latency,
                "throughput": 100 / latency,
                "requests": statistics.median(int(trial["requests"]) for trial in group),
            }
        )

    summary = json.loads((result_dir / "summary.json").read_text())
    scale_summary = json.loads((scale_result_dir / "summary.json").read_text())
    thousand_by_batch = {
        int(result["batch"]): float(result["median_query_seconds"]) for result in scale_summary["results"]
    }
    hundred_by_batch = {
        int(row["batch"]): float(row["latency"])
        for row in rows
        if int(row["concurrency"]) == 10 and int(row["batch"]) in (25, 100)
    }

    width, height = 1440, 1150
    left, right = 390, 1330
    top, row_gap = 245, 76
    chart_width = right - left
    log_min, log_max = math.log10(0.1), math.log10(100)
    colors = ["#85958c", "#4e7968", "#36795e", "#0b8f62", "#2d6a56"]
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
        '<rect width="100%" height="100%" rx="28" fill="#f7f8f4"/>',
        '<rect x="48" y="30" width="1344" height="1085" rx="24" fill="#ffffff" stroke="#dde3dc" stroke-width="2"/>',
        text(92, 100, "Jev × DuckDB: live Choice batching performance", 42, 720),
        text(92, 146, "Finite-label classification · nested JSON evidence · real Jev API · median of 3 runs", 23, 450, "#526159"),
        text(92, 198, "100 rows: batching and concurrency", 27, 700),
        text(92, 225, "Median query latency — lower is better (log scale)", 18, 500, "#607068"),
    ]

    for tick in (0.1, 1, 10, 100):
        x = left + (math.log10(tick) - log_min) / (log_max - log_min) * chart_width
        parts.append(f'<line x1="{x:.1f}" y1="235" x2="{x:.1f}" y2="625" stroke="#e5e9e4" stroke-width="2"/>')
        parts.append(text(x - 12, 652, f"{tick:g}s", 18, 500, "#66736c"))

    for index, row in enumerate(rows):
        y = top + index * row_gap
        latency = float(row["latency"])
        bar_width = (math.log10(latency) - log_min) / (log_max - log_min) * chart_width
        parts.extend(
            [
                text(92, y + 25, str(row["label"]), 21, 600),
                text(92, y + 48, f"{int(row['requests'])} HTTP request{'s' if int(row['requests']) != 1 else ''}", 16, 450, "#68766e"),
                f'<rect x="{left}" y="{y}" width="{max(8, bar_width):.1f}" height="48" rx="10" fill="{colors[index]}"/>',
                text(1100, y + 31, f"{latency:.3f}s", 21, 700),
                text(1250, y + 31, f"{float(row['throughput']):.0f} rows/s", 20, 650, "#0b7151"),
            ]
        )

    parts.extend(
        [
            text(92, 725, "100 vs 1,000 rows: batch-size comparison", 27, 700),
            text(92, 754, "Concurrency 10 · median end-to-end query latency", 18, 500, "#607068"),
            '<rect x="985" y="709" width="20" height="20" rx="4" fill="#0b8f62"/>',
            text(1015, 726, "100 rows", 17, 550),
            '<rect x="1135" y="709" width="20" height="20" rx="4" fill="#8abda7"/>',
            text(1165, 726, "1,000 rows", 17, 550),
        ]
    )
    comparison_top, comparison_bottom, comparison_max = 790, 1015, 1.0
    for tick in (0.0, 0.25, 0.5, 0.75, 1.0):
        y = comparison_bottom - (tick / comparison_max) * (comparison_bottom - comparison_top)
        parts.append(f'<line x1="180" y1="{y:.1f}" x2="1320" y2="{y:.1f}" stroke="#e5e9e4" stroke-width="2"/>')
        parts.append(text(115, y + 6, f"{tick:g}s", 17, 500, "#66736c"))
    for center, batch in ((500, 25), (960, 100)):
        values = ((hundred_by_batch[batch], "#0b8f62", -105), (thousand_by_batch[batch], "#8abda7", 15))
        for latency, color, offset in values:
            bar_height = min(latency / comparison_max, 1.0) * (comparison_bottom - comparison_top)
            x = center + offset
            y = comparison_bottom - bar_height
            parts.append(f'<rect x="{x}" y="{y:.1f}" width="90" height="{bar_height:.1f}" rx="9" fill="{color}"/>')
            parts.append(text(x + 8, y - 10, f"{latency:.3f}s", 19, 700))
        parts.append(text(center - 57, 1052, f"batch {batch}", 22, 650))
    transient = sum(int(count) for status, count in summary["http_statuses"].items() if status != "200")
    footer = (
        f"DuckDB 1.5.5 · Jev {summary['models'][0]} · macOS arm64 · matrix: {transient} transient retries · "
        "1,000-row run: 150/150 HTTP 200"
    )
    parts.append(
        text(
            92,
            1095,
            footer,
            16,
            450,
            "#6a756f",
        )
    )
    parts.append("</svg>")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text("\n".join(parts) + "\n")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("result_dir", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--scale-result", type=Path, required=True)
    args = parser.parse_args()
    render(args.result_dir, args.output, args.scale_result)


if __name__ == "__main__":
    main()
