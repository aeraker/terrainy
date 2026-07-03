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
import numpy as np
from shapely.geometry import Polygon
from owslib.wcs import WebCoverageService
from owslib.wms import WebMapService
import importlib.metadata
import shapely
import json
import contextlib
import os
import math

# Grid sizing
tile_pixel_length = 1024
tile_pixel_width = 1024

cachedir = os.path.expanduser("~/.cache/terrainy")


# fixme: Intergrate caching

class Connection(object):
    def __init__(self, **kw):
        self.kw = kw

    def _meter_resolution_to_crs_units(self, tif_res_m, lon, lat):
        crs = CRS.from_user_input(self.get_crs())
        if crs.is_geographic:
            m_per_deg_lat = 111320.0
            m_per_deg_lon = 111320.0 * math.cos(math.radians(lat))
            return tif_res_m / m_per_deg_lon, tif_res_m / m_per_deg_lat
        return tif_res_m, tif_res_m

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
        transform = rasterio.transform.Affine.translation(bbox[0], bbox[3]) * rasterio.transform.Affine.scale(xres,
                                                                                                              -yres)
        geometry = [shapely.geometry.shape(shp)
                    for shp, val in
                    rasterio.features.shapes((data_array != empty_data_array).max(axis=0).astype("int16"),
                                             transform=transform)
                    if val > 0]
        if not len(geometry):
            raise ValueError("Map has only empty tiles!")

        return gpd.GeoDataFrame(
            geometry=[gpd.GeoDataFrame(geometry=geometry).geometry.unary_union]
        ).set_crs(self.get_crs())

    @contextlib.contextmanager
    def open_tile(self, *arg, **kw):
        response = self.download_tile(*arg, **kw)
        with MemoryFile(response) as memfile:
            with memfile.open() as dataset:
                yield dataset

    def download(self, gdf, tif_res):
        # Convert data back to crs of map
        gdf = gdf.to_crs(self.get_crs())
        xmin, ymin, xmax, ymax = gdf.total_bounds
        center_lon = (xmin + xmax) / 2
        center_lat = (ymin + ymax) / 2
        tif_res_x, tif_res_y = self._meter_resolution_to_crs_units(
            tif_res, center_lon, center_lat
        )

        tile_extent_x = tile_pixel_width * tif_res_x
        tile_extent_y = tile_pixel_length * tif_res_y

        width = (xmax - xmin) / tif_res_x
        length = (ymax - ymin) / tif_res_y

        nr_cols = int(np.ceil(width / tile_pixel_length))
        nr_rows = int(np.ceil(length / tile_pixel_width))

        array = np.zeros((self.bands, tile_pixel_length * nr_rows, tile_pixel_width * nr_cols), dtype=self.dtype)

        for x_idx in range(nr_cols):
            for y_idx in range(nr_rows):
                print('Working on block %s,%s of %s,%s' % (x_idx + 1, y_idx + 1, nr_cols, nr_rows))

                x = xmin + x_idx * tile_extent_x
                y = ymax - y_idx * tile_extent_y - tile_extent_y

                polygon = (Polygon(
                    [(x, y), (x + tile_extent_x, y), (x + tile_extent_x, y + tile_extent_y), (x, y + tile_extent_y)]))

                with self.open_tile(
                    polygon.bounds, tif_res_x, (tile_pixel_width, tile_pixel_length), resy=tif_res_y
                ) as dataset:
                    data_array = dataset.read()

                    array[:, y_idx * tile_pixel_width:y_idx * tile_pixel_width + tile_pixel_width,
                    x_idx * tile_pixel_length:x_idx * tile_pixel_length + tile_pixel_length] = data_array[:, :, :]

        transform = Affine.translation(xmin, ymax) * Affine.scale(tif_res_x, -tif_res_y)
        return {"array": array, "transform": transform, "data": self.kw, "gdf": gdf}


def connect(connection_settings):
    """
    Connect to a terrainy source
    Args:
        connection_settings: A dictionary or a terrainy source object containing the connection settings for the terrainy source
    Returns:
        A Connection object
    """
    # Convert source object to dictionary if needed
    if hasattr(connection_settings, "to_dict"):
        connection_settings = connection_settings.to_dict()

    # Make sure connection_args is a dictionary
    connection_args = connection_settings.get("connection_args")
    if isinstance(connection_args, str):
        connection_settings["connection_args"] = json.loads(connection_args)

    eps = importlib.metadata.entry_points()
    if hasattr(eps, "select"):
        entries = eps.select(group="terrainy.connection")
    else:
        entries = eps["terrainy.connection"]
    connections = {entry.name: entry.load() for entry in entries}
    if connection_settings["connection_type"] not in connections:
        raise NotImplementedError("Unknown connection type")
    return connections[connection_settings["connection_type"]](**connection_settings)
