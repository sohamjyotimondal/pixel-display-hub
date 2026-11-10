"""Particle engine for the pixel clock.

Every lit pixel is a particle. Marbles = grain physics on an occupancy grid, fluid = FLIP solver.
release() lets a parked picture go, reform()/morph_to() fly particles to a new picture,
diff_to() drains the pixels that changed and drops the new ones in (minute change).
Coordinates are display pixels, y down. Gravity is passed in px/s^2.
"""
import math

import numpy as np
from numba import njit

PARKED, FREE, HOMING, LEAVING, FALLIN, FROZEN, HIDDEN, DEAD = range(8)
SLEEP_N = 12          # blocked substeps before a marble goes to sleep
ROLL = 5              # a marble rolls sideways if there is a drop within this many cells (heap slope ~1/ROLL)
G_MIN = 20.0          # px/s^2: below this in-plane gravity counts as "lying flat"
FALL_G = 520.0        # px/s^2 used for pixels dropping in / draining out while upright
AIR, FLUID, SOLID = 0, 1, 2


@njit(cache=True)
def _free(occ, n, cx, cy):
    return 0 <= cx < n and 0 <= cy < n and occ[cy, cx] == 0


@njit(cache=True)
def _roll_dir(occ, n, cx, cy, sx, sy, r0):
    """For axis-aligned gravity: +1/-1 if a supported marble can roll that way toward a drop, else 0."""
    px, py = -sy, sx
    for t in range(2):
        r = r0 if t == 0 else -r0
        for j in range(1, ROLL + 1):
            ax, ay = cx + j * px * r, cy + j * py * r
            if not _free(occ, n, ax, ay):
                break
            if _free(occ, n, ax + sx, ay + sy):
                return r
    return 0


@njit(cache=True)
def _timers(state, nxt, delay, x, y, vx, vy, sleep, occ, n, dt, fluid):
    for i in range(len(state)):
        if nxt[i] < 0:
            continue
        delay[i] -= dt
        if delay[i] > 0:
            continue
        s, old = nxt[i], state[i]
        nxt[i] = -1
        if old == PARKED and (s == LEAVING or (s == FREE and fluid)):
            occ[int(y[i]), int(x[i])] = 0
        if s == FREE:
            vx[i] = (np.random.random() - 0.5) * 16.0
            vy[i] = (np.random.random() - 0.5) * 16.0
            sleep[i] = 0
        elif s == LEAVING:
            vx[i] = (np.random.random() - 0.5) * 24.0
            vy[i] = -np.random.random() * 20.0
        state[i] = s


