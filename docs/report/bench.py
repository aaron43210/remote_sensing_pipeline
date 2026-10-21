"""
Speed of HydraNet vs the classical methods on the same scenes (one CPU).

    PYTHONPATH=libs/hydra python docs/report/bench.py <model.onnx> <agri_lut.npz> <out.json>
"""
import json
import sys
import time

from hydra import maps, synthetic
from hydra.classical.agri_lut import LUTInverter
from hydra.classical.mineral import MineralTeacher
from hydra.runtime import HydraRuntime

rt, lut, out = HydraRuntime(sys.argv[1]), LUTInverter.load(sys.argv[2]), sys.argv[3]
total = {k: [0.0, 0.0, 0] for k in ("mineral", "agri", "thermal")}   # hydra s, classical s, pixels
core = {k: [0.0, 0.0] for k in ("mineral", "agri")}                  # model vs method only


def timed(f, *a):
    t = time.perf_counter()
    r = f(*a)
    return r, time.perf_counter() - t


cube, wl, *_ = synthetic.scene(0)                                     # warm-up (numba JIT, ONNX session)
m = maps.scene_masks(cube, wl)
maps.hydra_mineral(rt, cube, wl, m == 3), maps.classical_mineral(cube, wl, m == 3)
for i in range(5):
    cube, wl, *_ = synthetic.scene(2000 + i, shape=(160, 160))
    m = maps.scene_masks(cube, wl)
    x, refl, _ = maps.mineral_inputs(cube, wl, m == 3)
    core["mineral"][0] += timed(rt.predict, "mineral", x)[1]
    core["mineral"][1] += timed(MineralTeacher(maps.mineral_features()).label, refl, x)[1]
    total["mineral"][0] += timed(maps.hydra_mineral, rt, cube, wl, m == 3)[1]
    total["mineral"][1] += timed(maps.classical_mineral, cube, wl, m == 3)[1]
    total["mineral"][2] += int((m == 3).sum())
    xa, _ = maps.agri_inputs(cube, wl, m == 2)
    core["agri"][0] += timed(rt.predict, "agri", xa)[1]
    core["agri"][1] += timed(lut.invert, xa)[1]
    total["agri"][0] += timed(maps.hydra_agri, rt, cube, wl, m == 2)[1]
    total["agri"][1] += timed(maps.classical_agri, lut, cube, wl, m == 2)[1]
    total["agri"][2] += int((m == 2).sum())
    lst, *_ = synthetic.lst_scene(3000 + i, shape=(512, 512))
    total["thermal"][0] += timed(maps.hydra_thermal, rt, lst)[1]
    total["thermal"][1] += timed(maps.classical_thermal, lst)[1]
    total["thermal"][2] += lst.size
json.dump({"T": total, "M": core}, open(out, "w"), indent=1)
for k, (a, b, n) in total.items():
    print(f"{k:8s} {n:8d} px  hydra {a:6.2f}s  classical {b:6.2f}s  ({b / a:.1f}x)")
