# Geotrack

Geotrack takes position reports from many devices, stores the latest position per device in PostGIS, and matches each position against circular geozones that users define. It pushes live positions and geozone alerts to browsers over WebSockets. A user can have any number of sessions open at once (laptop, phone, several tabs), and every one of them receives that user's events.

Stack: Python 3.12, FastAPI, SQLAlchemy 2 (async, asyncpg), GeoAlchemy2, Alembic, PostgreSQL 17 + PostGIS 3.5, Redis 7, Docker Compose. A Leaflet page is included as a demo client.

## Running

You need Docker with Compose v2.24 or newer.

```bash
docker compose up --build
```

Compose starts four services:

| Service | Role |
| --- | --- |
| `db` | PostgreSQL + PostGIS, published on `127.0.0.1:54329` only |
| `redis` | Pub/Sub between API instances; not published on the host |
| `migrate` | Runs `alembic upgrade head` once, then exits |
| `api` | Starts after `db` and `redis` are healthy and `migrate` has exited with code 0 |

Once everything is up:

- Demo map: http://localhost:8000/
- OpenAPI docs: http://localhost:8000/docs
- Probes: http://localhost:8000/health/live and http://localhost:8000/health/ready

All defaults are for development. To change credentials, ports or tuning, copy `.env.example` to `.env`. Compose reads it for its own variables and passes it to the API container. The database and Redis URLs of the containers are always set by compose.

On Apple Silicon the database image is `imresamu/postgis`, a multi-arch build of the official docker-postgis images (`postgis/postgis` publishes amd64 only). To use the official image, set `POSTGIS_IMAGE=postgis/postgis:17-3.5`.

### Trying it from the shell

```bash
# create a zone for alice (the caller is identified by X-User-ID)
curl -s -X POST localhost:8000/api/v1/geozones -H 'X-User-ID: alice' \
  -H 'Content-Type: application/json' \
  -d '{"name":"center","latitude":50.4501,"longitude":30.5234,"radius_m":1500}'

# one device reports from inside it
curl -s -X POST localhost:8000/api/v1/devices/unit-1/position \
  -H 'Content-Type: application/json' -d '{"latitude":50.451,"longitude":30.524}'

# watch alice's live stream (any WebSocket client, e.g. websocat)
websocat 'ws://localhost:8000/ws/live?user=alice'
```

To see the demo in a browser:

1. Open http://localhost:8000/ in two tabs.
2. Connect both as the same user.
3. Click the map to place a zone.
4. Start the generator (next section).

Both tabs show the same devices and the same alerts.

## Load generator

```bash
pip install httpx
python generator.py                       # 10,000 devices, one report every 3 s
python generator.py --devices 20000 --period 2 --duration 120
```

Each simulated vehicle drives toward a random waypoint inside an 8 km circle around Kyiv, with a little steering noise. When it arrives it picks a new waypoint, so positions drift like real trips instead of jumping or staying still.

The fleet is split across `--workers` gateway coroutines (10 by default). Each gateway reports its vehicles in chunks of `--chunk` reports (500 by default) to `POST /api/v1/positions`. Gateways start at staggered offsets, which spreads the requests evenly over the period. On a 503 the generator waits for `Retry-After` and resends the same chunk, up to 3 times.

Every 5 s it prints a line with: reports sent, reports queued, rejections, throttling, failures, current rate, and p50/p95 request time. A summary is printed on exit or Ctrl+C.

Other options:

| Option | Meaning |
| --- | --- |
| `--base-url` | Address of the API |
| `--lat`, `--lon` | Center of the area |
| `--radius-km` | Radius of the area |
| `--seed` | Fix the random seed for repeatable runs |

## API

### REST

All REST endpoints are under `/api/v1`. Errors use RFC 9457 problem details (`application/problem+json`) with `type`, `title`, `status` and `detail`, plus a field-by-field `errors` list for validation failures.

| Method | Path | Notes |
| --- | --- | --- |
| POST | `/geozones` | Body `{name?, latitude, longitude, radius_m}`, with `radius_m` in (0, 100000]. Returns 201; 409 when the user's zone limit (200) is reached |
| GET | `/geozones?limit=&offset=` | The caller's zones, oldest first |
| GET | `/geozones/{id}` | 404 when the zone belongs to someone else |
| PUT | `/geozones/{id}` | Replaces the whole zone (every required field must be sent) |
| PATCH | `/geozones/{id}` | Changes only the fields sent; `latitude` and `longitude` must come together |
| DELETE | `/geozones/{id}` | 204 |
| POST | `/devices/{device_id}/position` | Body `{latitude, longitude, timestamp?}`; returns 202 |
| POST | `/positions` | Body `{positions: [{device_id, latitude, longitude, timestamp?}, ...]}` with up to 5,000 reports. Returns 202 with `{queued, rejected, errors}` |

Geozone endpoints need the `X-User-ID` header (1–64 characters from `A-Z a-z 0-9 _ . @ -`). Without it they answer 401.

Ingest rules:

