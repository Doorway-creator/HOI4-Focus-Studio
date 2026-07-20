# HOI4 Focus Studio 6.11.0

- Adds a searchable source catalog with layered Vanilla, dependency-mod, and current-project resolution, source coverage, load order, conflicts, and dependency requirements.
- Adds imported character and national-spirit workflows for references, project-owned clones, intentional overrides, focus/event actions, and safe spirit upgrade chains.
- Adds the focus-editor Unlocks panel for imported technologies, equipment, units, doctrines, MIOs, modules, research bonuses, and ahead-of-time reductions.
- Adds a visible Sources screen plus direct imported-content entry points from Characters, National spirits, and the focus editor.
- Adds transactional multipart RAR importing: any selected volume resolves to part 1, all parts are verified, and catalogable files are filtered after reliable extraction.
- Uses a short Windows extraction root to support deeply nested source assets, then atomically publishes the filtered cache and always cleans temporary staging.
- Expands validation for duplicate or unresolved references, missing dependencies, conflicts, and invalid technology, equipment, module, and design references.
- Preserves existing projects through additive migration and exports only current-project additions, overrides, localisation, references, and scripted effects—not dependency-mod content.
