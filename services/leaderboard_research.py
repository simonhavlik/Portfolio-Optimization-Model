"""Build publication studies from the same dated holdings and price series as the board."""

from __future__ import annotations

import io
import json
from datetime import datetime, timezone
from dataclasses import dataclass

import numpy as np
import pandas as pd
from PIL import Image

from services import fund_archive
from services.filing_history import ARCHIVE, atomic_write, normalize_filing
from services.leaderboard import CACHE_DIR, _mapping, _read_prices, _series, build_board
from services.leaderboard_funds import FUNDS, by_slug
from services.leaderboard_charts import plt

DIRECTORY = CACHE_DIR / "research"
MANIFEST = DIRECTORY / "index.json"
SHORT = {
    "berkshire": "Berkshire",
    "scion": "Scion",
    "duquesne": "Duquesne",
    "valley-forge": "Valley Forge",
    "lone-pine": "Lone Pine",
    "valueact": "ValueAct",
    "renaissance": "Renaissance",
    "citadel": "Citadel",
    "third-point": "Third Point",
    "tiger-global": "Tiger Global",
    "elliott": "Elliott",
    "soros": "Soros",
    "himalaya": "Himalaya",
    "greenlight": "Greenlight",
    "abrams": "Abrams",
}


@dataclass(frozen=True)
class Study:
    key: str
    title: str
    cohort: str
    chart: str
    funds: tuple[str, ...]


STUDIES = (
    Study("berkshire", "Inside Buffett’s public equity book", "current", "filings", ("berkshire",)),
    Study("scion", "Inside Michael Burry’s long book", "scion", "filings", ("scion",)),
)


def study_index():
    from services.leaderboard_storage import STUDIES_KEY, hydrate_snapshot, published_snapshot

    saved = published_snapshot(STUDIES_KEY)
    if saved is not None:
        return saved
    try:
        return json.loads(MANIFEST.read_bytes())
    except (OSError, ValueError):
        return hydrate_snapshot(MANIFEST, STUDIES_KEY) or {}


def asset_path(revision: str, key: str, panel: str, extension: str):
    return DIRECTORY / f"{revision}-{key}-{panel}.{extension}"


def _source_books():
    from flask import has_app_context

    if has_app_context():
        return {fund["slug"]: fund_archive.timeline(fund_archive.documents(fund["slug"])) for fund in FUNDS}
    return {
        fund["slug"]: fund_archive.timeline(
            [
                normalize_filing(json.loads(file.read_bytes()))
                for file in (ARCHIVE / fund["slug"]).glob("??????????-??-??????.json")
            ]
        )
        for fund in FUNDS
    }


def _study_data(study, board, books, market, mapping):
    start, end = pd.Timestamp(board["start"]), pd.Timestamp(board["as_of"])
    calendar = market["SPY"].loc[:end].index
    fund_series, coverage, events, latest = {}, {}, {}, {}
    rows = {row["slug"]: row for row in board["rows"]}
    moves = []
    for slug in study.funds:
        models, _coverage = _series(by_slug(slug), books[slug], market, calendar, mapping)
        fund_series[slug] = models.loc[start:end]
        coverage[slug] = _coverage.loc[start:end]
        trail = []
        prior = None
        known = {}
        for book in books[slug]:
            filed = pd.Timestamp(book["filed"])
            if filed > end:
                break
            current = fund_archive.aggregate(book, equity_only=True)
            known = current
            during_crisis = (
                "2020-02-19" <= book["filed"] <= "2020-03-23"
                or "2022-01-03" <= book["filed"] <= "2022-10-12"
            )
            if prior and (filed >= start or study.chart == "crises" and during_crisis):
                change = fund_archive.changes(current, prior)
                selected = [r for r in change if r["action"] in {"New", "Exited", "Increased", "Reduced"}]
                if study.chart != "crises" or during_crisis:
                    moves.extend(_disclosed_moves(study, slug, book, selected, mapping))
                if filed < start:
                    prior = current
                    continue
                added = sum(r["action"] == "New" for r in selected)
                exited = sum(r["action"] == "Exited" for r in selected)
                adjusted = len(selected) - added - exited
                moved = sum(abs(r["weight_change"]) for r in change) / 2
                top = max(selected, key=lambda r: abs(r["weight_change"]), default=None)
                trail.append(
                    {
                        "date": filed,
                        "period": book["period"],
                        "value": sum(x["value"] for x in current),
                        "added": added,
                        "exited": exited,
                        "adjusted": adjusted,
                        "moved": moved,
                        "top_weight": max((x["weight"] for x in current), default=0),
                        "top": top["issuer"].title()[:23] if top else "",
                        "direction": top["action"] if top else "",
                        "change": top["weight_change"] if top else 0,
                    }
                )
            prior = current
        events[slug] = trail
        latest[slug] = {r["cusip"]: r["weight"] for r in known}
    benchmarks = {
        ticker: np.expm1(market[ticker].reindex(calendar)).loc[start:end]
        for ticker in ("SPY", "QQQ")
        if ticker in market
    }
    return {
        "study": study,
        "board": board,
        "rows": rows,
        "series": fund_series,
        "benchmarks": benchmarks,
        "events": events,
        "latest": latest,
        "coverage": coverage,
        "notes": _select_notes(study, moves),
    }


