# =============================================================
# OWNER: AARON
# =============================================================
"""
Chunked hyperspectral processing to avoid OOM.

Uses dask for lazy loading of Zarr arrays.
Only loads into memory when explicitly computed.
"""

import numpy as np
import logging

logger = logging.getLogger(__name__)


def load_zarr_lazy(zarr_path):
    """
    Load Zarr array as dask array (lazy, no memory).

    Returns:
        dask_array: lazy array, not loaded into memory
        wavelengths: list of wavelengths from metadata
    """
    import zarr
    import dask.array as da

    z = zarr.open(zarr_path, mode='r')
    dask_arr = da.from_zarr(z)

    wavelengths = z.attrs.get('wavelengths', [])

    logger.info(
        f"Lazy Zarr loaded: shape={dask_arr.shape}, "
        f"chunks={dask_arr.chunks}, "
        f"size={dask_arr.nbytes / (1024**2):.1f}MB"
    )

    return dask_arr, wavelengths


def process_chunked(zarr_path, process_fn, output_path, chunk_size=256):
    """
    Process a hyperspectral scene chunk-by-chunk.

    Args:
        zarr_path: input Zarr path
        process_fn: function(chunk, wavelengths) -> result_chunk
        output_path: output Zarr path
        chunk_size: spatial tile size
    """
    import zarr

    dask_arr, wavelengths = load_zarr_lazy(zarr_path)
    rows, cols, bands = dask_arr.shape

    # Create output Zarr
    out = zarr.open(
        output_path, mode='w',
        shape=(rows, cols),
        chunks=(chunk_size, chunk_size),
        dtype='float32',
        compressor=zarr.Blosc(cname='zstd', clevel=3)
    )

    # Process in tiles
    for i in range(0, rows, chunk_size):
        for j in range(0, cols, chunk_size):
            i_end = min(i + chunk_size, rows)
            j_end = min(j + chunk_size, cols)

            # Load only this tile into memory
            tile = dask_arr[i:i_end, j:j_end, :].compute()

            # Process tile
            result = process_fn(tile, wavelengths)

            # Write result
            out[i:i_end, j:j_end] = result

    logger.info(f"Chunked processing complete: {output_path}")
    return output_path
