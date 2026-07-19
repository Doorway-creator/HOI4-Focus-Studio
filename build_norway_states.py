import json, os, re
from pathlib import Path

ROOT = Path(__file__).resolve().parent
GAME = Path(os.environ.get("HOI4_GAME_PATH", ""))
if not os.environ.get("HOI4_GAME_PATH"):
    raise SystemExit("Set HOI4_GAME_PATH to your Hearts of Iron IV installation folder.")
PROJECT = ROOT / "projects" / "default_project.json"

names = {}
for path in (GAME / "localisation" / "english").rglob("*state*english.yml"):
    for line in path.read_text(encoding="utf-8-sig", errors="ignore").splitlines():
        match = re.match(r'\s*(STATE_\d+):\d*\s+"(.*)"', line)
        if match: names[match.group(1)] = match.group(2)

states = []
for path in (GAME / "history" / "states").glob("*.txt"):
    text = path.read_text(encoding="utf-8-sig", errors="ignore")
    if not re.search(r"(?m)^\s*owner\s*=\s*NOR\s*$", text): continue
    sid = re.search(r"(?m)^\s*id\s*=\s*(\d+)", text)
    manpower = re.search(r"(?m)^\s*manpower\s*=\s*(\d+)", text)
    provinces = re.search(r"provinces\s*=\s*\{([^}]*)\}", text, re.S)
    if not sid: continue
    number = int(sid.group(1))
    display_name = names.get(f"STATE_{number}", path.stem)
    if number == 143: display_name = "Trøndelag"
    states.append({
        "id": number, "name": display_name,
        "population": int(manpower.group(1)) if manpower else 0,
        "provinces": [int(x) for x in re.findall(r"\d+", provinces.group(1))] if provinces else []
    })

states.sort(key=lambda x: x["name"])
project = json.loads(PROJECT.read_text(encoding="utf-8"))
project["stateCatalog"] = states
PROJECT.write_text(json.dumps(project, ensure_ascii=False, indent=2), encoding="utf-8")
print(f"Added {len(states)} Norwegian states")
