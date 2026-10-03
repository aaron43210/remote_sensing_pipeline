# =============================================================
# OWNER: ANANTAHANARAYANAN
# =============================================================
"""
Handing the 10 bands to Ray, for the inference side.

ray.data.from_dask() takes a Dask DATAFRAME. Preprocessing produces a 3-D
ARRAY (rows, cols, 10). This is the one conversion between them:

    import dask.array as da, ray
    from handoff import to_dask_dataframe, to_image

    ten = da.from_zarr(store)                        # preprocessed.zarr
    ds = ray.data.from_dask(to_dask_dataframe(ten))  # 1 row per pixel
    ...predict...
    image = to_image(predictions, ten.shape)         # back to a map

Rows are pixels in row-major order: pixel (r, c) is row r * cols + c.
Columns are named after the target bands ('b450' ... 'b2350').

Needs dask[dataframe] (pandas) -- the inference image has it; the
preprocessing image does not, so the import is local.
"""

import numpy as np

import bands


def to_dask_dataframe(ten_band, names=None):
    """(rows, cols, n) dask array -> dask dataframe, one row per pixel."""
    import dask.dataframe as dd

    names = list(names or bands.column_names())
    rows, cols, n = ten_band.shape
    if n != len(names):
        raise ValueError(f"{n} bands but {len(names)} column names")

    # reshape re-chunks so that whole image rows stay together, which keeps
    # the row-major pixel order.
    return dd.from_dask_array(ten_band.reshape(rows * cols, n), columns=names)


def to_image(per_pixel, shape):
    """Per-pixel predictions back to a map: (rows * cols, k) -> (rows, cols, k)."""
    rows, cols = shape[0], shape[1]
    return np.asarray(per_pixel).reshape(rows, cols, -1)
