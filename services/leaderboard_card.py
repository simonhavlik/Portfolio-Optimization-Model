"""LinkedIn-ready leaderboard cards, rendered server-side with Pillow."""

from __future__ import annotations

import io
from datetime import datetime

from PIL import Image, ImageDraw, ImageFont
from matplotlib import font_manager

INK = (7, 8, 11)
PANEL = (11, 14, 18)
BRASS = (200, 161, 90)
BRASS_LIGHT = (232, 205, 151)
TEXT = (241, 237, 229)
MUTED = (172, 166, 155)
DIM = (138, 132, 121)
UP = (143, 199, 160)
DOWN = (209, 142, 126)


def _font(names: list[str], size: int) -> ImageFont.FreeTypeFont:
    for name in names:
        try:
            path = font_manager.findfont(name, fallback_to_default=False)
            return ImageFont.truetype(path, size)
        except Exception:
            continue
    return ImageFont.load_default()


def _fonts(size_scale: float):
    return {
        'title': _font(['DejaVu Sans:bold'], int(58 * size_scale)),
        'kicker': _font(['DejaVu Sans Mono'], int(15 * size_scale)),
        'sub': _font(['DejaVu Sans'], int(19 * size_scale)),
        'rank': _font(['DejaVu Sans Mono:bold'], int(20 * size_scale)),
        'fund': _font(['DejaVu Sans:bold'], int(22 * size_scale)),
        'meta': _font(['DejaVu Sans'], int(13 * size_scale)),
        'num': _font(['DejaVu Sans Mono:bold'], int(20 * size_scale)),
        'num_small': _font(['DejaVu Sans Mono'], int(14 * size_scale)),
        'foot': _font(['DejaVu Sans Mono'], int(11 * size_scale)),
    }


def _grid(draw: ImageDraw.ImageDraw, width: int, height: int, step: int, colour) -> None:
    for x in range(0, width, step):
        draw.line([(x, 0), (x, height)], fill=colour, width=1)
    for y in range(0, height, step):
        draw.line([(0, y), (width, y)], fill=colour, width=1)


def _trend(rows: list[dict]) -> tuple[float, float]:
    sharpe = [r['metrics']['sharpe_ratio'] for r in rows if r.get('metrics', {}).get('sharpe_ratio') is not None]
    if not sharpe:
        return 0.0, 1.0
    return min(0.0, min(sharpe)), max(sharpe) if max(sharpe) > 0 else 1.0


def render_card(board: dict, fmt: str = 'square') -> io.BytesIO:
    """Square 1080x1080 for the feed, landscape 1200x627 for link previews."""
    rows = [r for r in board.get('rows', []) if r.get('metrics', {}).get('sharpe_ratio') is not None][:10]
    landscape = fmt == 'landscape'
    W, H = (1200, 627) if landscape else (1080, 1080)
    scale = 0.82 if landscape else 1.0
    f = _fonts(scale)

    img = Image.new('RGB', (W, H), INK)
    draw = ImageDraw.Draw(img)
    _grid(draw, W, H, 72, (11, 13, 17))

    pad = int(56 * scale)
    draw.rectangle([pad - 18, pad - 18, W - pad + 18, H - pad + 18], outline=(28, 31, 36), width=1)

    # header
    draw.ellipse([pad, pad + 6, pad + 7, pad + 13], fill=BRASS)
    draw.text((pad + 16, pad), 'PORTFOLIO OPTIMIZER  /  FUND INTELLIGENCE', font=f['kicker'], fill=(150, 145, 136))

    title_y = pad + int(34 * scale)
    draw.text((pad, title_y), 'The fund leaderboard', font=f['title'], fill=TEXT)

    draw.rectangle([pad, title_y + int(64 * scale), pad + int(64 * scale), title_y + int(67 * scale)], fill=BRASS)

    sub_y = title_y + int(88 * scale)
    draw.text((pad, sub_y), f"{board.get('window_years', 5)}-year risk-adjusted ranking  ·  as of {board.get('as_of', '')}",
              font=f['sub'], fill=MUTED)

    # rows
    top = sub_y + int(56 * scale)
    bottom = H - pad - int(58 * scale)
    n = max(1, len(rows))
    row_h = (bottom - top) / n
    lo, hi = _trend(rows)

    for i, row in enumerate(rows):
        m = row['metrics']
        y = top + i * row_h
        mid = y + row_h / 2
        draw.line([(pad, y), (W - pad, y)], fill=(22, 25, 30), width=1)

        if i == 0:
            draw.rectangle([pad - 10, y + 2, W - pad + 10, y + row_h - 2], fill=(17, 19, 23))
            draw.rectangle([pad - 10, y + 2, pad - 7, y + row_h - 2], fill=BRASS)

        rank_colour = BRASS_LIGHT if i == 0 else (126, 120, 110)
        draw.text((pad, mid - 11 * scale), f'{i + 1:02d}', font=f['rank'], fill=rank_colour)

        name_x = pad + int(46 * scale)
        draw.text((name_x, mid - 22 * scale), row['name'], font=f['fund'], fill=TEXT)
        draw.text((name_x, mid + 2 * scale), f"{row['manager']}  ·  {'13F book' if row['kind'] == '13f' else row.get('ticker', 'listed')}",
                  font=f['meta'], fill=DIM)

        # score bar
        bar_x = int(W * 0.55)
        bar_w = int(W * 0.195)
        score = max(0, min(100, row.get('score') or 0))
        draw.line([(bar_x, mid + 2), (bar_x + bar_w, mid + 2)], fill=(30, 34, 40), width=6)
        draw.line([(bar_x, mid + 2), (bar_x + bar_w * score / 100, mid + 2)], fill=BRASS, width=6)

        draw.text((bar_x + bar_w + 14, mid - 10 * scale), f'{score:.0f}', font=f['num'], fill=BRASS_LIGHT)

        sharpe = m['sharpe_ratio']
        draw.text((W - pad, mid - 24 * scale), f'{sharpe:+.2f}', font=f['num'], fill=TEXT, anchor='ra')
        draw.text((W - pad, mid + 2 * scale), 'sharpe', font=f['num_small'], fill=DIM, anchor='ra')

    # footer
    foot_y = H - pad - int(18 * scale)
    draw.line([(pad, foot_y - int(18 * scale)), (W - pad, foot_y - int(18 * scale))], fill=(26, 29, 34), width=1)
    draw.text((pad, foot_y), 'Sharpe · Sortino · alpha · drawdown · CVaR · concentration', font=f['foot'], fill=(120, 116, 108))
    draw.text((W - pad, foot_y), 'optimizer.simonhavlik.com/leaderboard', font=f['foot'], fill=BRASS, anchor='ra')

    buffer = io.BytesIO()
    img.save(buffer, format='PNG', optimize=True)
    buffer.seek(0)
    return buffer
