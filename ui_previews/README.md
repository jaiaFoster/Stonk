# Algo Stock Advisor UI Previews

These are static HTML/CSS previews for comparing possible report redesign directions. They do not affect production Flask routes, `/run`, scoring, broker detection, API calls, or strategy logic.

## How to Open

Open `ui_previews/index.html` directly in a browser, then click any preview link.

No local server is required.

## Preview Directions

- `preview_01_dark_command_center.html` - best for an active-options-first trading control room. Highest signal for daily lifecycle decisions.
- `preview_02_light_brokerage.html` - best for a clean, familiar, approachable brokerage feel.
- `preview_03_macro_cockpit.html` - best if sector exposure and portfolio allocation should be visually prominent.
- `preview_04_morning_brief.html` - best for a softer daily advisor memo that answers “what needs attention today?”
- `preview_05_quant_terminal.html` - best for systematic users who want compact gates, verdicts, scores, and blockers, now with a muted black-terminal palette.

## Recommendation

For the final app direction, `preview_04_morning_brief.html` is probably the best default for a small trusted-user audience: it keeps active options first without making the whole product feel like a stressful trading terminal. `preview_01_dark_command_center.html` is the strongest choice if the app is primarily used for active calendar management.
