from __future__ import annotations

import base64
import importlib.util
import json
import re
import shutil
import threading
import time
import webbrowser
import os
import subprocess
import tempfile
import hashlib
import sys
import urllib.error
import urllib.request
import urllib.parse
import zipfile
from datetime import datetime
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from io import BytesIO
from pathlib import Path

from PIL import Image
from export_validation import validate_references
from project_migrations import migrate_project
from source_catalog import SourceCatalog
from source_importer import import_sources
from source_cache import rebuild_source_cache
from source_registry import SourceRegistry
from project_content import character_action_script, spirit_action_script
from clausewitz_parser import Block, parse as parse_clausewitz, serialize as serialize_clausewitz
from project_storage import ProjectStorage, default_app_data_root
from base_source import BaseSourceRequired, recover_base_source, require_base_source
from technology_tree import export_project_technologies, validate_project_technologies
from foreign_technology import foreign_link_effects, foreign_link_preview
from playset_snapshot import create_playset_snapshot, list_snapshots
from tester_bootstrap import prepare_tester_storage

ROOT = Path(__file__).resolve().parent
PROJECT_FILE = ROOT / "projects" / "default_project.json"
EXPORT_ROOT = ROOT / "exports"
APP_VERSION = "6.13.2"
APP_PORT = int(os.environ.get("HOI4_FOCUS_STUDIO_PORT", "0"))
APP_INSTANCE_TOKEN = os.environ.get("HOI4_FOCUS_STUDIO_INSTANCE_TOKEN", "")
GITHUB_RELEASES_API = "https://api.github.com/repos/Doorway-creator/HOI4-Focus-Studio/releases/latest"
UPDATE_ROOT = ROOT / "updates"
APP_DATA_ROOT = default_app_data_root()
prepare_tester_storage(APP_DATA_ROOT)
PROJECT_STORAGE = ProjectStorage(APP_DATA_ROOT, PROJECT_FILE)
SOURCE_ROOT = APP_DATA_ROOT / "sources"
SOURCE_CATALOG = SourceCatalog(SOURCE_ROOT / "catalog.sqlite3")
SOURCE_REGISTRY = SourceRegistry(APP_DATA_ROOT / "source_registry.json")
PLAYSET_ROOT = APP_DATA_ROOT / "playset_snapshots"
LOCAL_PATH_FIELDS = {"exportPath", "hoi4ModFolder"}


def known_source_package_roots() -> list[Path]:
    roots = []
    configured = os.environ.get("HOI4_FOCUS_STUDIO_SOURCE_PACKAGES", "").strip()
    if configured: roots.append(Path(configured))
    for start in (ROOT, Path.cwd()):
        roots.extend(parent / "source_packages" for parent in (start, *start.parents))
    # Production installs are commonly outside the repository checkout. Search the
    # standard local source-pack location without relying on a user name or tester
    # launcher environment variable.
    drive = Path((os.environ.get("SystemDrive") or ROOT.drive or Path.cwd().drive or "C:") + "\\")
    roots.append(drive / "GitHub" / "HOI4-Focus-Studio" / "source_packages")
    for package in SOURCE_REGISTRY.packages():
        path = Path(package.get("path", ""))
        if path.parent.name.lower() == "source_packages": roots.append(path.parent)
    return list(dict.fromkeys(path.resolve() for path in roots))


