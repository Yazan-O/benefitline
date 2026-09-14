# Benefitline web page

Open `index.html` in any browser, including straight from disk (`file://`). No build step, no bundler, no server.
Everything is in that one file; the only network requests are the Google Fonts stylesheet and the API when you point it at one.
With no API configured the page runs in **mock mode** and shows a small "mock" badge next to the wordmark.
Mock mode answers every action locally with the real `policyengine-us` 2.0.4 numbers from `gallery/ground_truth.json`, so the demo works offline.
Regenerate the embedded numbers after any engine change with `python _runs/2026-09-13_phase3_web/gen_mockdata.py` and paste `engine_data.js` back into the file; do not hand-edit them.
To talk to the deployed agent instead, add `?api=https://your-endpoint` to the URL, or set `window.BENEFITLINE_API = "https://your-endpoint"` before the page's script runs.
The page then sends `POST <API_BASE>` with the JSON payloads in `src/DESIGN.md`; the browser mints `session_id` once (uuid4 + `-benefitline`) and keeps it in `localStorage`.
Scripted family replies are read from `../gallery/scripts.json` when the page is served over http(s), and fall back to the copies embedded in the file otherwise.
The five households are **fictional**, labeled as such on screen; the rules, forms, deadlines and dollar amounts behind them are real Oklahoma and federal rules.
Screens: family chat, results with a rule link under every number, coordinator board (identifiers masked until Claim), decision cards, ledger with a ten-minute undo, and a "How it works" sheet.