# All figure coordinates use the full export. Only axes which need tick labels
# reserve a gutter. No page frame, decorative masthead, or outer image padding.
PAGE = "#10171c"
RULE = "#34434b"
PAPER = "#eef2ef"
SOFT = "#b0bec3"
ACCENTS = ("#f3c46c", "#75cad4", "#df91a1", "#9da9e2", "#aad29a", "#d59c77", "#d7b6d8")
CHART_TITLES = {
    "berkshire": ("Berkshire Hathaway", "Disclosed equity book"),
    "scion": ("Scion Asset Management", "Disclosed equity book"),
}


def _short(slug):
    return {
        **SHORT,
        "pershing": "Pershing",
        "himalaya": "Himalaya",
        "baupost": "Baupost",
        "greenlight": "Greenlight",
        "appaloosa": "Appaloosa",
        "bridgewater": "Bridgewater",
        "point72": "Point72",
        "markel": "Markel",
        "fairfax": "Fairfax",
        "wellington": "Wellington",
        "fisher": "Fisher",
        "aqr": "AQR",
        "two-sigma": "Two Sigma",
        "millennium": "Millennium",
        "gates-trust": "Gates Trust",
        "davis": "Davis",
        "de-shaw": "D. E. Shaw",
        "horizon-kinetics": "Horizon Kinetics",
        "akre": "Akre",
        "southeastern": "Southeastern",
        "valley-forge": "Valley Forge",
        "trian": "Trian",
        "icahn": "Icahn",
    }.get(slug, by_slug(slug)["name"])


def _disclosed_moves(study, slug, book, changes, mapping):
    """Keep the two largest directional changes and every material weight move."""
    if not changes:
        return []
    largest_up = next((row for row in changes if row["shares_change"] > 0), None)
    largest_down = next((row for row in changes if row["shares_change"] < 0), None)
    candidates = [
        row
        for i, row in enumerate(changes)
        if i < 4 or abs(row["weight_change"]) >= 0.05 or row is largest_up or row is largest_down
    ]
    return [
        dict(
            fund=slug,
            filed=book["filed"],
            period=book["period"],
            accession=book["accession"],
            url=book["url"],
            cusip=row["cusip"],
            issuer=row["issuer"].title(),
            ticker=mapping.get(row["cusip"]),
            action=row["action"],
            quantity=row["shares_change"],
            delta=row["weight_change"],
            weight=row["weight"],
            directional_leader=row is largest_up or row is largest_down,
        )
        for row in candidates
    ]


