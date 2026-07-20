from __future__ import annotations


def resolve(definitions: list[dict]) -> list[dict]:
    """Annotate layered definitions without losing shadowed sources."""
    groups = {}
    for item in definitions: groups.setdefault((item["entityType"], item["id"]), []).append(item)
    output = []
    for group in groups.values():
        ordered = sorted(group, key=lambda item: item.get("loadOrder", 0))
        conflict = len({item.get("raw", "") for item in ordered}) > 1
        for index, item in enumerate(ordered):
            output.append(item | {"overridden": index < len(ordered)-1, "conflict": conflict, "resolved": index == len(ordered)-1})
    return output
