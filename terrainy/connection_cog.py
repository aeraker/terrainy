from . import connection
import math
import os
import logging

import geopandas as gpd
import numpy as np
import rasterio
import shapely.geometry
from rasterio.transform import Affine
from rasterio.warp import Resampling, reproject

logger = logging.getLogger(__name__)


def format_lat_tile(south_edge):
    if south_edge >= 0:
        return "N%02d_00" % south_edge
    return "S%02d_00" % abs(south_edge)


def format_lon_tile(west_edge):
    if west_edge >= 0:
        return "E%03d_00" % west_edge
    return "W%03d_00" % abs(west_edge)


def iter_southwest_corners(minx, miny, maxx, maxy):
    if maxx <= minx or maxy <= miny:
        raise ValueError(
            "Invalid bounds: minx=%s, miny=%s, maxx=%s, maxy=%s"
            % (minx, miny, maxx, maxy)
        )
    for south_edge in range(int(math.floor(miny)), int(math.floor(maxy)) + 1):
        for west_edge in range(int(math.floor(minx)), int(math.floor(maxx)) + 1):
            yield south_edge, west_edge


class CogConnection(connection.Connection):
    """Download elevation from a 1°-tiled Cloud Optimized GeoTIFF URL template.

    connection_args:
      url_template: str with ``{lat}`` and ``{lon}`` placeholders
        (e.g. Copernicus GLO-30 naming: N59_00 / E010_00)
      tile_size_deg: float, default 1.0
      nodata: float, default -32767.0
    """

    bands = 1
    dtype = "float32"

    def __init__(self, **kw):
        connection.Connection.__init__(self, **kw)
        args = self.kw.get("connection_args") or {}
        if isinstance(args, str):
            import json
            args = json.loads(args)
            self.kw["connection_args"] = args
        self.url_template = args["url_template"]
        self.tile_size_deg = float(args.get("tile_size_deg", 1.0))
        self.nodata = float(args.get("nodata", -32767.0))
        os.environ.setdefault("AWS_NO_SIGN_REQUEST", "YES")

    def get_shape(self):
        return gpd.GeoDataFrame(
            geometry=[shapely.geometry.box(*self.get_bounds())],
            crs=self.get_crs(),
        )

    def get_bounds(self):
        return (-180.0, -90.0, 180.0, 90.0)

    def get_crs(self):
        crs = self.kw.get("crs_orig") or "EPSG:4326"
        if isinstance(crs, str) and crs.upper().startswith("EPSG:"):
            return int(crs.split(":")[1])
        return crs

    def tile_url(self, south_edge, west_edge):
        return self.url_template.format(
            lat=format_lat_tile(south_edge),
            lon=format_lon_tile(west_edge),
        )

    def urls_for_bounds(self, minx, miny, maxx, maxy):
        return [
            self.tile_url(south_edge, west_edge)
            for south_edge, west_edge in iter_southwest_corners(minx, miny, maxx, maxy)
        ]

    def download(self, gdf, tif_res):
        gdf = gdf.to_crs(self.get_crs())
        xmin, ymin, xmax, ymax = gdf.total_bounds
        center_lon = (xmin + xmax) / 2.0
        center_lat = (ymin + ymax) / 2.0
        tif_res_x, tif_res_y = self._meter_resolution_to_crs_units(
            tif_res, center_lon, center_lat
        )

        width = max(1, int(math.ceil((xmax - xmin) / tif_res_x)))
        height = max(1, int(math.ceil((ymax - ymin) / tif_res_y)))
        transform = Affine.translation(xmin, ymax) * Affine.scale(tif_res_x, -tif_res_y)

        array = np.full((self.bands, height, width), self.nodata, dtype=self.dtype)
        urls = self.urls_for_bounds(xmin, ymin, xmax, ymax)
        opened_any = False

        for url in urls:
            try:
                with rasterio.open(url) as src:
                    opened_any = True
                    src_nodata = (
                        src.nodata if src.nodata is not None else self.nodata
                    )
                    tile_data = np.full(
                        (height, width), self.nodata, dtype=self.dtype
                    )
                    reproject(
                        source=rasterio.band(src, 1),
                        destination=tile_data,
                        src_transform=src.transform,
                        src_crs=src.crs,
                        dst_transform=transform,
                        dst_crs=self.get_crs(),
                        resampling=Resampling.bilinear,
                        src_nodata=src_nodata,
                        dst_nodata=self.nodata,
                    )
                    valid = (tile_data != self.nodata) & (array[0] == self.nodata)
                    array[0, valid] = tile_data[valid]
            except rasterio.errors.RasterioIOError as e:
                logger.info("CogConnection: skipping unavailable tile %s (%s)", url, e)

        if not opened_any:
            raise RuntimeError("No COG tiles found for requested area")

        data = dict(self.kw)
        data["crs_orig"] = self.get_crs()
        return {"array": array, "transform": transform, "data": data, "gdf": gdf}

    def download_tile(self, bounds, tif_res, size, resy=None):
        raise NotImplementedError("Use download()")
