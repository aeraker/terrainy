import unittest
from unittest.mock import patch

from terrainy.connection_stac import (
    StacConnection,
    pick_asset_href,
    search_stac_items,
)


class TestStacHelpers(unittest.TestCase):
    def test_pick_asset_href_prefers_listed_keys(self):
        assets = {
            "thumbnail": {"href": "https://example.com/thumb.png"},
            "data": {"href": "https://example.com/data.tif"},
            "dtm": {"href": "https://example.com/dtm.tif"},
        }
        self.assertEqual(
            pick_asset_href(assets, ["dtm", "data"]),
            "https://example.com/dtm.tif",
        )
        self.assertEqual(
            pick_asset_href(assets, ["missing", "data"]),
            "https://example.com/data.tif",
        )

    def test_pick_asset_href_falls_back_to_any_tif(self):
        assets = {
            "other": {"href": "https://example.com/elev.tiff"},
        }
        self.assertEqual(
            pick_asset_href(assets, ["dtm"]),
            "https://example.com/elev.tiff",
        )

    def test_collections_for_resolution_reverses_above_threshold(self):
        con = StacConnection(
            connection_type="stac",
            crs_orig="EPSG:4326",
            connection_args={
                "stac_url": "https://example.com/search",
                "collections": ["hrdem-mosaic-1m", "hrdem-mosaic-2m"],
                "resolution_threshold_m": 1.5,
            },
        )
        self.assertEqual(
            con.collections_for_resolution(1.0),
            ["hrdem-mosaic-1m", "hrdem-mosaic-2m"],
        )
        self.assertEqual(
            con.collections_for_resolution(2.0),
            ["hrdem-mosaic-2m", "hrdem-mosaic-1m"],
        )


class TestSearchStacItems(unittest.TestCase):
    @patch("terrainy.connection_stac.requests.post")
    def test_search_uses_intersects_post(self, mock_post):
        mock_post.return_value.raise_for_status = lambda: None
        mock_post.return_value.json.return_value = {
            "features": [{"id": "a", "assets": {}}]
        }
        items = search_stac_items(
            "https://example.com/search",
            ["hrdem-mosaic-1m"],
            bbox=(-80, 45, -79, 46),
            geometry={"type": "Point", "coordinates": [-79.5, 45.5]},
        )
        self.assertEqual(len(items), 1)
        payload = mock_post.call_args.kwargs["json"]
        self.assertIn("intersects", payload)
        self.assertEqual(payload["collections"], ["hrdem-mosaic-1m"])


class TestCanadaHrdemCatalog(unittest.TestCase):
    def test_sources_include_hrdem_dtm_and_dem(self):
        import terrainy

        sources = terrainy.sources.load()
        self.assertIn("Canada HRDEM DTM", sources.index)
        self.assertIn("Canada HRDEM DEM", sources.index)
        dtm = sources.loc["Canada HRDEM DTM"]
        dem = sources.loc["Canada HRDEM DEM"]
        self.assertEqual(dtm.connection_type, "stac")
        self.assertEqual(dem.connection_type, "stac")
        dtm_con = terrainy.connection.connect(dtm)
        dem_con = terrainy.connection.connect(dem)
        self.assertIsInstance(dtm_con, StacConnection)
        self.assertEqual(dtm_con.asset_keys[0], "dtm")
        self.assertEqual(dem_con.asset_keys[0], "dsm")


if __name__ == "__main__":
    unittest.main()
