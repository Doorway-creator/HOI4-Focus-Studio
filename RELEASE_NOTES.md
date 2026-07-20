# HOI4 Focus Studio 6.12.0

- Adds stable internal project identities so renaming a project or exported mod never breaks protected base-source lookup, saving, loading, export, installation, updates, or backups.
- Adds guided, validated recovery of complete legacy mod folders or ZIPs into protected per-project LocalAppData storage without retaining the legacy name or path.
- Makes export publication transactional so failed exports leave no empty destination folder or partial ZIP.
- Adds Shift-drag focus-tree box selection, Ctrl-click selection toggling, highlighted multi-selection, and grouped movement with live connection redraw and one-step Undo/Redo.
- Adds Infrastructure (roads) and Railway construction rewards using HOI4's `infrastructure` and `rail_way` building identifiers.

- Adds a searchable source catalog with layered Vanilla, dependency-mod, and current-project resolution, source coverage, load order, conflicts, and dependency requirements.
- Adds imported character and national-spirit workflows for references, project-owned clones, intentional overrides, focus/event actions, and safe spirit upgrade chains.
- Adds the focus-editor Unlocks panel for imported technologies, equipment, units, doctrines, MIOs, modules, research bonuses, and ahead-of-time reductions.
- Adds a visible Sources screen plus direct imported-content entry points from Characters, National spirits, and the focus editor.
- Adds transactional multipart RAR importing: any selected volume resolves to part 1, all parts are verified, and catalogable files are filtered after reliable extraction.
- Uses a short Windows extraction root to support deeply nested source assets, then atomically publishes the filtered cache and always cleans temporary staging.
- Expands validation for duplicate or unresolved references, missing dependencies, conflicts, and invalid technology, equipment, module, and design references.
- Preserves existing projects through additive migration and exports only current-project additions, overrides, localisation, references, and scripted effects—not dependency-mod content.
