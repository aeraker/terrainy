from . import connection
from owslib.wcs import WebCoverageService
from urllib.parse import urlsplit

class WcsConnection(connection.Connection):
    bands = 1
    dtype = "float64"
    
    def __init__(self, **kw):
        connection.Connection.__init__(self, **kw)
        args = self.kw["connection_args"]
        self.wcs = WebCoverageService(**args)
        configured_url = (args.get("url") or "").split("?")[0]
        configured_host = urlsplit(configured_url).hostname
        if configured_host:
            for method in self.wcs.getOperationByName("GetCoverage").methods:
                method_host = urlsplit(method.get("url") or "").hostname
                if method_host and method_host.lower() != configured_host.lower():
                    method["url"] = configured_url
        self.layer = self.wcs[self.kw["layer"]]

    def download_tile(self, bounds, tif_res, size, resy=None):
        return self.wcs.getCoverage(
            identifier=self.layer.id,
            crs=self.kw.get("crs_orig") or self.layer.boundingboxes[0]["nativeSrs"],
            bbox=bounds,
            resx=tif_res, resy=resy if resy is not None else tif_res,
            format='GeoTIFF',
            interpolation="bilinear")

    def get_bounds(self):
        return self.layer.boundingboxes[0]["bbox"]
        
    def get_crs(self):
        crs = self.kw.get("crs_orig") or self.layer.boundingboxes[0]["nativeSrs"]
        # CRS:84 is OGC WGS84 lon/lat; pyproj does not recognize the WMS/WCS alias.
        if isinstance(crs, str) and crs.upper().replace(" ", "") in ("CRS:84", "CRS84"):
            crs = "OGC:CRS84"
        return crs
