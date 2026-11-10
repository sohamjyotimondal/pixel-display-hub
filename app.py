import uuid
from pathlib import Path

from fastapi import FastAPI, HTTPException, UploadFile
import cv2
import numpy as np
from PIL import Image, ImageOps
from rembg import new_session, remove
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

import device

UPLOADS = Path(__file__).parent / "uploads"
UPLOADS.mkdir(exist_ok=True)
MAX_BYTES = 15 * 1024 * 1024

app = FastAPI(title="Pixel Display Hub", lifespan=device.lifespan)
app.include_router(device.router)


@app.post("/upload")
async def upload(file: UploadFile):
    if not (file.content_type or "").startswith("image/"):
        raise HTTPException(400, "Only images allowed")
    data = await file.read()
    if len(data) > MAX_BYTES:
        raise HTTPException(413, "Image too large (max 15 MB)")
    ext = Path(file.filename or "").suffix.lower() or ".jpg"
    name = f"{uuid.uuid4().hex}{ext}"
    (UPLOADS / name).write_bytes(data)
    return {"url": f"/uploads/{name}", "name": file.filename, "size": len(data)}


SESSION = new_session("isnet-general-use")  # ~170 MB, downloaded once to ~/.u2net
EDGES = cv2.ximgproc.createStructuredEdgeDetection(str(Path(__file__).parent / "models" / "model.yml.gz"))


