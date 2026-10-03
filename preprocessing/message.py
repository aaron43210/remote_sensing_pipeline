# =============================================================
# OWNER: ANANTAHANARAYANAN
# =============================================================
"""
The Kafka contract: what comes in, what goes out, and how it is encoded.

IN  ('raw-data', from ingestion) -- ingestion's existing message, unchanged:
    scene_id, zarr_path (inside bucket raw-scenes), wavelengths, satellite,
    bbox, timestamp

OUT ('preprocessed-multiband', to ml_inference):
    data_path   preprocessed-data/<data_path> = multiband.npy (rows, cols, 10)
    zarr_path   preprocessed-data/<zarr_path> = same data, chunked, for Dask/Ray
    plus band_names, wavelengths, shape, quality -- see build()
"""

from datetime import datetime, timezone

import bands
import config


def decode(raw):
    """
    Kafka bytes -> dict. Accepts msgpack (Python services) or JSON (Go).

    The two producers on this pipeline disagree on encoding, so be tolerant.
    JSON's leading '{' is a valid 1-byte msgpack value with bytes left over,
    so msgpack raises and we fall through to JSON.
    """
    import msgpack
    try:
        return msgpack.unpackb(raw, raw=False)
    except Exception:
        import json
        return json.loads(raw.decode("utf-8"))


def encode(msg):
    """msgpack, because ml_inference decodes with msgpack."""
    import msgpack
    return msgpack.packb(msg, use_bin_type=True)


def build(source, info, shape, data_path):
    """Assemble the outgoing message. Primitives only -- it is serialised."""
    n = len(bands.TARGET_BANDS_NM)
    # The network asserts 10 inputs; fail here, not inside someone else's code.
    assert shape[2] == len(info["wavelengths"]) == n, (
        f"Contract violated: shape {shape}, {len(info['wavelengths'])} "
        f"wavelengths, {n} targets")

    scene_id = source["scene_id"]
    return {
        "scene_id": scene_id,
        "satellite": source.get("satellite"),
        "bbox": source.get("bbox"),
        "tasks": source.get("tasks", ["agriculture", "mineral", "thermal"]),
        "data_path": data_path,
        "zarr_path": f"{scene_id}/preprocessed.zarr",
        "bucket": config.BUCKET_OUT,
        "band_names": bands.column_names(),
        "target_nm": list(bands.TARGET_BANDS_NM),
        "wavelengths": [float(w) for w in info["wavelengths"]],
        "n_bands": n,
        "shape": [int(s) for s in shape],
        "interpolated_bands": info["interpolated_bands"],
        "dark_subtraction": bool(info["dark_subtraction"]),
        "quality": info["quality"],
        "timestamp": source.get("timestamp"),
        "processed_at": datetime.now(timezone.utc).isoformat(),
    }
