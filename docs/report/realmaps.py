"""
Real-imagery comparison for the report: reads a finished job's cleaned EMIT cube
(and Landsat LST) from object storage, runs HydraNet AND the classical methods on
every pixel, writes side-by-side figures and the agreement numbers.

    S3_ENDPOINT_URL=http://localhost:9000 PYTHONPATH=libs/hydra:libs/hs_common \
        python docs/report/realmaps.py <job_id> <name> <out_dir> <model.onnx> <agri_lut.npz>
"""
import json
import sys
import time
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import zarr
from matplotlib.colors import LinearSegmentedColormap, ListedColormap
from matplotlib.patches import Patch

from hs_common import geo, storage
from hs_common.storage import keys
from hydra import maps, spec
from hydra.classical.agri_lut import LUTInverter
from hydra.runtime import HydraRuntime

JOB, NAME, OUT = sys.argv[1], sys.argv[2], Path(sys.argv[3])
OUT.mkdir(parents=True, exist_ok=True)
rt, lut = HydraRuntime(sys.argv[4]), LUTInverter.load(sys.argv[5])
INK2 = "#52514e"
plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 9})
BLUE = LinearSegmentedColormap.from_list("b", geo.RAMPS["blue"])
ORANGE = LinearSegmentedColormap.from_list("o", geo.RAMPS["orange"])
res = {"job_id": JOB, "model": rt.version}


def show(ax, im, title, cmap=None, vr=None, bar=True):
    kw = {} if vr is None else {"vmin": vr[0], "vmax": vr[1]}
    h = ax.imshow(im, cmap=cmap, interpolation="nearest", **kw)
    ax.set_title(title, fontsize=9)
    ax.set_xticks([]), ax.set_yticks([])
    if bar and cmap is not None and not isinstance(cmap, ListedColormap) and im.ndim == 2:
        plt.colorbar(h, ax=ax, fraction=0.046, pad=0.02)


def save(fig, name):
    fig.savefig(OUT / f"real_{NAME}_{name}.png", dpi=170, bbox_inches="tight", facecolor="white")
    plt.close(fig)


def timed(f, *a):
    t = time.perf_counter()
    r = f(*a)
    return r, round(time.perf_counter() - t, 4)


