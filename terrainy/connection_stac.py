from . import connection
import json
import logging
import math
import os

import geopandas as gpd
import numpy as np
import rasterio
import requests
import shapely.geometry
import shapely.ops
from rasterio.transform import Affine
from rasterio.warp import Resampling, reproject

logger = logging.getLogger(__name__)


def pick_asset_href(assets, asset_keys):
    """Return the first GeoTIFF href matching preferred asset keys, else any .tif."""
    for key in asset_keys:
        asset = assets.get(key)
        if asset and asset.get("href", "").lower().endswith((".tif", ".tiff")):
            return asset["href"]
    for asset in assets.values():
        if asset.get("href", "").lower().endswith((".tif", ".tiff")):
            return asset["href"]
    return None


def search_stac_items(stac_url, collections, bbox, geometry=None, limit=100, timeout=60):
    """Search a STAC API; try POST intersects, then POST bbox, then GET bbox."""
    items = []
    try:
        payload = {
            "collections": collections,
            "limit": limit,
        }
        if geometry is not None:
            payload["intersects"] = geometry
        else:
            payload["bbox"] = list(bbox)
        response = requests.post(stac_url, json=payload, timeout=timeout)
        response.raise_for_status()
        items = response.json().get("features", [])
    except Exception as exc:
        logger.info("StacConnection: intersects search failed (%s); trying bbox POST", exc)
        try:
            payload = {
                "collections": collections,
                "bbox": list(bbox),
                "limit": limit,
            }
            response = requests.post(stac_url, json=payload, timeout=timeout)
            response.raise_for_status()
            items = response.json().get("features", [])
        except Exception as exc2:
            logger.info("StacConnection: bbox POST failed (%s); trying GET", exc2)
            params = {
                "collections": ",".join(collections),
                "bbox": ",".join(str(v) for v in bbox),
                "limit": limit,
            }
            response = requests.get(stac_url, params=params, timeout=timeout)
            response.raise_for_status()
            items = response.json().get("features", [])
    return items


class StacConnection(connection.Connection):
    """Download elevation by searching a STAC API and mosaicking COG assets.

    connection_args:
      stac_url: STAC search endpoint
      collections: list of collection ids (preference order)
      asset_keys: preferred asset keys (e.g. ["dtm", "data", ...])
      nodata: float, default -32767.0
      limit: max STAC items, default 100
      resolution_threshold_m: if tif_res exceeds this, reverse collections order
        (useful when a coarser mosaic is preferred for low-res requests)
    """

    bands = 1
    dtype = "float32"

    def __init__(self, **kw):
        connection.Connection.__init__(self, **kw)
        args = self.kw.get("connection_args") or {}
        if isinstance(args, str):
            args = json.loads(args)
            self.kw["connection_args"] = args
        self.stac_url = args["stac_url"]
        self.collections = list(args["collections"])
        self.asset_keys = list(
            args.get("asset_keys", ["data", "elevation", "geotiff", "cog", "dtm", "dsm"])
        )
        self.nodata = float(args.get("nodata", -32767.0))
        self.limit = int(args.get("limit", 100))
        self.resolution_threshold_m = args.get("resolution_threshold_m")
        if self.resolution_threshold_m is not None:
            self.resolution_threshold_m = float(self.resolution_threshold_m)
        os.environ.setdefault("AWS_NO_SIGN_REQUEST", "YES")

    def get_shape(self):
        return gpd.GeoDataFrame(
            geometry=[shapely.geometry.box(*self.get_bounds())],
            crs=self.get_crs(),
        )

    def get_bounds(self):
        # Prefer catalog geometry if present; else world.
        geom = self.kw.get("geometry")
        if geom is not None and hasattr(geom, "bounds"):
            return tuple(geom.bounds)
        return (-180.0, -90.0, 180.0, 90.0)

    def get_crs(self):
        crs = self.kw.get("crs_orig") or "EPSG:4326"
        if isinstance(crs, str) and crs.upper().startswith("EPSG:"):
            return int(crs.split(":")[1])
        return crs

    def collections_for_resolution(self, tif_res):
        collections = list(self.collections)
        if (
            self.resolution_threshold_m is not None
            and tif_res > self.resolution_threshold_m
        ):
            collections = list(reversed(collections))
        return collections

    def asset_urls_for_gdf(self, gdf, tif_res):
        gdf_wgs84 = gdf.to_crs(epsg=4326)
        minx, miny, maxx, maxy = gdf_wgs84.total_bounds
        geometry = shapely.geometry.mapping(
            shapely.ops.unary_union(list(gdf_wgs84.geometry))
        )
        collections = self.collections_for_resolution(tif_res)
        items = search_stac_items(
            self.stac_url,
            collections,
            bbox=(minx, miny, maxx, maxy),
            geometry=geometry,
            limit=self.limit,
        )
        logger.info("StacConnection: found %s STAC items", len(items))
        urls = []
        for item in items:
            href = pick_asset_href(item.get("assets", {}), self.asset_keys)
            if href:
                urls.append(href)
        return urls

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

        urls = self.asset_urls_for_gdf(gdf, tif_res)
        if not urls:
            raise RuntimeError("No STAC assets found for requested area")

        array = np.full((self.bands, height, width), self.nodata, dtype=self.dtype)
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
                logger.info("StacConnection: skipping unavailable asset %s (%s)", url, e)

        if not opened_any:
            raise RuntimeError("No STAC COG assets could be opened for requested area")

        data = dict(self.kw)
        data["crs_orig"] = self.get_crs()
        return {"array": array, "transform": transform, "data": data, "gdf": gdf}

    def download_tile(self, bounds, tif_res, size, resy=None):
        raise NotImplementedError("Use download()")