@njit(cache=True)
def _marbles(x, y, vx, vy, state, sleep, rest, order, occ, n, gx, gy, dt, wake, damp, vmax):
    gm = math.hypot(gx, gy)
    sx = sy = 0
    if gm > G_MIN:
        ux, uy = gx / gm, gy / gm
        sx = 1 if ux > 0.38 else (-1 if ux < -0.38 else 0)
        sy = 1 if uy > 0.38 else (-1 if uy < -0.38 else 0)
    # the two "slide" cells next to straight-down, for the 8-direction gravity step
    if sx != 0 and sy != 0:
        a1x, a1y, a2x, a2y = sx, 0, 0, sy
    else:
        a1x, a1y, a2x, a2y = sx - sy, sy + sx, sx + sy, sy - sx
    axis = (sx != 0) != (sy != 0)
    for k in range(len(order)):
        i = order[k]
        if state[i] != FREE:
            continue
        cx, cy = int(x[i]), int(y[i])
        if sleep[i] >= SLEEP_N:
            if wake:
                sleep[i] = 0
            elif (sx != 0 or sy != 0) and (_free(occ, n, cx + sx, cy + sy) or _free(occ, n, cx + a1x, cy + a1y)
                                           or _free(occ, n, cx + a2x, cy + a2y)
                                           or (axis and _roll_dir(occ, n, cx, cy, sx, sy, 1) != 0)):
                sleep[i] = 0
            else:
                continue
        vx[i] = (vx[i] + gx * dt) * damp
        vy[i] = (vy[i] + gy * dt) * damp
        sp = math.hypot(vx[i], vy[i])
        if sp > vmax:
            vx[i] *= vmax / sp
            vy[i] *= vmax / sp
        nx, ny = x[i] + vx[i] * dt, y[i] + vy[i] * dt
        ncx, ncy = math.floor(nx), math.floor(ny)
        if ncx == cx and ncy == cy:
            x[i], y[i] = nx, ny
            continue
        if _free(occ, n, ncx, ncy):
            occ[cy, cx] = 0
            occ[ncy, ncx] = 1
            x[i], y[i] = nx, ny
            sleep[i] = 0
            continue
        dxc, dyc = ncx - cx, ncy - cy
        # walls are lively, marble-on-marble hits soak up energy so a falling clump stays together
        e = rest[i] if not (0 <= ncx < n and 0 <= ncy < n) else rest[i] * 0.4
        moved = False
        if dxc != 0 and dyc != 0:
            # diagonal step blocked: keep the stronger axis if that cell is open
            xfirst = abs(vx[i]) >= abs(vy[i])
            for t in range(2):
                if (t == 0) == xfirst:
                    if _free(occ, n, ncx, cy):
                        occ[cy, cx] = 0
                        occ[cy, ncx] = 1
                        x[i] = nx
                        vy[i] = -vy[i] * e
                        moved = True
                        break
                elif _free(occ, n, cx, ncy):
                    occ[cy, cx] = 0
                    occ[ncy, cx] = 1
                    y[i] = ny
                    vx[i] = -vx[i] * e
                    moved = True
                    break
            if not moved:
                vx[i] = -vx[i] * e
                vy[i] = -vy[i] * e
        else:
            # straight step blocked: roll off to one side (never uphill), else bounce
            r0 = 1 if np.random.random() < 0.5 else -1
            for t in range(2):
                r = r0 if t == 0 else -r0
                ax, ay = ncx - dyc * r, ncy + dxc * r
                if gm > G_MIN and (ax - cx) * gx + (ay - cy) * gy <= 0:
                    continue
                if _free(occ, n, ax, ay):
                    occ[cy, cx] = 0
                    occ[ay, ax] = 1
                    x[i], y[i] = ax + 0.5, ay + 0.5
                    if dxc != 0:
                        along = abs(vx[i])
                        vx[i] *= 0.45
                        vy[i] = r * max(abs(vy[i]) * 0.8, 0.25 * along)
                    else:
                        along = abs(vy[i])
                        vy[i] *= 0.45
                        vx[i] = -r * max(abs(vx[i]) * 0.8, 0.25 * along)
                    moved = True
                    break
            if not moved:
                if dxc != 0:
                    vx[i] = -vx[i] * e
                    vy[i] *= 0.92
                    if abs(vx[i]) < 14.0:
                        vx[i] = 0.0
                else:
                    vy[i] = -vy[i] * e
                    vx[i] *= 0.92
                    if abs(vy[i]) < 14.0:
                        vy[i] = 0.0
        if not moved and axis and not _free(occ, n, cx + sx, cy + sy):
            # resting on something: roll toward the nearest drop so the heap levels like marbles, not sand
            r = _roll_dir(occ, n, cx, cy, sx, sy, 1 if np.random.random() < 0.5 else -1)
            if r != 0:
                ax, ay = cx - sy * r, cy + sx * r
                occ[cy, cx] = 0
                occ[ay, ax] = 1
                x[i], y[i] = ax + 0.5, ay + 0.5
                vx[i], vy[i] = -sy * r * 45.0, sx * r * 45.0
                moved = True
        if moved:
            sleep[i] = 0
        elif vx[i] * vx[i] + vy[i] * vy[i] < 64.0:
            sleep[i] += 1
            if sleep[i] >= SLEEP_N:
                vx[i] = 0.0
                vy[i] = 0.0
        else:
            sleep[i] = 0