def _select_notes(study, candidates):
    key = lambda row: (-abs(row["delta"]), row["filed"], row["fund"], row["cusip"])
    if study.chart == "filings":
        selected = [r for r in candidates if abs(r["delta"]) >= 0.05 or r["directional_leader"]]
        if len(selected) < 18:
            selected += sorted((r for r in candidates if r not in selected), key=key)[: 18 - len(selected)]
    else:
        groups = [sorted((r for r in candidates if r["fund"] == slug), key=key) for slug in study.funds]
        selected = []
        for i in range(21):
            selected.extend(group[i] for group in groups if len(group) > i)
            if len(selected) >= 21:
                break
        selected = selected[:21]
    return sorted(selected, key=lambda r: (r["filed"], r["fund"], -abs(r["delta"]), r["cusip"]))


def _references(notes):
    return {day: f"{i:02d}" for i, day in enumerate(sorted({n["filed"] for n in notes}), 1)}


def _quantity(value):
    for divisor, suffix in ((1e9, "B"), (1e6, "M"), (1e3, "k")):
        if abs(value) >= divisor:
            return (
                f"{abs(value) / divisor:.2g}{suffix}"
                if abs(value) / divisor < 10
                else f"{abs(value) / divisor:.0f}{suffix}"
            )
    return f"{abs(value):.0f}"


def _ledger(canvas, data, columns):
    from matplotlib.patches import Rectangle

    notes = data["notes"]
    single = data["study"].chart == "filings"
    dates_lookup = _references(notes)
    height_inches = canvas.bbox.height / canvas.figure.dpi
    title_y = 1 - 0.09 / height_inches
    method_y = 1 - 0.29 / height_inches
    head_y = 1 - 0.49 / height_inches
    first_y = 1 - 0.59 / height_inches
    last_y = 0.19 / height_inches
    canvas.text(
        0.012,
        title_y,
        f"{len(notes)} disclosed position changes",
        va="top",
        fontsize=12,
        color=PAPER,
        fontweight="bold",
    )
    method = (
        "Every ≥5 pp shift with changed shares, plus the biggest addition and reduction in each filing."
        if single
        else "Largest absolute weight moves per manager, balanced across the comparison."
    )
    canvas.text(0.012, method_y, method, va="top", fontsize=9.3, color=SOFT)
    rows = (len(notes) + columns - 1) // columns
    cell_height = (first_y - last_y) / rows
    width = 1 / columns
    colors = {s: ACCENTS[i % len(ACCENTS)] for i, s in enumerate(data["study"].funds)}
    for col in range(columns):
        x = col * width
        layout = (
            [(0.018, "Date / ID"), (0.27, "Security"), (0.795, "Shares"), (0.982, "Δ wt.")]
            if single
            else [
                (0.018, "Date / ID"),
                (0.24, "Manager"),
                (0.46, "Security"),
                (0.795, "Shares"),
                (0.982, "Δ wt."),
            ]
        )
        for fraction, label in layout:
            canvas.text(
                x + width * fraction,
                head_y,
                label,
                color=SOFT,
                fontsize=8.5,
                va="center",
                ha="right" if fraction >= 0.62 else "left",
            )
        for i, note in enumerate(notes[col * rows : (col + 1) * rows]):
            y = first_y - (i + 0.5) * cell_height
            if i % 2 == 0:
                canvas.add_artist(
                    Rectangle(
                        (x, y - 0.5 * cell_height),
                        width,
                        cell_height,
                        transform=canvas.transSubfigure,
                        facecolor="#18232a",
                        edgecolor="none",
                        zorder=-2,
                    )
                )
            day = note["filed"]
            color = colors[note["fund"]]
            date_label = day[2:].replace("-", "/") + " " + dates_lookup[day]
            canvas.text(
                x + 0.018 * width,
                y,
                date_label,
                va="center",
                fontsize=8.1,
                color=SOFT,
                fontfamily="DejaVu Sans Mono",
            )
            text = note["ticker"] or note["issuer"]
            if len(text) > 13:
                text = text[:12] + "…"
            if not single:
                canvas.text(
                    x + 0.24 * width, y, _short(note["fund"]), va="center", fontsize=8.5, color=color
                )
            canvas.text(
                x + (0.27 if single else 0.46) * width,
                y,
                text,
                va="center",
                fontsize=8.9,
                color=PAPER if single else color,
            )
            direction = {"New": "New", "Exited": "Exit", "Increased": "+", "Reduced": "−"}[note["action"]]
            canvas.text(
                x + 0.795 * width,
                y,
                f"{direction} {_quantity(note['quantity'])}",
                ha="right",
                va="center",
                fontsize=8.4,
                color=PAPER,
            )
            canvas.text(
                x + 0.982 * width,
                y,
                f"{note['delta'] * 100:+.1f}",
                ha="right",
                va="center",
                fontsize=8.7,
                color=ACCENTS[1] if note["delta"] >= 0 else ACCENTS[2],
                fontfamily="DejaVu Sans Mono",
            )
    if not single:
        canvas.text(
            0.012,
            0.006,
            "Δ wt. in percentage points. Changes may include corporate actions.",
            color=SOFT,
            fontsize=8.3,
            va="bottom",
        )
    else:
        canvas.text(
            0.012,
            0.006,
            "Dates are SEC filing dates. Shares compare successive disclosures. Δ wt. in percentage points.",
            color=SOFT,
            fontsize=8.3,
            va="bottom",
        )


