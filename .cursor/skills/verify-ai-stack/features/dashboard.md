# Dashboard data and controls

The user opens the OmniRoute dashboard, switches time ranges, and inspects usage, candidates, quota, errors and connection status.

## Sub-features

- `document`: the actual dashboard HTML identifies OmniRoute.
- `data-ranges`: all seven HTTP data routes accept 24h, 7d, 30d and all. Overview, timeseries, models and errors use the range; candidates, quota and connections are period-independent.
- `fresh-collector`: collection is recent and reports no collection error.
- `error-contract`: invalid period is 400 and an unknown route is 404.
- `rendered-controls`: period tabs, charts and theme controls work in a browser; separate UI gap.

## How to get to it (user POV)

- Open `http://127.0.0.1:20140/`.
- Choose one of the period tabs in the tablist named `기간`.
- Use the button named `화면 색상 전환` to change the theme.

## Driving it with control_stack.py

Preconditions:

- The manual dashboard must already be running under the known `server.py`, with its collector having populated the dashboard's own database. No listener on 20140 is a verified-unreachable prerequisite, not a core service failure. Do not start a server or second collector as part of this pass.

- **Document/data.** Run `python $ctl dashboard --run $run`. It reads the real document and `/api/overview`, `/api/timeseries`, `/api/candidates`, `/api/quota`, `/api/models`, `/api/errors`, `/api/connections` for each period.
- **Freshness.** Require `last_collect_at` within 600 seconds, no `last_collect_error`, and well-formed response objects. Read the per-route JSON artifacts.
- **Invalid requests.** The same command requires 400 on `/api/overview?range=invalid` and 404 on `/api/not-a-route`.
- **Rendered UI.** No browser driver is shipped. Use an available browser integration to open the URL, select `role=tab` names `24시간`, `7일`, `30일`, `전체`, and verify selected state plus corresponding rendered sections. Click `role=button` name `화면 색상 전환`. Capture before/after screenshots and an accessibility snapshot alongside actual API requests. Leave this entry unverified through HTTP results alone.
- **Conditional controls.** When data exists, inspect the model filter, clickable or Enter-activated sort headers, pagination and expandable error examples. Retry controls require a section error. Record the concrete data/error prerequisite when a control is not reachable. The connections endpoint has no dedicated connection section in this UI. Restore the original theme and range after testing.

## Gotchas

- An HTML response does not prove JavaScript loaded or charts rendered. Chart.js is loaded from a CDN.
- A stale but readable database fails freshness; do not relax the limit to pass it.
- The HTTP journey does not change model routing or write the production OmniRoute database. The pre-existing collector continues its own normal activity.
- Response artifacts can contain account labels and error text. Keep evidence local and inspect redaction before sharing.
