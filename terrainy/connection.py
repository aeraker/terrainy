import rasterio
import rasterio.mask
from rasterio.transform import Affine
import rasterio
from rasterio import MemoryFile
from rasterio.plot import show
import rasterio.mask
from rasterio.transform import Affine
import rasterio.rio.clip
from rasterio.crs import CRS
import geopandas as gpd
import time
import numpy as np
from shapely.geometry import Polygon
from owslib.wcs import WebCoverageService
from owslib.wms import WebMapService
import pkg_resources
import importlib.metadata
import shapely
import json
import contextlib
import os.path
import logging
import math

logger = logging.getLogger(__name__)

# Grid sizing
tile_pixel_length = 1024
tile_pixel_width = 1024

cachedir = os.path.expanduser("~/.cache/terrainy")

class Connection(object):
    def __init__(self, cache_tiles=True, **kw):
        self.kw = kw
        self.cache_tiles = cache_tiles

    def get_shape(self):
        bbox = self.get_bounds()
        empty_bbox = list(bbox)
        w = empty_bbox[2] - empty_bbox[0]
        empty_bbox[0] -= w
        empty_bbox[2] -= w
        with self.open_tile(empty_bbox,
                            (empty_bbox[2] - empty_bbox[0]) / tile_pixel_width,
                            (tile_pixel_width, tile_pixel_length)) as dataset:
            empty_data_array = dataset.read()
        
        with self.open_tile(bbox,
                            (bbox[2] - bbox[0]) / tile_pixel_width,
                            (tile_pixel_width, tile_pixel_length)) as dataset:
            data_array = dataset.read()

        xres = (bbox[2] - bbox[0]) / tile_pixel_width
        yres = (bbox[3] - bbox[1]) / tile_pixel_length
        transform = rasterio.transform.Affine.translation(bbox[0], bbox[3]) * rasterio.transform.Affine.scale(xres, -yres)            
        geometry = [shapely.geometry.shape(shp)
                    for shp, val in rasterio.features.shapes((data_array != empty_data_array).max(axis=0).astype("int16"), transform=transform)
                    if val > 0]
        if not len(geometry):
            raise ValueError("Map has only empty tiles!")
        
        return gpd.GeoDataFrame(
            geometry = [gpd.GeoDataFrame(geometry = geometry).geometry.unary_union]
        ).set_crs(self.get_crs())

    @contextlib.contextmanager
    def get_tile_file(self, bounds, tif_res, size = (tile_pixel_width, tile_pixel_length)):
        cachepath = os.path.join(cachedir, self.kw["title"], str(tif_res), "%s.tif" % (",".join(str(b) for b in bounds),))
        if not os.path.exists(cachepath):
            logger.info("Not cached: %s" % (cachepath,))

            cachedirpath = os.path.split(cachepath)[0]
            if not os.path.exists(cachedirpath):
                os.makedirs(cachedirpath)

            response = self.download_tile(bounds, tif_res, size)
            with open(cachepath, "wb") as f:
                f.write(response.read())
        else:
            logger.info("Cached: %s" % (cachepath,))
        yield cachepath

    @contextlib.contextmanager
    def open_tile(self, bounds, tif_res, size = (tile_pixel_width, tile_pixel_length)):
        if self.cache_tiles:
            with self.get_tile_file(bounds, tif_res, size = size) as cachepath:
                with rasterio.open(cachepath) as dataset:
                    yield dataset
        else:
            response = self.download_tile(bounds, tif_res, size)
            with MemoryFile(response) as memfile:
                with memfile.open() as dataset:
                    yield dataset

    def get_tileset_bounds(self, gdf, tif_res):
        gdf = gdf.to_crs(self.get_crs())
        xmin, ymin, xmax, ymax = gdf.total_bounds

        tile_m_length = tile_pixel_length * tif_res
        tile_m_width = tile_pixel_width * tif_res

        xmin = math.floor(xmin / tile_m_width) * tile_m_width
        xmax = math.ceil(xmax / tile_m_width) * tile_m_width
        ymin = math.floor(ymin / tile_m_length) * tile_m_length
        ymax = math.ceil(ymax / tile_m_length) * tile_m_length

        return xmin, ymin, xmax, ymax
        
    def get_tile_bounds(self, gdf, tif_res):
        # Convert data back to crs of map
        gdf = gdf.to_crs(self.get_crs())
        xmin, ymin, xmax, ymax = self.get_tileset_bounds(gdf, tif_res)
        
        tile_m_length = tile_pixel_length * tif_res
        tile_m_width = tile_pixel_width * tif_res

        width = (xmax - xmin) / tif_res
        length = (ymax - ymin) / tif_res

        nr_cols = int(np.ceil(width / tile_pixel_length))
        nr_rows = int(np.ceil(length / tile_pixel_width))

        array = np.zeros((self.bands, tile_pixel_length * nr_rows, tile_pixel_width * nr_cols), dtype=self.dtype)

        for x_idx in range(nr_cols):
            for y_idx in range(nr_rows):
                x = xmin + x_idx * tile_m_width
                y = ymax - y_idx * tile_m_length - tile_m_length

                polygon = (Polygon(
                    [(x, y), (x + tile_m_width, y), (x + tile_m_width, y + tile_m_length), (x, y + tile_m_length)]))

                yield x_idx, y_idx, polygon

                    
    def download(self, gdf, tif_res):
        gdf = gdf.to_crs(self.get_crs())
        
        # Convert data back to crs of map
        xmin, ymin, xmax, ymax = self.get_tileset_bounds(gdf, tif_res)

        for x_idx, y_idx, polygon in self.get_tile_bounds(gdf, tif_res):
            logger.info('Working on block %s,%s of %s,%s' % (x_idx + 1, y_idx + 1, nr_cols, nr_rows))
            with self.open_tile(polygon.bounds, tif_res, (tile_pixel_width, tile_pixel_length)) as dataset:
                data_array = dataset.read()

                array[:, y_idx * tile_pixel_width:y_idx * tile_pixel_width + tile_pixel_width,
                      x_idx * tile_pixel_length:x_idx * tile_pixel_length + tile_pixel_length] = data_array[:, :, :]
                
        transform = Affine.translation(xmin, ymax) * Affine.scale(tif_res, -tif_res)
        return {"array":array, "transform":transform, "data":self.kw, "gdf":gdf}


def connect(**data):
    connections = {entry.name: entry.load()
               for entry in importlib.metadata.entry_points()['terrainy.connection']}
    if data["connection_type"] not in connections:
        raise NotImplementedError("Unknown connection type")
    return connections[data["connection_type"]](**data)
