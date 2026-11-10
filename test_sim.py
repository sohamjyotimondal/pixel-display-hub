"""Checks that fail if the particle engine breaks. Run: python test_sim.py"""
import numpy as np

import faces
from sim import FREE, Sim

G, DT = 360.0, 1 / 30
A, B = faces.clock_bitmap(10, 42, 37), faces.clock_bitmap(10, 43, 5)


def run(s, seconds, gx, gy):
    for _ in range(int(seconds / DT)):
        s.step(DT, gx, gy)


def test_marbles_flip_and_reform():
    s = Sim(128)
    s.load(A)
    n0 = len(s)
    s.release(0, -G)
    run(s, 5, 0, -G)                                  # upside down: everything falls to the top
    assert len(s) == n0, "particles were lost or created while falling"
    cells = s.x.astype(int) + 128 * s.y.astype(int)
    assert len(np.unique(cells)) == n0, "two marbles share a pixel"
    assert s.at_rest(), "heap never came to rest"
    assert s.y.mean() < 30, "heap is not at the top"
    still = s.render().copy()
    run(s, 0.5, 0, -G)
    assert np.array_equal(still, s.render()), "a resting heap is shimmering"
    run(s, 2.5, 0, G)                                 # upright again: falls to the bottom
    s.reform(B, 0, G)
    run(s, 2.5, 0, G)
    assert s.settled(), "reform did not finish"
    assert np.array_equal(s.render().astype(bool), B), "reformed picture is not the target"


def test_fluid_flip_and_reform():
    s = Sim(128, fluid=True)
    s.load(A)
    n0 = len(s)
    s.release(0, -G)
    run(s, 3, 0, -G)
    assert len(s) == n0 and np.all(s.state == FREE)
    assert np.isfinite(s.x).all() and s.x.min() >= 0 and s.x.max() <= 128 and s.y.min() >= 0 and s.y.max() <= 128
    assert s.y.mean() < 40, "liquid did not pool at the top"
    assert np.hypot(s.vx, s.vy).max() < 2000, "fluid blew up"
    s.reform(B, 0, G)
    run(s, 2.5, 0, G)
    assert s.settled() and np.array_equal(s.render().astype(bool), B)


def test_minute_tick_and_morph():
    s = Sim(128)
    s.load(A)
    s.diff_to(B)                                      # changed pixels drain out, new ones drop in
    run(s, 2.5, 0, G)
    assert s.settled() and np.array_equal(s.render().astype(bool), B)
    yy, xx = np.mgrid[:128, :128]
    art = (xx - 64) ** 2 + (yy - 60) ** 2 < 40 ** 2   # far more pixels than the clock: forces splitting
    s.morph_to(art)
    run(s, 2.5, 0, G)
    assert s.settled() and np.array_equal(s.render().astype(bool), art)
    s.morph_to(A)                                     # and back: forces merging
    run(s, 2.5, 0, G)
    assert s.settled() and np.array_equal(s.render().astype(bool), A)


def test_interrupted_reform():
    s = Sim(128)
    s.load(A)
    s.release(0, -G)
    run(s, 3, 0, -G)
    s.reform(B, 0, G)
    run(s, 0.3, 0, G)
    s.release(G, 0)                                   # tipped again mid-flight
    run(s, 4, G, 0)
    cells = s.x.astype(int) + 128 * s.y.astype(int)
    assert len(np.unique(cells)) == len(s) and s.x.mean() > 90, "interrupted reform left a bad state"


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
