import os
import unittest
from pathlib import Path

from source_catalog import SourceCatalog


CATALOG = os.environ.get("HFS_REAL_TECH_CATALOG", "")


@unittest.skipUnless(CATALOG and Path(CATALOG).is_file(), "real isolated source catalog not supplied")
class RealTechnologyCatalogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.catalog = SourceCatalog(Path(CATALOG))

    def test_non_trivial_source_totals_and_shared_categories(self):
        vanilla = self.catalog.technology_tree("vanilla", "ENG")
        road = self.catalog.technology_tree("road_to_56", "NOR")
        self.assertGreater(vanilla["diagnostics"]["technologiesParsed"], 400)
        self.assertGreaterEqual(len(vanilla["categories"]), 10)
        self.assertGreater(road["diagnostics"]["technologiesParsed"], 800)
        self.assertGreaterEqual(len(road["categories"]), 12)
        self.assertIn("r56_vechicles_folder", road["categories"])

    def test_r56_germany_train_has_name_and_icon(self):
        tree = self.catalog.technology_tree("road_to_56", "GER", "r56_vechicles_folder")
        item = next(row for row in tree["items"] if row["entity_id"] == "r56_mid_wartime_train")
        self.assertNotEqual(item["display_name"], item["entity_id"])
        self.assertTrue(item["normalized"]["iconResolved"])

    def test_default_norway_naval_hides_picker_helpers(self):
        tree = self.catalog.technology_tree("road_to_56", "NOR", "mtgnavalfolder")
        self.assertGreater(len(tree["items"]), 30)
        self.assertFalse(any("_pick_" in row["entity_id"] for row in tree["items"]))

    def test_vanilla_uk_air_and_r56_italy_armour_are_readable(self):
        for profile, country, folder in (("vanilla", "ENG", "air_techs_folder"), ("road_to_56", "ITA", "armour_folder")):
            tree = self.catalog.technology_tree(profile, country, folder)
            self.assertGreater(len(tree["items"]), 20)
            self.assertGreater(sum(row["display_name"] != row["entity_id"] for row in tree["items"]), 20)
            self.assertGreater(sum(bool(row["normalized"].get("iconResolved")) for row in tree["items"]), 20)


if __name__ == "__main__":
    unittest.main()
