"""Self-contained HTML run report.

One file per run, no external assets: inline CSS and inline SVG only, so
the report opens anywhere (browser, email attachment, artifact hosting)
and survives being moved. Rendering is pure formatting — every number
shown here is computed from the run's report dict, trades, and daily
series; nothing is re-simulated.
"""
from __future__ import annotations

from html import escape
from typing import Any, Optional, Sequence

from full_python.models import Trade

_CSS = """
:root { --bg:#0f1419; --panel:#171e26; --ink:#dce3ea; --dim:#8a97a4;
        --green:#3fb68b; --red:#e0645c; --accent:#4da3ff; --grid:#2a3542; }
* { box-sizing:border-box; margin:0; padding:0; }
body { background:var(--bg); color:var(--ink); font:14px/1.5 -apple-system,'Segoe UI',Roboto,sans-serif; padding:24px; }
h1 { font-size:20px; margin-bottom:2px; }
h2 { font-size:15px; color:var(--dim); font-weight:600; margin:26px 0 10px;
     text-transform:uppercase; letter-spacing:.06em; }
.sub { color:var(--dim); font-size:12px; margin-bottom:18px; }
.grid { display:grid; grid-template-columns:repeat(auto-fit,minmax(150px,1fr)); gap:10px; }
.stat { background:var(--panel); border-radius:8px; padding:12px 14px; }
.stat .k { color:var(--dim); font-size:11px; text-transform:uppercase; letter-spacing:.05em; }
.stat .v { font-size:19px; font-weight:650; margin-top:2px; }
.pos { color:var(--green); } .neg { color:var(--red); }
.panel { background:var(--panel); border-radius:8px; padding:14px; overflow-x:auto; }
table { border-collapse:collapse; width:100%; font-size:13px; }
th, td { text-align:right; padding:5px 10px; border-bottom:1px solid var(--grid); white-space:nowrap; }
th { color:var(--dim); font-weight:600; } td:first-child, th:first-child { text-align:left; }
svg text { font:11px -apple-system,'Segoe UI',Roboto,sans-serif; fill:var(--dim); }
.footer { color:var(--dim); font-size:11px; margin-top:28px; }
"""


def _fmt_money(value: float) -> str:
    sign = "-" if value < 0 else ""
    return f"{sign}${abs(value):,.0f}"


def _money_cell(value: float) -> str:
    cls = "pos" if value > 0 else "neg" if value < 0 else ""
    return f'<span class="{cls}">{_fmt_money(value)}</span>'


def _stat(label: str, value: str, cls: str = "") -> str:
    return (
        f'<div class="stat"><div class="k">{escape(label)}</div>'
        f'<div class="v {cls}">{value}</div></div>'
    )


def _scale(value: float, lo: float, hi: float, out_lo: float, out_hi: float) -> float:
    if hi == lo:
        return (out_lo + out_hi) / 2.0
    return out_lo + (value - lo) / (hi - lo) * (out_hi - out_lo)


