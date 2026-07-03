from . import connection
from owslib.wms import WebMapService

class WmsConnection(connection.Connection):
    bands = 3
    dtype = "uint8"
    formats = ['image/geotiff',
               'image/png',
               'image/jpeg',
               'image/tiff',]

    def __init__(self, **kw):
        connection.Connection.__init__(self, **kw)
        self.wms = WebMapService(**self.kw["connection_args"])
        self.layer = self.wms[self.kw["layer"]]

        supported = self.wms.getOperationByName('GetMap').formatOptions
        self.file_format = [fmt for fmt in self.formats if fmt in supported][0]

    def download_tile(self, bounds, tif_res, size, resy=None):
        return self.wms.getmap(layers=[self.layer.id],
                               srs=self.get_crs(),
                               bbox=bounds,
                               size=size,
                               format=self.file_format)

    def get_bounds(self):
        return self.layer.boundingBox[:4]

    def get_crs(self):
        return self.layer.boundingBox[4]
