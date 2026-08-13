# 🕷 Spidey Tracker

**A retro-futuristic Spider-Man sighting intelligence network — live map, prediction engine, camera grid and an in-app AI operator, built as a pixel-art command console.**

> Fictional demo platform. It has no connection to real CCTV, satellite, law-enforcement or person-tracking systems of any kind, and it never claims otherwise. See [Data honesty](#data-honesty).

---

## What this is

A dense, single-screen operations console for tracking reported Spider-Man sightings. It is a working application, not a mockup: every counter, marker, probability and status light is computed from rows in the database, and the whole dashboard reacts in real time as data changes.

- **Live interactive map** — Leaflet, dark tiles, custom pixel markers whose colour encodes confidence and state
- **Real-time event stream** — Server-Sent Events push sightings, camera detections, alerts and predictions to every open tab, no polling and no refresh
- **Prediction engine** — recency-weighted dead reckoning over recent sightings, scored against district priors, producing candidate destinations with probabilities, an uncertainty zone and an ETA band
- **Sighting analysis** — real image measurement (suit-palette colour ratio, saturation, contrast, edge density) fused with motion plausibility and location correlation
- **NED AI** — an assistant that answers from live application state and can drive the UI: focus the map, open a sighting, switch modes, change filters
- **Camera network** — simulated feed grid with live status transitions
- **Demo network** — a simulator that keeps the console alive with plausible activity when there is no real data, clearly labelled as simulated throughout

---

## The console

```
┌─────────────────────────────────────────────────────────┬─────────────┐
│ 🕷 SPIDEY TRACKER   NAV   SEARCH        CITY  CLOCK  ●  │             │
├─────────────────────────────────────────────────────────┤ STATUS      │
│                                                         │             │
│                    LIVE CITY MAP                        │ NED AI      │
│      🟢   🔴        🕷    ····· PREDICTED PATH ·····▶    │             │
│           🟠   ⬜         ◇ PREDICTED                    │ PREDICTION  │
├──────────────┬──────────────┬───────────────────────────┴─────────────┤
│ LIVE FEED    │ LOCAL AREA   │ CAMERA NET │ ANALYTICS │ RADAR │ SYSTEM │
├──────────────┴──────────────┴─────────────────────────────────────────┤
│ GLOBAL NETWORK ticker                        STREAM ● DEMO ● SYSTEMS  │
└───────────────────────────────────────────────────────────────────────┘
```

Six map modes (MAP / SATELLITE / TERRAIN / HEATMAP / PREDICTION / RADAR), a
boot sequence that runs the real initialisation checks, keyboard shortcuts
(`1-6` views, `m s t h p d` map modes, `/` search, `n` NED, `Esc` close), and
distinct tablet and phone compositions — the phone build is a fullscreen map
with a floating status capsule, bottom tab bar and swipe-up sheet, not a
squeezed desktop.

---

## Running it

Requires **Python 3.8+**. No Node, no build step — the frontend is plain ES modules served by Flask.

```bash
pip3 install -r requirements.txt
python3 run.py
# → http://127.0.0.1:5000
```

On first boot the app creates `data/spidey.db`, seeds cities, districts and the camera grid, and backfills 24 hours of demo history so the charts and prediction engine have something to work with.

```bash
SPIDEY_DEMO=0 python3 run.py     # start with the demo network off
PORT=8080 python3 run.py         # different port
```

Copy `.env.example` if you want to pin configuration.

---

## Architecture

```
browser  ──── fetch ────►  Flask REST API  ────►  SQLite (WAL)
   ▲                            │
   └──── EventSource ◄──── SSE event bus ◄──── demo simulator (thread)
```

```
app/
├── __init__.py          application factory, error handling, boot wiring
├── config.py            every tunable, env-overridable
├── db.py                connections, write lock, transaction context
├── schema.sql           tables + indexes
├── seed.py              cities, districts, cameras, demo backfill
├── events.py            in-process pub/sub → SSE
├── services/
│   ├── geo.py           haversine, bearings, circular means, destination
│   ├── ai_analysis.py   image measurement + signal fusion
│   ├── prediction.py    movement vector → candidate scoring → route
│   ├── sightings.py     validate → analyse → persist → broadcast
│   ├── analytics.py     every aggregation the charts render
│   ├── ned.py           Claude engine + local intent engine, shared tools
│   └── simulator.py     the demo network
└── api/                 one blueprint per resource

static/
├── css/
│   ├── tokens.css       every colour, size, spacing and duration
│   ├── pixel.css        panels, buttons, bars, tabs, terminal primitives
│   ├── layout.css       console grid + tablet/phone compositions
│   └── components.css   map chrome, markers, radar, feed, charts, cameras
└── js/
    ├── core/            store, api client, SSE client, formatting, sound
    ├── components/      map, panels, ned, dossier, alerts
    └── main.js          boot, wiring, navigation, search, filters, actions
```

### State flow

```
SSE event ─► store.set() ─► rAF-coalesced notify ─► only the panels
                                                    watching that key
```

Components hold no domain data of their own. A single event that touches
sightings, prediction and analytics re-renders each affected panel exactly
once per frame, and any panel that does not watch those keys does no work.

### The sighting pipeline

Both a reported sighting and a simulated one travel the same path:

```
validate → infer motion → analyse → score → persist (one transaction)
        → broadcast → recompute prediction → raise alert
```

### The prediction engine

No fixed probabilities appear anywhere in the module. Recent sightings become movement legs; legs under 40 m are discarded as GPS noise. Bearings are combined with a **circular** mean weighted by recency (20-minute half-life) and confidence, which is why averaging 350° and 10° gives 0° rather than 180°. The resulting vector is dead-reckoned to the prediction horizon, and every district is scored on projection proximity × heading alignment × activity prior × time-of-day bias, then normalised into probabilities. Ranking margin, vector coherence and sample volume determine the confidence figure.

---

## Data honesty

This is the rule the codebase is built around: **the interface never presents guesswork as fact.**

- **Three analysis engines, always labelled.** `model` (a real detector — not shipped; `load_model()` is the hook), `heuristic` (genuine pixel measurement and geo correlation, but not object detection), and `demo` (synthesised for simulated records). Every analysis row stores `is_real_model`, and the UI badges accordingly.
- **Simulated data is marked.** Everything the simulator writes carries `is_demo = 1`; every event it emits carries `is_demo: true`.
- **Simulated records get simulated analysis.** A demo sighting has no image, so running the heuristic over it would be meaningless. It reports what the demo network is pretending its sensors produced — labelled `DEMO ANALYSIS`, never `is_real_model`.
- **Camera feeds are mock.** Every camera row is `is_mock = 1` and the API returns a `feed_notice` saying so.
- **Status is measured, not asserted.** `/api/system/status` runs a real database query, counts real SSE subscribers, and reports which AI engine is actually configured. A subsystem that is down reports as down.
- **NED states its engine.** Without `ANTHROPIC_API_KEY` it answers from a deterministic local intent engine and labels itself `LOCAL INTENT ENGINE`.

---

## API

| Method | Endpoint | Purpose |
|---|---|---|
| `GET` | `/api/sightings` | Filter by city, status, source, camera, confidence, window, bbox, free text |
| `GET` | `/api/sightings/:id` | One sighting with analysis and movement context |
| `POST` | `/api/sightings` | Report a sighting (JSON or multipart with image/video) |
| `PATCH` `DELETE` | `/api/sightings/:id` | Update / remove |
| `POST` | `/api/sightings/:id/reanalyze` | Re-run analysis against newer history |
| `GET` | `/api/predictions?city=` | Current prediction (`?refresh=1` to recompute) |
| `GET` | `/api/cameras`, `/api/cameras/:id` | Camera grid and detection log |
| `GET` | `/api/analytics?range=1h\|24h\|7d\|30d` | Summary, timeline, hours, areas, confidence, sources |
| `GET` | `/api/network` | Per-city tallies |
| `GET` | `/api/search?q=` | Sightings, cameras, areas, cities, predictions |
| `POST` | `/api/ai/chat` | Ask NED; returns a reply plus UI actions |
| `POST` | `/api/ai/analyze` | Analyse an image without creating a sighting |
| `GET` | `/api/system/status` | Measured subsystem health |
| `POST` | `/api/system/demo` | Toggle the demo network |
| `GET` | `/api/stream` | SSE event stream |

### Events

`sighting.created` · `sighting.updated` · `sighting.deleted` · `prediction.updated` · `camera.detected` · `camera.status_changed` · `alert.created` · `network.updated` · `system.status_changed`

---

## Enabling the real AI

```bash
export ANTHROPIC_API_KEY=sk-ant-...
python3 run.py
```

NED switches to the Claude API with tool calling over the same tools the local engine uses, so both answer from live data. If the API is unreachable it degrades to the local engine and says so rather than failing silently.

---

## What "dynamic" means here

No figure in the interface is a constant. Concretely:

- Change city and the map, feed, status, prediction, cameras, analytics and
  radar all re-derive from that city's rows.
- Post a sighting to `/api/sightings` from anywhere and every open tab updates
  within a frame — marker, feed row, counters, prediction, alert — with no
  polling and no refresh.
- Toggle a filter and the map, feed, counters and local-area ranking all
  narrow together, because they share one filter predicate.
- Kill the server and the header reports `NETWORK OFFLINE`, because the
  indicator reflects the actual `EventSource` state.
- Ask NED "where is he heading?" and the reply is generated from the current
  prediction, then it switches the map to prediction mode itself.

Verified by driving the real UI in headless Chrome: boot, all six map modes,
all four dock tabs, all six overlay views, marker and feed selection, the
dossier, search, filters, NED round-trip, the report form with a real image
upload, live SSE mutation of the DOM, and tablet/phone layouts.

---

## Notes on concurrency

The demo simulator writes from a background thread while request threads read and write. SQLite runs in WAL mode with a 15 s busy timeout, and multi-statement operations take a reentrant write lock via `db.transaction()` so a sighting, its analysis, its camera detection and its alert land together. Sighting references are derived from the row id rather than "read the last one and add one", which previously raced under concurrent inserts.

---

## Licence

MIT
