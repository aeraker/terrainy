import geopandas as gpd
import pandas as pd
import os.path
import json
import traceback
from importlib.resources import files
from . import connection 

sources_path = os.path.expanduser("~/.config/terrainy/sources.geojson")

def load():
    with files("terrainy").joinpath("sources.geojson").open("rb") as f:
        sources = gpd.read_file(f).set_index("title")
    if os.path.exists(sources_path):
        with open(sources_path, "rb") as f:
            sources = pd.concat((
                sources,
                gpd.read_file(f).set_index("title"))
            )
    return sources.loc[~sources.index.duplicated(keep='first')]

def dump(sources):
    sources_dir = os.path.dirname(sources_path)
    if not os.path.exists(sources_dir):
        os.makedirs(sources_dir)
    sources.to_file(sources_path, driver='GeoJSON')

def add_source(**kw):
    con = connection.connect(**kw)
    kw["crs_orig"] = con.get_crs()
    kw["geometry"] = con.get_shape().to_crs(4326).iloc[0].geometry
    s = load()
    s.loc[kw.pop("title")] = kw
    dump(s)

def add_mapproxy(data):
    for title, spec in data["sources"].items():
        try:
            args = {"title": title, "connection_type": spec["type"], "connection_args": {}}
            if "url" in spec:
                args["connection_args"]["url"] = spec["url"]
            if "req" in spec:
                if "url" in spec["req"]:
                    args["connection_args"]["url"] = spec["req"]["url"]
                if "layers" in spec["req"]:
                    args["layer"] = spec["req"]["layers"]

            if "url" in args["connection_args"]:
                args["connection_args"]["url"] = args["connection_args"]["url"].replace("%(", "{").replace(")s", "}")

            if "grid" in spec:
                grid = spec["grid"]
                if grid == "GLOBAL_WEBMERCATOR":
                    args["crs_orig"] = "EPSG:3857"
                else:
                    args["crs_orig"] = data["grids"][grid]["srs"]
        except Exception as e:
            print("Parser error for source %s: %s" % (title, e))
            print("\n".join(("    " + line for line in traceback.format_exc().split("\n"))))
        else:
            try:
                add_source(**args)
            except Exception as e:
                print("Unable to add source %s: %s: %s" % (
                    title, args, e))
                print("\n".join(("    " + line for line in traceback.format_exc().split("\n"))))