def _plot_notes(fig, data):
    """Attach short change labels directly to their position on the filing timeline."""
    notes = data["notes"]
    refs = _references(notes)
    groups = {day: [n for n in notes if n["filed"] == day] for day in refs}
    selected = []
    # Spread the labels over filing dates before showing a second change on a date.
    for depth in range(len(notes)):
        for group in groups.values():
            if len(group) > depth:
                selected.append(group[depth])
        if len(selected) >= 18:
            break
    if len(selected) > 21:
        selected = [selected[i] for i in np.linspace(0, len(selected) - 1, 21).astype(int)]
    selected.sort(key=lambda n: n["filed"])
    ax = fig.axes[0]
    box = ax.get_position()
    reserve = 0.145
    ax.set_position([box.x0, box.y0, box.width, box.height - reserve])
    for companion in fig.axes:
        if companion.get_label() == '<colorbar>':
            bounds = companion.get_position()
            companion.set_position([bounds.x0, box.y0, bounds.width, box.height - reserve])
    # The short timeline stays in historical time even for a simulated or cross-sectional plot.
    temporal = data["study"].chart not in {"crises", "simulation", "securities", "coverage", "universe"}
    if temporal:
        first, last = pd.Timestamp(data["board"]["start"]), pd.Timestamp(data["board"]["as_of"])
    else:
        first, last = pd.Timestamp(min(refs)), pd.Timestamp(max(refs))
    if first == last:
        last += pd.Timedelta(days=1)
    cols = (len(selected) + 2) // 3
    width = box.width if temporal else 0.944
    left = box.x0 if temporal else 0.032
    band = fig.add_axes([left, box.y1 - reserve + 0.009, width, reserve - 0.005], facecolor=PAGE)
    band.set_xlim(0, 1)
    band.set_ylim(0, 1)
    band.axis("off")
    band.plot([0, 1], [0.035, 0.035], color=RULE, lw=0.55)
    for i, note in enumerate(selected):
        col, row = divmod(i, 3)
        x = (pd.Timestamp(note["filed"]) - first) / (last - first)
        x = float(np.clip(x, 0.002, 0.998))
        label_x = (col + 0.5) / cols
        label_y = 0.87 - row * 0.26
        color = ACCENTS[1] if note["quantity"] > 0 else ACCENTS[2]
        ticker = note["ticker"] or note["issuer"].split()[0]
        action = {"New": "new", "Exited": "exit", "Increased": "+", "Reduced": "−"}[note["action"]]
        manager = f"{_short(note['fund'])[:12]} · " if len(data['study'].funds) > 1 else ''
        label = f"{refs[note['filed']]}  {manager}{action} {ticker[:9]}"
        band.plot([x, label_x], [0.035, label_y - 0.06], color=color, lw=0.45, alpha=0.50, zorder=1)
        band.scatter([x], [0.035], s=4, marker="s", color=color, clip_on=False)
        band.text(
            label_x,
            label_y,
            label,
            ha="center",
            va="center",
            fontsize=7.4 if len(data['study'].funds) > 1 else 8.7,
            color=color,
            bbox=dict(facecolor=PAGE, edgecolor="none", pad=0.3),
            zorder=3,
        )
    return len(selected)


