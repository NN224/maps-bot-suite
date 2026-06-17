"""Rich Live Dashboard — real-time view of bot + sessions + logs."""
from __future__ import annotations

import os
import subprocess
import time
from datetime import datetime
from pathlib import Path

from rich.align import Align
from rich.console import Console, Group
from rich.layout import Layout
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from rich import box

console = Console()
LOG_FILE = "/tmp/bot_suite.log"


def _is_running() -> int | None:
    try:
        r = subprocess.run(
            ["pgrep", "-f", "scenarios.runner"],
            capture_output=True, text=True, timeout=2,
        )
        if r.returncode == 0:
            pids = [int(x) for x in r.stdout.split() if x.strip().isdigit()]
            return pids[0] if pids else None
    except Exception:
        pass
    return None


def _fetch_stats() -> dict:
    from shared.db import _pg_exec
    try:
        rows = _pg_exec(
            "SELECT COUNT(*) AS total, "
            "SUM(CASE WHEN status='success' THEN 1 ELSE 0 END) AS success, "
            "SUM(CASE WHEN status='partial' THEN 1 ELSE 0 END) AS partial, "
            "SUM(CASE WHEN status='failed' THEN 1 ELSE 0 END) AS failed, "
            "ROUND(AVG(CASE WHEN status<>'failed' AND autocomplete_position IS NOT NULL "
            "  THEN autocomplete_position END)::numeric, 1) AS avg_rank, "
            "MIN(CASE WHEN status<>'failed' THEN autocomplete_position END) AS best_rank, "
            "SUM(CASE WHEN directions_clicked THEN 1 ELSE 0 END) AS directions, "
            "SUM(CASE WHEN call_clicked THEN 1 ELSE 0 END) AS calls, "
            "SUM(CASE WHEN website_clicked THEN 1 ELSE 0 END) AS website, "
            "SUM(CASE WHEN reviews_scrolled THEN 1 ELSE 0 END) AS reviews, "
            "ROUND(AVG(CASE WHEN status<>'failed' THEN dwell_time_seconds END)::numeric, 1) AS avg_dwell "
            "FROM bot_sessions WHERE DATE(created_at AT TIME ZONE 'Asia/Dubai') = CURRENT_DATE",
            fetchall=True,
        ) or [{}]
        return rows[0]
    except Exception:
        return {}


def _fetch_recent_sessions(limit: int = 12) -> list[dict]:
    from shared.db import _pg_exec
    try:
        return _pg_exec(
            "SELECT id, to_char(created_at AT TIME ZONE 'Asia/Dubai','HH24:MI') AS t, "
            "autocomplete_position AS rank, status, "
            "directions_clicked AS dir, call_clicked AS cal, "
            "website_clicked AS web, reviews_scrolled AS rev, "
            "ROUND(dwell_time_seconds::numeric, 0) AS dwell "
            "FROM bot_sessions ORDER BY id DESC LIMIT %s", (limit,), fetchall=True,
        ) or []
    except Exception:
        return []


def _active_biz() -> str:
    from shared.db import get_active_business
    b = get_active_business()
    if not b: return "(none)"
    icon = "🎯" if b["business_type"] == "sbo" else "🚗"
    return f"{icon} {b['name']} [{b['business_type']}]"


def _tail_log(n: int = 12) -> list[str]:
    try:
        if Path(LOG_FILE).exists():
            lines = Path(LOG_FILE).read_text(errors="ignore").splitlines()
            return lines[-n:]
    except Exception:
        pass
    return []


# ──────────────────────────────────────────────
# Panels
# ──────────────────────────────────────────────

DASH_TITLE = "bot-suite"  # overridden by the burst copy


def _rank_color(rank) -> str:
    """Color a Maps rank by how good it is (lower = better)."""
    try:
        r = int(rank)
    except (TypeError, ValueError):
        return "dim"
    if r <= 0:
        return "bold green"   # 0 = picked from autocomplete
    if r <= 3:
        return "bold green"
    if r <= 6:
        return "green"
    if r <= 10:
        return "yellow"
    return "red"


def header_panel() -> Panel:
    pid = _is_running()
    status_str = f"[bold green]● RUNNING[/bold green] [dim]PID {pid}[/dim]" if pid else "[dim]○ idle[/dim]"
    biz = _active_biz()
    now = datetime.now().strftime("%Y-%m-%d  %H:%M:%S")
    grid = Table.grid(expand=True)
    grid.add_column(justify="left")
    grid.add_column(justify="right")
    grid.add_row(f"🤖 [bold cyan]{DASH_TITLE}[/bold cyan]   {status_str}", f"[dim]{now}[/dim]")
    grid.add_row(f"🎯 [bold]{biz}[/bold]", "")
    return Panel(grid, border_style="cyan", padding=(0, 1))


def footer_panel() -> Panel:
    cmds = (
        "[cyan]run N[/cyan] start  ·  [cyan]run N --visible[/cyan] show browser  ·  "
        "[cyan]log[/cyan] live log  ·  [cyan]status[/cyan] stats  ·  [cyan]stop[/cyan] halt  ·  "
        "[dim]Ctrl+C to exit[/dim]"
    )
    return Panel(cmds, border_style="grey37", padding=(0, 1))


