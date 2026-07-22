# HOI4 Focus Studio 6.13.2

## Production source controls hotfix

- Makes Reselect source, Remove source, Move earlier, and Move later respond consistently in the standard Windows executable.
- Opens the native source picker in the foreground instead of leaving a hidden PowerShell dialog waiting behind the browser.
- Lets the direct executable select its own local port so it cannot attach the browser to an older Studio process on a fixed port.

## Automatic source recovery

- Searches the standard local `C:\GitHub\HOI4-Focus-Studio\source_packages` location without relying on a tester launcher or a user-specific path.
- Repairs a uniquely matched missing durable source registration before rebuilding the technology catalogue.
- Keeps cache publication transactional; a missing, ambiguous, or failed source import preserves the previously validated catalogue.

## Safety and compatibility

- Uses normal `%LOCALAPPDATA%\HOI4 Focus Studio` production storage when launched directly from `HOI4 Focus Studio.exe`.
- Preserves projects, protected base sources, stable project IDs, focus links, dependency metadata, export settings, and existing catalogues.
- Contains no technology editing, project schema, or imported-source semantic changes.
