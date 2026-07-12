# PWC hub

The cloud control plane for PWC: a single Cloudflare Worker in front of one D1
database, serving any number of workspaces (distinguished by a `workspace`
column on every row). It mirrors `scripts/taskdb.py` operation-for-operation
so a workspace can point its `.pwc/sources.json` at `{"store": "hub", ...}`
instead of `{"store": "local"}` with no change in what the CLI/skills call.
See `docs/hub-design.md` for the full design and the reasoning behind it.

This directory is a **public template** — no real database id, domain, or
secret lives here. Your actual deployment (ids, custom domain, backup wiring)
belongs in a private instance repo; see Decision 3 in the design doc.

## Deploy your own instance

```bash
# 1. Copy hub/ into your private instance repo, then from there:
wrangler d1 create pwc-hub                                    # note the printed database_id
# paste that id into wrangler.jsonc's d1_databases[0].database_id
wrangler d1 execute pwc-hub --remote --file=schema.sql         # apply the schema
wrangler secret put PWC_TOKEN                                  # bearer token every caller must send
wrangler deploy
```

## API surface

`GET /health` needs no auth and returns `{"ok": true}`. Every other route is
`POST /w/:workspace/:op` with `Authorization: Bearer <PWC_TOKEN>` and a JSON
body of that op's fields, where `:op` is one of taskdb.py's subcommands
(`summary`, `detail`, `stale`, `parked-aging`, `events`, `find-refs`,
`find-session`, `add-task`, `update-task`, `add-ref`, `log-event`,
`set-session`, `clear-session`, `archive`, `promote`, `merge`) plus two hub-only
additions, `export` and `import`, for moving a workspace's data in or out.
