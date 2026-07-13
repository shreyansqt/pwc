"""HTTP driver for hub-backed workspaces — the `hub` counterpart to local sqlite.

When a workspace's .pwc/store.json says {"store": "hub", ...}, taskdb.py routes
every subcommand here instead of touching a local database. The client is a dumb
passthrough by design: it POSTs the parsed argparse fields as JSON to
POST <url>/w/<workspace>/<op> and prints the response body verbatim — the hub
returns exactly the JSON the local emit() would have printed, so skills and
callers cannot tell the backends apart. All semantics live server-side
(hub/src/index.ts); keeping this thin is what keeps the two implementations from
drifting apart at the seam.

Online-only by design (v1): no read cache, no write spool — an unreachable hub
is a clean error, not silent divergence.
"""

from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

from _common import emit, fail, ssl_context as _ssl_context

# argparse Namespace fields that are routing, not operation arguments.
_SKIP = {"workspace", "func", "cmd"}

_DEFAULT_TOKEN_FILE = "~/.config/pwc/hub-token"


def _token(store: dict) -> str:
    path = Path(store.get("token_file") or _DEFAULT_TOKEN_FILE).expanduser()
    try:
        token = path.read_text().strip()
    except OSError:
        fail(f"hub token not found at {path} — put the bearer token there "
             f"(chmod 600), or set 'token_file' in .pwc/store.json")
    if not token:
        fail(f"hub token file {path} is empty")
    return token


def run(op: str, args, store: dict) -> None:
    """Execute one taskdb subcommand against the hub and print its response."""
    url = store["url"].rstrip("/") + f"/w/{store['workspace']}/{op}"
    payload = {k: v for k, v in vars(args).items() if k not in _SKIP}
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode(),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {_token(store)}",
            # urllib's default "Python-urllib/3.x" UA trips Cloudflare's browser
            # integrity check (error 1010) — identify as a real client instead.
            "User-Agent": "pwc-hub-client/1.0",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30, context=_ssl_context()) as resp:
            # Re-emit through the same formatter local mode uses, so output is
            # byte-identical between backends (the hub sends compact JSON).
            emit(json.loads(resp.read().decode()))
    except urllib.error.HTTPError as e:
        body = e.read().decode()
        try:
            message = json.loads(body).get("error", body)
        except ValueError:
            message = body[:300]
        fail(f"hub: {message}")
    except urllib.error.URLError as e:
        fail(f"hub unreachable ({store['url']}): {e.reason} — this workspace is "
             f"hub-backed and needs network for task-database operations")