@njit(cache=True)
def _others(x, y, vx, vy, tx, ty, state, sleep, occ, n, dt, w, zeta):
    """HOMING springs, FALLIN drops, LEAVING drains. None of these collide."""
    for i in range(len(state)):
        s = state[i]
        if s == HOMING:
            ex, ey = tx[i] + 0.5 - x[i], ty[i] + 0.5 - y[i]
            vx[i] += (w * w * ex - 2 * zeta * w * vx[i]) * dt
            vy[i] += (w * w * ey - 2 * zeta * w * vy[i]) * dt
            x[i] += vx[i] * dt
            y[i] += vy[i] * dt
            if ex * ex + ey * ey < 0.2 and vx[i] * vx[i] + vy[i] * vy[i] < 150.0:
                if occ[ty[i], tx[i]]:
                    state[i] = DEAD      # surplus particle merging into an already-filled pixel
                else:
                    occ[ty[i], tx[i]] = 1
                    state[i] = PARKED
                    x[i], y[i], vx[i], vy[i] = tx[i] + 0.5, ty[i] + 0.5, 0.0, 0.0
        elif s == FALLIN:
            vy[i] += FALL_G * dt
            y[i] += vy[i] * dt
            if y[i] >= ty[i] + 0.5:
                if vy[i] > 90.0 and sleep[i] == 0:
                    y[i] = ty[i] + 0.5
                    vy[i] = -vy[i] * 0.22   # one small bounce on landing
                    sleep[i] = 1
                elif occ[ty[i], tx[i]]:
                    state[i] = DEAD
                else:
                    occ[ty[i], tx[i]] = 1
                    state[i] = PARKED
                    x[i], y[i], vx[i], vy[i] = tx[i] + 0.5, ty[i] + 0.5, 0.0, 0.0
                    sleep[i] = 0
        elif s == LEAVING:
            vy[i] += FALL_G * dt
            x[i] += vx[i] * dt
            y[i] += vy[i] * dt
            if y[i] > n + 2 or x[i] < -2 or x[i] > n + 2:
                state[i] = DEAD


@njit(cache=True)
def _place_unique(x, y, state, occ, n):
    """Give every FREE particle its own cell (nearest open one). Needed when entering marble physics."""
    for i in range(len(state)):
        if state[i] != FREE:
            continue
        cx = min(max(int(x[i]), 0), n - 1)
        cy = min(max(int(y[i]), 0), n - 1)
        if occ[cy, cx]:
            found = False
            for rad in range(1, n):
                for dy in range(-rad, rad + 1):
                    for dx in range(-rad, rad + 1):
                        if max(abs(dx), abs(dy)) != rad:
                            continue
                        if _free(occ, n, cx + dx, cy + dy):
                            cx, cy = cx + dx, cy + dy
                            found = True
                            break
                    if found:
                        break
                if found:
                    break
            if not found:
                state[i] = DEAD
                continue
        occ[cy, cx] = 1
        x[i], y[i] = cx + 0.5, cy + 0.5


@njit(cache=True)
def _render(x, y, state, n, fluid):
    out = np.zeros((n, n), np.uint8)
    g = np.zeros((n, n), np.uint8)      # free particles only
    for i in range(len(state)):
        if state[i] >= HIDDEN:
            continue
        cx, cy = int(math.floor(x[i])), int(math.floor(y[i]))
        if 0 <= cx < n and 0 <= cy < n:
            out[cy, cx] = 1
            if state[i] == FREE:
                g[cy, cx] = 1
    if fluid:
        # close pinholes inside the liquid so it reads as a solid body
        for cy in range(1, n - 1):
            for cx in range(1, n - 1):
                if g[cy, cx] == 0:
                    c = (g[cy - 1, cx - 1] + g[cy - 1, cx] + g[cy - 1, cx + 1] + g[cy, cx - 1] + g[cy, cx + 1]
                         + g[cy + 1, cx - 1] + g[cy + 1, cx] + g[cy + 1, cx + 1])
                    if c >= 5:
                        out[cy, cx] = 1
    return out