def choose_source_archive() -> str:
    """Use a native Windows picker that remains available in the packaged executable."""
    if os.name == "nt":
        script = (
            "[Console]::OutputEncoding=[System.Text.UTF8Encoding]::new();"
            "Add-Type -AssemblyName System.Windows.Forms;"
            "$owner=New-Object System.Windows.Forms.Form;"
            "$owner.StartPosition='CenterScreen';$owner.Size=New-Object System.Drawing.Size(1,1);"
            "$owner.ShowInTaskbar=$false;$owner.TopMost=$true;$owner.Opacity=0.01;"
            "$dialog=New-Object System.Windows.Forms.OpenFileDialog;"
            "$dialog.Title='Choose HOI4 source archive';"
            "$dialog.Filter='Source archives (*.zip;*.rar)|*.zip;*.rar|All files (*.*)|*.*';"
            "$owner.Show();$owner.Activate();$owner.BringToFront();[System.Windows.Forms.Application]::DoEvents();"
            "try{if($dialog.ShowDialog($owner) -eq [System.Windows.Forms.DialogResult]::OK){[Console]::Out.Write($dialog.FileName)}}"
            "finally{$dialog.Dispose();$owner.Close();$owner.Dispose()}"
        )
        result = subprocess.run(["powershell.exe", "-NoProfile", "-STA", "-Command", script], capture_output=True, text=True, encoding="utf-8", errors="replace", creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        if result.returncode: raise RuntimeError("The native source picker could not open: " + (result.stderr.strip() or f"PowerShell exit code {result.returncode}"))
        return result.stdout.strip()
    import tkinter as tk
    from tkinter import filedialog
    window = tk.Tk(); window.withdraw(); window.attributes("-topmost", True)
    try: return filedialog.askopenfilename(title="Choose HOI4 source archive", filetypes=[("Source archives", "*.zip *.rar"), ("All files", "*.*")])
    finally: window.destroy()


def recover_missing_registered_sources() -> dict:
    """Repair uniquely identifiable missing package paths before a rebuild."""
    recovered, unresolved = [], []
    for package in SOURCE_REGISTRY.packages():
        if not package.get("enabled") or Path(package.get("path", "")).is_file():
            continue
        matches = SOURCE_REGISTRY.recovery_candidates(package.get("id", ""), known_source_package_roots())
        if len(matches) == 1:
            repaired, _ = SOURCE_REGISTRY.register(matches[0]["path"], package.get("id", ""))
            recovered.append({"packageId": repaired["id"], "name": repaired["name"], "path": repaired["path"]})
        else:
            unresolved.append({"packageId": package.get("id", ""), "name": package.get("name", ""), "matches": matches})
    return {"recovered": recovered, "unresolved": unresolved}


class CleanupDirectory:
    """A deterministic staging directory that also cleans itself during exception unwinding."""
    def __init__(self, parent: Path, prefix: str):
        self.path = Path(tempfile.mkdtemp(prefix=prefix, dir=parent))
    @property
    def name(self): return str(self.path)
    def cleanup(self):
        if self.path.exists(): shutil.rmtree(self.path, ignore_errors=True)
    def __del__(self): self.cleanup()


def public_project(project: dict) -> dict:
    """Keep computer-specific paths out of projects, backups, and exported mods."""
    return {key: value for key, value in project.items() if key not in LOCAL_PATH_FIELDS}


def load_current_project() -> dict:
    project = PROJECT_STORAGE.load()
    legacy_icons = ROOT / "projects" / "icons"
    protected_icons = PROJECT_STORAGE.icons(project["projectId"])
    if legacy_icons.is_dir() and not protected_icons.exists():
        shutil.copytree(legacy_icons, protected_icons)
    return project


def selected_directory(value: object, label: str) -> Path:
    raw = str(value or "").strip()
    if not raw:
        raise ValueError(f"Choose a {label} folder first.")
    path = Path(raw).expanduser()
    if not path.is_absolute():
        raise ValueError(f"The {label} folder must be an absolute path.")
    return path.resolve()


def catalog_lookup(kind: str, target: str) -> list[dict]:
    mapped = {"aircraft_module": "module", "tank_module": "module", "ship_module": "module", "research_bonus": "technology", "ahead_of_time": "technology", "category_bonus": "technology_category"}.get(kind, kind)
    if not SOURCE_CATALOG.path.exists(): return []
    return [row for row in SOURCE_CATALOG.search(mapped, target, 200) if row["entity_id"] == target]


def preserved_unknown_script(raw: str, normalized_keys: set[str]) -> str:
    if not str(raw).strip(): return ""
    try:
        document = parse_clausewitz(str(raw))
        return serialize_clausewitz(Block([entry for entry in document.entries if entry.key not in normalized_keys]))
    except ValueError:
        return str(raw)


def matching_brace(text: str, opening: int) -> int:
    depth = 0
    quoted = False
    escaped = False
    for i in range(opening, len(text)):
        c = text[i]
        if quoted:
            if escaped:
                escaped = False
            elif c == "\\":
                escaped = True
            elif c == '"':
                quoted = False
        elif c == '"':
            quoted = True
        elif c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return i
    raise ValueError("Unmatched brace")


def replace_line(block: str, key: str, value: str) -> str:
    pattern = rf"(?m)^(\s*){re.escape(key)}\s*=.*$"
    if re.search(pattern, block):
        return re.sub(pattern, rf"\1{key} = {value}", block, count=1)
    return block.replace("\n", f"\n\t\t{key} = {value}\n", 1)


def timed_idea_id(focus_id: str, index: int) -> str:
    return re.sub(r"[^A-Za-z0-9_]", "_", f"NHO_editor_timed_{focus_id}_{index}")


def generated_effect_scripts(focus: dict) -> list[str]:
    scripts = []
    for index, effect in enumerate(focus.get("effects", [])):
        category, kind = effect.get("category"), effect.get("type")
        amount = float(effect.get("amount", 0))
        duration = int(effect.get("duration", 0) or 0)
        if category == "political":
            if duration > 0 and kind in {"political_power", "stability", "war_support"}:
                scripts.append(f"add_timed_idea = {{ idea = {timed_idea_id(focus['id'], index)} days = {duration} }}")
            elif kind == "political_power": scripts.append(f"add_political_power = {amount:g}")
            elif kind == "stability": scripts.append(f"add_stability = {amount/100:g}")
            elif kind == "war_support": scripts.append(f"add_war_support = {amount/100:g}")
            elif kind == "popularity": scripts.append(f"add_popularity = {{ ideology = {effect.get('ideology','neutrality')} popularity = {amount/100:g} }}")
        elif category == "state":
            state = int(effect.get("state", 0)); level = max(1, int(amount)); province = int(effect.get("province", 0) or 0)
            if not state: continue
            construction = f"add_building_construction = {{ type = {kind} level = {level} instant_build = yes"
            if kind in {"rail_way", "bunker", "coastal_bunker", "naval_base"} and province:
                construction += f" province = {province}"
            construction += " }"
            extras = []
            if kind in {"industrial_complex", "arms_factory", "dockyard"}:
                extras.append(f"add_extra_state_shared_building_slots = {level}")
            extras.append(construction)
            scripts.append(f"{state} = {{ " + " ".join(extras) + " }")
        elif category == "technology":
            technology = re.sub(r"[^A-Za-z0-9_]", "", effect.get("technology", ""))
            if not technology: continue
            if kind == "unlock": scripts.append(f"set_technology = {{ {technology} = 1 }}")
            else:
                bonus = float(effect.get("amount", 50)) / 100
                name = re.sub(r"[^A-Za-z0-9_]", "_", f"NHO_editor_{focus['id']}_{technology}_bonus")
                scripts.append(f"add_tech_bonus = {{ name = {name} bonus = {bonus:g} uses = 1 technology = {technology} }}")
    for index, unlock in enumerate(focus.get("unlocks", [])):
        target = re.sub(r"[^A-Za-z0-9_.:-]", "", str(unlock.get("targetId", "")))
        if not target: continue
        action = unlock.get("action", "instant_research")
        if action in {"instant_research", "technology_unlock"}:
            scripts.append(f"set_technology = {{ {target} = 1 }}")
        elif action in {"research_bonus", "category_bonus", "ahead_of_time"}:
            bonus = max(0, float(unlock.get("bonus", 50))) / 100
            uses = max(1, int(unlock.get("uses", 1)))
            name = re.sub(r"[^A-Za-z0-9_]", "_", f"NHO_unlock_{focus.get('id')}_{index}")
            selector = "category" if action == "category_bonus" else "technology"
            ahead = f" ahead_reduction = {max(0, float(unlock.get('aheadReduction', 0))):g}" if action == "ahead_of_time" else ""
            scripts.append(f"add_tech_bonus = {{ name = {name} bonus = {bonus:g} uses = {uses} {selector} = {target}{ahead} }}")
        elif action in {"module_availability", "equipment_availability", "unit_availability"}:
            technology = re.sub(r"[^A-Za-z0-9_]", "", str(unlock.get("unlockTechnology", "")))
            if technology: scripts.append(f"set_technology = {{ {technology} = 1 }}")
    scripts.extend(foreign_link_effects(focus))
    return scripts


def diplomacy_focus_scripts(focus: dict) -> list[str]:
    scripts = []
    for action in focus.get("diplomacy", []):
        target = re.sub(r"[^A-Z0-9_]", "", action.get("target", ""))
        if not target:
            continue
        states = [int(x) for x in re.findall(r"\d+", str(action.get("states", "")))]
        if action.get("type") == "war_goal":
            goal = re.sub(r"[^A-Za-z0-9_]", "", action.get("warGoalType", "take_state_focus"))
            generator = f" generator = {{ {' '.join(map(str, states))} }}" if states else ""
            scripts.append(f"create_wargoal = {{ type = {goal} target = {target}{generator} }}")
        elif action.get("_eventId"):
            scripts.append(f"{target} = {{ country_event = {{ id = {action['_eventId']} }} }}")
    return scripts


def render_focus(focus: dict, events: list[dict], decisions: list[dict], characters: list[dict], national_spirits: list[dict]) -> str:
    raw = str(focus.get("raw", "") or "").strip()

    # The editor historically allowed the Advanced Raw Block field to contain
    # either a complete `focus = { ... }` block or only one/more reward effects.
    # The old exporter treated effect-only text as a complete focus, producing
    # loose commands outside any focus block and making HOI4 reject the tree.
    # Normalize effect-only/malformed raw text into a valid focus block first.
    is_complete_focus = False
    focus_match = re.search(r"\bfocus\s*=\s*\{", raw)
    if focus_match:
        try:
            opening = raw.find("{", focus_match.start(), focus_match.end())
            closing = matching_brace(raw, opening)
            is_complete_focus = not raw[closing + 1:].strip()
        except (ValueError, IndexError):
            is_complete_focus = False

    if not is_complete_focus:
        reward_fragment = raw if raw else "add_political_power = 50"
        reward_lines = "\n".join("\t\t\t" + line.strip() for line in reward_fragment.splitlines() if line.strip())
        raw = (
            "focus = {\n"
            "\t\tid = NEW_FOCUS\n"
            "\t\ticon = GFX_goal_generic_political_pressure\n"
            "\t\tx = 0\n"
            "\t\ty = 0\n"
            "\t\tcost = 10\n"
            "\t\tcompletion_reward = {\n"
            f"{reward_lines}\n"
            "\t\t}\n"
            "\t}"
        )
    raw = replace_line(raw, "id", focus["id"])
    raw = replace_line(raw, "icon", focus.get("icon") or "GFX_goal_generic_political_pressure")
    raw = replace_line(raw, "x", str(round(focus.get("x", 0))))
    raw = replace_line(raw, "y", str(round(focus.get("y", 0))))
    raw = re.sub(r"(?m)^\s*relative_position_id\s*=.*\n?", "", raw)
    raw = re.sub(r"(?m)^\s*prerequisite\s*=\s*\{[^\n]*\}\s*\n?", "", raw)
    raw = re.sub(r"(?m)^\s*mutually_exclusive\s*=\s*\{[^\n]*\}\s*\n?", "", raw)
    insert = []
    for source in focus.get("prerequisites", []):
        insert.append(f"\t\tprerequisite = {{ focus = {source} }}")
    mutual = focus.get("mutuallyExclusive", [])
    if mutual:
        insert.append("\t\tmutually_exclusive = { " + " ".join(f"focus = {x}" for x in mutual) + " }")
    if insert:
        marker = re.search(r"(?m)^\s*icon\s*=.*$", raw)
        pos = marker.end() if marker else raw.find("\n")
        raw = raw[:pos] + "\n" + "\n".join(insert) + raw[pos:]
    linked = [event for event in events if event.get("linkedFocus") == focus["id"]]
    generated = generated_effect_scripts(focus) + diplomacy_focus_scripts(focus)
    generated.extend(f"add_ideas = {sp['id']}" for sp in national_spirits if sp.get("grantedByFocus") == focus["id"])
    generated.extend(f"remove_ideas = {sp['id']}" for sp in national_spirits if sp.get("removedByFocus") == focus["id"])
    generated.extend(filter(None, (character_action_script(action) for action in focus.get("characterActions", []))))
    for action in focus.get("spiritActions", []): generated.extend(spirit_action_script(action))
    linked_decisions = [decision for decision in decisions if decision.get("linkedFocus") == focus["id"]]
    linked_characters = [character for character in characters if character.get("linkedFocus") == focus["id"]]
    if linked or generated or linked_decisions or linked_characters:
        reward = re.search(r"completion_reward\s*=\s*\{", raw)
        if reward:
            opening = raw.find("{", reward.start(), reward.end())
            closing = matching_brace(raw, opening)
            calls = []
            for event in linked:
                delay = max(0, int(event.get('delayDays', 0) or 0))
                suffix = f" days = {delay}" if delay else ""
                call = f"{event.get('kind', 'country_event')} = {{ id = {event['id']}{suffix} }}"
                if event["id"] not in raw[opening:closing]:
                    calls.append("\t\t\t" + call)
            calls.extend("\t\t\t" + effect for effect in generated)
            calls.extend("\t\t\tunlock_decision_tooltip = " + decision["id"] for decision in linked_decisions)
            calls.extend("\t\t\trecruit_character = " + character["id"] for character in linked_characters)
            if calls:
                raw = raw[:closing] + "\n" + "\n".join(calls) + "\n\t\t" + raw[closing:]
    return "\t" + raw.lstrip()


def _normalize_version(value: str) -> tuple[int, int, int | None]:
    nums = [int(x) for x in re.findall(r"\d+", str(value or "v0_80"))]
    if len(nums) == 1:
        nums.insert(0, 0)
    major = nums[0] if nums else 0
    minor = nums[1] if len(nums) > 1 else 80
    fix = nums[2] if len(nums) > 2 else None
    return major, minor, fix


def _next_export_version(current: str, bump: str) -> str:
    major, minor, fix = _normalize_version(current)
    if bump == "minor":
        return f"v{major}_{minor + 1}"
    if bump == "hotfix":
        return f"v{major}_{minor}_{(fix or 0) + 1}"
    return f"v{major}_{minor}" + (f"_{fix}" if fix is not None else "")


def _export_name(project: dict, version: str) -> str:
    folder_name = re.sub(r"[^A-Za-z0-9_-]", "_", project.get("exportFolder", "Norway_Remade"))
    safe_version = re.sub(r"[^A-Za-z0-9_-]", "_", version)
    return f"{folder_name}_{safe_version}"


def _make_versioned_zip(export_root: Path, package_dir: Path) -> Path:
    zip_base = export_root / package_dir.name
    zip_path = Path(str(zip_base) + ".zip")
    temporary_base = export_root.parent / f".hfs-zip-{os.getpid()}-{time.time_ns()}"
    temporary_zip = Path(str(temporary_base) + ".zip")
    try:
        shutil.make_archive(str(temporary_base), "zip", package_dir)
        os.replace(temporary_zip, zip_path)
    finally:
        temporary_zip.unlink(missing_ok=True)
    return zip_path


def export_project(project: dict, export_root: Path = EXPORT_ROOT) -> Path:
    migrated, _ = migrate_project(project)
    project.clear(); project.update(migrated)
    reference_errors = validate_references(project, catalog_lookup)
    technology_errors, _ = validate_project_technologies(project, catalog_lookup)
    reference_errors.extend(technology_errors)
    if reference_errors:
        raise ValueError("Export validation failed:\n" + "\n".join(reference_errors))
    version = re.sub(r"[^A-Za-z0-9_.-]", "_", project.get("exportVersion", "v077_editor_export"))
    folder_name = _export_name(project, version)
    final_package = export_root / folder_name
    export_root.parent.mkdir(parents=True, exist_ok=True)
    staging_context = CleanupDirectory(export_root.parent, ".hfs-export-")
    package_dir = Path(staging_context.name) / folder_name
    target = package_dir / folder_name
    package_dir.mkdir(parents=True, exist_ok=True)
    source_mod = require_base_source(PROJECT_STORAGE, project)
    shutil.copytree(source_mod, target)
    descriptor = (target / "descriptor.mod").read_text(encoding="utf-8-sig")
    descriptor = re.sub(r'(?m)^\s*version\s*=\s*"[^"]+"', f'version="{version}"', descriptor, count=1)
    display_name = project.get('modDisplayName', 'Norway Remade')
    version_label = version if str(version).lower().startswith('v') else f'v{version}'
    descriptor = re.sub(r'(?m)^\s*name\s*=\s*"[^"]+"', f'name="{display_name} — {version_label}"', descriptor, count=1)
    (target / "descriptor.mod").write_text(descriptor, encoding="utf-8")
    external = descriptor + f'\npath="mod/{folder_name}"\n'
    (package_dir / f"{folder_name}.mod").write_text(external, encoding="utf-8")
    focus_file = target / "common" / "national_focus" / "norway.txt"
    old = focus_file.read_text(encoding="utf-8-sig")
    tree_start = old.find("focus_tree = {")
    opening = old.find("{", tree_start)
    header_end = old.find("focus = {", opening)
    header = old[:header_end]
    diplomacy_actions = []
    for focus in project.get("focuses", []):
        for action in focus.get("diplomacy", []):
            if action.get("type") != "war_goal":
                action["_eventId"] = f"NHO_diplomacy.{len(diplomacy_actions) + 1}"
                diplomacy_actions.append((focus, action))
    rendered = "\n\n".join(render_focus(x, project.get("events", []), project.get("decisions", []), project.get("characters", []), project.get("nationalSpirits", [])) for x in project.get("focuses", []))
    focus_file.write_text(header + rendered + "\n}\n", encoding="utf-8")
    export_project_technologies(project, target, PROJECT_STORAGE.icons(project["projectId"]), catalog_lookup)

    events_dir = target / "events"
    events_dir.mkdir(parents=True, exist_ok=True)
    namespace = project.get("eventNamespace", "NHO_editor")
    event_lines = [f"add_namespace = {namespace}", ""]
    for event in project.get("events", []):
        kind = event.get("kind", "country_event")
        trigger_mode = event.get("triggerMode", "focus" if event.get("linkedFocus") else "manual")
        event_lines.extend([
            f"{kind} = {{", f"\tid = {event['id']}", f"\ttitle = {event.get('titleKey', event['id'] + '.t')}",
            f"\tdesc = {event.get('descKey', event['id'] + '.d')}", f"\tpicture = {event.get('picture', 'GFX_report_event_generic_read_write')}",
            "\tis_triggered_only = yes", f"\ttrigger = {{ {event.get('trigger', 'always = yes')} }}",
        ])
        event_actions = [character_action_script(action) for action in event.get("characterActions", [])]
        for action in event.get("spiritActions", []): event_actions.extend(spirit_action_script(action))
        for option_index, option in enumerate(event.get("options", [])):
            event_lines.extend(["\toption = {", f"\t\tname = {option.get('nameKey', event['id'] + '.a')}", f"\t\t{option.get('effect', 'add_political_power = 10')}", "\t}"])
            if option_index == 0 and event_actions:
                event_lines[-1:-1] = ["\t\t" + line for line in event_actions if line]
        event_lines.extend(["}", ""])
    (events_dir / "NHO_editor_events.txt").write_text("\n".join(event_lines), encoding="utf-8")

    # Date-driven events are fired from an on_daily hook. HOI4 does not have
    # a real clock-hour selector for scripted events, so the chosen day is the
    # precise scheduling unit. A country flag prevents repeats and also makes
    # late-loaded saves catch up safely.
    dated_events = [e for e in project.get("events", []) if e.get("triggerMode") == "date"]
    if dated_events:
        from datetime import date, timedelta
        on_actions_dir = target / "common" / "on_actions"
        on_actions_dir.mkdir(parents=True, exist_ok=True)
        lines = ["on_actions = {", "\ton_daily = {", "\t\teffect = {"]
        for event in dated_events:
            try:
                chosen = date(int(event.get("dateYear", 1936)), int(event.get("dateMonth", 1)), int(event.get("dateDay", 1)))
            except ValueError:
                chosen = date(1936, 1, 1)
            previous = chosen - timedelta(days=1)
            tag = re.sub(r"[^A-Z]", "", str(event.get("dateCountry", "NOR")).upper())[:3] or "NOR"
            flag = "NHO_event_fired_" + re.sub(r"[^A-Za-z0-9_]", "_", event["id"])
            call_kind = event.get("kind", "country_event")
            lines.extend([
                "\t\t\tif = {",
                f"\t\t\t\tlimit = {{ original_tag = {tag} date > {previous.year}.{previous.month}.{previous.day} NOT = {{ has_country_flag = {flag} }} }}",
                f"\t\t\t\tset_country_flag = {flag}",
                f"\t\t\t\t{call_kind} = {{ id = {event['id']} }}",
                "\t\t\t}",
            ])
        lines.extend(["\t\t}", "\t}", "}", ""])
        (on_actions_dir / "NHO_editor_scheduled_events.txt").write_text("\n".join(lines), encoding="utf-8")

    if diplomacy_actions:
        dip_lines = ["add_namespace = NHO_diplomacy", ""]
        for focus, action in diplomacy_actions:
            event_id = action["_eventId"]
            target_tag = re.sub(r"[^A-Z0-9_]", "", action.get("target", "SWE"))
            kind = action.get("type", "non_aggression")
            states = [int(x) for x in re.findall(r"\d+", str(action.get("states", "")))]
            dip_lines.extend(["country_event = {", f"\tid = {event_id}", f"\ttitle = {event_id}.t", f"\tdesc = {event_id}.d", "\tpicture = GFX_report_event_generic_diplomacy", "\tis_triggered_only = yes", "\toption = {", f"\t\tname = {event_id}.a", "\t\tai_chance = { base = 50 }"])
            if kind == "non_aggression":
                dip_lines.append("\t\tdiplomatic_relation = { country = FROM relation = non_aggression_pact active = yes }")
            elif kind == "guarantee":
                dip_lines.append("\t\tgive_guarantee = FROM")
            elif kind == "demand_land":
                dip_lines.extend(f"\t\t{state} = {{ transfer_state_to = FROM }}" for state in states)
            dip_lines.extend(["\t}", "\toption = {", f"\t\tname = {event_id}.b", "\t\tai_chance = { base = 50 }"])
            if kind == "demand_land" and action.get("warOnRefusal"):
                generator = f" generator = {{ {' '.join(map(str, states))} }}" if states else ""
                dip_lines.append(f"\t\tFROM = {{ create_wargoal = {{ type = take_state_focus target = ROOT{generator} }} }}")
            dip_lines.extend(["\t}", "}", ""])
        (events_dir / "NHO_diplomacy_events.txt").write_text("\n".join(dip_lines), encoding="utf-8")

    decisions = project.get("decisions", [])
    if decisions:
        category_dir = target / "common" / "decisions" / "categories"; category_dir.mkdir(parents=True, exist_ok=True)
        (category_dir / "NHO_editor_categories.txt").write_text("NHO_editor_decision_category = { icon = GFX_decision_generic_political_address visible = { always = yes } }\n", encoding="utf-8")
        decision_dir = target / "common" / "decisions"; decision_dir.mkdir(parents=True, exist_ok=True)
        lines = ["NHO_editor_decision_category = {"]; permanent_ideas = ["ideas = {", "\tcountry = {"]; has_permanent_ideas = False
        for decision in decisions:
            did = re.sub(r"[^A-Za-z0-9_]", "_", decision["id"]); duration = max(1, int(decision.get("duration", 100))); cost = max(0, int(decision.get("cost", 0))); amount = float(decision.get("amount", 0)); effect_type = decision.get("effectType", "manpower"); linked_focus = decision.get("linkedFocus", "")
            lines.extend([f"\t{did} = {{", "\t\ticon = GFX_decision_generic_political_address", f"\t\tcost = {cost}", f"\t\tdays_remove = {duration}", f"\t\tvisible = {{ {'has_completed_focus = ' + linked_focus if linked_focus else 'always = yes'} }}"])
            completion = []
            if effect_type == "political_power": completion.append(f"add_political_power = {amount:g}")
            elif effect_type == "manpower": completion.append(f"add_manpower = {amount:g}")
            elif effect_type == "manpower_factor":
                if decision.get("permanent"):
                    idea = f"NHO_editor_decision_idea_{did}"; completion.append(f"add_ideas = {idea}"); permanent_ideas.extend([f"\t\t{idea} = {{", "\t\t\tallowed = { always = no }", "\t\t\tremoval_cost = -1", f"\t\t\tmodifier = {{ recruitable_population_factor = {amount/100:g} }}", "\t\t}"]); has_permanent_ideas = True
                else: lines.append(f"\t\tmodifier = {{ recruitable_population_factor = {amount/100:g} }}")
            completion.extend(f"recruit_character = {character['id']}" for character in project.get("characters", []) if character.get("linkedDecision") == decision["id"])
            if completion: lines.append("\t\tcomplete_effect = { " + " ".join(completion) + " }")
            lines.extend(["\t}", ""])
        lines.append("}"); (decision_dir / "NHO_editor_decisions.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
        if has_permanent_ideas:
            permanent_ideas.extend(["\t}", "}"]); ideas_dir = target / "common" / "ideas"; ideas_dir.mkdir(parents=True, exist_ok=True); (ideas_dir / "NHO_editor_decision_ideas.txt").write_text("\n".join(permanent_ideas) + "\n", encoding="utf-8")

    characters = [item for item in project.get("characters", []) if item.get("ownership") != "reference"]
    if characters:
        char_dir = target / "common" / "characters"; char_dir.mkdir(parents=True, exist_ok=True); portrait_dir = target / "gfx" / "leaders" / "NHO_editor"; portrait_dir.mkdir(parents=True, exist_ok=True); lines = ["characters = {"]
        for character in characters:
            cid = re.sub(r"[^A-Za-z0-9_]", "_", character["id"]); role = character.get("role", "General"); skill = max(1, min(10, int(character.get("skill", 2)))); attack = max(0, min(10, int(character.get("attack", skill)))); defense = max(0, min(10, int(character.get("defense", skill)))); planning = max(0, min(10, int(character.get("planning", skill)))); logistics = max(0, min(10, int(character.get("logistics", skill)))); maneuvering = max(0, min(10, int(character.get("maneuvering", skill)))); coordination = max(0, min(10, int(character.get("coordination", skill)))); portrait = f"gfx/leaders/NHO_editor/{cid}.dds"; data_url = character.get("portraitData")
            if data_url:
                image = Image.open(BytesIO(base64.b64decode(data_url.split(",", 1)[1]))).convert("RGBA"); image.thumbnail((156, 210), Image.Resampling.LANCZOS); canvas = Image.new("RGBA", (156, 210), (0, 0, 0, 0)); canvas.alpha_composite(image, ((156-image.width)//2, (210-image.height)//2)); canvas.save(target / portrait)
            lines.extend([f"\t{cid} = {{", f'\t\tname = "{character.get("name", cid).replace(chr(34), chr(39))}"']); portrait_kind = "navy" if role in ("Admiral", "Captain") else "army"
            if data_url: lines.append(f'\t\tportraits = {{ {portrait_kind} = {{ large = "{portrait}" }} }}')
            allowed = str(character.get("allowedConditions", character.get("availability", ""))).strip()
            visible = str(character.get("visibleConditions", "")).strip()
            if allowed: lines.extend(["\t\tallowed = {", *["\t\t\t" + line for line in allowed.splitlines()], "\t\t}"])
            if visible: lines.extend(["\t\tvisible = {", *["\t\t\t" + line for line in visible.splitlines()], "\t\t}"])
            traits = " ".join(re.findall(r"[A-Za-z0-9_]+", str(character.get("traits", ""))))
            trait_script = f" traits = {{ {traits} }}" if traits else ""
            roles = set(character.get("roles", [])) | {role}
            if roles & {"Admiral", "Captain"}: lines.append(f"\t\tnavy_leader = {{ skill = {skill} attack_skill = {attack} defense_skill = {defense} maneuvering_skill = {maneuvering} coordination_skill = {coordination}{trait_script} }}")
            if roles & {"General", "Field Officer"}: lines.append(f"\t\tcorps_commander = {{ skill = {skill} attack_skill = {attack} defense_skill = {defense} planning_skill = {planning} logistics_skill = {logistics}{trait_script} }}")
            for slot in character.get("advisorSlots", []):
                slot_id = re.sub(r"[^A-Za-z0-9_]", "", str(slot))
                if slot_id: lines.append(f"\t\tadvisor = {{ slot = {slot_id} cost = {max(0, int(character.get('cost', 0) or 0))} ai_will_do = {{ {character.get('aiWillDo') or 'factor = 1'} }} }}")
            raw_script = str(character.get("rawScript", "")).strip()
            preserved = preserved_unknown_script(character.get("preservedSourceScript", character.get("sourceRaw", "")), {"name", "portraits", "allowed", "visible", "corps_commander", "field_marshal", "navy_leader", "advisor", "country_leader", "cost", "ai_will_do"})
            if preserved: lines.extend("\t\t" + line for line in preserved.splitlines())
            if raw_script: lines.extend("\t\t" + line for line in raw_script.splitlines())
            lines.append("\t}")
        lines.append("}"); (char_dir / "NHO_editor_characters.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")

    national_spirits = [item for item in project.get("nationalSpirits", []) if item.get("ownership") != "reference"]
    if national_spirits:
        ideas_dir = target / "common" / "ideas"
        ideas_dir.mkdir(parents=True, exist_ok=True)
        idea_icon_dir = target / "gfx" / "interface" / "ideas"
        idea_icon_dir.mkdir(parents=True, exist_ok=True)
        interface_dir = target / "interface"
        interface_dir.mkdir(parents=True, exist_ok=True)
        idea_lines = ["ideas = {", "\tcountry = {"]
        sprite_lines = ["spriteTypes = {"]
        for spirit in national_spirits:
            sid = re.sub(r"[^A-Za-z0-9_]", "_", spirit.get("id", "NHO_idea"))
            icon_key = spirit.get("picture") or spirit.get("icon") or f"GFX_{sid}"
            allowed = str(spirit.get("allowedConditions", "always = yes")).strip() or "always = yes"
            visible = str(spirit.get("visibleConditions", "")).strip()
            idea_lines.extend([f"\t\t{sid} = {{", "\t\t\tallowed = { " + allowed + " }", f"\t\t\tremoval_cost = {int(spirit.get('removalCost', -1) or 0)}", f"\t\t\tpicture = {icon_key}"])
            if visible: idea_lines.append("\t\t\tvisible = { " + visible + " }")
            modifiers = str(spirit.get("modifiers", "")).strip()
            if modifiers:
                idea_lines.append("\t\t\tmodifier = {")
                idea_lines.extend("\t\t\t\t" + line.strip() for line in modifiers.splitlines() if line.strip())
                idea_lines.append("\t\t\t}")
            for field, block_name in (("targetedModifiers", "targeted_modifier"), ("equipmentBonuses", "equipment_bonus"), ("researchBonuses", "research_bonus")):
                content = str(spirit.get(field, "")).strip()
                if content:
                    idea_lines.append(f"\t\t\t{block_name} = {{")
                    idea_lines.extend("\t\t\t\t" + line.strip() for line in content.splitlines() if line.strip())
                    idea_lines.append("\t\t\t}")
            raw_extra = str(spirit.get("raw", "")).strip()
            preserved = preserved_unknown_script(spirit.get("preservedSourceScript", spirit.get("sourceRaw", "")), {"picture", "allowed", "visible", "removal_cost", "modifier", "targeted_modifier", "equipment_bonus", "research_bonus"})
            if preserved: idea_lines.extend("\t\t\t" + line for line in preserved.splitlines())
            if raw_extra:
                idea_lines.extend("\t\t\t" + line for line in raw_extra.splitlines())
            idea_lines.append("\t\t}")
            data_url = spirit.get("iconImage")
            if data_url:
                image = Image.open(BytesIO(base64.b64decode(data_url.split(",", 1)[1]))).convert("RGBA")
                side = min(image.size)
                left = (image.width - side) // 2; top = (image.height - side) // 2
                image = image.crop((left, top, left + side, top + side)).resize((64, 64), Image.Resampling.LANCZOS)
                filename = f"{sid}.dds"
                image.save(idea_icon_dir / filename)
                sprite_lines.extend(["\tspriteType = {", f'\t\tname = "{icon_key}"', f'\t\ttexturefile = "gfx/interface/ideas/{filename}"', "\t}"])
        idea_lines.extend(["\t}", "}"])
        (ideas_dir / "NHO_editor_national_spirits.txt").write_text("\n".join(idea_lines) + "\n", encoding="utf-8")
        sprite_lines.append("}")
        if len(sprite_lines) > 2:
            (interface_dir / "NHO_editor_national_spirits.gfx").write_text("\n".join(sprite_lines) + "\n", encoding="utf-8")

    timed_ideas = ["ideas = {", "\tcountry = {"]
    timed_localisation = []
    modifier_map = {"political_power": "political_power_gain", "stability": "stability_factor", "war_support": "war_support_factor"}
    for focus in project.get("focuses", []):
        for index, effect in enumerate(focus.get("effects", [])):
            if effect.get("category") != "political" or int(effect.get("duration", 0) or 0) <= 0: continue
            modifier = modifier_map.get(effect.get("type"))
            if not modifier: continue
            idea = timed_idea_id(focus["id"], index)
            value = float(effect.get("amount", 0)) / 100
            timed_ideas.extend([f"\t\t{idea} = {{", "\t\t\tallowed = { always = no }", "\t\t\tremoval_cost = -1", f"\t\t\tmodifier = {{ {modifier} = {value:g} }}", "\t\t}"])
            timed_localisation.extend([f' {idea}:0 "{focus.get("name", "Focus")} – Temporary Effect"', f' {idea}_desc:0 "A temporary national modifier granted by {focus.get("name", focus["id"])}."'])
    timed_ideas.extend(["\t}", "}"])
    if timed_localisation:
        ideas_dir = target / "common" / "ideas"; ideas_dir.mkdir(parents=True, exist_ok=True)
        (ideas_dir / "NHO_editor_generated_ideas.txt").write_text("\n".join(timed_ideas) + "\n", encoding="utf-8")

    event_sprites = ["spriteTypes = {"]
    event_picture_dir = target / "gfx" / "event_pictures"
    for event in project.get("events", []):
        data_url = event.get("imageData")
        if not data_url: continue
        image = Image.open(BytesIO(base64.b64decode(data_url.split(",", 1)[1]))).convert("RGBA")
        # HOI4's real event picture sizes from eventwindow.gui and the vanilla assets:
        # country/report event = 210x176, newspaper/news event = 397x153.
        target_size = (397, 153) if event.get("kind") == "news_event" else (210, 176)
        tw, th = target_size
        fit = event.get("imageFit", "cover")
        user_scale = max(0.05, float(event.get("imageScale", 100) or 100) / 100.0)
        offset_x = int(float(event.get("imageX", 0) or 0))
        offset_y = int(float(event.get("imageY", 0) or 0))
        if fit == "fill":
            resized = image.resize((max(1, int(tw * user_scale)), max(1, int(th * user_scale))), Image.Resampling.LANCZOS)
        else:
            base = max(tw / image.width, th / image.height) if fit == "cover" else min(tw / image.width, th / image.height)
            ratio = max(0.01, base * user_scale)
            resized = image.resize((max(1, round(image.width * ratio)), max(1, round(image.height * ratio))), Image.Resampling.LANCZOS)
        canvas = Image.new("RGBA", target_size, (0, 0, 0, 0))
        px = (tw - resized.width) // 2 + offset_x
        py = (th - resized.height) // 2 + offset_y
        canvas.alpha_composite(resized, (px, py))
        event_picture_dir.mkdir(parents=True, exist_ok=True)
        stem = re.sub(r"[^A-Za-z0-9_]", "_", event["id"])
        filename = f"NHO_event_{stem}.dds"
        canvas.save(event_picture_dir / filename)
        event["picture"] = f"GFX_NHO_event_{stem}"
        event_sprites.extend(["\tspriteType = {", f'\t\tname = "{event["picture"]}"', f'\t\ttexturefile = "gfx/event_pictures/{filename}"', "\t}"])
    event_sprites.append("}")
    if len(event_sprites) > 2:
        interface_dir = target / "interface"
        interface_dir.mkdir(parents=True, exist_ok=True)
        (interface_dir / "NHO_editor_event_pictures.gfx").write_text("\n".join(event_sprites) + "\n", encoding="utf-8")

    loc_dir = target / "localisation" / "english"
    loc_dir.mkdir(parents=True, exist_ok=True)
    loc = ["l_english:"]
    for focus in project.get("focuses", []):
        loc.append(f" {focus['id']}:0 \"{focus.get('name', focus['id']).replace(chr(34), chr(39))}\"")
        loc.append(f" {focus['id']}_desc:0 \"{focus.get('description', '').replace(chr(34), chr(39))}\"")
    for event in project.get("events", []):
        loc.append(f" {event.get('titleKey', event['id']+'.t')}:0 \"{event.get('title', event['id']).replace(chr(34), chr(39))}\"")
        loc.append(f" {event.get('descKey', event['id']+'.d')}:0 \"{event.get('description', '').replace(chr(34), chr(39))}\"")
        for option in event.get("options", []):
            loc.append(f" {option.get('nameKey', event['id']+'.a')}:0 \"{option.get('text', 'Continue').replace(chr(34), chr(39))}\"")
    diplomacy_names = {"non_aggression": "a non-aggression pact", "guarantee": "a guarantee of independence", "demand_land": "territorial concessions"}
    for focus, action in diplomacy_actions:
        event_id = action["_eventId"]
        request = diplomacy_names.get(action.get("type"), "a diplomatic agreement")
        loc.extend([f' {event_id}.t:0 "Norway Requests {request.title()}"', f' {event_id}.d:0 "Norway has approached us to request {request}. How shall we answer?"', f' {event_id}.a:0 "Accept the Norwegian request"', f' {event_id}.b:0 "Refuse"'])
    if project.get("decisions"):
        loc.append(' NHO_editor_decision_category:0 "National Initiatives"')
        loc.append(' NHO_editor_decision_category_desc:0 "Decisions created in HOI4 Focus Studio."')
    for decision in project.get("decisions", []):
        did = re.sub(r"[^A-Za-z0-9_]", "_", decision["id"])
        loc.append(f' {did}:0 "{decision.get("name", did).replace(chr(34), chr(39))}"')
        loc.append(f' {did}_desc:0 "{decision.get("description", "").replace(chr(34), chr(39))}"')
        if decision.get("effectType") == "manpower_factor" and decision.get("permanent"):
            idea = f"NHO_editor_decision_idea_{did}"
            loc.append(f' {idea}:0 "{decision.get("name", did)} – Permanent Effect"')
            loc.append(f' {idea}_desc:0 "A permanent national modifier granted by this decision."')
    for character in project.get("characters", []):
        cid = re.sub(r"[^A-Za-z0-9_]", "_", character["id"])
        loc.append(f' {cid}:0 \"{character.get("localisation", character.get("name", cid)).replace(chr(34), chr(39))}\"')
        if str(character.get("description", "")).strip(): loc.append(f' {cid}_desc:0 \"{str(character.get("description", "")).replace(chr(34), chr(39)).replace(chr(10), " ")}\"')
    for spirit in project.get("nationalSpirits", []):
        sid = re.sub(r"[^A-Za-z0-9_]", "_", spirit.get("id", "NHO_idea"))
        loc.append(f' {sid}:0 "{spirit.get("name", sid).replace(chr(34), chr(39))}"')
        loc.append(f' {sid}_desc:0 "{spirit.get("description", "").replace(chr(34), chr(39))}"')
    loc.extend(timed_localisation)
    (loc_dir / "NHO_editor_l_english.yml").write_text("\n".join(loc) + "\n", encoding="utf-8-sig")

    # Copy project assets first, then resolve ownership from actual staged files.
    custom_icons = PROJECT_STORAGE.icons(project["projectId"])
    goal_dir = target / "gfx" / "interface" / "goals"
    goal_dir.mkdir(parents=True, exist_ok=True)
    interface_dir = target / "interface"
    interface_dir.mkdir(parents=True, exist_ok=True)

    if custom_icons.exists():
        for icon in custom_icons.rglob("*.dds"):
            relative_icon = icon.relative_to(custom_icons)
            destination_icon = goal_dir / relative_icon
            destination_icon.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(icon, destination_icon)
    icon_report = resolve_focus_icons(project, target, custom_icons)
    report_text = json.dumps(icon_report, ensure_ascii=False, indent=2)
    (package_dir / "focus_icon_export_report.json").write_text(report_text, encoding="utf-8")
    (target / "focus_icon_export_report.json").write_text(report_text, encoding="utf-8")
    # This small sidecar makes a later folder import lossless. HOI4 ignores JSON.
    for focus in project.get("focuses", []):
        for action in focus.get("diplomacy", []):
            action.pop("_eventId", None)
    (target / "hoi4_focus_studio_project.json").write_text(json.dumps(public_project(project), ensure_ascii=False, indent=2), encoding="utf-8")
    validate_exported_mod(target)
    export_root.mkdir(parents=True, exist_ok=True)
    previous = export_root / f".previous-{folder_name}-{time.time_ns()}"
    if final_package.exists(): os.replace(final_package, previous)
    try: os.replace(package_dir, final_package)
    except Exception:
        if previous.exists(): os.replace(previous, final_package)
        raise
    if previous.exists(): shutil.rmtree(previous)
    staging_context.cleanup()
    return final_package


def _sprite_records(mod_dir: Path) -> list[dict]:
    records = []
    block_re = re.compile(r"spriteType\s*=\s*\{.*?\}", re.I | re.S)
    for gfx_file in (mod_dir / "interface").rglob("*.gfx"):
        text = gfx_file.read_text(encoding="utf-8-sig", errors="ignore")
        for match in block_re.finditer(text):
            name = re.search(r'\bname\s*=\s*"([^"]+)"', match.group(0), re.I)
            texture = re.search(r'\btexturefile\s*=\s*"([^"]+)"', match.group(0), re.I)
            if name and texture:
                records.append({"name": name.group(1), "texture": texture.group(1).replace("\\", "/").lstrip("/"), "file": gfx_file, "block": match.group(0)})
    return records


def resolve_focus_icons(project: dict, mod_dir: Path, asset_library: Path) -> list[dict]:
    """Resolve custom focus icons from files/definitions, never from key prefixes."""
    interface_dir = mod_dir / "interface"; interface_dir.mkdir(parents=True, exist_ok=True)
    goal_dir = mod_dir / "gfx" / "interface" / "goals"; goal_dir.mkdir(parents=True, exist_ok=True)
    library_sources = {f"GFX_{path.stem}": f"projects/icons/{path.relative_to(asset_library).as_posix()}" for path in asset_library.rglob("*.dds")} if asset_library.exists() else {}
    library_keys = set(library_sources)
    for focus in project.get("focuses", []):
        key = str(focus.get("icon", "")).strip()
        if key.startswith("GFX_") and focus.get("iconImage") and not any(path.stem == key[4:] for path in goal_dir.rglob("*.dds")):
            try:
                raw = base64.b64decode(str(focus["iconImage"]).split(",", 1)[1])
                image = Image.open(BytesIO(raw)).convert("RGBA").resize((95, 85), Image.Resampling.LANCZOS)
                destination = goal_dir / "project" / f"{key[4:]}.dds"
                destination.parent.mkdir(parents=True, exist_ok=True); image.save(destination)
                library_keys.add(key)
                library_sources[key] = f"project focus {focus.get('id', '')} iconImage"
            except Exception as exc:
                raise ValueError(f"Could not create DDS for {focus.get('id', 'focus')} ({key}): {exc}") from exc

    records = _sprite_records(mod_dir)
    dds_by_key = {}
    for dds in (mod_dir / "gfx").rglob("*.dds"):
        dds_by_key.setdefault(f"GFX_{dds.stem}", []).append(dds.relative_to(mod_dir).as_posix())
    focus_keys = {str(f.get("icon", "")).strip() for f in project.get("focuses", []) if str(f.get("icon", "")).strip()}
    record_keys_with_files = {
        record["name"] for record in records
        if (mod_dir / Path(record["texture"].replace("/", os.sep))).is_file()
    }
    custom_keys = library_keys | record_keys_with_files | (focus_keys & set(dds_by_key))
    generated = []
    chosen_paths = {}
    for key in sorted(focus_keys & custom_keys):
        candidates = dds_by_key.get(key, [])
        valid_records = [r for r in records if r["name"] == key and (mod_dir / Path(r["texture"].replace("/", os.sep))).is_file()]
        preferred = next((r["texture"] for r in valid_records if r["texture"] in candidates), candidates[0] if candidates else None)
        if not preferred:
            raise ValueError(f"Project-owned focus icon {key} has no DDS in the staged mod.")
        chosen_paths[key] = preferred
        key_records = [r for r in records if r["name"] == key]
        if not key_records:
            generated.extend(["\tspriteType = {", f'\t\tname = "{key}"', f'\t\ttexturefile = "{preferred}"', "\t}"])
        else:
            for gfx_file in {r["file"] for r in key_records}:
                text = gfx_file.read_text(encoding="utf-8-sig", errors="ignore")
                def normalize(match):
                    block = match.group(0)
                    name = re.search(r'\bname\s*=\s*"([^"]+)"', block, re.I)
                    if not name or name.group(1) != key:
                        return block
                    return re.sub(r'(\btexturefile\s*=\s*")[^"]+("\s*)', rf'\g<1>{preferred}\g<2>', block, count=1, flags=re.I)
                gfx_file.write_text(re.sub(r"spriteType\s*=\s*\{.*?\}", normalize, text, flags=re.I | re.S), encoding="utf-8")
    generated_file = interface_dir / "NHO_editor_generated_focus_icons.gfx"
    if generated:
        old = generated_file.read_text(encoding="utf-8-sig", errors="ignore") if generated_file.exists() else "spriteTypes = {\n}\n"
        generated_file.write_text(old.rsplit("}", 1)[0] + "\n" + "\n".join(generated) + "\n}\n", encoding="utf-8")

    records = _sprite_records(mod_dir)
    report = []
    errors = []
    for focus in project.get("focuses", []):
        key = str(focus.get("icon", "")).strip()
        owned = key in custom_keys
        key_records = [r for r in records if r["name"] == key]
        paths = {r["texture"] for r in key_records}
        chosen = chosen_paths.get(key)
        resolved = (not owned) or (bool(chosen) and (mod_dir / Path(chosen.replace("/", os.sep))).is_file() and paths == {chosen})
        if owned and not resolved:
            errors.append(f"{focus.get('id', 'focus')} ({key})")
        report.append({
            "focusId": focus.get("id", ""), "iconKey": key, "ownership": "custom" if owned else "vanilla",
            "resolved": resolved, "ddsSource": (library_sources.get(key) or (f"project/imported mod:{chosen}" if owned and chosen else None)),
            "exportedPath": chosen, "spriteDefinitionFile": next((r["file"].relative_to(mod_dir).as_posix() for r in key_records if r["texture"] == chosen), None),
        })
    if errors:
        raise ValueError("Unresolved project-owned focus icons: " + ", ".join(errors))
    return report


def install_test_build(project: dict) -> Path:
    hoi4_mods = selected_directory(project.get("hoi4ModFolder"), "Hearts of Iron IV mod")
    hoi4_mods.mkdir(parents=True, exist_ok=True)
    folder_name = re.sub(r"[^A-Za-z0-9_-]", "_", project.get("testFolder", "Norway_Remade_Test"))
    destination = hoi4_mods / folder_name
    with tempfile.TemporaryDirectory(prefix="hoi4-focus-studio-") as temp:
        staged_package = export_project(project, Path(temp))
        staged_mod = staged_package / staged_package.name
        validate_exported_mod(staged_mod)
        staged_destination = Path(temp) / folder_name
        shutil.copytree(staged_mod, staged_destination)
        staged_descriptor = staged_destination / "descriptor.mod"
        descriptor = staged_descriptor.read_text(encoding="utf-8-sig")
        version = str(project.get("exportVersion", "0.78"))
        display_name = project.get("modDisplayName", "Norway Remade")
        version_label = version if version.lower().startswith("v") else f"v{version}"
        descriptor = re.sub(r'(?m)^\s*name\s*=\s*"[^"]+"', f'name="{display_name} — {version_label}"', descriptor, count=1)
        descriptor = re.sub(r'(?m)^\s*version\s*=\s*"[^"]+"', f'version="{version}"', descriptor, count=1)
        staged_descriptor.write_text(descriptor, encoding="utf-8")
        validate_exported_mod(staged_destination)
        backup_root = PROJECT_STORAGE.backups(project["projectId"]); backup_root.mkdir(parents=True, exist_ok=True)
        if destination.exists():
            stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            shutil.make_archive(str(backup_root/f"{folder_name}_{stamp}"), "zip", destination)
            shutil.rmtree(destination)
        shutil.move(str(staged_destination), destination)
    descriptor_path = destination / "descriptor.mod"
    descriptor = descriptor_path.read_text(encoding="utf-8-sig")
    version = str(project.get("exportVersion", "0.78"))
    display_name = project.get("modDisplayName", "Norway Remade")
    version_label = version if str(version).lower().startswith('v') else f'v{version}'
    descriptor = re.sub(r'(?m)^\s*name\s*=\s*"[^"]+"', f'name="{display_name} — {version_label}"', descriptor, count=1)
    descriptor = re.sub(r'(?m)^\s*version\s*=\s*"[^"]+"', f'version="{version}"', descriptor, count=1)
    descriptor_path.write_text(descriptor, encoding="utf-8")
    external = descriptor + f'\npath="{destination.as_posix()}"\n'
    (hoi4_mods / f"{folder_name}.mod").write_text(external, encoding="utf-8")
    return destination


def validate_exported_mod(mod_dir: Path) -> None:
    """Validate a complete staged mod before an installed copy is replaced."""
    required = [mod_dir / "descriptor.mod", mod_dir / "common" / "national_focus"]
    missing = [str(path.relative_to(mod_dir)) for path in required if not path.exists()]
    if missing:
        raise ValueError("Staged mod is incomplete: " + ", ".join(missing))
    focus_files = list((mod_dir / "common" / "national_focus").glob("*.txt"))
    if not focus_files or not any("focus_tree" in path.read_text(encoding="utf-8-sig", errors="ignore") for path in focus_files):
        raise ValueError("Staged mod has no readable focus tree.")
    report_path = mod_dir / "focus_icon_export_report.json"
    if not report_path.is_file():
        raise ValueError("Staged mod is missing its focus icon export report.")
    report = json.loads(report_path.read_text(encoding="utf-8"))
    unresolved = [row.get("focusId", "focus") for row in report if row.get("ownership") == "custom" and not row.get("resolved")]
    if unresolved:
        raise ValueError("Staged mod has unresolved project-owned icons: " + ", ".join(unresolved))
    generated = mod_dir / "interface" / "NHO_editor_generated_focus_icons.gfx"
    if generated.exists():
        text = generated.read_text(encoding="utf-8-sig", errors="ignore")
        for texture in re.findall(r'texturefile\s*=\s*"([^"]+)"', text, re.I):
            if not (mod_dir / Path(texture.replace("/", os.sep))).is_file():
                raise ValueError(f"Staged mod is missing sprite texture: {texture}")


def _sprite_icon_map(text_files: dict[str, str]) -> dict[str, str]:
    """Return GFX sprite name -> normalized texture path from imported .gfx files."""
    mapping = {}
    block_re = re.compile(r"spriteType\s*=\s*\{(.*?)\}", re.I | re.S)
    name_re = re.compile(r"\bname\s*=\s*[\"']?([^\"'\s}]+)", re.I)
    tex_re = re.compile(r"\btexturefile\s*=\s*[\"']([^\"']+)[\"']", re.I)
    for filename, content in text_files.items():
        low = filename.replace('\\', '/').lower()
        if not low.endswith('.gfx'):
            continue
        for block in block_re.findall(content):
            nm = name_re.search(block)
            tx = tex_re.search(block)
            if nm and tx:
                mapping[nm.group(1)] = tx.group(1).replace('\\', '/').lstrip('/')
    return mapping


def restore_focus_icons(project: dict, binary_files: dict[str, str], text_files: dict[str, str] | None = None) -> tuple[int, int, int]:
    """Restore imported focus images and attach previews using sprite definitions when available."""
    text_files = text_files or {}
    icon_dir = PROJECT_STORAGE.icons(project["projectId"])
    icon_dir.mkdir(parents=True, exist_ok=True)
    sprite_map = _sprite_icon_map(text_files)
    by_path = {}
    by_stem = {}
    decoded = 0
    discovered = 0

    for name, data_url in (binary_files or {}).items():
        normalized = name.replace("\\", "/")
        lower = normalized.lower()
        if not (("/gfx/interface/" in lower) or lower.startswith("gfx/interface/")):
            continue
        if not lower.endswith((".dds", ".png", ".tga")):
            continue
        discovered += 1
        filename = Path(normalized).name
        stem = Path(filename).stem
        try:
            raw = base64.b64decode(data_url.split(",", 1)[1])
            source = Image.open(BytesIO(raw)).convert("RGBA")
            preview = source.copy()
            preview.thumbnail((95, 85), Image.Resampling.LANCZOS)
            canvas = Image.new("RGBA", (95, 85), (0, 0, 0, 0))
            canvas.alpha_composite(preview, ((95-preview.width)//2, (85-preview.height)//2))
            out_png = BytesIO(); canvas.save(out_png, format="PNG")
            preview_url = "data:image/png;base64," + base64.b64encode(out_png.getvalue()).decode("ascii")
            canvas.save(icon_dir / f"{stem}.png")
            try:
                canvas.save(icon_dir / f"{stem}.dds")
            except Exception:
                pass
            # Store several keys because mod folders may include a selected-root prefix.
            nlow = normalized.lower().lstrip('/')
            marker = nlow.find('gfx/interface/')
            rel = nlow[marker:] if marker >= 0 else nlow
            by_path[rel] = preview_url
            by_path[rel.replace('gfx/interface/goals/', 'gfx/interface/')] = preview_url
            by_stem[stem.lower()] = preview_url
            decoded += 1
        except Exception:
            continue

    attached = 0
    bundled = ROOT / "icon-previews"
    for focus in project.get("focuses", []):
        gfx = focus.get("icon", "")
        preview = None
        tex = sprite_map.get(gfx)
        if tex:
            tlow = tex.lower().lstrip('/')
            preview = by_path.get(tlow) or by_stem.get(Path(tlow).stem.lower())
        if not preview:
            stem_guess = gfx[4:] if gfx.startswith('GFX_') else gfx
            preview = by_stem.get(stem_guess.lower())
        if not preview:
            bundled_png = bundled / f"{gfx}.png"
            if bundled_png.exists():
                try:
                    preview = "data:image/png;base64," + base64.b64encode(bundled_png.read_bytes()).decode("ascii")
                except Exception:
                    preview = None
        if preview:
            focus["iconImage"] = preview
            attached += 1
    return discovered, decoded, attached


def import_mod_files(files: dict[str, str], binary_files: dict[str, str] | None = None, current_project: dict | None = None) -> tuple[dict, str]:
    """Import either a lossless Studio sidecar or an older scripted mod tree."""
    binary_files = binary_files or {}
    current = current_project or load_current_project()
    for name, content in files.items():
        if name.replace("\\", "/").lower().endswith("hoi4_focus_studio_project.json"):
            imported = json.loads(content)
            if not isinstance(imported.get("focuses"), list):
                raise ValueError("The Studio project file does not contain a focus list.")
            imported.setdefault("stateCatalog", current.get("stateCatalog", []))
            imported.setdefault("events", [])
            imported.setdefault("decisions", [])
            imported.setdefault("characters", [])
            imported["projectId"] = current["projectId"]
            discovered, decoded, attached = restore_focus_icons(imported, binary_files, files)
            note = f" Found {discovered} image files, decoded {decoded}, and attached {attached} focus pictures."
            return imported, "Lossless Studio project data was found and restored." + note

    normalized = {name.replace("\\", "/"): content for name, content in files.items()}
    focus_name = next((name for name in normalized if name.lower().endswith("/common/national_focus/norway.txt") or name.lower() == "common/national_focus/norway.txt"), None)
    if not focus_name:
        raise ValueError("No common/national_focus/norway.txt was found in that folder.")
    marker = "common/national_focus/norway.txt"
    prefix = focus_name[:-len(marker)]
    import_root = ROOT / "imports" / f"folder_{int(time.time() * 1000)}"
    for name, content in normalized.items():
        relative = name[len(prefix):] if name.startswith(prefix) else name
        relative = relative.lstrip("/")
        if ".." in Path(relative).parts:
            continue
        destination = import_root / Path(relative)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(content, encoding="utf-8")
    recovery_spec = importlib.util.spec_from_file_location("recover_exported_mod", ROOT / "recover_exported_mod.py")
    recovery = importlib.util.module_from_spec(recovery_spec)
    recovery_spec.loader.exec_module(recovery)
    loc = recovery.localisation(import_root)
    focuses = recovery.recover_focuses(import_root, loc)
    events = recovery.recover_events(import_root, loc, focuses)
    recovery.validate(focuses)
    imported = {"projectId": current["projectId"], "title": current.get("title", "New HOI4 Project"), "exportVersion": current.get("exportVersion", "v077_editor_export"), "eventNamespace": "NHO_editor", "focuses": focuses, "events": events, "decisions": [], "characters": [], "nationalSpirits": [], "stateCatalog": current.get("stateCatalog", [])}
    discovered, decoded, attached = restore_focus_icons(imported, binary_files, normalized)
    note = f" Found {discovered} image files, decoded {decoded}, and attached {attached} focus pictures."
    return imported, "Older mod detected: focus scripts and localisation were reconstructed." + note


def _release_request(url: str) -> bytes:
    request = urllib.request.Request(url, headers={"Accept": "application/vnd.github+json", "User-Agent": f"HOI4-Focus-Studio/{APP_VERSION}"})
    with urllib.request.urlopen(request, timeout=20) as response:
        return response.read()


def check_for_updates() -> dict:
    try:
        release = json.loads(_release_request(GITHUB_RELEASES_API))
    except (OSError, urllib.error.URLError, json.JSONDecodeError) as exc:
        return {"ok": False, "offline": True, "installedVersion": APP_VERSION, "error": f"GitHub could not be reached: {exc}"}
    assets = release.get("assets", [])
    zip_asset = next((a for a in assets if a.get("name", "").lower().endswith("windows.zip")), None)
    checksum_asset = next((a for a in assets if a.get("name", "").lower().endswith(".sha256")), None)
    latest = str(release.get("tag_name", "")).lstrip("v")
    current_tuple = tuple(int(x) for x in re.findall(r"\d+", APP_VERSION))
    latest_tuple = tuple(int(x) for x in re.findall(r"\d+", latest))
    return {"ok": True, "installedVersion": APP_VERSION, "latestVersion": latest, "updateAvailable": latest_tuple > current_tuple,
            "releaseNotes": release.get("body", ""), "releaseUrl": release.get("html_url", ""), "downloadReady": bool(zip_asset and checksum_asset),
            "zipUrl": zip_asset.get("browser_download_url") if zip_asset else None, "zipName": zip_asset.get("name") if zip_asset else None,
            "checksumUrl": checksum_asset.get("browser_download_url") if checksum_asset else None}


def verify_update_zip(zip_path: Path, checksum_path: Path) -> str:
    if not zip_path.is_file() or not checksum_path.is_file():
        raise ValueError("The update ZIP and its .sha256 checksum file are both required.")
    expected = re.search(r"\b([a-fA-F0-9]{64})\b", checksum_path.read_text(encoding="utf-8-sig", errors="ignore"))
    if not expected:
        raise ValueError("The checksum file does not contain a valid SHA-256 value.")
    digest = hashlib.sha256()
    with zip_path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    actual = digest.hexdigest()
    if actual.lower() != expected.group(1).lower():
        raise ValueError("Update verification failed: the SHA-256 checksum does not match.")
    return actual


def download_update() -> dict:
    release = check_for_updates()
    if not release.get("ok") or not release.get("downloadReady"):
        raise ValueError(release.get("error") or "This release has no Windows ZIP and checksum.")
    UPDATE_ROOT.mkdir(parents=True, exist_ok=True)
    zip_path = UPDATE_ROOT / Path(release["zipName"]).name
    checksum_path = UPDATE_ROOT / (zip_path.name + ".sha256")
    zip_path.write_bytes(_release_request(release["zipUrl"]))
    checksum_path.write_bytes(_release_request(release["checksumUrl"]))
    return {"ok": True, "version": release["latestVersion"], "path": str(zip_path), "sha256": verify_update_zip(zip_path, checksum_path)}


def stage_update(zip_path: Path, checksum_path: Path) -> Path:
    verify_update_zip(zip_path, checksum_path)
    stage = UPDATE_ROOT / "staged"
    if stage.exists():
        shutil.rmtree(stage)
    stage.mkdir(parents=True)
    with zipfile.ZipFile(zip_path) as archive:
        for member in archive.infolist():
            destination = (stage / member.filename).resolve()
            if stage.resolve() not in destination.parents and destination != stage.resolve():
                raise ValueError("The update ZIP contains an unsafe path.")
        archive.extractall(stage)
    children = list(stage.iterdir())
    payload = children[0] if len(children) == 1 and children[0].is_dir() else stage
    if not ((payload / "HOI4 Focus Studio.exe").exists() or (payload / "server.py").exists()):
        raise ValueError("The ZIP is not a valid HOI4 Focus Studio Windows update.")
    return payload


def launch_update(zip_path: Path, checksum_path: Path) -> None:
    payload = stage_update(zip_path, checksum_path)
    program_root = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else ROOT
    executable = program_root / "HOI4 Focus Studio.exe" if getattr(sys, "frozen", False) else Path(sys.executable).resolve()
    arguments = ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(ROOT / "apply_update.ps1"), "-ProcessId", str(os.getpid()), "-InstallRoot", str(program_root), "-StagedRoot", str(payload), "-Executable", str(executable)]
    if not getattr(sys, "frozen", False):
        arguments.extend(["-ServerScript", str(ROOT / "server.py")])
    subprocess.Popen(arguments, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))


class Handler(SimpleHTTPRequestHandler):
    def end_headers(self):
        self.send_header("Cache-Control", "no-store, no-cache, must-revalidate")
        super().end_headers()

    def translate_path(self, path: str) -> str:
        path = path.split("?", 1)[0]
        if path == "/":
            path = "/index.html"
        return str(ROOT / path.lstrip("/"))

    def send_json(self, data, status=200):
        payload = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self):
        request_path = self.path.split("?", 1)[0].rstrip("/") or "/"
        if request_path == "/api/version":
            self.send_json({"version": APP_VERSION, "port": APP_PORT})
            return
        if request_path == "/api/runtime":
            health = SOURCE_CATALOG.health()
            self.send_json({
                "version": APP_VERSION,
                "pid": os.getpid(),
                "executable": str(Path(sys.executable).resolve()),
                "serverRoot": str(ROOT.resolve()),
                "storageRoot": str(APP_DATA_ROOT.resolve()),
                "requiredStorageRoot": os.environ.get("HOI4_FOCUS_STUDIO_REQUIRED_STORAGE_ROOT", ""),
                "cataloguePath": str(SOURCE_CATALOG.path.resolve()),
                "port": APP_PORT,
                "instanceToken": APP_INSTANCE_TOKEN,
                "cache": health,
                "renderAllowed": bool(health["compatible"] and health["localisations"] > 0 and health["iconAssets"] > 0),
            })
            return
        if request_path == "/api/update/check":
            self.send_json(check_for_updates())
            return
        if request_path == "/api/project":
            self.send_json(load_current_project())
            return
        if request_path == "/api/base-source/status":
            project = load_current_project()
            try:
                source = require_base_source(PROJECT_STORAGE, project)
                self.send_json({"ok": True, "available": True, "projectId": project["projectId"], "files": sum(1 for p in source.rglob("*") if p.is_file())})
            except BaseSourceRequired as exc:
                self.send_json({"ok": True, "available": False, "projectId": project["projectId"], "message": str(exc)})
            return
        if request_path == "/api/sources":
            registry = SOURCE_REGISTRY.migrate_catalog(SOURCE_CATALOG.path)
            packages = registry["packages"]
            sources = SOURCE_CATALOG.sources() if SOURCE_CATALOG.path.exists() else []
            for source in sources:
                package = next((item for item in packages if source["id"] in item.get("sourceIds", ())), None)
                source.update({"packageId": package.get("id", "") if package else "", "currentSourcePath": package.get("path", "") if package else "", "sourcePathExists": bool(package and Path(package.get("path", "")).is_file()), "registered": bool(package and package.get("enabled"))})
            self.send_json({"sources": sources, "packages": packages, "cache": SOURCE_CATALOG.health()})
            return
        if request_path == "/api/playset-snapshots":
            self.send_json({"snapshots": list_snapshots(PLAYSET_ROOT)})
            return
        if request_path == "/api/catalog/search":
            query = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
            entity_type = query.get("type", [""])[0]; text = query.get("q", [""])[0]
            self.send_json({"items": SOURCE_CATALOG.search(entity_type, text, min(250, int(query.get("limit", [100])[0]))) if SOURCE_CATALOG.path.exists() else []})
            return
        if request_path == "/api/technology-tree":
            query = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
            profile = query.get("profile", ["vanilla"])[0]; country = query.get("country", ["NOR"])[0]
            category = query.get("category", [""])[0]; text = query.get("q", [""])[0]; include_hidden = query.get("includeHidden", ["0"])[0] == "1"
            health = SOURCE_CATALOG.health()
            if not health["compatible"]:
                self.send_json({"items": [], "categories": [], "profile": profile, "country": country, "cacheIncompatible": True, "cache": health})
            else:
                self.send_json(SOURCE_CATALOG.technology_tree(profile, country, category, text, include_hidden) | {"cache": health})
            return
        if request_path == "/api/source-asset":
            query = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
            source = re.sub(r"[^A-Za-z0-9_.-]", "", query.get("source", [""])[0]); name = Path(query.get("name", [""])[0]).name
            asset = (SOURCE_ROOT / "assets" / source / name).resolve(); expected = (SOURCE_ROOT / "assets").resolve()
            if not str(asset).startswith(str(expected)) or not asset.is_file(): self.send_error(404); return
            payload = asset.read_bytes(); self.send_response(200); self.send_header("Content-Type", "image/png" if asset.suffix.lower()==".png" else "image/vnd-ms.dds"); self.send_header("Content-Length", str(len(payload))); self.end_headers(); self.wfile.write(payload); return
        if request_path == "/api/source-icon":
            query = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
            source = re.sub(r"[^A-Za-z0-9_.-]", "", query.get("source", [""])[0]); name = Path(query.get("name", [""])[0]).name
            asset = (SOURCE_ROOT / "assets" / source / name).resolve(); assets_root = (SOURCE_ROOT / "assets").resolve()
            if not str(asset).startswith(str(assets_root)) or not asset.is_file(): self.send_error(404); return
            preview = SOURCE_ROOT / "previews" / source / (asset.stem + ".png")
            if not preview.is_file() or preview.stat().st_mtime_ns < asset.stat().st_mtime_ns:
                preview.parent.mkdir(parents=True, exist_ok=True)
                temporary = preview.with_suffix(".tmp.png")
                with Image.open(asset) as image: image.convert("RGBA").save(temporary, "PNG")
                os.replace(temporary, preview)
            payload = preview.read_bytes(); self.send_response(200); self.send_header("Content-Type", "image/png"); self.send_header("Cache-Control", "public, max-age=31536000, immutable"); self.send_header("Content-Length", str(len(payload))); self.end_headers(); self.wfile.write(payload); return
        super().do_GET()

    def do_POST(self):
        request_path = self.path.split("?", 1)[0].rstrip("/") or "/"
        length = int(self.headers.get("Content-Length", 0))
        try:
            data = json.loads(self.rfile.read(length))
            if request_path in {"/api/project", "/api/export", "/api/install"}:
                data["projectId"] = load_current_project()["projectId"]
            if request_path == "/api/project":
                data, _ = migrate_project(data)
                PROJECT_STORAGE.save(public_project(data))
                self.send_json({"ok": True})
            elif request_path == "/api/icon":
                name = re.sub(r"[^A-Za-z0-9_-]", "_", data.get("name", "custom_focus"))
                raw = base64.b64decode(data["png"].split(",", 1)[1])
                image = Image.open(BytesIO(raw)).convert("RGBA").resize((95, 85), Image.Resampling.LANCZOS)
                project = load_current_project()
                out = PROJECT_STORAGE.icons(project["projectId"])
                out.mkdir(parents=True, exist_ok=True)
                image.save(out / f"{name}.png")
                image.save(out / f"{name}.dds")
                self.send_json({"ok": True, "path": str(out / f'{name}.dds')})
            elif request_path == "/api/export":
                export_root = selected_directory(data.get("exportPath"), "export destination")
                current_version = str(data.get("exportVersion", "v0_80"))
                bump = str(data.get("versionBump", "minor"))
                export_version = _next_export_version(current_version, bump)
                data["exportVersion"] = export_version
                exported = export_project(data, export_root)
                zip_path = _make_versioned_zip(export_root, exported)
                PROJECT_STORAGE.save(public_project(data))
                opened = False
                if data.get("openExportFolder", True):
                    try:
                        os.startfile(str(exported))
                        opened = True
                    except Exception:
                        try:
                            subprocess.Popen(["explorer", str(exported)])
                            opened = True
                        except Exception:
                            opened = False
                self.send_json({"ok": True, "path": str(exported), "zipPath": str(zip_path), "version": export_version, "opened": opened})
            elif request_path == "/api/install":
                self.send_json({"ok": True, "path": str(install_test_build(data)), "note": "Enable Norway Remade in the HOI4 launcher. The stable .mod file is updated automatically."})
            elif request_path == "/api/select-folder":
                import tkinter as tk
                from tkinter import filedialog
                window = tk.Tk(); window.withdraw(); window.attributes("-topmost", True)
                chosen = filedialog.askdirectory(title=str(data.get("title", "Choose folder")), mustexist=False)
                window.destroy()
                self.send_json({"ok": True, "path": chosen})
            elif request_path in {"/api/base-source/select-folder", "/api/base-source/select-zip"}:
                import tkinter as tk
                from tkinter import filedialog
                window = tk.Tk(); window.withdraw(); window.attributes("-topmost", True)
                if request_path.endswith("select-zip"):
                    chosen = filedialog.askopenfilename(title="Choose complete old mod ZIP", filetypes=[("ZIP archive", "*.zip")])
                else:
                    chosen = filedialog.askdirectory(title="Choose complete old mod folder", mustexist=True)
                window.destroy(); self.send_json({"ok": True, "path": chosen})
            elif request_path == "/api/base-source/recover":
                project = load_current_project()
                self.send_json(recover_base_source(PROJECT_STORAGE, project["projectId"], Path(str(data.get("path", "")))))
            elif request_path == "/api/update/download":
                self.send_json(download_update())
            elif request_path == "/api/update/select-zip":
                import tkinter as tk
                from tkinter import filedialog
                window = tk.Tk(); window.withdraw(); window.attributes("-topmost", True)
                chosen = filedialog.askopenfilename(title="Choose HOI4 Focus Studio update ZIP", filetypes=[("ZIP update", "*.zip")])
                window.destroy()
                self.send_json({"ok": True, "path": chosen})
            elif request_path == "/api/source/select":
                self.send_json({"ok": True, "path": choose_source_archive()})
            elif request_path == "/api/source/import":
                chosen = Path(str(data.get("path", ""))).resolve()
                if not chosen.exists(): raise ValueError("The selected source archive does not exist.")
                SOURCE_REGISTRY.migrate_catalog(SOURCE_CATALOG.path)
                package, inspected = SOURCE_REGISTRY.register(chosen)
                try: rebuilt = rebuild_source_cache(SOURCE_ROOT, SOURCE_REGISTRY.enabled_paths())
                except Exception as exc:
                    self.send_json({"ok": True, "package": package, "sources": inspected["sources"], "rebuildFailed": True, "rebuildError": str(exc)}); return
                self.send_json({"ok": True, "package": package, "sources": inspected["sources"], "rebuild": rebuilt})
            elif request_path == "/api/source/reselect":
                chosen = Path(str(data.get("path", ""))).resolve()
                if not chosen.exists(): raise ValueError("The selected source archive does not exist.")
                SOURCE_REGISTRY.migrate_catalog(SOURCE_CATALOG.path)
                package, inspected = SOURCE_REGISTRY.register(chosen, str(data.get("packageId", "")))
                try: rebuilt = rebuild_source_cache(SOURCE_ROOT, SOURCE_REGISTRY.enabled_paths())
                except Exception as exc:
                    self.send_json({"ok": True, "package": package, "sources": inspected["sources"], "rebuildFailed": True, "rebuildError": str(exc)}); return
                self.send_json({"ok": True, "package": package, "sources": inspected["sources"], "rebuild": rebuilt})
            elif request_path == "/api/source/recover-local":
                SOURCE_REGISTRY.migrate_catalog(SOURCE_CATALOG.path)
                identifier = str(data.get("packageId", "")); matches = SOURCE_REGISTRY.recovery_candidates(identifier, known_source_package_roots())
                if len(matches) != 1:
                    self.send_json({"ok": True, "recovered": False, "matches": matches}); return
                package, inspected = SOURCE_REGISTRY.register(matches[0]["path"], identifier)
                try: rebuilt = rebuild_source_cache(SOURCE_ROOT, SOURCE_REGISTRY.enabled_paths())
                except Exception as exc:
                    self.send_json({"ok": True, "recovered": True, "package": package, "sources": inspected["sources"], "rebuildFailed": True, "rebuildError": str(exc)}); return
                self.send_json({"ok": True, "recovered": True, "package": package, "sources": inspected["sources"], "rebuild": rebuilt})
            elif request_path == "/api/source/remove":
                SOURCE_REGISTRY.migrate_catalog(SOURCE_CATALOG.path)
                package = SOURCE_REGISTRY.remove(str(data.get("packageId", "")))
                paths = SOURCE_REGISTRY.enabled_paths(); rebuilt = None
                if paths:
                    try: rebuilt = rebuild_source_cache(SOURCE_ROOT, paths)
                    except Exception as exc:
                        self.send_json({"ok": True, "package": package, "rebuildFailed": True, "rebuildError": str(exc)}); return
                self.send_json({"ok": True, "package": package, "rebuild": rebuilt})
            elif request_path == "/api/source/rebuild":
                SOURCE_REGISTRY.migrate_catalog(SOURCE_CATALOG.path)
                recovery = recover_missing_registered_sources()
                if recovery["unresolved"]:
                    names = ", ".join(item["name"] for item in recovery["unresolved"])
                    raise ValueError(f"Registered source package is missing and could not be recovered automatically: {names}. Use Reselect source.")
                self.send_json({"ok": True, "automaticRecovery": recovery["recovered"]} | rebuild_source_cache(SOURCE_ROOT, SOURCE_REGISTRY.enabled_paths()))
            elif request_path == "/api/playset/select-folders":
                import tkinter as tk
                from tkinter import filedialog
                window = tk.Tk(); window.withdraw(); window.attributes("-topmost", True)
                chosen = filedialog.askdirectory(title="Choose an installed mod folder for the frozen playset snapshot", mustexist=True)
                window.destroy(); self.send_json({"ok": True, "path": chosen})
            elif request_path == "/api/playset/snapshot":
                sources = data.get("sources", [])
                if not isinstance(sources, list) or not sources: raise ValueError("Choose at least one source folder in playset load order.")
                self.send_json({"ok": True, "snapshot": create_playset_snapshot(PLAYSET_ROOT, str(data.get("name", "Custom playset snapshot")), sources)})
            elif request_path == "/api/foreign-technology/preview":
                self.send_json({"ok": True} | foreign_link_preview(data))
            elif request_path == "/api/technology/diagnostics/export":
                diagnostics = data.get("diagnostics", {})
                if not isinstance(diagnostics, dict): raise ValueError("Technology diagnostics are missing.")
                report_root = APP_DATA_ROOT / "diagnostics"; report_root.mkdir(parents=True, exist_ok=True)
                report = report_root / f"technology-source-{int(time.time())}.txt"
                lines = ["HOI4 Focus Studio Technology Source Diagnostics", ""] + [f"{key}: {json.dumps(value, ensure_ascii=False) if isinstance(value, (list, dict)) else value}" for key, value in diagnostics.items()]
                report.write_text("\n".join(lines) + "\n", encoding="utf-8")
                self.send_json({"ok": True, "path": str(report)})
            elif request_path == "/api/technology/validate":
                checked, _ = migrate_project(data)
                errors, warnings = validate_project_technologies(checked, catalog_lookup)
                self.send_json({"ok": not errors, "errors": errors, "warnings": warnings})
            elif request_path == "/api/update/install":
                zip_path = Path(str(data.get("path", ""))).resolve()
                checksum_path = Path(str(data.get("checksumPath") or (str(zip_path) + ".sha256"))).resolve()
                launch_update(zip_path, checksum_path)
                self.send_json({"ok": True, "message": "Update verified. Studio will close, replace program files, and reopen."})
                threading.Timer(0.5, self.server.shutdown).start()
            elif request_path == "/api/import-mod":
                imported, mode = import_mod_files(data.get("files", {}), data.get("binaryFiles", {}), load_current_project())
                PROJECT_STORAGE.save(public_project(imported))
                self.send_json({"ok": True, "project": imported, "mode": mode})
            else:
                self.send_json({"error": "Unknown endpoint"}, 404)
        except BaseSourceRequired as exc:
            self.send_json({"error": str(exc), "code": exc.code, "recoveryRequired": True}, 409)
        except Exception as exc:
            self.send_json({"error": str(exc)}, 500)


if __name__ == "__main__":
    server = ThreadingHTTPServer(("127.0.0.1", APP_PORT), Handler)
    APP_PORT = server.server_address[1]
    query = "?instance=" + urllib.parse.quote(APP_INSTANCE_TOKEN) if APP_INSTANCE_TOKEN else ""
    if os.environ.get("HOI4_FOCUS_STUDIO_NO_BROWSER") != "1":
        threading.Timer(0.7, lambda: webbrowser.open(f"http://127.0.0.1:{APP_PORT}/{query}")).start()
    print(f"HOI4 Focus Studio is running at http://127.0.0.1:{APP_PORT}")
    print("Close this window to stop it.")
    server.serve_forever()