objs = [o["Key"] for o in storage.client().list_objects_v2(Bucket=storage.bucket(), Prefix=keys.silver(JOB, "")).get("Contents", [])]
if any(k.endswith("reflectance.zarr/.zarray") or "reflectance.zarr" in k for k in objs):
    z = zarr.open_array(storage.zarr_store(keys.silver(JOB, "reflectance.zarr")), mode="r")
    cube = np.asarray(z[:], dtype=np.float32)
    wl = np.asarray(z.attrs["wavelengths"], float)
    cube[(cube <= 0).all(axis=-1)] = np.nan
    ck = keys.silver(JOB, "cloud.npy")
    masks = maps.scene_masks(cube, wl, storage.get_numpy(ck) if storage.exists(ck) else None)
    veg, bare = masks == 2, masks == 3
    e = res["emit"] = {"shape": list(cube.shape),
                       "fractions": {n: round(float((masks == k).mean()), 4) for k, n in
                                     enumerate(["no data / cloud", "water", "vegetation", "bare"])}}

    def band(nm):
        return cube[..., int(np.argmin(np.abs(wl - nm)))]
    rgb = np.dstack([band(650), band(560), band(470)])
    rgb = np.clip(rgb / np.nanpercentile(rgb, 98), 0, 1)
    rgb[~np.isfinite(rgb)] = 1
    lc = ListedColormap(["#ffffff", geo.SLOT["blue"], geo.SLOT["aqua"], geo.SLOT["orange"]])
    fig, axs = plt.subplots(1, 2, figsize=(9, 4.3))
    show(axs[0], rgb, "EMIT true colour (650/560/470 nm)")
    show(axs[1], masks, "Land cover routing", lc, (0, 3))
    axs[1].legend(handles=[Patch(color=c, label=l) for c, l in zip(lc.colors, ["no data", "water", "vegetation", "bare"])],
                  fontsize=7, loc="lower center", bbox_to_anchor=(0.5, -0.2), ncol=4, frameon=False)
    save(fig, "rgb")

    if veg.sum() > 50:
        for _ in range(2):                                    # warm-up: steady-state timings
            maps.hydra_agri(rt, cube, wl, veg), maps.classical_agri(lut, cube, wl, veg)
        nn, t_nn = timed(maps.hydra_agri, rt, cube, wl, veg)
        cl, t_cl = timed(maps.classical_agri, lut, cube, wl, veg)
        ag = maps.agreement(nn, cl)
        e["agri"] = {p: {k: ag[f"agri_{p}"][k] for k in ("nmae", "r2", "mae")} for p in spec.AGRI_PARAMS}
        e["agri_timing_s"] = {"hydra": t_nn, "classical": t_cl, "pixels": int(veg.sum())}
        for p, rng in (("LAI", (0, 6)), ("Cab", (0, 80))):
            a, b = nn[f"agri_{p}"], cl[f"agri_{p}"]
            fig, axs = plt.subplots(1, 3, figsize=(13, 4))
            show(axs[0], b, f"{p}: classical (PROSAIL LUT, k=20)", BLUE, rng)
            show(axs[1], a, f"{p}: HydraNet {rt.version}", BLUE, rng)
            show(axs[2], np.abs(a - b), "absolute difference", ORANGE, (0, (rng[1] - rng[0]) * 0.15))
            save(fig, f"agri_{p}")
        fig, axs = plt.subplots(1, 3, figsize=(12, 3.8))
        for ax, p in zip(axs, ("Cab", "Cw", "LAI")):
            a, b = nn[f"agri_{p}"][veg], cl[f"agri_{p}"][veg]
            ok = np.isfinite(a) & np.isfinite(b)
            ax.hexbin(b[ok], a[ok], gridsize=45, cmap=BLUE, mincnt=1, bins="log")
            lo, hi = np.percentile(b[ok], [0.5, 99.5])
            ax.plot([lo, hi], [lo, hi], color=INK2, lw=1, ls="--")
            ax.set_xlabel(f"classical {p}"), ax.set_ylabel(f"HydraNet {p}")
            ax.set_title(f"{p}: error {e['agri'][p]['nmae'] * 100:.1f} % of range", fontsize=9)
            ax.spines[["top", "right"]].set_visible(False)
        fig.tight_layout()
        save(fig, "agri_scatter")

    if bare.sum() > 50:
        for _ in range(2):
            maps.hydra_mineral(rt, cube, wl, bare), maps.classical_mineral(cube, wl, bare)
        nn, t_nn = timed(maps.hydra_mineral, rt, cube, wl, bare)
        cl, t_cl = timed(maps.classical_mineral, cube, wl, bare)
        ag = maps.agreement(nn, cl)
        e["mineral"] = {"class_agreement": ag["mineral_class"]["pixel_agreement"],
                        "abundance_mae": ag["mineral_abundance"]["mae"], "quality_mae": ag["mineral_quality"]["mae"],
                        "pixels": int(bare.sum()), "timing_s": {"hydra": t_nn, "classical": t_cl},
                        "classes": {spec.MINERAL_CLASSES[k]: int((cl["mineral_class"][bare] == k).sum())
                                    for k in np.unique(cl["mineral_class"][bare]) if k < len(spec.MINERAL_CLASSES)}}
        pal = ListedColormap(["#ffffff"] + geo.mineral_palette(spec.MINERAL_CLASSES))

        def cls(c):
            o = np.zeros(masks.shape)
            o[bare] = c[bare] + 1
            return o
        dis = np.where(bare, (nn["mineral_class"] != cl["mineral_class"]).astype(float), np.nan)
        fig, axs = plt.subplots(1, 3, figsize=(13, 4))
        show(axs[0], cls(cl["mineral_class"]), "Dominant mineral: classical (SAM)", pal, (0, 13))
        show(axs[1], cls(nn["mineral_class"]), f"Dominant mineral: HydraNet {rt.version}", pal, (0, 13))
        axs[2].imshow(dis, cmap=ListedColormap(["#d9d8d2", "#e34948"]), vmin=0, vmax=1, interpolation="nearest")
        axs[2].set_title(f"Disagreement (red): {(1 - e['mineral']['class_agreement']) * 100:.2f} % of bare pixels", fontsize=9)
        axs[2].set_xticks([]), axs[2].set_yticks([])
        save(fig, "mineral_class")
        top = int(np.nanargmax(np.nanmean(cl["mineral_abundance"][bare], axis=0)))       # most abundant per SMA
        a, b = nn["mineral_abundance"][..., top], cl["mineral_abundance"][..., top]
        a, b = np.where(bare, a, np.nan), np.where(bare, b, np.nan)
        fig, axs = plt.subplots(1, 3, figsize=(13, 4))
        show(axs[0], b, f"{spec.MINERALS[top]} abundance: classical (SMA)", BLUE, (0, 1))
        show(axs[1], a, f"{spec.MINERALS[top]} abundance: HydraNet {rt.version}", BLUE, (0, 1))
        show(axs[2], np.abs(a - b), "absolute difference", ORANGE, (0, 0.1))
        save(fig, "mineral_abundance")
        e["mineral"]["abundance_figure_mineral"] = spec.MINERALS[top]

