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


def render(result_dir: Path, output: Path) -> None:
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
                "latency": latency,
                "throughput": 100 / latency,
                "requests": statistics.median(int(trial["requests"]) for trial in group),
            }
        )

    scale = next(trial for trial in trials if trial["trial"] == "crosschunk-stream")
    summary = json.loads((result_dir / "summary.json").read_text())
    best = min(rows, key=lambda row: float(row["latency"]))
    speedup = float(rows[0]["latency"]) / float(best["latency"])

    width, height = 1440, 900
    left, right = 390, 1330
    top, row_gap = 265, 92
    chart_width = right - left
    log_min, log_max = math.log10(0.1), math.log10(100)
    colors = ["#85958c", "#4e7968", "#36795e", "#0b8f62", "#2d6a56"]
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
        '<rect width="100%" height="100%" rx="28" fill="#f7f8f4"/>',
        '<rect x="48" y="42" width="1344" height="816" rx="24" fill="#ffffff" stroke="#dde3dc" stroke-width="2"/>',
        text(92, 112, "Jev × DuckDB: live batching performance", 42, 720),
        text(92, 158, "100 unique nested JSON rows · real Jev API · median of 3 runs", 23, 450, "#526159"),
        text(92, 205, "Median query latency — lower is better (log scale)", 25, 650),
    ]

    for tick in (0.1, 1, 10, 100):
        x = left + (math.log10(tick) - log_min) / (log_max - log_min) * chart_width
        parts.append(f'<line x1="{x:.1f}" y1="225" x2="{x:.1f}" y2="690" stroke="#e5e9e4" stroke-width="2"/>')
        parts.append(text(x - 12, 720, f"{tick:g}s", 18, 500, "#66736c"))

    for index, row in enumerate(rows):
        y = top + index * row_gap
        latency = float(row["latency"])
        bar_width = (math.log10(latency) - log_min) / (log_max - log_min) * chart_width
        parts.extend(
            [
                text(92, y + 28, str(row["label"]), 22, 600),
                text(92, y + 57, f"{int(row['requests'])} HTTP request{'s' if int(row['requests']) != 1 else ''}", 17, 450, "#68766e"),
                f'<rect x="{left}" y="{y}" width="{max(8, bar_width):.1f}" height="54" rx="11" fill="{colors[index]}"/>',
                text(1100, y + 35, f"{latency:.3f}s", 22, 700),
                text(1250, y + 35, f"{float(row['throughput']):.0f} rows/s", 21, 650, "#0b7151"),
            ]
        )

    cards = [
        (92, f"{speedup:.0f}×", "faster than one-at-a-time"),
        (515, f"{float(best['latency']):.3f}s", "fastest 100-row query"),
        (938, f"{float(scale['query_seconds']):.3f}s", f"{int(scale['rows']):,} unique rows"),
    ]
    for x, value, label in cards:
        parts.extend(
            [
                f'<rect x="{x}" y="762" width="360" height="70" rx="14" fill="#edf5f0"/>',
                text(x + 20, 794, value, 27, 750, "#08754f"),
                text(x + 20, 819, label, 16, 500, "#53645a"),
            ]
        )
    transient = sum(int(count) for status, count in summary["http_statuses"].items() if status != "200")
    parts.append(
        text(
            92,
            852,
            f"DuckDB 1.5.5 · Jev {summary['models'][0]} · macOS arm64 · {transient} transient response(s) retried successfully",
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
    args = parser.parse_args()
    render(args.result_dir, args.output)


if __name__ == "__main__":
    main()
