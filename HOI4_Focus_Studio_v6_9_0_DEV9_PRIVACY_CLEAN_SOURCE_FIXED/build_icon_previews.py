import json, re
from pathlib import Path
from PIL import Image

ROOT = Path(__file__).resolve().parent
GAME = Path(r"E:\SteamLibrary\steamapps\common\Hearts of Iron IV")
MOD = ROOT / "base_mod" / "Norwegian_Kings_Yes_DLC_Tree_Test"
PROJECT = ROOT / "projects" / "default_project.json"
OUT = ROOT / "icon-previews"
OUT.mkdir(exist_ok=True)

project = json.loads(PROJECT.read_text(encoding="utf-8"))
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

PROJECT.write_text(json.dumps(project, ensure_ascii=False, indent=2), encoding="utf-8")
print(f"Created {made} icon previews from {len(wanted)} focus icon keys")