@njit(cache=True)
def _flip(x, y, vx, vy, idx, n, gx, gy, dt, u, v, pu, pv, du, dv, ctype, dens,
          iters, ratio, rest_d, drift_k, push_iters, damp):
    """One FLIP fluid step (after Matthias Mueller, Ten Minute Physics #18). Fluid cell = 2 px, 1-cell solid border."""
    h = 2.0
    h1 = 0.5
    F = u.shape[0]
    m = len(idx)
    r = 0.5
    lo, hi = r, n - r
    for k in range(m):
        i = idx[k]
        vx[i] = (vx[i] + gx * dt) * damp
        vy[i] = (vy[i] + gy * dt) * damp
        x[i] += vx[i] * dt
        y[i] += vy[i] * dt
    # --- push particles apart (spatial hash), keeps about one particle per pixel
    ps = 2.2 * r
    pn = int(n / ps) + 1
    start = np.zeros(pn * pn + 1, np.int32)
    ids = np.zeros(m, np.int32)
    for it in range(push_iters):
        start[:] = 0
        for k in range(m):
            i = idx[k]
            ci = min(max(int(x[i] / ps), 0), pn - 1)
            cj = min(max(int(y[i] / ps), 0), pn - 1)
            start[ci * pn + cj] += 1
        first = 0
        for c in range(pn * pn):
            first += start[c]
            start[c] = first
        start[pn * pn] = first
        for k in range(m):
            i = idx[k]
            ci = min(max(int(x[i] / ps), 0), pn - 1)
            cj = min(max(int(y[i] / ps), 0), pn - 1)
            start[ci * pn + cj] -= 1
            ids[start[ci * pn + cj]] = i
        md = 2.0 * r
        for k in range(m):
            i = idx[k]
            ci = int(x[i] / ps)
            cj = int(y[i] / ps)
            for a in range(max(ci - 1, 0), min(ci + 1, pn - 1) + 1):
                for b in range(max(cj - 1, 0), min(cj + 1, pn - 1) + 1):
                    c = a * pn + b
                    for q in range(start[c], start[c + 1]):
                        j = ids[q]
                        if j == i:
                            continue
                        dx, dy = x[j] - x[i], y[j] - y[i]
                        d2 = dx * dx + dy * dy
                        if d2 > md * md or d2 == 0.0:
                            continue
                        d = math.sqrt(d2)
                        s = 0.5 * (md - d) / d
                        x[i] -= dx * s
                        y[i] -= dy * s
                        x[j] += dx * s
                        y[j] += dy * s
    # --- walls
    for k in range(m):
        i = idx[k]
        if x[i] < lo:
            x[i] = lo
            vx[i] = 0.0
        if x[i] > hi:
            x[i] = hi
            vx[i] = 0.0
        if y[i] < lo:
            y[i] = lo
            vy[i] = 0.0
        if y[i] > hi:
            y[i] = hi
            vy[i] = 0.0
    # --- particles to grid
    pu[:, :] = u
    pv[:, :] = v
    du[:, :] = 0.0
    dv[:, :] = 0.0
    u[:, :] = 0.0
    v[:, :] = 0.0
    for a in range(F):
        for b in range(F):
            ctype[a, b] = SOLID if (a == 0 or b == 0 or a == F - 1 or b == F - 1) else AIR
    for k in range(m):
        i = idx[k]
        a = min(max(int((x[i] + h) * h1), 0), F - 1)
        b = min(max(int((y[i] + h) * h1), 0), F - 1)
        if ctype[a, b] == AIR:
            ctype[a, b] = FLUID
    for comp in range(2):
        ox = 0.0 if comp == 0 else 0.5 * h
        oy = 0.5 * h if comp == 0 else 0.0
        f = u if comp == 0 else v
        d = du if comp == 0 else dv
        for k in range(m):
            i = idx[k]
            px = min(max(x[i] + h, h), (F - 1) * h)
            py = min(max(y[i] + h, h), (F - 1) * h)
            x0 = min(int((px - ox) * h1), F - 2)
            tx = ((px - ox) - x0 * h) * h1
            x1 = min(x0 + 1, F - 2)
            y0 = min(int((py - oy) * h1), F - 2)
            ty = ((py - oy) - y0 * h) * h1
            y1 = min(y0 + 1, F - 2)
            sx, sy = 1.0 - tx, 1.0 - ty
            pvv = vx[i] if comp == 0 else vy[i]
            f[x0, y0] += pvv * sx * sy
            d[x0, y0] += sx * sy
            f[x1, y0] += pvv * tx * sy
            d[x1, y0] += tx * sy
            f[x1, y1] += pvv * tx * ty
            d[x1, y1] += tx * ty
            f[x0, y1] += pvv * sx * ty
            d[x0, y1] += sx * ty
        for a in range(F):
            for b in range(F):
                if d[a, b] > 0.0:
                    f[a, b] /= d[a, b]
    for a in range(F):
        for b in range(F):
            solid = ctype[a, b] == SOLID
            if solid or (a > 0 and ctype[a - 1, b] == SOLID):
                u[a, b] = pu[a, b]
            if solid or (b > 0 and ctype[a, b - 1] == SOLID):
                v[a, b] = pv[a, b]
    # --- density (for drift compensation)
    dens[:, :] = 0.0
    for k in range(m):
        i = idx[k]
        px = min(max(x[i] + h, h), (F - 1) * h)
        py = min(max(y[i] + h, h), (F - 1) * h)
        x0 = int((px - 0.5 * h) * h1)
        tx = ((px - 0.5 * h) - x0 * h) * h1
        x1 = min(x0 + 1, F - 2)
        y0 = int((py - 0.5 * h) * h1)
        ty = ((py - 0.5 * h) - y0 * h) * h1
        y1 = min(y0 + 1, F - 2)
        sx, sy = 1.0 - tx, 1.0 - ty
        dens[x0, y0] += sx * sy
        dens[x1, y0] += tx * sy
        dens[x1, y1] += tx * ty
        dens[x0, y1] += sx * ty
    # --- make the grid velocity field incompressible (FLIP delta is measured against this pre-solve field)
    pu[:, :] = u
    pv[:, :] = v
    for it in range(iters):
        for a in range(1, F - 1):
            for b in range(1, F - 1):
                if ctype[a, b] != FLUID:
                    continue
                sx0 = 0.0 if ctype[a - 1, b] == SOLID else 1.0
                sx1 = 0.0 if ctype[a + 1, b] == SOLID else 1.0
                sy0 = 0.0 if ctype[a, b - 1] == SOLID else 1.0
                sy1 = 0.0 if ctype[a, b + 1] == SOLID else 1.0
                ssum = sx0 + sx1 + sy0 + sy1
                if ssum == 0.0:
                    continue
                div = u[a + 1, b] - u[a, b] + v[a, b + 1] - v[a, b]
                comp_ = dens[a, b] - rest_d
                if comp_ > 0.0:
                    div -= drift_k * comp_
                p = -div / ssum * 1.9
                u[a, b] -= sx0 * p
                u[a + 1, b] += sx1 * p
                v[a, b] -= sy0 * p
                v[a, b + 1] += sy1 * p
    # --- grid back to particles (PIC/FLIP blend)
    for comp in range(2):
        ox = 0.0 if comp == 0 else 0.5 * h
        oy = 0.5 * h if comp == 0 else 0.0
        f = u if comp == 0 else v
        pf = pu if comp == 0 else pv
        for k in range(m):
            i = idx[k]
            px = min(max(x[i] + h, h), (F - 1) * h)
            py = min(max(y[i] + h, h), (F - 1) * h)
            x0 = min(int((px - ox) * h1), F - 2)
            tx = ((px - ox) - x0 * h) * h1
            x1 = min(x0 + 1, F - 2)
            y0 = min(int((py - oy) * h1), F - 2)
            ty = ((py - oy) - y0 * h) * h1
            y1 = min(y0 + 1, F - 2)
            sx, sy = 1.0 - tx, 1.0 - ty
            wsum = 0.0
            pic = 0.0
            corr = 0.0
            for q in range(4):
                if q == 0:
                    a, b, wq = x0, y0, sx * sy
                elif q == 1:
                    a, b, wq = x1, y0, tx * sy
                elif q == 2:
                    a, b, wq = x1, y1, tx * ty
                else:
                    a, b, wq = x0, y1, sx * ty
                if comp == 0:
                    valid = ctype[a, b] != AIR or (a > 0 and ctype[a - 1, b] != AIR)
                else:
                    valid = ctype[a, b] != AIR or (b > 0 and ctype[a, b - 1] != AIR)
                if valid:
                    wsum += wq
                    pic += wq * f[a, b]
                    corr += wq * (f[a, b] - pf[a, b])
            if wsum > 0.0:
                old = vx[i] if comp == 0 else vy[i]
                new = (1.0 - ratio) * (pic / wsum) + ratio * (old + corr / wsum)
                if comp == 0:
                    vx[i] = new
                else:
                    vy[i] = new


