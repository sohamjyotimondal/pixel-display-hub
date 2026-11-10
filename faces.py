"""What the clock shows when nothing is falling: digits, seconds bar, saved artwork."""
import numpy as np

FONT = {  # 5x7
    "0": "01110 10001 10011 10101 11001 10001 01110",
    "1": "00100 01100 00100 00100 00100 00100 01110",
    "2": "01110 10001 00001 00010 00100 01000 11111",
    "3": "11111 00010 00100 00010 00001 10001 01110",
    "4": "00010 00110 01010 10010 11111 00010 00010",
    "5": "11111 10000 11110 00001 00001 10001 01110",
    "6": "00110 01000 10000 11110 10001 10001 01110",
    "7": "11111 00001 00010 00100 01000 01000 01000",
    "8": "01110 10001 10001 01110 10001 10001 01110",
    "9": "01110 10001 10001 01111 00001 00010 01100",
    " ": "00000 00000 00000 00000 00000 00000 00000",
}
GLYPH = {c: np.array([[b == "1" for b in row] for row in rows.split()]) for c, rows in FONT.items()}

BAR_SEG, BAR_H = 2, 3     # one seconds marble = 2 x 3 px, 60 of them = 120 px


def _put(bm, glyph, x, y, scale):
    g = np.kron(glyph, np.ones((scale, scale), bool))
    bm[y:y + g.shape[0], x:x + g.shape[1]] |= g


def bar_slot(sec: int, n: int = 128):
    """Top-left corner of the marble for a given second."""
    return (n - 60 * BAR_SEG) // 2 + sec * BAR_SEG, n - 8


def clock_bitmap(hour: int, minute: int, second: int, n: int = 128, layout: str = "inline",
                 hour24: bool = True, seconds: bool = True) -> np.ndarray:
    """Digits plus one bar marble for every completed second of this minute."""
    bm = np.zeros((n, n), bool)
    if not hour24:
        hour = hour % 12 or 12
    hh = f"{hour:02d}" if hour24 else f"{hour:2d}"
    mm = f"{minute:02d}"
    if layout == "stacked":
        s = n // 18                       # 7 at 128
        w, h, gap = 5 * s, 7 * s, s
        x0 = (n - 2 * w - gap) // 2
        y0 = (n - 12 - 2 * h - gap) // 2
        for i, c in enumerate(hh):
            _put(bm, GLYPH[c], x0 + i * (w + gap), y0, s)
        for i, c in enumerate(mm):
            _put(bm, GLYPH[c], x0 + i * (w + gap), y0 + h + gap, s)
    else:
        s = n // 25                       # 5 at 128
        w, h, gap = 5 * s, 7 * s, 4 * s // 5
        total = 4 * w + s + 4 * gap
        x, y0 = (n - total) // 2, (n - 12 - h) // 2
        for i, c in enumerate(hh + ":" + mm):
            if c == ":":
                bm[y0 + 2 * s:y0 + 3 * s, x:x + s] = True
                bm[y0 + 4 * s:y0 + 5 * s, x:x + s] = True
                x += s + gap
            else:
                _put(bm, GLYPH[c], x, y0, s)
                x += w + gap
    if seconds:
        for sec in range(second):
            bx, by = bar_slot(sec, n)
            bm[by:by + BAR_H, bx:bx + BAR_SEG] = True
    return bm


def scale_art(frame, n: int = 128) -> np.ndarray:
    """Saved artwork (8..128 px square) to display size, nearest neighbour, centred."""
    a = np.asarray(frame, bool)
    k = max(n // a.shape[0], 1)
    a = np.kron(a, np.ones((k, k), bool))[:n, :n]
    out = np.zeros((n, n), bool)
    oy, ox = (n - a.shape[0]) // 2, (n - a.shape[1]) // 2
    out[oy:oy + a.shape[0], ox:ox + a.shape[1]] = a
    return out