def stats_panel() -> Panel:
    s = _fetch_stats()
    total = s.get("total") or 0
    success = s.get("success") or 0
    rate = f"{100*success//total}%" if total else "—"
    grid = Table.grid(padding=(0, 2))
    grid.add_column(justify="left")
    grid.add_column(justify="right", style="bold")
    grid.add_row("📊 Total today", f"[cyan]{total}[/cyan]")
    grid.add_row("✅ Success", f"[green]{success}[/green]  ({rate})")
    grid.add_row("⚡ Partial", f"[yellow]{s.get('partial') or 0}[/yellow]")
    grid.add_row("❌ Failed", f"[red]{s.get('failed') or 0}[/red]")
    grid.add_row("", "")
    avg_r, best_r = s.get("avg_rank"), s.get("best_rank")
    grid.add_row("📍 Avg rank", f"[{_rank_color(avg_r)}]#{avg_r}[/]" if avg_r else "[dim]—[/dim]")
    grid.add_row("🏆 Best rank", f"[{_rank_color(best_r)}]#{best_r}[/]" if best_r else "[dim]—[/dim]")
    grid.add_row("", "")
    grid.add_row("🧭 Directions", str(s.get("directions") or 0))
    grid.add_row("📞 Calls", str(s.get("calls") or 0))
    grid.add_row("🌐 Website", str(s.get("website") or 0))
    grid.add_row("⭐ Reviews", str(s.get("reviews") or 0))
    grid.add_row("⏱️  Avg dwell", f"{s.get('avg_dwell') or '—'}s")
    return Panel(grid, title="📊 Today's Stats", border_style="cyan", padding=(0, 1))


def sessions_panel() -> Panel:
    rows = _fetch_recent_sessions(12)
    t = Table(box=box.SIMPLE_HEAVY, show_header=True, expand=True, padding=(0, 1))
    t.add_column("Time", style="dim", width=6)
    t.add_column("Status", width=8)
    t.add_column("Rank", justify="right", width=5)
    t.add_column("Dir", width=3)
    t.add_column("Cal", width=3)
    t.add_column("Web", width=3)
    t.add_column("Rev", width=3)
    t.add_column("Dwell", justify="right", width=6)
    for r in rows:
        st = r.get("status", "?")
        style = {"success": "green", "partial": "yellow", "failed": "red", "pending": "dim", "running": "cyan"}.get(st, "white")
        rank = r.get("rank")
        t.add_row(
            r.get("t", "") or "",
            f"[{style}]{st}[/{style}]",
            f"[{_rank_color(rank)}]#{rank}[/]" if rank is not None else "[dim]—[/dim]",
            "✓" if r.get("dir") else "—",
            "✓" if r.get("cal") else "—",
            "✓" if r.get("web") else "—",
            "✓" if r.get("rev") else "—",
            f"{r.get('dwell') or 0}s",
        )
    return Panel(t, title="📋 Recent Sessions", border_style="cyan", padding=(0, 0))


def log_panel() -> Panel:
    lines = _tail_log(40)
    if not lines:
        body = "[dim](no log yet — start the bot with [cyan]bot run 5[/cyan])[/dim]"
    else:
        colored = []
        for line in lines:
            ln = line[-160:]  # keep more of each line now that the panel is wide
            if "ERROR" in line or "❌" in line or "✗" in line or "crashed" in line:
                colored.append(f"[red]{ln}[/red]")
            elif "WARNING" in line or "⚠" in line:
                colored.append(f"[yellow]{ln}[/yellow]")
            elif "✓" in line or "🎯" in line or "✅" in line or "LANDED" in line:
                colored.append(f"[green]{ln}[/green]")
            elif "→ STEP" in line or "→" in line:
                colored.append(f"[cyan]{ln}[/cyan]")
            else:
                colored.append(f"[dim]{ln}[/dim]")
        body = "\n".join(colored)
    return Panel(body, title="📜 Live Log — every action (English)", border_style="green", padding=(0, 1))


def make_layout() -> Layout:
    layout = Layout()
    layout.split_column(
        Layout(name="header", size=4),
        Layout(name="body"),
        Layout(name="footer", size=3),
    )
    # Live log is now the BIG panel on the right; stats + recent sessions stack
    # in a narrower left column.
    layout["body"].split_row(
        Layout(name="left", ratio=1),
        Layout(name="log", ratio=2),
    )
    layout["left"].split_column(
        Layout(name="stats"),
        Layout(name="sessions", size=16),
    )
    return layout


def live_dashboard():
    """Run the live dashboard until Ctrl+C."""
    layout = make_layout()
    try:
        with Live(layout, refresh_per_second=0.5, screen=True) as live:
            while True:
                layout["header"].update(header_panel())
                layout["stats"].update(stats_panel())
                layout["sessions"].update(sessions_panel())
                layout["log"].update(log_panel())
                layout["footer"].update(footer_panel())
                time.sleep(5)
    except KeyboardInterrupt:
        console.print("\n👋 Dashboard closed")