lst_key = next((k for k in objs if k.endswith("thermal/lst.tif")), None)
if lst_key:
    lst = np.asarray(geo.read_cog(storage.get_bytes(lst_key))[0], float)
    nn, t_nn = timed(maps.hydra_thermal, rt, lst)
    cl, t_cl = timed(maps.classical_thermal, lst)
    ag = maps.agreement(nn, cl)
    res["thermal"] = {k: ag[k]["pixel_agreement"] for k in ("thermal_global_class", "thermal_uhi_class",
                                                            "thermal_local_anomaly", "thermal_hotspots")}
    res["thermal"]["uhi_intensity_c"] = {"hydra": nn["uhi_intensity_c"], "classical": cl["uhi_intensity_c"]}
    lo, hi = np.nanpercentile(lst, [2, 98])
    div = ListedColormap(geo.THERMAL_UHI_COLORS)
    fig, axs = plt.subplots(1, 3, figsize=(13, 4))
    show(axs[0], lst, "Landsat land surface temperature (°C)", ORANGE, (lo, hi))
    for ax, mm, t in ((axs[1], cl, "Heat zones: classical rules"), (axs[2], nn, f"Heat zones: HydraNet {rt.version}")):
        u = np.where(np.isfinite(lst), mm["thermal_uhi_class"].astype(float), np.nan)
        u[u > 2] = np.nan
        ax.imshow(u, cmap=div, vmin=0, vmax=2, interpolation="nearest")
        ax.set_title(t, fontsize=9), ax.set_xticks([]), ax.set_yticks([])
    axs[2].legend(handles=[Patch(color=c, label=l) for c, l in zip(geo.THERMAL_UHI_COLORS, spec.THERMAL_UHI_CLASSES)],
                  fontsize=7, loc="lower center", bbox_to_anchor=(0.5, -0.18), ncol=3, frameon=False)
    save(fig, "thermal_uhi")
    fig, axs = plt.subplots(1, 2, figsize=(9, 4))
    for ax, mm, t in ((axs[0], cl, "Hotspots: classical (p95, ≥ 10 px)"), (axs[1], nn, f"Hotspots: HydraNet {rt.version}")):
        ax.imshow(lst, cmap="Greys", vmin=lo, vmax=hi, interpolation="nearest")
        ax.imshow(np.where(mm["thermal_hotspots"] > 0, 1.0, np.nan), cmap=ListedColormap(["#e34948"]), interpolation="nearest")
        ax.set_title(t, fontsize=9), ax.set_xticks([]), ax.set_yticks([])
    save(fig, "thermal_hotspots")

json.dump(res, open(OUT / f"real_{NAME}.json", "w"), indent=1, default=float)
print(json.dumps(res, default=float)[:1500])
