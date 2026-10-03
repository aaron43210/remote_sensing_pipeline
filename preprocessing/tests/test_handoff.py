# =============================================================
# OWNER: ANANTAHANARAYANAN
# =============================================================
"""
The array -> dataframe step in front of ray.data.from_dask().

If pixel order slipped here, every prediction would land on the wrong spot
of the map, and nothing would crash to say so.
"""

import dask.array as da
import numpy as np
import pytest

import handoff

pytest.importorskip("dask.dataframe")


def test_one_row_per_pixel_in_row_major_order():
    rows, cols = 7, 5
    image = np.arange(rows * cols * 10, dtype="f4").reshape(rows, cols, 10)
    lazy = da.from_array(image, chunks=(3, 2, 10))          # awkward chunks

    df = handoff.to_dask_dataframe(lazy).compute()

    assert list(df.columns)[:2] == ["b450", "b680"] and len(df) == rows * cols
    r, c = 4, 3
    np.testing.assert_array_equal(df.iloc[r * cols + c].values, image[r, c])


def test_predictions_go_back_to_the_right_pixels():
    rows, cols = 6, 4
    per_pixel = np.arange(rows * cols * 2).reshape(rows * cols, 2)
    image = handoff.to_image(per_pixel, (rows, cols, 10))

    assert image.shape == (rows, cols, 2)
    np.testing.assert_array_equal(image[2, 3], per_pixel[2 * cols + 3])


def test_band_count_must_match_names():
    with pytest.raises(ValueError, match="column names"):
        handoff.to_dask_dataframe(da.zeros((2, 2, 3)))