def _equity_svg(daily: Sequence[tuple[str, float, float]]) -> str:
    """Cumulative equity polyline with drawdown shading and peak marker."""
    if not daily:
        return "<p class='sub'>No daily data.</p>"
    width, height, pad = 880.0, 260.0, 40.0
    cumulative = [row[2] for row in daily]
    lo = min(min(cumulative), 0.0)
    hi = max(max(cumulative), 0.0)

    def x(i: int) -> float:
        return _scale(i, 0, max(len(daily) - 1, 1), pad, width - 10)

    def y(v: float) -> float:
        return _scale(v, lo, hi, height - 24, 12)

    points = " ".join(f"{x(i):.1f},{y(v):.1f}" for i, v in enumerate(cumulative))
    peak_points, peak = [], cumulative[0]
    for i, v in enumerate(cumulative):
        peak = max(peak, v)
        peak_points.append(f"{x(i):.1f},{y(peak):.1f}")
    dd_area = (
        " ".join(peak_points)
        + " "
        + " ".join(f"{x(i):.1f},{y(v):.1f}" for i, v in reversed(list(enumerate(cumulative))))
    )
    zero_y = y(0.0)
    n = len(daily)
    ticks = sorted({0, n // 4, n // 2, (3 * n) // 4, n - 1})
    tick_labels = "".join(
        f'<text x="{x(i):.1f}" y="{height - 6:.1f}" text-anchor="middle">{escape(daily[i][0][5:])}</text>'
        for i in ticks
    )
    return f"""<svg viewBox="0 0 {width:.0f} {height:.0f}" role="img" aria-label="Equity curve">
<line x1="{pad}" y1="{zero_y:.1f}" x2="{width - 10}" y2="{zero_y:.1f}" stroke="#2a3542" stroke-dasharray="4 4"/>
<polygon points="{dd_area}" fill="#e0645c" opacity="0.16"/>
<polyline points="{" ".join(peak_points)}" fill="none" stroke="#8a97a4" stroke-width="1" opacity="0.5"/>
<polyline points="{points}" fill="none" stroke="#4da3ff" stroke-width="2"/>
<text x="{pad}" y="{y(hi) + 4:.1f}">{escape(_fmt_money(hi))}</text>
<text x="{pad}" y="{max(y(lo) - 4, 12):.1f}">{escape(_fmt_money(lo))}</text>
{tick_labels}
</svg>"""


def _histogram_svg(pnls: Sequence[float], bins: int = 25) -> str:
    if not pnls:
        return "<p class='sub'>No trades.</p>"
    width, height, pad = 880.0, 180.0, 40.0
    lo, hi = min(pnls), max(pnls)
    if lo == hi:
        lo, hi = lo - 1.0, hi + 1.0
    counts = [0] * bins
    for pnl in pnls:
        index = min(int((pnl - lo) / (hi - lo) * bins), bins - 1)
        counts[index] += 1
    top = max(counts)
    bar_width = (width - pad - 10) / bins
    bars = []
    for i, count in enumerate(counts):
        if count == 0:
            continue
        bin_mid = lo + (i + 0.5) / bins * (hi - lo)
        bar_height = _scale(count, 0, top, 0, height - 42)
        color = "#3fb68b" if bin_mid > 0 else "#e0645c"
        bars.append(
            f'<rect x="{pad + i * bar_width:.1f}" y="{height - 24 - bar_height:.1f}" '
            f'width="{max(bar_width - 1.5, 1):.1f}" height="{bar_height:.1f}" fill="{color}" opacity="0.85"/>'
        )
    zero_x = _scale(0.0, lo, hi, pad, width - 10)
    return f"""<svg viewBox="0 0 {width:.0f} {height:.0f}" role="img" aria-label="Trade P&L histogram">
{"".join(bars)}
<line x1="{zero_x:.1f}" y1="10" x2="{zero_x:.1f}" y2="{height - 24}" stroke="#8a97a4" stroke-dasharray="3 3"/>
<text x="{pad}" y="{height - 8:.1f}">{escape(_fmt_money(lo))}</text>
<text x="{width - 10:.1f}" y="{height - 8:.1f}" text-anchor="end">{escape(_fmt_money(hi))}</text>
<text x="{pad}" y="16">max bin: {top} trades</text>
</svg>"""


def _monthly_table(monthly: dict[str, dict[str, float]]) -> str:
    """Renders build_monthly_breakdown output: month -> {net_pnl, days_with_trades}."""
    if not monthly:
        return "<p class='sub'>No monthly data.</p>"
    values = {month: float(bucket.get("net_pnl", 0.0)) for month, bucket in monthly.items()}
    peak = max(abs(v) for v in values.values()) or 1.0
    rows = []
    for month in sorted(values):
        value = values[month]
        days = monthly[month].get("days_with_trades", "")
        alpha = min(abs(value) / peak, 1.0) * 0.45
        color = f"rgba(63,182,139,{alpha:.2f})" if value >= 0 else f"rgba(224,100,92,{alpha:.2f})"
        rows.append(
            f'<tr><td>{escape(month)}</td>'
            f'<td style="background:{color}">{_money_cell(value)}</td>'
            f"<td>{days}</td></tr>"
        )
    return (
        "<table><tr><th>Month</th><th>Net P&L</th><th>Days w/ trades</th></tr>"
        f"{''.join(rows)}</table>"
    )


def _trade_stats(trades: Sequence[Trade]) -> dict[str, Any]:
    pnls = [t.net_pnl for t in trades]
    wins = sorted(p for p in pnls if p > 0)
    losses = sorted(p for p in pnls if p <= 0)
    gross_win = sum(wins)
    gross_loss = -sum(losses)

    def median(values: Sequence[float]) -> float:
        if not values:
            return 0.0
        mid = len(values) // 2
        return values[mid] if len(values) % 2 else (values[mid - 1] + values[mid]) / 2.0

    streak = max_streak = 0
    for pnl in pnls:
        streak = streak + 1 if pnl <= 0 else 0
        max_streak = max(max_streak, streak)
    quantities: dict[int, int] = {}
    for trade in trades:
        quantities[trade.quantity] = quantities.get(trade.quantity, 0) + 1
    return {
        "count": len(pnls),
        "win_rate": len(wins) / len(pnls) if pnls else 0.0,
        "profit_factor": gross_win / gross_loss if gross_loss > 0 else float("inf"),
        "mean_win": gross_win / len(wins) if wins else 0.0,
        "median_win": median(wins),
        "largest_win": wins[-1] if wins else 0.0,
        "mean_loss": -gross_loss / len(losses) if losses else 0.0,
        "median_loss": median(losses),
        "largest_loss": losses[0] if losses else 0.0,
        "max_consecutive_losses": max_streak,
        "quantities": quantities,
    }


def render_html_report(
    report: dict[str, Any],
    trades: Sequence[Trade],
    daily: Sequence[tuple[str, float, float]],
    rejections: Optional[dict[str, int]] = None,
) -> str:
    surv = report.get("survivability", {})
    daily_metrics = report.get("daily", {})
    stats = _trade_stats(trades)
    strategy = report.get("strategy", {})
    data = report.get("data", {})
    net = surv.get("net_pnl", 0.0)
    pf = stats["profit_factor"]
    bootstrap = report.get("bootstrap", {})
    annualized_ci = bootstrap.get("annualized_net_pnl_95", {})
    top_5_day_share = daily_metrics.get("top_5_day_share") or 0.0

    exit_rows = "".join(
        f"<tr><td>{escape(reason)}</td><td>{count}</td></tr>"
        for reason, count in sorted(report.get("exit_reasons", {}).items(), key=lambda kv: -kv[1])
    )
    qty_rows = "".join(
        f"<tr><td>{qty} contract{'s' if qty != 1 else ''}</td><td>{count}</td></tr>"
        for qty, count in sorted(stats["quantities"].items())
    )
    rejection_rows = "".join(
        f"<tr><td>{escape(reason)}</td><td>{count}</td></tr>"
        for reason, count in sorted((rejections or {}).items(), key=lambda kv: -kv[1])
    )
    rejection_section = (
        f"<h2>Rejected signals by gate</h2><div class='panel'><table>"
        f"<tr><th>Gate</th><th>Count</th></tr>{rejection_rows}</table></div>"
        if rejection_rows
        else ""
    )

    return f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{escape(str(strategy.get('name', 'run')))} — {escape(str(report.get('run_id', '')))}</title>
<style>{_CSS}</style></head><body>
<h1>{escape(str(strategy.get('name', 'Run report')))}</h1>
<div class="sub">run {escape(str(report.get('run_id', '')))} &middot;
{escape(str(data.get('start_timestamp_utc', ''))[:10])} &rarr; {escape(str(data.get('end_timestamp_utc', ''))[:10])} &middot;
{escape(str(data.get('dataset_name', '')))}</div>

<div class="grid">
{_stat("Net P&L", _fmt_money(net), "pos" if net >= 0 else "neg")}
{_stat("Trades", str(stats['count']))}
{_stat("Win rate", f"{stats['win_rate'] * 100:.1f}%")}
{_stat("Profit factor", "∞" if pf == float('inf') else f"{pf:.3f}")}
{_stat("Max drawdown", _fmt_money(surv.get('max_drawdown', 0.0)), "neg")}
{_stat("Daily Sharpe (ann.)", f"{daily_metrics.get('sharpe_annualized', 0.0):.2f}")}
{_stat("Profitable days", f"{daily_metrics.get('profitable_day_rate', 0.0) * 100:.1f}%")}
{_stat("Best day", _fmt_money(daily_metrics.get('best_day_pnl', 0.0)), "pos")}
{_stat("Worst day", _fmt_money(daily_metrics.get('worst_day_pnl', 0.0)), "neg")}
{_stat("Max losing streak", f"{stats['max_consecutive_losses']} trades")}
{_stat("Time underwater", f"{daily_metrics.get('max_time_underwater_days', 0)} days")}
{_stat("Ambiguous exits", str(report.get('ambiguous_exits', 0)))}
</div>

<h2>Session-block bootstrap</h2>
<div class="panel"><table>
<tr><th>Measure</th><th>Result</th></tr>
<tr><td>Annualized net P&amp;L 95% interval</td><td>{_fmt_money(annualized_ci.get('lower', 0.0))} to {_fmt_money(annualized_ci.get('upper', 0.0))}</td></tr>
<tr><td>Median max drawdown</td><td>{_fmt_money(bootstrap.get('max_drawdown_median', 0.0))}</td></tr>
<tr><td>p95 adverse max drawdown</td><td>{_fmt_money(bootstrap.get('max_drawdown_p95_adverse', 0.0))}</td></tr>
<tr><td>p99 adverse max drawdown</td><td>{_fmt_money(bootstrap.get('max_drawdown_p99_adverse', 0.0))}</td></tr>
<tr><td>P(total net &le; 0)</td><td>{bootstrap.get('probability_total_net_nonpositive', 0.0) * 100:.2f}%</td></tr>
<tr><td>Method</td><td>{bootstrap.get('draws', 0)} draws, {bootstrap.get('block_length_sessions', 0)}-session moving blocks, seed {bootstrap.get('seed', '')}</td></tr>
</table></div>

<h2>Right-tail dependency</h2>
<div class="panel"><table>
<tr><th>Removal</th><th>Remaining net P&amp;L</th></tr>
<tr><td>Best trade</td><td>{_fmt_money(surv.get('pnl_without_best_trade', 0.0))}</td></tr>
<tr><td>Top 3 trades</td><td>{_fmt_money(surv.get('pnl_without_top_3_trades', 0.0))}</td></tr>
<tr><td>Top 5 trades</td><td>{_fmt_money(surv.get('pnl_without_top_5_trades', 0.0))}</td></tr>
<tr><td>Top 10 trades</td><td>{_fmt_money(surv.get('pnl_without_top_10_trades', 0.0))}</td></tr>
<tr><td>Top 5 days</td><td>{_fmt_money(daily_metrics.get('pnl_without_top_5_days', 0.0))} remaining ({top_5_day_share * 100:.1f}% of net removed)</td></tr>
</table></div>

<h2>Equity curve (daily close, drawdown shaded)</h2>
<div class="panel">{_equity_svg(daily)}</div>

<h2>Trade P&L distribution</h2>
<div class="panel">{_histogram_svg([t.net_pnl for t in trades])}</div>

<h2>Winners vs losers</h2>
<div class="panel"><table>
<tr><th></th><th>Mean</th><th>Median</th><th>Largest</th></tr>
<tr><td>Wins</td><td>{_money_cell(stats['mean_win'])}</td><td>{_money_cell(stats['median_win'])}</td><td>{_money_cell(stats['largest_win'])}</td></tr>
<tr><td>Losses</td><td>{_money_cell(stats['mean_loss'])}</td><td>{_money_cell(stats['median_loss'])}</td><td>{_money_cell(stats['largest_loss'])}</td></tr>
</table></div>

<h2>Monthly P&L</h2>
<div class="panel">{_monthly_table(report.get('monthly', {}))}</div>

<h2>Exits &amp; sizing</h2>
<div class="panel"><table>
<tr><th>Exit reason</th><th>Count</th></tr>{exit_rows}
</table><br><table>
<tr><th>Entry size</th><th>Count</th></tr>{qty_rows}
</table></div>

{rejection_section}

<div class="footer">Deterministic run — same data + configs reproduce this report byte-for-byte.
Strategy hash {escape(str(strategy.get('parameter_hash', ''))[:12])} &middot;
simulation hash {escape(str(report.get('simulation', {}).get('parameter_hash', ''))[:12])}</div>
</body></html>
"""
