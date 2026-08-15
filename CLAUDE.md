# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

KlipperScreen is a Python 3 / GTK 3 touchscreen interface for 3D printers running Klipper firmware via Moonraker API. It runs as a kiosk-style desktop application (typically on a Raspberry Pi with touchscreen).

## Key Commands

**Lint (the only automated check):**
```
pycodestyle --ignore=E402,W503,W504 --max-line-length=120 --max-doc-length=120 screen.py ks_includes panels
```

**Run the application:**
```
python screen.py
```

There is no test suite. CI runs only the pycodestyle linter above.

## Code Style

- PEP 8 with max line length 120 characters
- Ignored rules: E402 (module-level imports not at top), W503/W504 (line break before/after binary operator)
- **Small, focused functions.** Aim for 5-10 lines per function. Extract logic into named helpers rather than writing long functions.
- **Pure functions where possible.** Functions should take inputs, return outputs, and avoid side effects. Side effects (I/O, mutating shared state) should be pushed to the edges.

## Git Workflow

- **Always commit and push when you're done with a task.** Do not wait to be asked — committing and pushing is part of completing the work.
- Create small, focused commits as you go so changes are easy to review and revert.
- Each commit should address a single concern (one bug fix, one feature, one refactor).
- Use a succinct imperative commit title (e.g. "Add retry logic for API calls").
- Include gotchas, caveats, or non-obvious side effects in the commit message body.
- Never add "Co-Authored-By" lines or email addresses to commit messages.
- Push freely without asking, but never use `git push --force` or any force-push variant.
- **Keep all documentation up to date.** When changing behavior, update CLAUDE.md and code comments in the same commit. Stale docs are worse than no docs.

## Architecture

**Communication flow:**
```
KlipperScreen (GTK UI) ←WebSocket/REST→ Moonraker ←→ Klipper firmware
```

**Entry point:** `screen.py` — `KlipperScreen(Gtk.Window)` is the main application class.

**Core modules (`ks_includes/`):**
- `config.py` — INI-style configuration parser with Jinja2 templating for menu conditionals
- `printer.py` — Printer state model (temperatures, fans, positions, etc.)
- `KlippyWebsocket.py` — WebSocket client for real-time Moonraker updates
- `KlippyRest.py` — REST API client for queries and file operations
- `KlippyGcodes.py` — G-code command wrapper
- `KlippyGtk.py` — GTK helper utilities (buttons, images, dialogs)
- `screen_panel.py` — `ScreenPanel` base class that all panels inherit from
- `files.py` — G-code file management and metadata
- `sdbus_nm.py` — NetworkManager/WiFi integration via D-Bus

**Custom widgets (`ks_includes/widgets/`):** keyboard, keypad, heatergraph, bedmap, objectmap, prompts, lockscreen, screensaver.

**Panel system (`panels/`):** ~36 feature panels, each a GTK container loaded dynamically. `base_panel.py` provides the titlebar, action bar (back/home/estop), and content area. Navigation uses a stack (`_cur_panels`).

**Configuration (`config/`):** Default settings in INI format. Menu definitions use Jinja2 templates with printer state conditionals (e.g., `enable: {{ printer.extruders.count > 0 }}`).

**Theming (`styles/`):** CSS-based GTK themes. `base.css` provides shared styles; theme directories (`material-dark/`, etc.) override with `style.css`. SVG icons with runtime color replacement.

## Key Patterns

- All UI runs on the GLib main loop; use `GLib.idle_add()` to schedule UI updates from non-main threads
- Panels register for Moonraker subscription updates via `self._printer.data` and callback methods
- Internationalization via gettext (`_("string")`) — 30+ languages, translations managed via Weblate
- Multi-printer support: config sections like `[printer MyPrinter]` with per-printer connection settings

## Dependencies

- Production: `scripts/KlipperScreen-requirements.txt`
- Development: `scripts/dev-requirements.txt` (pycodestyle, pygobject-stubs)
- System packages: `scripts/system-dependencies.json`
