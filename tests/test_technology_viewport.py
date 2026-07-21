import json
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def viewport(expression):
    script = f"const V=require(process.argv[1]); console.log(JSON.stringify({expression}))"
    result = subprocess.run(["node", "-e", script, str(ROOT / "technology_viewport.js")], capture_output=True, text=True, check=True)
    return json.loads(result.stdout)


class TechnologyViewportTests(unittest.TestCase):
    def test_very_wide_tree_includes_first_and_last_node_rectangles(self):
        bounds = viewport("V.graphBounds([{x:-1200,y:0,width:184,height:86},{x:4200,y:0,width:184,height:86}],[])")
        self.assertEqual(bounds["minX"], -1292); self.assertEqual(bounds["maxX"], 4292)

    def test_very_tall_tree_includes_top_and_bottom_rectangles(self):
        bounds = viewport("V.graphBounds([{x:0,y:-900},{x:0,y:5100}],[])")
        self.assertLess(bounds["minY"], -900); self.assertGreater(bounds["maxY"], 5100)

    def test_negative_coordinates_receive_positive_workspace_offset(self):
        result = viewport("V.workspace(V.graphBounds([{x:-700,y:-300}],[]),140)")
        self.assertGreater(result["offsetX"], 700); self.assertGreater(result["offsetY"], 300)

    def test_fit_accounts_for_actual_viewport_between_sidebars(self):
        wide = viewport("V.fitScale({width:2000,height:800},1200,700,{padding:96,min:.4,max:1.25})")
        narrow = viewport("V.fitScale({width:2000,height:800},700,700,{padding:96,min:.4,max:1.25})")
        self.assertGreater(wide, narrow); self.assertGreaterEqual(narrow, .4)

    def test_panning_at_minimum_and_maximum_zoom_keeps_cursor_anchor(self):
        minimum = viewport("V.zoomAround(900,400,300,200,1,.4)")
        maximum = viewport("V.zoomAround(100,50,300,200,.4,1.5)")
        self.assertAlmostEqual((900+300)/1, (minimum["left"]+300)/.4)
        self.assertAlmostEqual((100+300)/.4, (maximum["left"]+300)/1.5)

    def test_edge_extents_are_included_in_graph_bounds(self):
        bounds = viewport("V.graphBounds([{x:0,y:0}],[{source:{x:-500,y:-400},target:{x:900,y:700}}])")
        self.assertEqual(bounds["minX"], -500); self.assertEqual(bounds["maxY"], 700)

    def test_no_shifted_node_is_permanently_outside_workspace(self):
        count = viewport("(()=>{const n=[{x:-1000,y:-500},{x:2500,y:1800}],b=V.graphBounds(n,[]),w=V.workspace(b,140),s=n.map(x=>({...x,x:x.x+w.offsetX,y:x.y+w.offsetY}));return V.outsideCount(s,{minX:0,minY:0,maxX:w.width,maxY:w.height})})()")
        self.assertEqual(count, 0)

    def test_inspector_resize_switches_and_navigation_controls_are_wired(self):
        app = (ROOT / "app.js").read_text(encoding="utf-8")
        html = (ROOT / "index.html").read_text(encoding="utf-8")
        css = (ROOT / "style.css").read_text(encoding="utf-8")
        for token in ("ResizeObserver", "inspector-collapsed", "toggleTechInspector", "technologyViewCenter", "refreshTechnologyViewport", "ArrowLeft", "e.ctrlKey", "e.shiftKey", "e.button", "Home"):
            self.assertIn(token, app + html + css)
        self.assertIn("overflow:scroll", css)
        self.assertIn("tabindex=\"0\"", html)

    def test_game_modder_country_category_and_reflow_recalculate(self):
        app = (ROOT / "app.js").read_text(encoding="utf-8")
        self.assertIn("requestAnimationFrame(()=>refreshTechnologyViewport", app)
        self.assertIn("renderTechnologyTree();fitTechnologyTree()", app)
        self.assertIn("restoreTechnologyView", app)
        self.assertIn("$('#techCountry').oninput=$('#techCountry').onchange=$('#techCategory').oninput", app)
        self.assertIn("requestId!==techLoadSequence", app)

    def test_viewport_diagnostics_include_required_values(self):
        app = (ROOT / "app.js").read_text(encoding="utf-8")
        for label in ("Graph bounds X", "Graph bounds Y", "Viewport", "Pan offset", "Zoom", "Nodes outside bounds"):
            self.assertIn(label, app)


if __name__ == "__main__":
    unittest.main()