def _dated_markers(ax, data):
    refs = _references(data["notes"])
    for date in refs:
        ax.axvline(pd.Timestamp(date), lw=0.35, color=SOFT, alpha=0.26, zorder=0)


def _page(data, report=False, canvas=None):
    study, board = data["study"], data["board"]
    name, subject = CHART_TITLES[study.key]
    if study.key == "berkshire":
        subject = f"Warren Buffett · {data['rows']['berkshire']['metrics']['volatility']:.1%} volatility"
    elif study.key == "scion":
        subject = (
            f"Michael Burry · {data['rows']['scion']['metrics']['annualized_return']:.1%} annualized return"
        )
    fig = canvas if canvas is not None else plt.figure(figsize=(5.35, 6.65), dpi=400, facecolor=PAGE)
    fig.text(0.016, 0.985, name, va="top", color=PAPER, fontsize=14, fontweight="bold")
    fig.text(
        0.016,
        0.944 if report else 0.921,
        "Portfolio statistics" if report else subject,
        va="top",
        color=SOFT,
        fontsize=10,
    )
    period = f"{board['start'][:7]} – {board['as_of'][:7]}"
    if study.chart == "crises" and not report:
        period = "2020 · 2022"
    fig.text(
        0.984,
        0.944 if report else 0.921,
        period,
        va="top",
        ha="right",
        color=PAPER,
        fontsize=9,
        fontfamily="DejaVu Sans Mono",
    )
    return fig


def _rule(fig, y):
    from matplotlib.lines import Line2D

    fig.add_artist(Line2D([0, 1], [y, y], transform=fig.transSubfigure, color=RULE, linewidth=0.6))


def _note(fig, text):
    _rule(fig, 0.055)
    fig.text(0.016, 0.027, text, va="center", color=SOFT, fontsize=8)


def _axis(fig, box):
    ax = fig.add_axes(box, facecolor=PAGE)
    for edge in ax.spines.values():
        edge.set_visible(False)
    ax.tick_params(length=0, labelsize=9, colors=SOFT, pad=3)
    ax.grid(axis="y", color=RULE, linewidth=0.55)
    ax.set_axisbelow(True)
    ax.margins(x=0.008, y=0.09)
    return ax


def _date_axis(ax, start, end):
    ax.set_xlim(pd.Timestamp(start), pd.Timestamp(end))
    ticks = pd.date_range(start, end, periods=5)
    ax.set_xticks(ticks, [day.strftime("%b ’%y") for day in ticks])
    labels = ax.get_xticklabels()
    labels[0].set_ha("left")
    labels[-1].set_ha("right")


def _legend(fig, handles, y=0.868, columns=3):
    if handles:
        fig.legend(
            handles=handles,
            loc="upper left",
            bbox_to_anchor=(0.012, y),
            frameon=False,
            ncol=columns,
            fontsize=9,
            handlelength=1.15,
            handletextpad=0.4,
            columnspacing=0.9,
            labelcolor=PAPER,
            borderaxespad=0,
            labelspacing=0.26,
        )


def _paths(returns):
    blocks = np.lib.stride_tricks.sliding_window_view(np.log1p(returns.dropna()), 21)
    picks = np.random.default_rng(801).integers(0, len(blocks), size=(3000, 12))
    paths = 100 * np.exp(blocks[picks].reshape(3000, 252).cumsum(axis=1))
    paths = np.column_stack((np.full(3000, 100), paths))
    return np.percentile(paths, [10, 50, 90], axis=0), paths[:, -1]


