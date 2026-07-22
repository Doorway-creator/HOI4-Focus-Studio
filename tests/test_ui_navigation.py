import re
import unittest
from pathlib import Path


class SourceNavigationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        root = Path(__file__).resolve().parents[1]
        cls.html = (root / "index.html").read_text(encoding="utf-8")
        cls.script = (root / "app.js").read_text(encoding="utf-8")

    def test_top_bar_has_visible_sources_button_and_screen(self):
        self.assertRegex(self.html, r'<button[^>]+id="topSources"[^>]+data-tab="sources"[^>]*>Sources</button>')
        self.assertIn('id="sourcesTab"', self.html)
        self.assertIn("header [data-tab]", self.script)

    def test_source_screen_exposes_packages_coverage_dependencies_and_warnings(self):
        for control in ('id="sourceList"', 'id="sourceWarnings"', 'id="rebuildCatalog"', 'id="characterCatalog"', 'id="ideaCatalog"'):
            self.assertIn(control, self.html)
        for behavior in ("Enable as project dependency", "Catalog load order", "Move earlier", "Move later", "Unavailable source:"):
            self.assertIn(behavior, self.script)

    def test_production_source_actions_have_live_handlers_and_progress(self):
        for action in ("selectAndReplaceSource(source)", "removeRegisteredSource(source)", "move(-1)", "move(1)"):
            self.assertIn(action, self.script)
        self.assertIn("addEventListener('click'", self.script)
        self.assertIn("Removing ${source.name} and rebuilding", self.script)
        self.assertIn("already the earliest project dependency", self.script)
        self.assertIn("Recovered source from local package folder", self.script)

    def test_character_and_spirit_shortcuts_open_imported_browsers(self):
        self.assertIn('id="browseImportedCharacters"', self.html)
        self.assertIn('id="browseImportedSpirits"', self.html)
        self.assertRegex(self.script, r"browseImportedCharacters.*openTab\('sources'\)")
        self.assertRegex(self.script, r"browseImportedSpirits.*openTab\('sources'\)")

    def test_focus_unlocks_shortcut_reaches_unlock_panel(self):
        self.assertIn('id="openFocusUnlocks"', self.html)
        self.assertIn("$('#unlockPanel')?.scrollIntoView", self.script)
        self.assertIn("$('#unlockSearch')?.focus", self.script)

    def test_missing_base_source_recovery_controls_are_reachable(self):
        for identifier in ("baseRecoveryDialog", "recoverBaseFolder", "recoverBaseZip", "baseRecoveryStatus"):
            self.assertIn(f'id="{identifier}"', self.html)
        self.assertIn("/api/base-source/status", self.script)
        self.assertIn("/api/base-source/recover", self.script)
        self.assertIn("recoveryRequired", self.script)


if __name__ == "__main__":
    unittest.main()
