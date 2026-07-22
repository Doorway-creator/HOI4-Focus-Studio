# HOI4 Focus Studio 6.13.1

## Technology source fidelity hotfix

- Restores player-facing technology names, recursive localisation, source-driven layouts, and packaged Vanilla/Road to 56 icon resolution, including inherited BBA and NSB assets.
- Validates technology catalogue schema and fidelity before rendering and provides visible rebuild actions with progress and diagnostics.
- Keeps imported technologies read-only and preserves the experimental Technology Trees, Focus Lock, and foreign-technology linking introduced in 6.13.0.

## Stable source recovery

- Stores registered package locations in a durable registry outside the replaceable SQLite catalogue and migrates existing 6.13.0 registrations additively.
- Merges duplicate registrations by stable package/source ID, updates paths without relying on filenames, and displays the current path with Reselect source and Remove source actions.
- Automatically searches known local `source_packages` folders for missing packages, preferring stable package identity and using exact filename only as a fallback.
- Uses a native Windows file picker when automatic recovery finds no match and an in-app chooser when several valid copies are available.
- Corrects portable multipart-RAR extraction to use the true short absolute `C:\HFSRC` staging root.

## Safety and compatibility

- Source-cache rebuilds remain transactional: a failed import or rebuild leaves the previous validated catalogue active.
- The normal executable launches directly and uses `%LOCALAPPDATA%\HOI4 Focus Studio` without a tester launcher or storage override.
- Existing projects, protected base sources, stable project IDs, focus links, imported dependencies, export settings, playset snapshots, and source registry data are preserved during migration and update.
- This hotfix does not add technology rename or icon-override editing; that work remains deferred.
