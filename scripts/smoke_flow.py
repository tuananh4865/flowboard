#!/usr/bin/env python3
"""End-to-end smoke test: project -> image -> video through a running agent.

Needs (on your own machine): agent running (`make agent`), the Flowboard
Bridge extension loaded, and a signed-in flow.google.com tab on a Pro/Ultra
plan. Costs real Flow credits (1 image + 1 video, lite quality).

    python scripts/smoke_flow.py [--agent http://127.0.0.1:8101] [--skip-video]

Exit 0 only if both an image and a video came back. On any failure the raw
Flow response is printed so it can be pasted back for debugging.
"""
import argparse
import json
import sys
import time
import urllib.error
import urllib.request


def call(base, method, path, body=None):
    req = urllib.request.Request(
        base + path,
        method=method,
        data=json.dumps(body).encode() if body is not None else None,
        headers={"content-type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.status, json.loads(r.read() or b"null")
    except urllib.error.HTTPError as e:
        raw = e.read().decode(errors="replace")
        try:
            return e.code, json.loads(raw)
        except ValueError:
            return e.code, raw


def fail(step, detail):
    print(f"\nFAIL at {step}:\n{json.dumps(detail, indent=2, default=str)[:4000]}")
    sys.exit(1)


def run_request(base, rtype, params, timeout_s):
    code, req = call(base, "POST", "/api/requests", {"type": rtype, "params": params})
    if code != 200:
        fail(f"enqueue {rtype}", req)
    rid, deadline = req["id"], time.time() + timeout_s
    while time.time() < deadline:
        _, row = call(base, "GET", f"/api/requests/{rid}")
        if row["status"] in ("done", "failed"):
            if row["status"] == "failed" or row.get("error"):
                fail(rtype, row)
            return row["result"]
        time.sleep(3)
    fail(rtype, f"timeout after {timeout_s}s")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--agent", default="http://127.0.0.1:8101")
    ap.add_argument("--skip-video", action="store_true")
    a = ap.parse_args()
    base = a.agent.rstrip("/")

    code, health = call(base, "GET", "/api/health")
    print("health:", json.dumps(health.get("ws_stats") if isinstance(health, dict) else health))
    if code != 200 or not health.get("ws_stats", {}).get("connected"):
        fail("extension connection", health)
    if not health["ws_stats"].get("flow_key_present"):
        fail("bearer token", "Open flow.google.com in a signed-in tab, then retry.")

    _, board = call(base, "POST", "/api/boards", {"name": "smoke-test"})
    code, proj = call(base, "POST", f"/api/boards/{board['id']}/project")
    if code != 200:
        fail("create project (tRPC)", proj)
    pid = proj["flow_project_id"]
    print("project:", pid)

    img = run_request(base, "gen_image",
                      {"prompt": "a red apple on a white table", "project_id": pid,
                       "variant_count": 1}, 180)
    ids = img.get("media_ids") or []
    if not ids:
        fail("gen_image returned no media_ids", img)
    print("image OK:", ids[0])

    if a.skip_video:
        print("\nPASS (image only)")
        return
    vid = run_request(base, "gen_video",
                      {"prompt": "the apple slowly rotates", "project_id": pid,
                       "start_media_id": ids[0], "video_quality": "lite"}, 600)
    if not (vid.get("media_ids") or []):
        fail("gen_video returned no media_ids", vid)
    print("video OK:", vid["media_ids"][0])
    print("\nPASS (image + video)")


if __name__ == "__main__":
    main()
