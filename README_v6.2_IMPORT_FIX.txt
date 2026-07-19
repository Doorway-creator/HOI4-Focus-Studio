HOI4 Focus Studio v6.2 IMPORT FIX

- Uses port 8766 so an older Studio server on 8765 cannot intercept requests.
- Import endpoint accepts trailing slashes and query strings.
- Launcher finds normal Python first instead of relying only on the Codex runtime.
- Open http://127.0.0.1:8766 after launching.