- Ingest endpoints are not tied to a user.
- A report whose timestamp is more than 30 s in the future, or more than 6 h old, is refused.
- A timestamp without a timezone is treated as UTC.
- A batch is accepted partially when only part of it fits in the queue.
- 503 with `Retry-After` comes back only when nothing fits.
- Request bodies larger than 1 MiB get 413.

### WebSocket `/ws/live?user=<id>`

A connection with a missing or invalid user is refused at the handshake (HTTP 403).

The client may send:

- `{"viewport": [west, south, east, north]}` to receive only devices inside the map bounds. `west > east` means the box crosses the antimeridian.
- `{"viewport": null}` to receive all devices.

The server sends JSON frames, each with a `kind`:

| Kind | Content |
| --- | --- |
| `hello` | First frame; includes the user and the push interval |
| `positions` | `items: [{device_id, latitude, longitude, reported_at}]`. The first push after connecting or changing the viewport is a full view; later pushes contain only devices that changed. At most one push per interval (1 s), chunked at 2,000 items |
| `alert` | `items: [{geozone_id, device_id, latitude, longitude, reported_at}]`. One frame per ingest batch per user (at most 500 items) |
| `geozones_changed` | `{action: created / updated / deleted, id}`. Sent to every session of the owner, whichever session made the change |
| `gone` | `device_ids`: devices not heard from for 2 minutes, which the map should remove |
| `error` | `detail` for an invalid command; the connection stays open |

## How it works

```
 devices / generator.py
        │ POST /api/v1/positions  (202, partial accept, 503 + Retry-After)
        ▼
 ┌───────────────────────── API instance ──────────────────────────┐
 │ Intake ─► PositionPipeline (bounded) ─► one flusher ─► PostGIS   │
 │                                           │  one SQL statement:  │
 │                                           │  upsert + zone match │
 │                                           ▼                      │
 │                                     Redis Pub/Sub ◄── other instances
 │                                           │                      │
 │                           RedisBus (one task, one PubSub)        │
 │                     positions │                 │ user frames    │
 │                               ▼                 ▼                │
 │                      PositionCache           Hub: user → sessions│
 │                     (grid + seq log)            │                │
 │                               └── LiveClient ◄──┘  ─► WebSocket  │
 └──────────────────────────────────────────────────────────────────┘
```

The code is organized by feature:

| Package | Contents |
| --- | --- |
| `src/geotrack/zones` | Geozone schemas, ORM repository, service, routes |
| `src/geotrack/telemetry` | Ingest schemas, intake, pipeline, SQL store, processor |
| `src/geotrack/live` | Wire frames, position cache, sessions, hub, Redis bus, WebSocket route |

`wiring.py` builds the components of one process into a typed `Runtime`, and `app.py` exposes it to routes through FastAPI dependencies. Tests build the same app with in-memory fakes.

### Spatial queries

- **Data types.**
  - Zones store `lat`, `lon` and `radius_m` as plain columns.
  - PostGIS derives two generated columns from them: `center geography(Point,4326)` and `envelope geometry(Polygon,4326)`.
  - The application never builds geometries itself.
  - `geography` measures true meters on the WGS 84 spheroid, which is what a radius in meters means anywhere in the world. `geometry` in EPSG:4326 measures degrees.
- **Why an envelope column.**
  - The radius differs per zone, so `ST_DWithin(center, point, radius_m)` cannot use an index on `center` by itself.
  - `envelope` is a conservative bounding box of each circle, computed by the immutable SQL function `geozone_envelope`. It uses the smallest meridional radius of curvature plus a 1% margin, and widens to the full longitude range near the poles or across the antimeridian.
  - The box carries a GiST index.
  - Matching is `envelope && point` (index) followed by the exact `ST_DWithin(center, position, radius_m)` on geography.
  - No distances are computed in Python.
- **One statement per ingest batch.** A single SQL statement:
  1. unnests the column arrays;
  2. keeps the newest report per device;
  3. upserts the latest positions, updating only rows whose new timestamp is later and returning only the rows it changed;
  4. joins those rows to the geozones.

  Out-of-order and duplicate reports therefore neither move a device backwards nor raise alerts. Rows are written in device-id order, so concurrent writers would lock in the same order.
- **Tests.**
  - Integration tests check the circle boundary at 12 bearings (99.5% of the radius inside, 100.5% outside) for zones at the equator, in Kyiv, Sydney, Svalbard, on the antimeridian and next to the North Pole, with radii of 250 m and 40 km.
  - An `EXPLAIN` test confirms the envelope index answers the match against 20,000 zones.

### Handling 10,000 devices

- **The request path does no I/O.**
  - Handlers validate the body and offer the reports to `PositionPipeline`, which never awaits.
  - It accepts reports while `pending + in flight` is below `GEOTRACK_PIPELINE_CAPACITY` (60,000). Beyond that, devices get 503 with `Retry-After`, so memory stays bounded.
