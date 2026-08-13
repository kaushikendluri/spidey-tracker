-- Spidey Tracker schema.
-- Timestamps are stored as REAL unix epoch seconds (UTC) because every
-- analytical query in the app is time-window based; ISO strings are derived
-- at serialisation time only.

PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS users (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    handle        TEXT    NOT NULL UNIQUE,
    display_name  TEXT    NOT NULL,
    role          TEXT    NOT NULL DEFAULT 'analyst',
    created_at    REAL    NOT NULL
);

CREATE TABLE IF NOT EXISTS cities (
    id            TEXT    PRIMARY KEY,
    name          TEXT    NOT NULL,
    country       TEXT    NOT NULL,
    latitude      REAL    NOT NULL,
    longitude     REAL    NOT NULL,
    default_zoom  INTEGER NOT NULL DEFAULT 12,
    timezone      TEXT    NOT NULL DEFAULT 'UTC',
    is_primary    INTEGER NOT NULL DEFAULT 0
);

-- Named districts inside a city. Drives prediction candidates, the local-area
-- panel and the report form's location picker.
CREATE TABLE IF NOT EXISTS locations (
    id            TEXT    PRIMARY KEY,
    city_id       TEXT    NOT NULL REFERENCES cities(id) ON DELETE CASCADE,
    name          TEXT    NOT NULL,
    latitude      REAL    NOT NULL,
    longitude     REAL    NOT NULL,
    radius_km     REAL    NOT NULL DEFAULT 1.5,
    -- Baseline likelihood of activity, 0..1, used as a prior by the
    -- prediction engine before observed movement is factored in.
    activity_bias REAL    NOT NULL DEFAULT 0.5
);

CREATE TABLE IF NOT EXISTS cameras (
    id             TEXT    PRIMARY KEY,
    label          TEXT    NOT NULL,
    city_id        TEXT    NOT NULL REFERENCES cities(id) ON DELETE CASCADE,
    location_id    TEXT             REFERENCES locations(id) ON DELETE SET NULL,
    latitude       REAL    NOT NULL,
    longitude      REAL    NOT NULL,
    heading        REAL    NOT NULL DEFAULT 0,
    status         TEXT    NOT NULL DEFAULT 'live',   -- live|offline|analyzing|detected|error
    stream_url     TEXT,
    -- 0 = mock/simulated feed, 1 = an operator-supplied authorised feed.
    is_mock        INTEGER NOT NULL DEFAULT 1,
    last_status_at REAL    NOT NULL,
    created_at     REAL    NOT NULL
);

