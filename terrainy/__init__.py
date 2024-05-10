import rasterio
import rasterio.mask
import rasterio
import rasterio.mask
from rasterio.transform import Affine
import rasterio.rio.clip

import geopandas as gpd
import pkg_resources
import shapely
from shapely.geometry import box, mapping
from rasterio.warp import calculate_default_transform, reproject, Resampling
import json

from . import connection
from . import sources


def download(gdf, title, tif_res):
    "Downloads raster data for a shape from a given source"
    data = sources.load().loc[title]
    con = connection.connect(**data)
    return con.download(gdf, tif_res)


def getFeatures(gdf):
    """Function to parse features from GeoDataFrame in such a manner that rasterio wants them"""
    return [json.loads(gdf.to_json())['features'][0]['geometry']]


def clip_to_area(file, area, to_bounds=True):
    with rasterio.open(file) as src:
        area = area.to_crs(src.crs)
        if to_bounds:
            clip = shapely.geometry.box(**area.bounds.iloc[0].astype(int))
        else:
            clip = area.geometry[0]
        out_image, out_transform = rasterio.mask.mask(src, [clip], filled=not to_bounds, crop=True, all_touched=True)
        out_meta = src.meta.copy()
    out_meta.update({
        "driver": "GTiff",
        "height": out_image.shape[1],
        "width": out_image.shape[2],
        "transform": out_transform})
    with rasterio.open(file, "w", **out_meta) as dest:
        dest.write(out_image)


def getFeatures(gdf):
    """Function to parse features from GeoDataFrame in such a manner that rasterio wants them"""
    return [json.loads(gdf.to_json())['features'][0]['geometry']]


def geom_to_gdf(geom, geom_crs, buffer=None):
    feature_coll = {
        "type": "FeatureCollection",
        "features": [
            {
                "id": "0",
                "type": "Feature",
                "properties": {"name": "Polygon"},
                "geometry": mapping(geom),
                "bbox": geom.bounds
            }
        ]
    }
    df = gpd.GeoDataFrame.from_features(feature_coll)
    df = df.set_crs(geom_crs)

    if buffer is not None:
        df = df.buffer(buffer, resolution=2, join_style=3)

    shapes = getFeatures(df)
    return shapes


def crop_raster(file, geom, geom_crs, buffer=None, driver=None):
    if driver:
        driver = driver
    else:
        driver = "GTiff"

    shapes = geom_to_gdf(geom, geom_crs, buffer=buffer)

    with rasterio.open(file) as src:
        out_image, out_transform = rasterio.mask.mask(src, shapes, filled=True, nodata=-9999, crop=True, all_touched=True)
        out_meta = src.meta
        out_meta.update({"driver": driver,
                         "height": out_image.shape[1],
                         "width": out_image.shape[2],
                         "transform": out_transform})

    with rasterio.open(file, "w", **out_meta) as dest:
        dest.write(out_image)


def reproject_raster_to_project_crs(filename, out_crs, resampling=None):
    """ reproject an image to a new crs:
        inputs:
        filename: string, path to file to reproject
        out_crs: int, epsg code of destination crs"""
    dst_crs = ('EPSG:' + str(out_crs))

    if not resampling:
        resampling = Resampling.nearest

    with rasterio.open(filename) as src:
        transform, width, height = calculate_default_transform(
            src.crs, dst_crs, src.width, src.height, *src.bounds)
        kwargs = src.meta.copy()
        kwargs.update({
            'crs': dst_crs,
            'transform': transform,
            'width': width,
            'height': height
        })
        bands = [src.read(i) for i in range(1, src.count + 1)]
        src_crs = src.crs
        dst_transform = transform

    with rasterio.open(filename, 'w', **kwargs) as dst:
        for i, band in enumerate(bands):
            reproject(
                source=band,
                destination=rasterio.band(dst, i + 1),
                src_transform=src.transform,
                src_crs=src_crs,
                dst_transform=dst_transform,
                dst_crs=dst_crs,
                resampling=resampling)


def export(data_dict, out_path, out_crs, crop_geom=None, crop_geom_crs=None, buffer=None, driver=None, resampling=None):
    if driver == "PNG":
        ras_meta = {
            'driver': 'PNG',
            'height': data_dict["array"].shape[1],
            'width': data_dict["array"].shape[2],
            'count': 3,
            'crs': data_dict["data"]["crs_orig"],
            'dtype': data_dict["array"].dtype,
            'transform': data_dict["transform"],
            'nodata': 0
        }

        with rasterio.open(out_path, 'w', **ras_meta) as png:
            png.write(data_dict["array"][0:3])

        reproject_raster_to_project_crs(out_path, out_crs, resampling=resampling)
        if crop_geom is not None:
            crop_raster(out_path, crop_geom, crop_geom_crs, buffer=buffer, driver="PNG")


    else:
        ras_meta = {'driver': 'GTiff',
                    'dtype': data_dict["array"].dtype,
                    'nodata': None,
                    'width': data_dict["array"].shape[2],
                    'height': data_dict["array"].shape[1],
                    'count': data_dict["array"].shape[0],
                    'crs': data_dict["data"]["crs_orig"],
                    'transform': data_dict["transform"],
                    'tiled': False,
                    'interleave': 'band'}

        with rasterio.open(out_path, 'w', **ras_meta) as tif:
            tif.write(data_dict["array"])

        reproject_raster_to_project_crs(out_path, out_crs, resampling=resampling)

        if crop_geom is not None:
            crop_raster(out_path, crop_geom, crop_geom_crs, buffer=buffer, driver="GTiff")


def get_maps(gdf):
    "Returns the available map sources available from your input shapefile"
    s = sources.load()
    s = s.loc[s.geometry.is_valid]
    return s.loc[s.contains(gdf["geometry"][0])]


def choose_map(title):
    "Returns the shape you want to use to get data from, based on the title"
    s = sources.load()
    return s.loc[s["title"] == title]


# Legacy names
getMaps = get_maps
chooseMap = choose_map
getDTM = download
getImagery = download
export_terrain = export
export_imagery = export

# fixme: Make clipping work to actual shape
# def getFeatures(gdf):
#     """Function to parse features from GeoDataFrame in such a manner that rasterio wants them, from
#     https: // automating - gis - processes.github.io / CSC18 / lessons / L6 / clipping - raster.html"""
#     import json
#     return [json.loads(gdf.to_json())['features'][0]['geometry']]
#
# def clipTif(raster, shape):
#     # with fiona.open(clip_shape_, "r") as shapefile:
#     #     shapes = [feature["geometry"] for feature in shapefile]
#     #shapes = shape.geometry[0]
#
#     with rasterio.open(raster) as src:
#         out_image, out_transform = rasterio.mask.mask(src, shape, crop=True)
#         out_meta = src.meta
#
#     out_meta.update({"driver": "GTiff",
#                      "height": out_image.shape[1],
#                      "width": out_image.shape[2],
#                      "transform": out_transform})