def _metric(v, fmt):
    return format(v, fmt) if v is not None and np.isfinite(v) else "—"


def _drawdown(series):
    curve = (1 + series).cumprod()
    return (curve / curve.cummax().clip(lower=1) - 1) * 100


def _disclosure_chart(fig, data):
    from matplotlib.ticker import MaxNLocator

    slug = data["study"].funds[0]
    board, trail = data["board"], data["events"][slug]
    ax = _axis(fig, (0.095, 0.32, 0.89, 0.439))
    nav = (1 + data["series"][slug]).cumprod() * 100
    handles = []
    for name, line, color, style in [
        (_short(slug), nav, ACCENTS[0], "-"),
        *[
            (symbol, (1 + daily).cumprod() * 100, color, dash)
            for (symbol, daily), color, dash in zip(data["benchmarks"].items(), ACCENTS[1:3], ("--", ":"))
        ],
    ]:
        handles.append(
            ax.plot(
                line.index,
                line,
                style,
                color=color,
                lw=1.65 if name == _short(slug) else 1.15,
                label=f"{name}  ${line.iloc[-1]:.0f}",
                zorder=3,
            )[0]
        )
    _legend(fig, handles)
    fig.text(0.016, 0.778, "Growth of $100", color=SOFT, fontsize=9)
    _date_axis(ax, board["start"], board["as_of"])
    _dated_markers(ax, data)
    ax.yaxis.set_major_locator(MaxNLocator(6))
    lookup = nav.reindex(pd.DatetimeIndex([event["date"] for event in trail]), method="ffill")
    ax.scatter(
        lookup.index, lookup, marker="o", s=11, edgecolors=PAGE, linewidth=0.45, color=ACCENTS[0], zorder=4
    )
    # Select major moves from different parts of the window to avoid overlapped labels.
    labels = []
    for event in sorted(trail, key=lambda e: -e["moved"]):
        if all(
            abs((event["date"] - e["date"]).days) > (nav.index[-1] - nav.index[0]).days * 0.23
            for e in labels
        ):
            labels.append(event)
        if len(labels) == 2:
            break
    for i, e in enumerate(sorted(labels, key=lambda e: e["date"])):
        day = e["date"]
        y = lookup.loc[day]
        right = (day - nav.index[0]) / (nav.index[-1] - nav.index[0]) > 0.55
        action = {"Increased": "Added", "Reduced": "Cut", "Exited": "Exited", "New": "New"}[e["direction"]]
        issuer = e["top"].removesuffix(" Corp New").removesuffix(" Inc")
        ax.annotate(
            f"{action} · {issuer[:16]}\n{e['change'] * 100:+.1f} pp",
            (day, y),
            xytext=(-3 if right else 3, -22 if i else 17),
            textcoords="offset points",
            ha="right" if right else "left",
            va="center",
            fontsize=8.5,
            color=PAPER,
            bbox=dict(facecolor=PAGE, edgecolor="none", pad=1),
            zorder=6,
            arrowprops=dict(arrowstyle="-", color=SOFT, lw=0.65, shrinkB=3),
        )
    if slug == 'berkshire':
        from services.research_events import BERKSHIRE_EVENTS

        events = [(day, title, url) for day, title, url in BERKSHIRE_EVENTS
                  if board['start'] <= day <= board['as_of']]
        for index, (day, title, url) in enumerate(events, 1):
            when = pd.Timestamp(day)
            ax.axvline(when, color='#edba76', lw=.65, ls='--', alpha=.65)
            ax.annotate(f'E{index}', (when, .13 if index % 2 else .24),
                        xycoords=ax.get_xaxis_transform(), color='#edba76', fontsize=7.8,
                        ha='center', bbox={'facecolor': PAGE, 'edgecolor': 'none', 'pad': .6})
            col, row = divmod(index-1, 2)
            fig.text(.016 + col*.248, .075 - row*.032,
                     f'E{index} {day[2:]}  {title}', color='#edba76', fontsize=7.3)
        data['corporate_events'] = [{'date': day, 'title': title, 'url': url} for day,title,url in events]

    # The value strip shares the price chart's date axis, as volume does on
    # a price chart. Its scale is the value actually reported in the filings.
    ax.tick_params(axis="x", labelbottom=False)
    strip = _axis(fig, (0.095, 0.15, 0.89, 0.117))
    strip.grid(False)
    if trail:
        in_billions = max(event["value"] for event in trail) >= 1e9
        scale, unit = (1e9, "B") if in_billions else (1e6, "M")
        series = pd.Series({event["date"]: event["value"] / scale for event in trail}).sort_index()
        strip.fill_between(series.index, series, step="post", color="#59838a", alpha=0.48)
        strip.step(series.index, series, where="post", color="#92c4c4", lw=0.9)
        strip.set_ylim(0, series.max() * 1.12)
        strip.set_yticks([0, series.max()], ["0", f"{series.max():.0f}"])
        fig.text(
            0.016,
            0.277,
            f"Last reported long equity value, USD {unit}   ·   {series.iloc[-1]:,.1f}{unit} at last filing",
            color=SOFT,
            fontsize=8.8,
        )
    _date_axis(strip, board["start"], board["as_of"])
    if slug != 'berkshire':
        _note(fig, "SEC 13F · Dots mark filing dates. Labels show weight changes.")