CREATE TABLE IF NOT EXISTS sightings (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    ref            TEXT    NOT NULL UNIQUE,           -- human ref e.g. SGT-4821
    city_id        TEXT    NOT NULL REFERENCES cities(id) ON DELETE CASCADE,
    location_id    TEXT             REFERENCES locations(id) ON DELETE SET NULL,
    area           TEXT    NOT NULL,
    latitude       REAL    NOT NULL,
    longitude      REAL    NOT NULL,
    ts             REAL    NOT NULL,                  -- observation time
    created_at     REAL    NOT NULL,
    updated_at     REAL    NOT NULL,
    confidence     REAL    NOT NULL DEFAULT 0,        -- 0..100
    status         TEXT    NOT NULL DEFAULT 'unverified', -- active|confirmed|unverified|expired|dismissed
    source         TEXT    NOT NULL DEFAULT 'citizen',    -- citizen|camera|network|demo
    camera_id      TEXT             REFERENCES cameras(id) ON DELETE SET NULL,
    reporter       TEXT,
    description    TEXT,
    image_path     TEXT,
    video_path     TEXT,
    direction      REAL,                              -- bearing degrees 0..360
    speed_kmh      REAL,
    ai_verified    INTEGER NOT NULL DEFAULT 0,
    is_demo        INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_sightings_ts        ON sightings(ts DESC);
CREATE INDEX IF NOT EXISTS idx_sightings_city_ts   ON sightings(city_id, ts DESC);
CREATE INDEX IF NOT EXISTS idx_sightings_conf      ON sightings(confidence DESC);
CREATE INDEX IF NOT EXISTS idx_sightings_status    ON sightings(status);
CREATE INDEX IF NOT EXISTS idx_sightings_latlng    ON sightings(latitude, longitude);
CREATE INDEX IF NOT EXISTS idx_sightings_camera    ON sightings(camera_id);
CREATE INDEX IF NOT EXISTS idx_sightings_source    ON sightings(source);

-- One analysis row per sighting, produced by app/services/ai_analysis.py.
CREATE TABLE IF NOT EXISTS sighting_analysis (
    sighting_id    INTEGER PRIMARY KEY REFERENCES sightings(id) ON DELETE CASCADE,
    engine         TEXT    NOT NULL,   -- heuristic|model|demo
    engine_label   TEXT    NOT NULL,   -- shown verbatim in the UI
    is_real_model  INTEGER NOT NULL DEFAULT 0,
    probability    REAL    NOT NULL DEFAULT 0,
    visual_match   REAL    NOT NULL DEFAULT 0,
    motion_match   REAL    NOT NULL DEFAULT 0,
    pattern_match  REAL    NOT NULL DEFAULT 0,
    location_match REAL    NOT NULL DEFAULT 0,
    notes          TEXT,
    created_at     REAL    NOT NULL
);

CREATE TABLE IF NOT EXISTS predictions (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    city_id        TEXT    NOT NULL REFERENCES cities(id) ON DELETE CASCADE,
    created_at     REAL    NOT NULL,
    origin_sighting INTEGER        REFERENCES sightings(id) ON DELETE SET NULL,
    -- JSON payload: candidates, route, vector, probability zone.
    payload        TEXT    NOT NULL,
    confidence     REAL    NOT NULL DEFAULT 0,
    eta_min        REAL,
    eta_max        REAL,
    is_current     INTEGER NOT NULL DEFAULT 1
);

CREATE INDEX IF NOT EXISTS idx_pred_city ON predictions(city_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_pred_cur  ON predictions(city_id, is_current);

CREATE TABLE IF NOT EXISTS camera_detections (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    camera_id      TEXT    NOT NULL REFERENCES cameras(id) ON DELETE CASCADE,
    sighting_id    INTEGER          REFERENCES sightings(id) ON DELETE SET NULL,
    ts             REAL    NOT NULL,
    confidence     REAL    NOT NULL DEFAULT 0,
    summary        TEXT,
    is_demo        INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_det_cam_ts ON camera_detections(camera_id, ts DESC);
CREATE INDEX IF NOT EXISTS idx_det_ts     ON camera_detections(ts DESC);

CREATE TABLE IF NOT EXISTS alerts (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    kind           TEXT    NOT NULL,   -- sighting|camera|system|prediction
    severity       TEXT    NOT NULL DEFAULT 'info', -- info|warning|critical
    title          TEXT    NOT NULL,
    body           TEXT,
    sighting_id    INTEGER          REFERENCES sightings(id) ON DELETE CASCADE,
    city_id        TEXT             REFERENCES cities(id) ON DELETE CASCADE,
    created_at     REAL    NOT NULL,
    acknowledged   INTEGER NOT NULL DEFAULT 0,
    is_demo        INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_alerts_created ON alerts(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_alerts_ack     ON alerts(acknowledged, created_at DESC);

-- Rolling per-city counters for the global network panel. Recomputed rather
-- than trusted blindly, but cached here so the panel does not scan the whole
-- sightings table on every poll.
CREATE TABLE IF NOT EXISTS network_stats (
    city_id        TEXT    PRIMARY KEY REFERENCES cities(id) ON DELETE CASCADE,
    total          INTEGER NOT NULL DEFAULT 0,
    last_24h       INTEGER NOT NULL DEFAULT 0,
    high_confidence INTEGER NOT NULL DEFAULT 0,
    last_sighting_ts REAL,
    updated_at     REAL    NOT NULL
);

CREATE TABLE IF NOT EXISTS ai_messages (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id     TEXT    NOT NULL,
    role           TEXT    NOT NULL,  -- user|assistant
    content        TEXT    NOT NULL,
    engine         TEXT,              -- claude|local
    payload        TEXT,              -- JSON: actions, data attachments
    created_at     REAL    NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_ai_session ON ai_messages(session_id, created_at);

CREATE TABLE IF NOT EXISTS settings (
    key            TEXT    PRIMARY KEY,
    value          TEXT    NOT NULL,
    updated_at     REAL    NOT NULL
);
