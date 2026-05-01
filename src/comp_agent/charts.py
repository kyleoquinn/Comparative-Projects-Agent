from __future__ import annotations

from pathlib import Path

from comp_agent.models import CompCandidate, MetricSummary, SourceLogEntry


def _escape(text: object) -> str:
    return (
        str(text)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def _write_svg(path: str | Path, body: str, width: int = 1200, height: int = 675) -> Path:
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    svg = f"""<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">
  <rect width="{width}" height="{height}" fill="#fbfaf7"/>
  <style>
    text {{ font-family: Aptos, Arial, sans-serif; fill: #262626; }}
    .muted {{ fill: #666; font-size: 22px; }}
    .title {{ font-size: 34px; font-weight: 700; }}
    .label {{ font-size: 22px; font-weight: 600; }}
    .small {{ font-size: 18px; fill: #666; }}
  </style>
{body}
</svg>
"""
    output.write_text(svg, encoding="utf-8")
    return output


def create_comp_readiness_chart(candidates: list[CompCandidate], path: str | Path) -> Path:
    rows = []
    y = 130
    for candidate in candidates[:8]:
        width = int(max(0, min(100, candidate.relevance_score)) * 7.2)
        rows.append(f'<text x="70" y="{y}" class="label">{_escape(candidate.comp_type.title())}</text>')
        rows.append(f'<rect x="360" y="{y - 26}" width="720" height="34" rx="4" fill="#e8e3dc"/>')
        rows.append(f'<rect x="360" y="{y - 26}" width="{width}" height="34" rx="4" fill="#4f7f73"/>')
        rows.append(f'<text x="1100" y="{y}" class="small">{candidate.relevance_score}/100</text>')
        rows.append(f'<text x="360" y="{y + 35}" class="small">{_escape(candidate.status.replace("_", " "))}</text>')
        y += 72
    body = "\n".join(
        [
            '<text x="70" y="70" class="title">Comp Readiness by Lane</text>',
            '<text x="70" y="102" class="muted">Higher score means stronger strategic fit once sources are confirmed.</text>',
            *rows,
        ]
    )
    return _write_svg(path, body)


def create_source_coverage_chart(source_log: list[SourceLogEntry], path: str | Path) -> Path:
    types: dict[str, int] = {}
    for entry in source_log:
        types[entry.source_type] = types.get(entry.source_type, 0) + 1
    rows = []
    max_count = max(types.values(), default=1)
    y = 145
    for source_type, count in sorted(types.items()):
        width = int((count / max_count) * 650)
        rows.append(f'<text x="70" y="{y}" class="label">{_escape(source_type)}</text>')
        rows.append(f'<rect x="430" y="{y - 26}" width="650" height="34" rx="4" fill="#e8e3dc"/>')
        rows.append(f'<rect x="430" y="{y - 26}" width="{width}" height="34" rx="4" fill="#6f7fa8"/>')
        rows.append(f'<text x="1100" y="{y}" class="small">{count}</text>')
        y += 72
    body = "\n".join(
        [
            '<text x="70" y="70" class="title">Planned Source Coverage</text>',
            '<text x="70" y="102" class="muted">Source categories queued for evidence collection.</text>',
            *rows,
        ]
    )
    return _write_svg(path, body)


def create_metric_snapshot(metrics: list[MetricSummary], path: str | Path) -> Path:
    cards = []
    positions = [(70, 130), (640, 130), (70, 350), (640, 350)]
    for metric, (x, y) in zip(metrics[:4], positions):
        cards.append(f'<rect x="{x}" y="{y}" width="490" height="155" rx="6" fill="#ffffff" stroke="#d9d3ca"/>')
        cards.append(f'<text x="{x + 24}" y="{y + 45}" class="small">{_escape(metric.metric)}</text>')
        cards.append(f'<text x="{x + 24}" y="{y + 88}" class="label">{_escape(metric.value)}</text>')
        cards.append(f'<text x="{x + 24}" y="{y + 125}" class="small">Confidence: {_escape(metric.confidence)}</text>')
    body = "\n".join(
        [
            '<text x="70" y="70" class="title">Concept Metrics Snapshot</text>',
            '<text x="70" y="102" class="muted">Stable summary tiles for early presentations.</text>',
            *cards,
        ]
    )
    return _write_svg(path, body)