def _table(fig, top, bottom, columns, rows, boundaries):
    from matplotlib.patches import Rectangle

    height = (top - bottom) / (len(rows) + 1)
    for i, row in enumerate([columns, *rows]):
        y = top - i * height
        if i == 0 or i % 2:
            fig.add_artist(
                Rectangle(
                    (0, y - height),
                    1,
                    height,
                    transform=fig.transFigure,
                    facecolor="#1b272e" if i == 0 else "#131e24",
                    edgecolor="none",
                    zorder=-2,
                )
            )
        _rule(fig, y)
        for j, text in enumerate(row):
            left = j == 0
            x = boundaries[j] + 0.014 if left else boundaries[j + 1] - 0.015
            fig.text(
                x,
                y - height * 0.52,
                str(text),
                va="center",
                ha="left" if left else "right",
                fontsize=9.3 if i else 8.8,
                color=SOFT if i == 0 else PAPER,
                fontfamily="DejaVu Sans" if left or i == 0 else "DejaVu Sans Mono",
            )
    _rule(fig, bottom)


def _export(fig, revision, key, panel):
    stream = io.BytesIO()
    try:
        fig.savefig(stream, format="png", facecolor=PAGE)
    finally:
        plt.close(fig)
    png = stream.getvalue()
    atomic_write(asset_path(revision, key, panel, "png"), png)
    with Image.open(io.BytesIO(png)) as im:
        im.thumbnail((2048, 2600))
        preview = io.BytesIO()
        im.convert("RGB").save(preview, "WEBP", quality=84, method=5)
    atomic_write(asset_path(revision, key, panel, "webp"), preview.getvalue())