- **A single flusher writes to the database.**
  - It cuts batches of up to 2,000 reports, waiting at most 100 ms for a batch to fill.
  - The hot path therefore never holds more than one pooled connection. The rest of the pool (`pool_size=10`, `max_overflow=10`, `pool_timeout=10 s`, pre-ping) stays free for REST calls.
  - Every connection has `statement_timeout = 5 s`.
  - Ingest transactions set `synchronous_commit = off`: they only overwrite latest positions, so losing the last few milliseconds in a crash costs nothing that the next report does not fix.
- **Database outages cause backpressure, not loss.** A connection-level failure keeps the batch and retries it with exponential backoff (0.2 s up to 5 s). While that happens the batch still counts toward capacity, so devices are throttled instead of losing data. Data errors are logged and the batch is dropped.
- **Shutdown drains the queue.** On SIGTERM the pipeline is drained within `GEOTRACK_PIPELINE_DRAIN_TIMEOUT` before Redis and the engine close. Anything not saved in time is counted as `lost`.
- **The event loop never blocks.**
  - All database and Redis I/O is async (asyncpg, redis.asyncio).
  - Live pushes cost time proportional to what changed in the viewport.
  - JSON for each device position is encoded once and reused for every session.

### WebSocket state

- **Sessions per user.** `Hub` maps each user to a set of `LiveClient`s, so every tab or device of that user receives the same frames and nobody else's.
- **Positions are pulled, not queued.**
  - `PositionCache` keeps the latest entry per device with a global sequence number, in a change-ordered log and in 0.25° grid buckets.
  - Each session remembers the last sequence it sent. Once per push interval it reads the entries that are newer than that, inside its viewport, from just the grid cells the viewport covers.
  - A slow socket automatically gets coalesced, current data. No position frame is ever queued, and there is no resync logic.
- **Alerts are never dropped silently.** Alerts and control frames wait in a bounded per-session deque. If that deque overflows, the session is closed with code 1013 so the browser reconnects and reloads.
- **Stale devices.** Devices silent for `GEOTRACK_LIVE_DEVICE_TTL_SECONDS` are evicted and announced with `gone`. On startup the cache is warmed from the positions of the last TTL.
- **Several API instances.**
  - Instances share events through Redis Pub/Sub. Positions go to `geotrack:positions`. Alerts and zone changes go to `geotrack:user:<id>`, already encoded as client frames.
  - An instance subscribes to a user channel only while that user has a session on it.
  - Every instance, including the one that wrote the data, receives events through its own subscription, so there is one delivery path and no duplicates.
  - A single task owns the PubSub connection and reconnects with jittered backoff, restoring the user subscriptions.

### Docker

- Multi-stage image; the runtime stage holds only the virtual environment and the migrations.
- The API runs as a non-root user with a read-only root filesystem, all capabilities dropped, `no-new-privileges`, and an init process.
- Migrations run in their own one-shot service, so scaling the API never races Alembic.
- The container healthcheck uses `/health/ready`, which checks the database (`SELECT 1`), Redis (`PING`), the bus subscription and the flusher, each with a timeout.

## Measured results

Measured on an Apple M4 Pro laptop, with Docker Desktop and everything running on the same machine.

| Scenario | Result |
| --- | --- |
| 10,000 devices every 3 s for 60 s | 3,330 reports/s sustained, 0 failures, request p50 5 ms / p95 9 ms, API about 80 MiB RSS |
| 30 s run with a 3 km zone and two sessions of its owner | Both sessions received every alert (13,414 items each); alert latency from report to browser p50 150 ms / p95 160 ms |
| Database stopped for 14 s under load | Up to 41,000 reports buffered, then flushed after recovery; 0 failed, 0 lost |
| Database stopped for 26 s under load | Queue capped at 60,000 and devices throttled with 503; all 315,000 accepted reports were stored after recovery |
| Redis stopped for 6 s under load | Ingest and storage continued; `/health/ready` reported 503; the bus reconnected by itself |

## Trade-offs and limitations

- **Alert semantics.** Every report inside a zone raises an alert, which matches the task wording. There is no enter/exit state.
- **Delivery guarantee.** Delivery across instances is at-most-once: Redis Pub/Sub keeps no history, so events published while Redis is down are lost. Stored positions are not affected.
- **Authentication is a mock.** `X-User-ID` and `?user=` identify the caller without proof. Ingest is unauthenticated, so it needs a gateway or tokens before being exposed.
- **No position history.** Only the latest position per device is kept.
- **Single writer per instance.** One flusher per instance keeps database load predictable. Scaling ingest means more instances, and the sorted upsert keeps concurrent writers from deadlocking.

## Development

```bash
python3.12 -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"
pre-commit install

black --check . && flake8 && mypy src tests generator.py
pytest tests/unit                      # no services needed
docker compose up -d --wait db
pytest                                 # adds the PostGIS integration tests
```

Integration tests create and migrate their own `geotrack_test` database. They are skipped when PostGIS is not reachable. Schema changes go through Alembic (`alembic revision -m "..."`, `alembic upgrade head`).
