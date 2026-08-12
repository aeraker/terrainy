import unittest

from terrainy.connection_cog import (
    CogConnection,
    format_lat_tile,
    format_lon_tile,
    iter_southwest_corners,
)


class TestCogConnection(unittest.TestCase):
    def test_format_lat_lon(self):
        self.assertEqual(format_lat_tile(59), "N59_00")
        self.assertEqual(format_lat_tile(-1), "S01_00")
        self.assertEqual(format_lon_tile(10), "E010_00")
        self.assertEqual(format_lon_tile(-10), "W010_00")

    def test_urls_for_bounds(self):
        con = CogConnection(
            connection_type="cog",
            crs_orig="EPSG:4326",
            connection_args={
                "url_template": "https://example.com/{lat}/{lon}.tif",
            },
        )
        urls = con.urls_for_bounds(10.7, 59.9, 10.8, 59.95)
        self.assertEqual(urls, ["https://example.com/N59_00/E010_00.tif"])

    def test_invalid_bbox(self):
        with self.assertRaises(ValueError):
            list(iter_southwest_corners(1, 1, 0, 0))


if __name__ == "__main__":
    unittest.main()
