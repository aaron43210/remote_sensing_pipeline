"""Regenerate the georeferenced ENVI BSQ (good bands) from the existing GeoTIFF
cube, then verify it reads back with wavelengths + CRS.  No zarr access needed.
"""
import config  # sets PROJ_DATA before rasterio loads
import raster_io
import zarr_to_tif as z


def main():
    ref = raster_io.read_raster(config.INPUT_RASTER)
    if ref.wavelengths is None or ref.good_quality_mask is None:
        raise SystemExit("cube is missing wavelengths/good-quality metadata (band_metadata.json)")

    bsq, n = z.write_bsq(
        ref.data, ref.wavelengths, ref.good_quality_mask,
        good_only=True, transform=ref.transform, crs=ref.crs,
    )
    print(f"wrote {bsq.name} + .hdr  ({n} good bands)")

    chk = raster_io.read_raster(bsq)
    wl = chk.wavelengths
    print(f"verify: bands={chk.count} crs={chk.crs} units={chk.wavelength_units}")
    print(f"verify: wavelengths {'OK' if wl is not None else 'MISSING'}"
          + (f" {wl.min():.1f}-{wl.max():.1f} nm" if wl is not None else ""))


if __name__ == "__main__":
    main()
