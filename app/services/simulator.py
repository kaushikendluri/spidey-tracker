"""Demo network simulator.

Generates plausible activity so the dashboard is alive with no real data.
Everything it writes is tagged `is_demo = 1` and every event it publishes
carries `is_demo: true`, so the UI can badge it as DEMO NETWORK. It is off the
moment demo mode is toggled off, and it never touches non-demo records.

The simulation is a random walk with memory: it keeps a per-city "subject"
whose heading and district persist between events, which is what makes the
prediction engine produce a coherent path instead of noise.
"""

import random
import threading
import time

from .. import db as dbmod
from .. import events
from . import sightings as sightings_service
from . import geo


class Simulator(object):
    def __init__(self, app):
        self.app = app
        self._thread = None
        self._stop = threading.Event()
        self._enabled = threading.Event()
        self._lock = threading.RLock()
        self._rng = random.Random()
        self._subjects = {}
        self._next_event_at = 0.0
        self._tick_count = 0

    # --- control ---------------------------------------------------------

    @property
    def enabled(self):
        return self._enabled.is_set()

    def start(self, enabled=True):
        with self._lock:
            if enabled:
                self._enabled.set()
            else:
                self._enabled.clear()
            if self._thread and self._thread.is_alive():
                return
            self._stop.clear()
            self._thread = threading.Thread(target=self._run, name="spidey-simulator")
            self._thread.daemon = True
            self._thread.start()

    def stop(self):
        self._stop.set()
        self._enabled.clear()

    def set_enabled(self, enabled):
        with self._lock:
            was = self.enabled
            if enabled:
                self._enabled.set()
                self._next_event_at = time.time() + 3.0
            else:
                self._enabled.clear()
            if was != enabled:
                events.publish("system.status_changed", {"demo_mode": bool(enabled)})
                self._persist(enabled)
        return self.enabled

    def _persist(self, enabled):
        try:
            with dbmod.connection(self.app.config["DATABASE"]) as conn:
                dbmod.set_setting(conn, "demo_mode", "1" if enabled else "0")
        except Exception:
            pass

    # --- loop ------------------------------------------------------------

    def _run(self):
        while not self._stop.is_set():
            try:
                if self._enabled.wait(timeout=1.0):
                    now = time.time()
                    if now >= self._next_event_at:
                        self._tick()
                        self._schedule_next()
            except Exception as exc:  # keep the thread alive through bad ticks
                self.app.logger.warning("simulator tick failed: %s", exc)
                self._schedule_next()
            self._stop.wait(0.6)

    def _schedule_next(self):
        lo = self.app.config["DEMO_MIN_INTERVAL"]
        hi = max(lo + 1, self.app.config["DEMO_MAX_INTERVAL"])
        self._next_event_at = time.time() + self._rng.uniform(lo, hi)

    def _tick(self):
        self._tick_count += 1
        with dbmod.connection(self.app.config["DATABASE"]) as conn:
            # Every few events, do housekeeping instead of a new sighting so the
            # dashboard shows camera churn and status decay too.
            roll = self._rng.random()
            if roll < 0.16:
                self._camera_churn(conn)
            elif roll < 0.24:
                self._expire(conn)
            else:
                self._emit_sighting(conn)

            if self._tick_count % 4 == 0:
                self._expire(conn)
                self._settle_cameras(conn)

    # --- behaviours ------------------------------------------------------

    def _pick_city(self, conn):
        """New York dominates; other cities fire occasionally."""
        weights = {"nyc": 0.55, "la": 0.15, "tokyo": 0.10,
                   "london": 0.09, "chennai": 0.07, "rio": 0.04}
        rows = dbmod.query(conn, "SELECT id FROM cities")
        if not rows:
            return None
        pool, total = [], 0.0
        for row in rows:
            w = weights.get(row["id"], 0.05)
            total += w
            pool.append((total, row["id"]))
        pick = self._rng.uniform(0, total)
        for cumulative, city_id in pool:
            if pick <= cumulative:
                return city_id
        return pool[-1][1]

    def _subject(self, conn, city_id):
        """Persistent per-city subject: an actual position, heading and speed.

        Tracking a real position rather than "which district am I in" matters:
        the subject must only cover the distance its speed allows in the time
        since its last sighting. Jumping between districts on every event
        produced implied speeds in the hundreds of km/h, which made the
        prediction engine's dead reckoning meaningless.
        """
        districts = dbmod.query(
            conn,
            "SELECT id,name,latitude,longitude,radius_km,activity_bias "
            "FROM locations WHERE city_id = ?",
            (city_id,),
        )
        if not districts:
            return None
        index = {d["id"]: dict(d) for d in districts}

        subject = self._subjects.get(city_id)
        if not subject or subject.get("location_id") not in index:
            start = max(districts, key=lambda d: d["activity_bias"] * self._rng.random())
            subject = {
                "location_id": start["id"],
                "latitude": start["latitude"],
                "longitude": start["longitude"],
                "heading": self._rng.uniform(0, 360),
                "speed": self._rng.uniform(22, 44),
                "last_ts": time.time() - self._rng.uniform(60, 300),
            }
        subject["_districts"] = index
        self._subjects[city_id] = subject
        return subject

    def _advance(self, subject, city_id):
        """Dead-reckon the subject forward by the distance its speed allows."""
        districts = list(subject["_districts"].values())
        now = time.time()

        # Distance travelled must match the gap that will be recorded between
        # the two sightings, or the implied speed the analyser computes is
        # fiction. No lower bound: two events a fraction of a second apart move
        # the subject a fraction of a metre, which is correct. The upper bound
        # stops a paused simulator teleporting on resume.
        elapsed_h = max(0.0, min(600.0, now - subject["last_ts"])) / 3600.0
        subject["last_ts"] = now

        subject["speed"] = max(12.0, min(65.0, subject["speed"] + self._rng.gauss(0, 5)))
        subject["heading"] = (subject["heading"] + self._rng.gauss(0, 22)) % 360

        # Occasionally steer toward a high-activity district so the walk has
        # intent instead of drifting at random forever.
        if self._rng.random() < 0.35:
            target = max(districts,
                         key=lambda d: d["activity_bias"] * self._rng.random())
            want = geo.bearing_deg(subject["latitude"], subject["longitude"],
                                   target["latitude"], target["longitude"])
            subject["heading"] = geo.mean_bearing(
                [subject["heading"], want], [0.55, 0.45]
            ) or subject["heading"]

        distance = subject["speed"] * elapsed_h
        lat, lon = geo.destination(subject["latitude"], subject["longitude"],
                                   subject["heading"], distance)

        # Keep the subject inside the city's district cloud.
        nearest, nearest_d = None, None
        for d in districts:
            gap = geo.haversine_km(lat, lon, d["latitude"], d["longitude"])
            if nearest_d is None or gap < nearest_d:
                nearest, nearest_d = d, gap
        if nearest_d is not None and nearest_d > max(6.0, nearest["radius_km"] * 2.5):
            subject["heading"] = geo.bearing_deg(lat, lon,
                                                 nearest["latitude"], nearest["longitude"])
            lat, lon = geo.destination(lat, lon, subject["heading"], distance)
            nearest_d = geo.haversine_km(lat, lon, nearest["latitude"], nearest["longitude"])

        subject["latitude"], subject["longitude"] = lat, lon
        subject["location_id"] = nearest["id"] if nearest else subject["location_id"]
        return nearest

    def _emit_sighting(self, conn):
        city_id = self._pick_city(conn)
        if not city_id:
            return
        subject = self._subject(conn, city_id)
        if not subject:
            return
        district = self._advance(subject, city_id)
        if not district:
            return

        lat, lon = subject["latitude"], subject["longitude"]

        camera_id = None
        source = "citizen"
        roll = self._rng.random()
        if roll < 0.42:
            cams = dbmod.query(
                conn, "SELECT id FROM cameras WHERE location_id = ? AND status != 'offline'",
                (district["id"],),
            )
            if cams:
                camera_id = self._rng.choice(cams)["id"]
                source = "camera"
        elif roll < 0.58:
            source = "network"

        payload = {
            "city_id": city_id,
            "location_id": district["id"],
            "area": district["name"],
            "latitude": lat,
            "longitude": lon,
            "ts": time.time(),
            "source": source,
            "camera_id": camera_id,
            "reporter": "DEMO NETWORK",
            "description": self._description(district["name"]),
            "direction": subject["heading"],
            "speed_kmh": subject["speed"],
            "is_demo": True,
        }
        try:
            sightings_service.create(self.app, conn, payload)
        except sightings_service.ValidationError as exc:
            self.app.logger.warning("simulator produced invalid sighting: %s", exc.message)

    def _camera_churn(self, conn):
        """Flip a camera between live/analyzing/offline/error."""
        row = dbmod.query(
            conn, "SELECT id, status, label FROM cameras ORDER BY RANDOM() LIMIT 1", one=True
        )
        if not row:
            return
        transitions = {
            "live": ["analyzing", "analyzing", "offline", "live"],
            "analyzing": ["live", "live", "detected", "error"],
            "detected": ["analyzing", "live"],
            "offline": ["live", "offline", "error"],
            "error": ["offline", "live"],
        }
        nxt = self._rng.choice(transitions.get(row["status"], ["live"]))
        if nxt == row["status"]:
            return
        now = time.time()
        dbmod.execute(
            conn, "UPDATE cameras SET status = ?, last_status_at = ? WHERE id = ?",
            (nxt, now, row["id"]),
        )
        events.publish("camera.status_changed", {
            "camera_id": row["id"], "label": row["label"],
            "status": nxt, "previous": row["status"],
            "ts": now, "is_demo": True,
        })

    def _settle_cameras(self, conn):
        """Cameras stuck in 'detected' fall back to 'live' after a while."""
        cutoff = time.time() - 90
        rows = dbmod.query(
            conn, "SELECT id, label FROM cameras WHERE status = 'detected' AND last_status_at < ?",
            (cutoff,),
        )
        for row in rows:
            dbmod.execute(
                conn, "UPDATE cameras SET status = 'live', last_status_at = ? WHERE id = ?",
                (time.time(), row["id"]),
            )
            events.publish("camera.status_changed", {
                "camera_id": row["id"], "label": row["label"],
                "status": "live", "previous": "detected",
                "ts": time.time(), "is_demo": True,
            })

    def _expire(self, conn):
        sightings_service.expire_stale(self.app, conn)

    DESCRIPTIONS = [
        "Rooftop transit observed between two towers.",
        "Web-line anchor reported on building facade.",
        "Fast aerial movement, no ground contact recorded.",
        "Bystander footage: red and blue figure, two frames.",
        "Subject descended vertically before departing.",
        "Swing arc reported across the avenue.",
        "Figure landed on fire escape then moved north.",
        "Low-altitude pass reported above traffic.",
    ]

    def _description(self, area):
        return "%s (%s)" % (self._rng.choice(self.DESCRIPTIONS), area)

    # --- introspection ---------------------------------------------------

    def status(self):
        return {
            "enabled": self.enabled,
            "running": bool(self._thread and self._thread.is_alive()),
            "next_event_in": max(0.0, round(self._next_event_at - time.time(), 1))
                              if self.enabled else None,
            "interval": [self.app.config["DEMO_MIN_INTERVAL"],
                         self.app.config["DEMO_MAX_INTERVAL"]],
            "events_emitted": self._tick_count,
        }
