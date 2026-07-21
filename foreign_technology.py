from __future__ import annotations

import re


VALID_RESULTS = {"equipment_shipment", "research_bonus", "direct_unlock", "project_adaptation"}
UNSUPPORTED_RESULTS = {"temporary_licence", "permanent_rights", "blueprints"}


def _identifier(value: object, label: str) -> str:
    text = str(value or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", text):
        raise ValueError(f"Invalid {label} identifier.")
    return text


def foreign_link_preview(link: dict) -> dict:
    """Render only conservative, source-independent HOI4 syntax.

    Licensing is deliberately not guessed: HOI4 licensing behavior varies with the
    selected source environment and normally needs events/decisions on both sides.
    """
    technology = _identifier(link.get("technologyId"), "technology")
    source_country = _identifier(link.get("sourceCountry"), "country")
    conditions = link.get("conditions", {})
    available, visible, bypass = [], [], []
    researched = f"{source_country} = {{ has_tech = {technology} }}"
    if conditions.get("availableWhenResearched"): available.append(researched)
    if conditions.get("visibleWhenResearched"): visible.append(researched)
    if conditions.get("bypassWhenResearched"): bypass.append(researched)
    if conditions.get("requireCountryExists"): available.append(f"country_exists = {source_country}")
    if conditions.get("requireNotCapitulated"): available.append(f"{source_country} = {{ has_capitulated = no }}")
    if conditions.get("requireNotAtWar"): available.append("has_war = no")

    result = str(link.get("result", "")).strip()
    effects, limitation = [], ""
    if result == "direct_unlock":
        effects.append(f"set_technology = {{ {technology} = 1 }}")
    elif result == "research_bonus":
        bonus = max(0.0, min(10.0, float(link.get("bonus", 0.5))))
        effects.append(f"add_tech_bonus = {{ name = HFS_foreign_{technology}_bonus bonus = {bonus:g} uses = 1 technology = {technology} }}")
    elif result == "equipment_shipment":
        equipment = _identifier(link.get("equipmentId"), "equipment")
        amount = max(1, int(link.get("amount", 100)))
        effects.append(f"add_equipment_to_stockpile = {{ type = {equipment} amount = {amount} producer = {source_country} }}")
    elif result == "project_adaptation":
        limitation = "Create and validate the project-owned adaptation in the advanced technology editor before exporting it."
    elif result in UNSUPPORTED_RESULTS:
        limitation = "This source environment has no validated one-sided HOI4 effect for this licensing request. Use a project-owned event/decision implementation; Studio will not guess syntax."
    elif result:
        raise ValueError("Unsupported foreign technology result.")

    def block(name: str, lines: list[str]) -> str:
        return f"{name} = {{\n\t" + "\n\t".join(lines) + "\n}" if lines else ""
    return {
        "available": block("available", available),
        "visible": block("visible", visible),
        "bypass": block("bypass", bypass),
        "effects": "\n".join(effects),
        "limitation": limitation,
        "valid": not limitation,
    }


def foreign_link_effects(focus: dict) -> list[str]:
    scripts = []
    for link in focus.get("foreignTechnologyLinks", []):
        preview = foreign_link_preview(link)
        if link.get("result") and not preview["valid"]:
            raise ValueError(
                f"{focus.get('id', 'focus')}: foreign technology {link.get('technologyId', '')} "
                f"uses an unsupported result ({link.get('result')}). {preview['limitation']}"
            )
        if preview["effects"]: scripts.append(preview["effects"])
    return scripts
