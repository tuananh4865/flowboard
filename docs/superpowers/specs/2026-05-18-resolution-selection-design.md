# Resolution Selection Download — Design

## Overview

When a user clicks the download button on a Flowboard node, present a dropdown
with available resolution options. If the selected resolution differs from the
cached asset's resolution, call the Flow Upscale API, poll until complete, then
serve the upscaled file.

**In scope:** image and video nodes. No audio, no batch.

---

## Resolutions

| Media   | Value   | Label  | Upscale? | Notes               |
|---------|---------|--------|----------|---------------------|
| image   | `1K`    | 1K     | No       | Default cached asset |
| image   | `2K`    | 2K     | Yes      | 2× on shortest edge |
| image   | `4K`    | 4K     | Yes      | 4× on shortest edge |
| video   | `270`   | 270p   | No       | Cached asset is 720p |
| video   | `720`   | 720p   | No       | Default cached asset |
| video   | `1080`  | 1080p  | Yes      |                     |
| video   | `4K`    | 4K     | Yes      | 50 credits          |

---

## UI — Frontend

### Dropdown trigger

`frontend/src/canvas/NodeCard.tsx` — replace the `handleDownload` click handler
with a dropdown that shows on button press.

**Options per media type** (image vs video) are computed from the node's
`type` field (`"image"` | `"video"`). The node data's `mediaIds` array is assumed
to contain at least one media ID.

### Download flow (TypeScript)

```
onDownloadClick(resolution)
  → if resolution == cached_resolution
      direct download via mediaUrl(id)
  → else
      call POST /api/media/{id}/upscale { resolution }
      poll GET /api/media/{id}/status every 2 s until ready
      on ready → direct download via mediaUrl(id)
```

### State needed per node

- `upscaleStatus`: `"idle"` | `"pending"` | `"upscaling"` | `"done"` | `"error"`
- `upscaleProgress`: number (0–100) — for the future progress bar
- `selectedResolution`: string — tracked so the dropdown shows the last chosen
  value

The Zustand store in `board.ts` is extended with these fields on the node data
shape.

---

## API — Backend

### New endpoint

```
POST /api/media/{media_id}/upscale
Body:  { "resolution": "2K" | "4K" | "1080" | "4K" }
200:   { "job_id": "<uuid>", "status": "pending" }
```

**Note:** `270p` and `720p` do NOT call the upscale endpoint — they use the
cached asset directly.

### Flow Upscale API (to be reverse-engineered)

The endpoint path, auth mechanism, and response shape are captured from the
Flow Web UI via Chrome DevTools network panel. Currently unknown — this section
will be updated once the live traffic is captured.

The backend is assumed to call the Flow API using the same
`flow_client.api_request()` pattern already used for generation.

### Polling

```
GET /api/media/{media_id}/status
200: { "status": "pending" | "upscaling" | "done" | "error",
       "url": "<GCS URL when done>" }
```

The existing `GET /api/media/{media_id}/status` in `media.py` already covers
cache states; extend it to also reflect in-progress upscale job state.

### File serve

`GET /media/{media_id}` already handles GCS → client streaming. No change needed
there; the upscale URL is stored back into `asset.url` / `asset.local_path` so
the same path works for upscaled assets.

---

## Data model

`Asset` model (`db/models.py`) fields used:

- `uuid_media_id`: unique media identifier (already indexed)
- `url`: GCS signed URL or path
- `local_path`: local cache path
- `status`: `pending | ready | upscaling | error`
- `upscale_job_id`: UUID of the active Flow upscale job (nullable)
- `upscale_resolution`: string (nullable)

---

## Error handling

| Condition                      | Behaviour                                          |
|--------------------------------|----------------------------------------------------|
| Upscale API returns error      | Set node `status = "error"`, show toast            |
| Poll times out (>120 s)         | Set `status = "error"`, show toast, allow retry    |
| User picks same resolution      | Direct download, no API call                       |
| No media IDs on node           | Show toast "no media to download"                   |
| Asset not yet cached            | Show toast "media not ready, please wait"          |

---

## Out of scope (Phase 2)

- Progress bar during upscale (only status text: "Upscaling…")
- Credit cost display / confirmation
- Batch download with mixed resolutions
- Storing upscale presets per node
