import json, os, re
from pathlib import Path
from PIL import Image
from project_storage import ProjectStorage, default_app_data_root
from base_source import require_base_source

ROOT = Path(__file__).resolve().parent
GAME = Path(os.environ.get("HOI4_GAME_PATH", ""))
if not os.environ.get("HOI4_GAME_PATH"):
    raise SystemExit("Set HOI4_GAME_PATH to your Hearts of Iron IV installation folder.")
STORAGE = ProjectStorage(default_app_data_root(), ROOT / "projects" / "default_project.json")
project = STORAGE.load()
MOD = require_base_source(STORAGE, project)
OUT = ROOT / "icon-previews"
OUT.mkdir(exist_ok=True)

wanted = {f.get("icon", "") for f in project["focuses"]}
mappings = {}

def scan_gfx(root):
    if not root.exists(): return
    for path in root.rglob("*.gfx"):
        text = path.read_text(encoding="utf-8-sig", errors="ignore")
        for block in re.findall(r"spriteType\s*=\s*\{.*?\}", text, re.S | re.I):
            name = re.search(r'name\s*=\s*"([^\"]+)"', block, re.I)
            texture = re.search(r'texturefile\s*=\s*"([^\"]+)"', block, re.I)
            if name and texture and name.group(1) in wanted:
                mappings[name.group(1)] = (texture.group(1).replace("/", "\\"), root)

scan_gfx(GAME / "interface")
scan_gfx(MOD / "interface")

made = 0
for focus in project["focuses"]:
    key = focus.get("icon", "")
    item = mappings.get(key)
    if not item: continue
    texture, source_root = item
    candidates = []
    if source_root == GAME / "interface": candidates.append(GAME / texture)
    else: candidates.extend([MOD / texture, GAME / texture])
    source = next((p for p in candidates if p.exists()), None)
    if not source: continue
    try:
        image = Image.open(source).convert("RGBA")
        destination = OUT / f"{key}.png"
        image.save(destination)
        focus["iconImage"] = f"/icon-previews/{destination.name}"
        made += 1
    except Exception as exc:
        print(f"Could not convert {source}: {exc}")

STORAGE.save(project)
print(f"Created {made} icon previews from {len(wanted)} focus icon keys")