def rebuild():
    """Prepare all share images offline from saved prices and original filings."""
    books = _source_books()
    market = _read_prices()
    mapping = _mapping()
    for ticker, series in market.items():
        market[ticker] = series.tz_localize(None) if series.index.tz is not None else series
    from services.holdings_style_figures import TOPICS, main_chart, report
    from services.fund_research_report import render_report
    from services.holdings_style import load

    styles = load()
    classifications = CACHE_DIR / 'issuer_industries.json'
    sector_ids = json.loads(classifications.read_bytes()) if classifications.is_file() else {}
    for cusip, row in sector_ids.items():
        row['key'] = f"cik:{row['cik']}" if row.get('cik') and row.get('sic') not in {'6722', '6726'} else cusip
    crowd = {p['key']: p['count'] for p in styles.get('crowded', [])}
    group = tuple(styles.get('universe', [])[:20])
    style_visual_data = {**styles, 'universe': group,
                         'pairs': [pair for pair in styles.get('pairs', [])
                                   if pair['left'] in group and pair['right'] in group]}
    studies = (*STUDIES, *(Study(key, title, 'long', 'universe', group)
                          for key, (title, _) in TOPICS.items() if group))
    keys = {study.cohort for study in studies}
    boards = {key: build_board(cohort=key) for key in keys}
    index = {}
    revision = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S%f")
    for study in studies:
        board = boards[study.cohort]
        if not board.get("revision"):
            continue
        if study.chart == 'universe':
            candidates = []
            preferred_keys = {row['key'] for row in styles['crowded'][:22]}
            for slug in study.funds:
                filings = [f for f in books[slug] if f['period'] <= styles['as_of']][-8:]
                for previous, filing in zip(filings, filings[1:]):
                    before = fund_archive.aggregate(previous, equity_only=True)
                    after = fund_archive.aggregate(filing, equity_only=True)
                    changes = [r for r in fund_archive.changes(after, before) if r['shares_change']]
                    if study.key == 'entry-exit':
                        changes = [r for r in changes if r['action'] in {'New','Exited'}]
                    elif study.key == 'concentration':
                        concentrated = [r for r in changes
                                        if r['weight'] >= .05 or r['weight'] - r['weight_change'] >= .05]
                        changes = concentrated or changes
                    elif study.key in {'crowding','shared-stocks','sector-history','sector-gaps'}:
                        if study.key == 'crowding':
                            preferred = [r for r in changes if sector_ids.get(r['cusip'], {}).get('key') in preferred_keys]
                        elif study.key == 'shared-stocks':
                            preferred = [r for r in changes if 3 <= crowd.get(sector_ids.get(r['cusip'], {}).get('key'), 0)]
                        elif study.key == 'sector-history':
                            preferred = [r for r in changes if sector_ids.get(r['cusip'], {}).get('sector') == 'BusEq']
                        else:
                            preferred = [r for r in changes if r['cusip'] not in sector_ids]
                        if preferred:
                            changes = preferred
                    candidates.extend(_disclosed_moves(study, slug, filing, changes, mapping))
            data = {'study': study, 'board': board, 'notes': _select_notes(study, candidates)}
        else:
            data = _study_data(study, board, books, market, mapping)
        count = len(data["notes"])
        if count < 15:
            raise ValueError(f"{study.key} has only {count} dated portfolio changes")
        columns = 4 if count > 63 else 3 if count > 33 else 2
        lines = (count + columns - 1) // columns
        ledger_height = 0.88 + lines * 0.146
        chart_height = 5.04
        sheet = plt.figure(figsize=(12.8, chart_height + ledger_height), dpi=300, facecolor=PAGE)
        main, ledger = sheet.subfigures(2, 1, height_ratios=[chart_height, ledger_height], hspace=0)
        main.set_facecolor(PAGE)
        ledger.set_facecolor(PAGE)
        if study.chart == 'universe':
            main_chart(main, study, style_visual_data)
        else:
            _disclosure_chart(_page(data, canvas=main), data)
        label_count = _plot_notes(main, data)
        _ledger(ledger, data, columns)
        _export(sheet, revision, study.key, "chart")
        if study.chart == 'universe':
            fig = plt.figure(figsize=(9.6, 8.0), dpi=400, facecolor=PAGE)
            report(fig, study, style_visual_data)
        else:
            fig = plt.figure(figsize=(9.6, 8.0), dpi=400, facecolor=PAGE)
            title = _page(data, report=True, canvas=fig)
            render_report(title, data, styles)
        _export(fig, revision, study.key, "report")
        index[study.key] = {
            "title": study.title,
            "revision": revision,
            "cohort": study.cohort,
            "funds": list(study.funds),
            "notes": data["notes"],
            "events": data.get("corporate_events", []),
            "plot_labels": label_count,
            "chart_size": [3840, int((chart_height + ledger_height) * 300)],
            "report_size": [3840, 3200],
        }
    atomic_write(MANIFEST, json.dumps(index).encode())
    print(f"Built {len(index) * 2} chart and report graphics", flush=True)


if __name__ == "__main__":
    rebuild()