def subject_box(mask: np.ndarray, margin: float = 0.08):
    """Square box around the mask's bounding box, with margin. None if mask is empty."""
    ys, xs = np.where(mask)
    if len(xs) == 0:
        return None
    x0, x1, y0, y1 = xs.min(), xs.max(), ys.min(), ys.max()
    side = int(max(x1 - x0, y1 - y0) * (1 + 2 * margin))
    cx, cy = (x0 + x1) // 2, (y0 + y1) // 2
    return (cx - side // 2, cy - side // 2, cx - side // 2 + side, cy - side // 2 + side)


def to_grid(a: np.ndarray, n: int) -> np.ndarray:
    """Average a 0..1 float image down to n x n cells."""
    return np.asarray(Image.fromarray((np.clip(a, 0, 1) * 255).astype(np.uint8)).resize((n, n), Image.BOX), float) / 255


def ink_mask(rgb: np.ndarray) -> np.ndarray:
    """Pencil/pen strokes on paper or a board: flatten lighting, local threshold, drop specks, keep densest cluster."""
    dark = rgb.min(axis=2)  # red/blue ink is dark in some channel, pencil in all
    flat = cv2.divide(dark, cv2.medianBlur(dark, 51), scale=255)
    b = cv2.adaptiveThreshold(flat, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY_INV, 51, 12)
    n, lab, st, _ = cv2.connectedComponentsWithStats(b, connectivity=8)
    keep = np.zeros(n, bool); keep[1:] = st[1:, cv2.CC_STAT_AREA] >= 30
    b = (keep[lab] * 255).astype(np.uint8)
    if not b.any():
        return b
    # strokes touching the photo border are table edges, page edges, background: not the drawing
    n, lab, st, _ = cv2.connectedComponentsWithStats(b, connectivity=8)
    h, w = b.shape
    touches = (st[:, cv2.CC_STAT_LEFT] == 0) | (st[:, cv2.CC_STAT_TOP] == 0) | \
              (st[:, cv2.CC_STAT_LEFT] + st[:, cv2.CC_STAT_WIDTH] >= w) | (st[:, cv2.CC_STAT_TOP] + st[:, cv2.CC_STAT_HEIGHT] >= h)
    touches[0] = True
    inner = np.where(touches[lab], 0, b).astype(np.uint8)
    if inner.any():
        b = inner
    # straight AND thin strokes are tape, table or page edges: a pen stroke is thicker, a letter is curved
    n, lab, st, _ = cv2.connectedComponentsWithStats(b, connectivity=8)
    for i in range(1, n):
        if max(st[i, cv2.CC_STAT_WIDTH], st[i, cv2.CC_STAT_HEIGHT]) < 0.04 * max(h, w):
            continue
        pts = np.column_stack(np.where(lab == i)[::-1]).astype(np.float32)
        vx, vy, x0, y0 = cv2.fitLine(pts, cv2.DIST_L2, 0, 0.01, 0.01).ravel()
        along = (pts[:, 0] - x0) * vx + (pts[:, 1] - y0) * vy
        across = np.abs((pts[:, 0] - x0) * vy - (pts[:, 1] - y0) * vx)
        thickness = len(pts) / max(along.max() - along.min(), 1)
        # ponytail: tape edges measured 1.7-2.3 px thick, pencil strokes 3.2+; tune if a real stroke vanishes
        if (across < 8).mean() >= 0.9 and thickness <= 3.0:
            b[lab == i] = 0
    if not b.any():
        return b
    # group strokes into clusters and keep every real drawing (a heart next to a signature is two clusters).
    # Reject: dense texture, strokes on a textured surface (fabric pattern), straight lines (tape / page edges),
    # and marks with under a fifth of the biggest drawing's ink.
    dens = cv2.blur((b > 0).astype(np.float32), (101, 101))
    g = flat.astype(np.float32)
    mu = cv2.blur(g, (15, 15))
    sd = np.sqrt(np.maximum(cv2.blur(g * g, (15, 15)) - mu * mu, 0))
    inkwide = cv2.dilate(b, np.ones((9, 9), np.uint8)) > 0
    textured = (sd > 5) & ~inkwide
    blob = cv2.dilate(b, np.ones((50, 50), np.uint8))
    n, lab, _, _ = cv2.connectedComponentsWithStats(blob, connectivity=8)
    ink = np.bincount(lab[b > 0], minlength=n)
    clean = []
    for i in range(1, n):
        if ink[i] < 50:
            continue
        region, strokes = lab == i, (lab == i) & (b > 0)
        tex = textured[region].sum() / max((region & ~inkwide).sum(), 1)
        pts = np.column_stack(np.where(strokes)[::-1]).astype(np.float32)
        vx, vy, x0, y0 = cv2.fitLine(pts, cv2.DIST_L2, 0, 0.01, 0.01).ravel()
        linear = float((np.abs((pts[:, 0] - x0) * vy - (pts[:, 1] - y0) * vx) < 12).mean())
        # ponytail: cutoffs from 6 photos (drawings: tex<=0.29, linear<=0.59; junk: tex>=0.46 or linear>=0.84)
        if np.median(dens[strokes]) <= 0.2 and tex < 0.45 and linear < 0.8:
            clean.append(i)
    if not clean:
        clean = [int(np.argmax(ink[1:])) + 1]
    keep = [i for i in clean if ink[i] >= 0.2 * ink[clean].max()]
    b = np.where(np.isin(lab, keep), b, 0).astype(np.uint8)
    # deskew small tilts of the kept drawing only; 90/180 turns are the user's Rotate button
    pts = np.column_stack(np.where(b > 0)[::-1]).astype(np.float32)
    (cx, cy), (w, h), ang = cv2.minAreaRect(pts)
    if w < h:
        ang += 90
    ang = ((ang + 45) % 90) - 45
    if abs(ang) <= 15:
        b = cv2.warpAffine(b, cv2.getRotationMatrix2D((cx, cy), ang, 1), b.shape[::-1])
    return b


def pixelate(img: Image.Image, n: int, mode: str = "auto", style: str = "filled", anim: str = "none",
             frames: int = 12, edge_thr: float = 0.15) -> tuple[list[np.ndarray], str]:
    """Returns (frames, mode used). object: segmented silhouette with the subject's own edges carved out
    from 32 px up. ink: handwriting / drawing strokes. auto picks ink when segmentation finds no subject."""
    m = None
    if mode != "ink":
        m = np.asarray(remove(img, session=SESSION, only_mask=True)) > 127
        if mode == "auto" and not 0.025 < m.mean() < 0.7:  # ponytail: no clear subject -> treat as ink
            mode, m = "ink", None
        else:
            mode = "object"
    margin = 0.08 + (0.1 if anim != "none" else 0)
    if mode == "ink":
        src = ink_mask(np.asarray(img))
        box = subject_box(src > 0, margin)
        if box is None:
            return [np.zeros((n, n), bool)], mode
        mcrop = (np.asarray(Image.fromarray(src).crop(box).resize((512, 512), Image.NEAREST)) > 127).astype(np.uint8)
        k = max(1, int(512 / n * 0.6))  # fatten strokes to ~1 cell so thin lines survive downsampling
        mcrop = cv2.dilate(mcrop, np.ones((k, k), np.uint8)).astype(np.float32)
        e, fill = np.zeros_like(mcrop), 0.08
    else:
        box = subject_box(m, margin)
        if box is None:
            return [np.zeros((n, n), bool)], mode
        crop = img.crop(box).resize((512, 512))
        mcrop = (np.asarray(Image.fromarray(m.astype(np.uint8) * 255).crop(box).resize((512, 512))) > 127).astype(np.float32)
        e, fill = np.zeros_like(mcrop), 0.5
        if n >= 32:  # ponytail: below 32 cells inner detail is noise, silhouette only
            e = EDGES.detectEdges(np.float32(cv2.cvtColor(np.asarray(crop), cv2.COLOR_RGB2BGR)) / 255)
            e[mcrop < 0.5] = 0
    out = []
    for k in range(frames if anim != "none" else 1):
        t = 2 * np.pi * k / frames
        M = np.float32([[1, 0, 0], [0, 1, 0]])
        if anim == "float":
            M[1, 2] = 512 / n * 1.5 * np.sin(t)  # 1.5 cells up/down
        elif anim == "wobble":
            M = cv2.getRotationMatrix2D((256, 256), 7 * np.sin(t), 1)
        elif anim == "breathe":
            M = cv2.getRotationMatrix2D((256, 256), 0, 1 + 0.07 * np.sin(t))
        mk, ek = cv2.warpAffine(mcrop, M, (512, 512)), cv2.warpAffine(e, M, (512, 512))
        inside = to_grid(mk, n) >= fill
        edges = inside & (to_grid(ek, n) > edge_thr)
        if style == "lines" and mode == "object":
            eroded = cv2.erode(inside.astype(np.uint8), np.ones((3, 3), np.uint8)) > 0
            out.append((inside & ~eroded) | edges)
        else:
            out.append(inside & ~edges)
    return out, mode


@app.post("/process")
def process(name: str, size: int = 8, invert: bool = False, style: str = "filled", anim: str = "none",
            mode: str = "auto", rot: int = 0):
    path = UPLOADS / Path(name).name
    if not path.is_file():
        raise HTTPException(404, "Image not found")
    size = max(4, min(size, 128))
    img = ImageOps.exif_transpose(Image.open(path)).convert("RGB")
    img.thumbnail((1024, 1024))
    if rot % 360:
        img = img.rotate(-(rot % 360), expand=True)
    grids, used = pixelate(img, size, mode, style, anim)
    if not grids[0].any():  # ponytail: nothing found -> old mean threshold as fallback
        a = np.asarray(ImageOps.fit(img.convert("L"), (size, size), Image.LANCZOS), float)
        grids, used = [a > a.mean()], "threshold"
    grids = [g != invert for g in grids]
    token = uuid.uuid4().hex[:12]          # lets the phone save this result to the clock's library
    device.RECENT[token] = {"size": size, "fps": 8, "frames": grids}
    while len(device.RECENT) > 8:
        device.RECENT.pop(next(iter(device.RECENT)))
    frames = [g.tolist() for g in grids]
    return {"size": size, "rows": frames[0], "frames": frames, "fps": 8, "mode": used, "token": token}


app.mount("/uploads", StaticFiles(directory=UPLOADS), name="uploads")
app.mount("/", StaticFiles(directory=Path(__file__).parent / "static", html=True), name="static")
