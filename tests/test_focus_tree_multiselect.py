import json
import shutil
import subprocess
import unittest
from pathlib import Path


class FocusTreeMultiSelectTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = Path(__file__).resolve().parents[1]
        cls.app = (cls.root / "app.js").read_text(encoding="utf-8")
        cls.html = (cls.root / "index.html").read_text(encoding="utf-8")
        cls.css = (cls.root / "style.css").read_text(encoding="utf-8")
        cls.node = shutil.which("node")

    def run_helper(self, expression):
        if not self.node:
            self.skipTest("Node.js is required for focus-tree interaction tests")
        script = (
            "const h=require('./focus_tree_interactions.js');"
            f"console.log(JSON.stringify({expression}));"
        )
        result = subprocess.run(
            [self.node, "-e", script], cwd=self.root, check=True,
            capture_output=True, text=True,
        )
        return json.loads(result.stdout)

    def test_box_selection_selects_every_intersecting_focus(self):
        result = self.run_helper(
            "h.focusIdsInRectangle([{id:'a',x:0,y:0},{id:'b',x:3,y:0}],"
            "{left:390,right:560,top:150,bottom:240},"
            "f=>({x:460+f.x*180,y:190+f.y*110}))"
        )
        self.assertEqual(result, ["a"])
        self.assertIn('e.button===0&&e.shiftKey', self.app)
        self.assertIn('id="selectionBox"', self.html)

    def test_partial_rectangle_intersection_counts(self):
        result = self.run_helper(
            "h.focusIdsInRectangle([{id:'edge',x:0,y:0}],"
            "{left:537,right:540,top:225,bottom:230},"
            "f=>({x:460,y:190}))"
        )
        self.assertEqual(result, ["edge"])

    def test_ctrl_click_toggles_individual_selection(self):
        result = self.run_helper(
            "[h.toggleSelection(['a','b'],'b'),h.toggleSelection(['a'],'c')]"
        )
        self.assertEqual(result, [["a"], ["a", "c"]])
        self.assertIn('event?.ctrlKey', self.app)

    def test_group_move_preserves_offsets(self):
        result = self.run_helper(
            "(()=>{const f=[{id:'a',x:1,y:2},{id:'b',x:4,y:8},{id:'c',x:9,y:9}];"
            "h.moveFocusGroup(f,['a','b'],{a:{x:1,y:2},b:{x:4,y:8}},2.26,-1.24);return f})()"
        )
        self.assertEqual(result, [
            {"id": "a", "x": 3.25, "y": 0.75},
            {"id": "b", "x": 6.25, "y": 6.75},
            {"id": "c", "x": 9, "y": 9},
        ])

    def test_group_move_is_one_undo_and_redo_action(self):
        result = self.run_helper(
            "(()=>{let p={focuses:[{id:'a',x:0,y:0},{id:'b',x:2,y:3}]},history=[],future=[];"
            "history.push(JSON.stringify(p));h.moveFocusGroup(p.focuses,['a','b'],"
            "{a:{x:0,y:0},b:{x:2,y:3}},1,2);const moved=JSON.stringify(p);"
            "future.push(JSON.stringify(p));p=JSON.parse(history.pop());const undone=JSON.stringify(p);"
            "history.push(JSON.stringify(p));p=JSON.parse(future.pop());return{moved,undone,redone:JSON.stringify(p)}})()"
        )
        self.assertNotEqual(result["moved"], result["undone"])
        self.assertEqual(result["moved"], result["redone"])
        self.assertIn('if(!moved)snapshot()', self.app)

    def test_connection_lines_update_during_group_move(self):
        move_handler = self.app[self.app.index('function dragStart'):self.app.index('function renderEditor')]
        self.assertIn('moveFocusGroup', move_handler)
        self.assertIn('renderEdges()', move_handler)

    def test_right_mouse_panning_and_selection_highlight_remain_available(self):
        self.assertIn('![0,2].includes(e.button)', self.app)
        self.assertIn('beginCanvasPan(e)', self.app)
        self.assertIn("view.scrollLeft=sl-(ev.clientX-sx)", self.app)
        self.assertIn('.focus.selected', self.css)
        self.assertIn("e.key==='Escape'", self.app)


if __name__ == "__main__":
    unittest.main()