class Sim:
    def __init__(self, n: int = 128, fluid: bool = False, bounce: float = 0.3, seed: int = 1):
        self.n, self.fluid, self.bounce = n, fluid, bounce
        self.rng = np.random.default_rng(seed)
        self.occ = np.zeros((n, n), np.uint8)
        F = n // 2 + 2
        self._grid = [np.zeros((F, F)) for _ in range(6)] + [np.zeros((F, F), np.int8), np.zeros((F, F))]
        self._gref = (0.0, 0.0)
        self._calm = 0.0
        self._alloc(np.zeros(0), np.zeros(0))

    # ---- particle storage
    def _alloc(self, xs, ys):
        m = len(xs)
        self.x, self.y = xs.astype(np.float64) + 0.5, ys.astype(np.float64) + 0.5
        self.vx, self.vy = np.zeros(m), np.zeros(m)
        self.tx, self.ty = xs.astype(np.int32), ys.astype(np.int32)
        self.state = np.full(m, PARKED, np.int8)
        self.nxt = np.full(m, -1, np.int8)
        self.delay = np.zeros(m)
        self.sleep = np.zeros(m, np.int16)
        self.rest = self.bounce * self.rng.uniform(0.7, 1.3, m)

    _FIELDS = ("x", "y", "vx", "vy", "tx", "ty", "state", "nxt", "delay", "sleep", "rest")

    def _take(self, idx):
        for f in self._FIELDS:
            setattr(self, f, getattr(self, f)[idx])

    def _append(self, **cols):
        m = len(next(iter(cols.values())))
        base = dict(x=np.zeros(m), y=np.zeros(m), vx=np.zeros(m), vy=np.zeros(m), tx=np.zeros(m, np.int32),
                    ty=np.zeros(m, np.int32), state=np.full(m, PARKED, np.int8), nxt=np.full(m, -1, np.int8),
                    delay=np.zeros(m), sleep=np.zeros(m, np.int16), rest=self.bounce * self.rng.uniform(0.7, 1.3, m))
        base.update(cols)
        for f in self._FIELDS:
            setattr(self, f, np.concatenate([getattr(self, f), base[f].astype(getattr(self, f).dtype)]))

    def __len__(self):
        return len(self.x)

    # ---- content
    def load(self, bitmap):
        """Park one particle on every lit pixel."""
        ys, xs = np.nonzero(bitmap)
        self._alloc(xs, ys)
        self.occ[:] = 0
        self.occ[ys, xs] = 1

    def release(self, gx, gy, spread=0.28):
        """Let everything go. Parked pixels crumble in a wave, leading edge (along gravity) first."""
        n = self.n
        self.state[self.state == HIDDEN] = DEAD
        loose = np.isin(self.state, (HOMING, FALLIN, FROZEN))
        self.state[loose] = FREE
        self.nxt[loose] = -1
        self.sleep[loose] = 0
        if not self.fluid:
            _place_unique(self.x, self.y, self.state, self.occ, n)
        parked = (self.state == PARKED)
        if parked.any():
            gm = math.hypot(gx, gy)
            if gm > G_MIN:
                proj = (self.x[parked] * gx + self.y[parked] * gy) / gm
            else:  # lying flat: burst outward from the centre instead
                proj = -np.hypot(self.x[parked] - n / 2, self.y[parked] - n / 2)
            span = max(float(np.ptp(proj)), 1.0)
            self.delay[parked] = (proj.max() - proj) / span * spread + self.rng.uniform(0, 0.03, int(parked.sum()))
            self.nxt[parked] = FREE
        self._gref = (0.0, 0.0)
        self._calm = 0.0

    def _assign(self, target, delay_fn):
        """Send every live particle to a pixel of `target`. Counts are reconciled by splitting or merging."""
        live = np.nonzero(np.isin(self.state, (PARKED, FREE, HOMING, FALLIN, FROZEN)))[0]
        self.state[np.isin(self.state, (HIDDEN,))] = DEAD
        tys, txs = np.nonzero(target)
        self.occ[:] = 0
        if len(txs) == 0:
            self.state[live] = LEAVING
            self.nxt[live] = -1
            return
        if len(live) == 0:
            self.load(np.zeros_like(target))
            self.diff_to(target)
            return
        if len(live) < len(txs):      # too few: split extra particles off existing ones
            src = self.rng.choice(live, len(txs) - len(live))
            self._append(x=self.x[src], y=self.y[src], vx=self.vx[src], vy=self.vy[src],
                         state=np.full(len(src), FROZEN, np.int8))
            live = np.concatenate([live, np.arange(len(self) - len(src), len(self))])
        elif len(live) > len(txs):    # too many: the surplus heads for an already-claimed pixel and merges
            extra = self.rng.integers(0, len(txs), len(live) - len(txs))
            txs, tys = np.concatenate([txs, txs[extra]]), np.concatenate([tys, tys[extra]])
        # strip matching: same left-to-right order, then top-to-bottom inside each strip -> short, uncrossed paths
        k = len(live)
        strip = max(int(math.sqrt(k)), 1)
        po = live[np.argsort(self.x[live], kind="stable")]
        to = np.argsort(txs, kind="stable")
        for s in range(0, k, strip):
            p, t = po[s:s + strip], to[s:s + strip]
            p = p[np.argsort(self.y[p], kind="stable")]
            t = t[np.argsort(tys[t], kind="stable")]
            self.tx[p], self.ty[p] = txs[t], tys[t]
        self.state[live] = FROZEN
        self.nxt[live] = HOMING
        self.delay[live] = delay_fn(live)
        self.sleep[live] = 0

    def reform(self, target, gx, gy, spread=0.45):
        """From a settled heap back into a picture. The top of the heap leaves first, so it unzips upward."""
        def delays(live):
            gm = math.hypot(gx, gy)
            if gm > G_MIN:
                proj = (self.x[live] * gx + self.y[live] * gy) / gm
            else:
                proj = self.rng.uniform(0, 1, len(live))
            span = max(float(np.ptp(proj)), 1.0)
            return (proj - proj.min()) / span * spread + self.rng.uniform(0, 0.04, len(live))
        self._assign(target, delays)

    def morph_to(self, target, spread=0.25):
        """Picture to picture (clock <-> artwork)."""
        self._assign(target, lambda live: self.rng.uniform(0, spread, len(live)))

    def diff_to(self, target):
        """Small change (minute tick): pixels no longer lit drain out the bottom, new ones drop in from the top."""
        if not np.all(self.state == PARKED) or (self.nxt >= 0).any():
            self.morph_to(target)
            return
        cx, cy = self.x.astype(np.int32), self.y.astype(np.int32)
        gone = ~target[cy, cx].astype(bool)
        if gone.any():
            yy = self.y[gone]
            self.nxt[gone] = LEAVING
            self.delay[gone] = (yy.max() - yy) / max(float(np.ptp(yy)), 1.0) * 0.22 + self.rng.uniform(0, 0.05, int(gone.sum()))
        new = target.astype(bool) & ~self.occ.astype(bool)
        tys, txs = np.nonzero(new)
        if len(txs):
            m = len(txs)
            span = max(float(np.ptp(tys)), 1.0)
            self._append(x=txs + 0.5, y=-self.rng.uniform(2, 14, m), tx=txs, ty=tys,
                         state=np.full(m, HIDDEN, np.int8), nxt=np.full(m, FALLIN, np.int8),
                         delay=0.18 + (tys.max() - tys) / span * 0.3 + self.rng.uniform(0, 0.05, m))

    def kick(self, strength=160.0):
        free = self.state == FREE
        m = int(free.sum())
        self.vx[free] += self.rng.normal(0, strength, m)
        self.vy[free] += self.rng.normal(0, strength, m)
        self.sleep[free] = 0
        self._calm = 0.0

    def set_fluid(self, fluid: bool):
        if fluid == self.fluid:
            return
        self.fluid = fluid
        free = self.state == FREE
        if fluid:
            cy, cx = self.y[free].astype(np.int32), self.x[free].astype(np.int32)
            self.occ[np.clip(cy, 0, self.n - 1), np.clip(cx, 0, self.n - 1)] = 0
        else:
            _place_unique(self.x, self.y, self.state, self.occ, self.n)
        self.sleep[free] = 0
        self._calm = 0.0

    # ---- stepping
    def step(self, dt, gx, gy):
        n = self.n
        if len(self) == 0:
            return
        _timers(self.state, self.nxt, self.delay, self.x, self.y, self.vx, self.vy, self.sleep, self.occ, n, dt, self.fluid)
        # a real change in gravity wakes sleeping particles
        rx, ry = self._gref
        wake = math.hypot(gx - rx, gy - ry) > 0.07 * max(math.hypot(rx, ry), math.hypot(gx, gy), G_MIN)
        if wake:
            self._gref = (gx, gy)
            self._calm = 0.0
        free = self.state == FREE
        if free.any():
            if self.fluid:
                idx = np.nonzero(free)[0].astype(np.int32)
                if self._calm < 0.6:      # a calm pool is frozen: no shimmer, no CPU
                    for _ in range(2):
                        _flip(self.x, self.y, self.vx, self.vy, idx, n, gx, gy, dt / 2, *self._grid,
                              30, 0.9, 3.4, 60.0, 2, 0.999)
                    speed = float(np.mean(np.hypot(self.vx[idx], self.vy[idx])))
                    self._calm = self._calm + dt if speed < 5.0 and not (self.nxt >= 0).any() else 0.0
            else:
                order = np.argsort(-(self.x * gx + self.y * gy), kind="stable").astype(np.int32)
                sub = max(1, int(math.ceil(dt * 240)))
                for _ in range(sub):
                    _marbles(self.x, self.y, self.vx, self.vy, self.state, self.sleep, self.rest, order, self.occ, n,
                             gx, gy, dt / sub, wake, 0.9985, 0.95 * sub / dt)
                    wake = False
        for _ in range(2):
            _others(self.x, self.y, self.vx, self.vy, self.tx, self.ty, self.state, self.sleep, self.occ, n, dt / 2, 15.0, 0.72)
        dead = self.state == DEAD
        if dead.any():
            self._take(~dead)

    def render(self):
        return _render(self.x, self.y, self.state, self.n, self.fluid)

    def settled(self):
        """True when every particle is parked on its pixel with nothing pending."""
        return bool(np.all(self.state == PARKED) and not (self.nxt >= 0).any())

    def at_rest(self):
        """True when a released heap or pool has stopped moving."""
        free = self.state == FREE
        if not np.all(free | (self.state == PARKED)) or (self.nxt >= 0).any():
            return False
        if self.fluid:
            return self._calm >= 0.6
        return bool(np.all(self.sleep[free] >= SLEEP_N))
