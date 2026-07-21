import json
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def zoom(expression):
    script = f"const Z=require(process.argv[1]); console.log(JSON.stringify({expression}))"
    result = subprocess.run(["node", "-e", script, str(ROOT / "zoom_controller.js")], capture_output=True, text=True, check=True)
    return json.loads(result.stdout)


class SharedZoomControllerTests(unittest.TestCase):
    def test_cursor_anchor_is_stable_at_readable_minimum_and_large_maximum(self):
        minimum = zoom("Z.anchoredScroll(900,400,300,200,1,.5)")
        maximum = zoom("Z.anchoredScroll(100,50,300,200,.5,2)")
        self.assertAlmostEqual((900 + 300) / 1, (minimum["left"] + 300) / .5)
        self.assertAlmostEqual((100 + 300) / .5, (maximum["left"] + 300) / 2)

    def test_focus_and_technology_use_the_same_controller(self):
        app = (ROOT / "app.js").read_text(encoding="utf-8")
        self.assertIn("focusZoomController=StudioZoom.create", app)
        self.assertIn("technologyZoomController=StudioZoom.create", app)
        self.assertNotIn("function setZoom(value,cx=null,cy=null){const view=", app)

    def test_technology_zoom_controls_are_visible_and_synchronised(self):
        html = (ROOT / "index.html").read_text(encoding="utf-8")
        app = (ROOT / "app.js").read_text(encoding="utf-8")
        for control in ("techZoomOut", "techZoomPercent", "techZoomIn", "techZoomReset", "fitTechnologyTree"):
            self.assertIn(f'id="{control}"', html)
            self.assertIn(control, app)
        self.assertIn('min="50" max="200" value="100"', html)
        self.assertIn("$('#techZoomPercent').textContent", app)

    def test_view_memory_is_keyed_by_all_four_dimensions(self):
        app = (ROOT / "app.js").read_text(encoding="utf-8")
        for control in ("techSource", "techCountry", "techCategory", "techViewMode"):
            self.assertIn(f"$('#{control}')?.value", app)
        self.assertIn("technologyViewMemory.set", app)
        self.assertIn("technologyViewMemory.get", app)
        self.assertIn("saved?.scale??1", app)

    def test_focus_zoom_limits_and_wheel_factor_are_unchanged(self):
        app = (ROOT / "app.js").read_text(encoding="utf-8")
        self.assertIn("min:.35,max:1.6", app)
        self.assertIn("zoomLevel*(e.deltaY<0?1.1:.9)", app)


if __name__ == "__main__":
    unittest.main()
