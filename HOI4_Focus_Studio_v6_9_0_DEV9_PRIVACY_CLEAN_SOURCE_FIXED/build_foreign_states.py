"""Build the Europe + Canada state picker used by Focus Studio diplomacy."""

import json
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parent
GAME = Path(r"E:\SteamLibrary\steamapps\common\Hearts of Iron IV")
OUT = ROOT / "data" / "europe_canada_states.json"
TAGS = {
    "ALB", "AUS", "BEL", "BUL", "CAN", "CZE", "DEN", "ENG", "EST", "FIN",
    "FRA", "GER", "GRE", "HOL", "HUN", "IRE", "ITA", "LAT", "LIT", "LUX",
    "NOR", "POL", "POR", "ROM", "SOV", "SPR", "SWE", "SWI", "TUR", "YUG",
}


def localisation():
    names = {}
    folder = GAME / "localisation" / "english"
    for path in folder.rglob("*.yml"):
        for line in path.read_text(encoding="utf-8-sig", errors="ignore").splitlines():
            match = re.match(r'\s*(STATE_\d+):\d*\s+"(.*)"\s*$', line)
            if match:
                names[match.group(1)] = re.sub(r"\$(?:HIGHLIGHT|white|blue|red|green)\$|\$RESET\$", "", match.group(2))
    return names


def main():
    names = localisation()
    states = []
    for path in (GAME / "history" / "states").glob("*.txt"):
        text = path.read_text(encoding="utf-8-sig", errors="ignore")
        sid = re.search(r"(?m)^\s*id\s*=\s*(\d+)", text)
        owner = re.search(r"(?m)^\s*owner\s*=\s*([A-Z0-9_]+)", text)
        manpower = re.search(r"(?m)^\s*manpower\s*=\s*(\d+)", text)
        if not sid or not owner or owner.group(1) not in TAGS:
            continue
        state_id = int(sid.group(1))
        states.append({
            "id": state_id,
            "name": names.get(f"STATE_{state_id}", path.stem.split("-", 1)[-1].strip()),
            "owner": owner.group(1),
            "population": int(manpower.group(1)) if manpower else 0,
        })
    states.sort(key=lambda state: (state["owner"], state["name"], state["id"]))
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(states, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Wrote {len(states)} states to {OUT}")


if __name__ == "__main__":
    main()
