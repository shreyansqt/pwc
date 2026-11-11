# Agent orchestrators compared with PWC

*Researched 10.11.2026 for task `pwc-orchestrator-compare`. Shreyans asked for this
comparison on 10.11.2026. Facts come from the project READMEs, docs, release pages
and the GitHub API on that day. "Not verified" means that no primary source
confirmed the fact. These projects release several times a day, so check the
facts again before a decision that depends on them.*

## Decision and status (updated 11.11.2026)

- **Decision: combine PWC with Orca.** On 10.11.2026 Shreyans chose Orca over the
  T3 Code recommendation below. PWC keeps the task database, the routing and the
  sources. Orca is the place where workers run, on the Mac and on the phone.
- **Why Orca won:**
  - Orca runs the installed `claude` binary in a terminal, so Claude work uses the
    Claude subscription in the normal way. T3 Code drives Claude through the Claude
    Agent SDK. That is a grey area under Anthropic's terms, and open T3 issues
    report "Credit balance is too low" errors (#29393, #31577, #29674).
  - Codex and Cursor use their existing CLI logins in Orca. T3 Code's Cursor
    adapter needs a separate sign-in.
  - Orca reads the transcripts of each CLI, so it shows and resumes sessions that
    PWC started outside Orca.
- **Built in PWC (commits db532ab and 4f4db9c):**
  - `pwc spawn --launcher orca`, or `<workspace>/.pwc/launcher.json` with
    `{"launcher": "orca"}`, opens the worker with `orca terminal create`;
  - `pwc done` closes the Orca tab through `$ORCA_TERMINAL_HANDLE`;
  - smarta uses Orca by default since 11.11.2026; side-projects and home-admin
    still use iTerm2.
- **Limits today:** `--ssh` remote workers and opencode with a seed prompt still
  need iTerm2. Orca's SSH mode is not used, because SSH-host sessions are
  unreachable from the phone when the desktop sleeps (Orca issue #40079). The Mac
  mini will join later as a remote Orca server (`orca serve`).
- **Relay and client data:** the Orca relay is end-to-end encrypted by design
  (keys pinned in the pairing QR code). Push notification text is not (Orca issue
  #25462), and the relay servers are in the US and Hong Kong. Keep this in mind for
  smarta work; see [`data-handling.md`](data-handling.md).
- **T3 Code trial (started 11.11.2026):** Shreyans wants both tools tested in
  real use. side-projects uses T3 Code (`.pwc/launcher.json` = `t3`), and smarta
  uses Orca. PWC's `t3` launcher starts a worker with `t3 thread start` and
  records it as `t3:<thread id>`. The first test thread answered on the Claude
  subscription through the Agent SDK, without a "Credit balance is too low"
  error. Known gaps: the t3 CLI cannot stop or archive a thread (archive by hand
  in the app), PWC cannot pass the reasoning effort, `pwc cost` cannot price T3
  threads, and T3 Connect is not end-to-end encrypted.
- **Still open:**
  - the phone test (pairing, dictation, notifications), for Orca and T3 Code;
  - the decision between Orca and T3 Code after the trial;
  - the board page on the hub ([pwc #21](https://github.com/shreyansqt/pwc/issues/21)
    (hub web app: a board UI for PWC tasks)).

The research below is from 10.11.2026 and is kept as it was written. Its
recommendation of T3 Code is superseded by the decision above.

## Research summary (10.11.2026)

- **Recommendation: combine.** PWC keeps the task database, the routing and the
  sources (Slack, email, calendar, GitHub, Jira). **T3 Code** becomes the place
  where the worker sessions run, and Shreyans uses it from his phone.
- T3 Code supports the same six harnesses that PWC routes to: Claude, Codex,
  Cursor, Grok, OpenCode and Antigravity (`agy`). It has native iOS and Android
  apps, a web client, a background-service mode for the Mac mini, and access over
  Tailscale. It also has a CLI (`t3 thread start|send|show|list`) that `pwc spawn`
  can call instead of opening an iTerm2 tab.
- **Orca** (Stably AI) is the second choice. It is equally strong on the phone and
  on harnesses. It is better at one thing: it can resume sessions that were started
  outside Orca. Its task layer is experimental and lives inside the running app.
- **No tool has a task board that can replace the PWC board.** Each tool tracks
  sessions, not tasks from Slack, email or a calendar. So PWC builds one small
  missing part: a board page on the existing hub Worker, with a link from each task
  to its session.
- **Replace PWC: no.** None of the tools routes by quota and cost, and none keeps
  tasks that have no repository or ticket.

## How I identified the products

- **"Orca"** has many matches on GitHub. Only **Orca by Stably AI**
  (`stablyai/orca`, onorca.dev) is a coding-agent orchestrator with a phone app,
  SSH hosts and a task list. That is the same job as PWC. The other repos named
  "orca" are small copies of its wording, or they do a different thing
  (`VirtusLab/orca` runs fixed AI development flows). So I chose Stably AI's Orca.
- **"T3 Code"** is `pingdotgg/t3code`, by Theo Browne's company T3. A search found
  only forks and copies of it (for example "t1code" and "t4code"). It is the only
  product with this name, so the choice is clear.

## What PWC is today (the baseline)

- PWC is a CLI (`pwc`) plus skills. A Claude Code session is the coordinator.
- The task database is SQLite, or the **hub**: a Cloudflare Worker with a D1
  database and an HTTP API (`POST /w/<workspace>/<op>`). The side-projects
  workspace uses the hub today.
- Tasks can come from any source. A task does not need a ticket.
- `pwc spawn` routes each task to a harness and a model by live quota and cost.
  The harnesses are `claude`, `codex`, `cursor-agent`, `opencode`, `agy` and
  `grok`.
- Each worker opens in an iTerm2 tab. A remote worker runs in tmux on another Mac
  over SSH (Claude harness only).
- Gaps: no phone access except Claude remote control, no UI, and the iTerm2
  dependency.

## The options

### T3 Code (`pingdotgg/t3code`)

- **What it is:** a server (`t3`) that runs agent CLIs, with three clients: a
  desktop app (Electron), a web client (app.t3.codes or the local server) and
  native iOS and Android apps. Each thread can get its own git worktree (a second
  working copy of the repository).
- **Maintainer, licence, activity:** T3 (Theo Browne). MIT licence. The repo
  started on 08.02.2026. About 55k stars. Latest stable release v0.1.8 on
  09.11.2026, with nightly builds several times per day. The README calls the
  project "very very early". It does not accept most outside contributions.
- **Phone:** native iOS and Android apps, and a web client. Remote access over
  Tailscale (`t3 serve --tailscale-serve`), over T3 Connect (a sign-in relay
  through a Cloudflare tunnel, no port forwarding), over LAN or over SSH. Pairing
  uses a one-time link per device, and you can revoke it.
- **Harnesses:** Codex (through `codex app-server`), Claude Code (through the
  Claude Agent SDK), Cursor, Grok Build, OpenCode and Google Antigravity. Each CLI
  must be installed and logged in on the host.
- **Resume:** T3 threads resume inside T3. Import of sessions that started outside
  T3: not verified.
- **Storage and access from PWC:** state is in `~/.t3` (SQLite not verified).
  The model is projects and threads; there is no task board. External tools can
  use:
  - the host CLI `t3 thread start|send|show|list` (with `send --wait`), which
    needs no sign-in on the host;
  - an MCP server at `<address>/mcp`, with OAuth and permission levels from
    read-only to full access.
- **Machines:** the desktop app adds remote Macs or Linux hosts over SSH. It can
  also choose a machine for a new thread. T3 Connect allows 3 linked machines per
  account for free.
- **Terminal:** its own terminals (Ghostty-based). No iTerm2 and no tmux needed.
- **Cost and setup:** free; it uses your own subscriptions. Install with a `curl`
  script, a Homebrew cask, or `npx t3@latest` for a test. `t3 up` or
  `t3 service install` runs it as a background service on macOS.

### Orca (`stablyai/orca`)

- **What it is:** a desktop "agent development environment". It runs many agents
  in parallel git worktrees, with a chat view or a terminal view, diffs, a browser,
  a CLI and scheduled automations.
- **Maintainer, licence, activity:** Stably AI. MIT licence. The repo started on
  17.03.2026. Latest release v1.4.255 on 09.11.2026. The GitHub API shows about
  121k stars and very high commit activity. Older posts give much lower star
  counts, so treat the number with care.
- **Phone:** native iOS app ("Orca IDE") and an Android APK. The phone pairs with
  the desktop app by QR code, through a relay or on the LAN. Without the desktop
  app, run a headless server: `orca serve --pairing-address <tailscale-ip>
  --mobile-pairing`. The phone gets a notification when an agent needs input.
  Web client: not verified.
- **Harnesses:** "any CLI agent"; the site lists 27, including Claude Code, Codex,
  Cursor, Gemini, OpenCode and Grok.
- **Resume:** "Agent Session History" reads the on-disk transcripts of each CLI,
  so it also shows sessions that started in a plain terminal, and it can resume
  them. This fits PWC's rule that a task resumes its prior session.
- **Storage and access from PWC:** an experimental orchestration layer (turn it on
  in Settings, Experimental) with `orca orchestration task-create`, `task-list`,
  `task-update`, `worker-start`, `worker-stop`, `dispatch` and `send`. The CLI
  talks to the running app. Where the state is on disk: not verified. It also
  shows GitHub and Linear issues. MCP and hooks are mentioned but not verified.
- **Machines:** local, SSH hosts, a remote Orca server, or a cloud VM per
  worktree. Agents keep running when the laptop sleeps.
- **Terminal:** its own terminal with splits. No iTerm2.
- **Cost and setup:** free. `brew install --cask stablyai/orca/orca` or a dmg.

### Other options I checked

- **Happy** (`slopus/happy`, MIT, about 24.6k stars): one end-to-end encrypted
  phone app (iOS, Android, web) for Claude, Codex and Grok. Gemini and OpenCode
  through other adapters. cursor-agent: not verified. You start `happy claude`
  instead of `claude`, so `pwc spawn` could use it. No task board, no UI on the Mac.
  It is the best "phone only" add-on, but T3 Code and Orca cover more harnesses
  and add a desktop UI.
- **CloudCLI** (`siteboon/claudecodeui`, AGPL-3.0): a web UI for Claude Code,
  Cursor CLI and Codex. It finds existing Claude sessions. No native app, and no
  end-to-end encryption (not verified).
- **Agent Deck** (`asheshgoplani/agent-deck`, MIT): a tmux session manager with
  SQLite, a JSON CLI, SSH hosts and a local web UI. It is the closest to PWC's
  design, but its phone access is only a web page or a chat bot.
- **Official vendor apps:** Claude remote control (Claude only), Codex in the
  ChatGPT phone app (since 25.06.2026, paired from the Codex desktop app), and
  Cursor remote control (Cursor app only, not the `cursor-agent` CLI). These are
  three separate apps, and only Claude's works with a session that PWC started.
- **Not a fit:** Vibe Kanban (the README says the project is "sunsetting"),
  Omnara (now a different product with its own agent loop), Claude Squad (no
  phone), Nimbalyst (iOS only, no cursor-agent), CC Pocket (Claude and Codex
  only), Conductor (phone app not verified, closed source).

## Comparison table

| Need | PWC today | T3 Code | Orca | Happy |
|---|---|---|---|---|
| 1. Phone, all harnesses | Claude only (remote control) | iOS, Android, web; all 6 PWC harnesses | iOS, Android APK; all 6 PWC harnesses | iOS, Android, web; Claude, Codex, Grok (cursor-agent not verified) |
| 2. Task board UI | None | Threads only, no board | Session list; experimental task list | Session list only |
| 3. Own task database, any source | Yes (SQLite or hub D1, HTTP API) | No. PWC can drive it through the CLI or MCP | Experimental, inside the app. PWC can drive it through the CLI | No |
| 4. Harnesses, routing | 6 harnesses, routed by quota and cost | 6 harnesses, no routing | 27 agents, no routing | 3 to 5, no routing |
| Resume outside sessions | Yes (its own sessions) | Not verified | Yes (reads transcripts) | Not verified |
| 5. Workspaces, two Macs | Per-workspace boards; remote workers over SSH (Claude only) | Projects; remote hosts over SSH; Tailscale | Repos; SSH hosts; `orca serve` | One daemon per Mac |
| 6. Terminal | iTerm2 required | Own terminals | Own terminal | Your terminal |
| Licence, cost | Own code | MIT, free | MIT, free | MIT, free |
| Maturity | Daily use since 2026 | "Very very early", daily releases | Daily releases, experimental task layer | Stable releases |

## Recommendation: combine PWC with T3 Code

**Split of work:**

- **PWC keeps:** the task database (hub D1), sources and find-work, routing by
  quota and cost, the coordinator, costs, and `pwc done`.
- **T3 Code gives:** the place where workers run, a desktop UI for sessions, and
  phone access for every harness.
- **PWC builds:** a small board page served by the hub Worker. The hub already has
  the HTTP API, so the page only reads `summary` and calls `update-task` and
  `log-event`. Each task links to its T3 thread. Then the phone shows the board and
  one tap opens the session. The hub design already planned a phone surface on
  this API (see [`hub-design.md`](hub-design.md), Decision 5).

**Why T3 Code over Orca:**

- Its harness list is the same as PWC's list, so routing needs no change.
- It has a web client and a background-service mode, so the headless Mac mini can
  host it without a desktop app that stays open.
- Its thread CLI needs no running GUI and no experimental setting. That makes it a
  simpler target for `pwc spawn`.
- Orca stays the fallback. Choose Orca if the trial shows that T3 Code cannot
  resume existing sessions, or that its Claude adapter does not use the Claude
  subscription.

**Why not replace PWC:** no option keeps tasks from Slack, email or a calendar, and
no option routes by quota and cost. Those are the two parts Shreyans values most.

**Why not only build the missing parts in PWC:** a phone client that drives Codex,
Cursor and OpenCode sessions is a large project (terminals, pairing, push
notifications, adapters for each harness). T3 Code and Orca already give this for
free. The board page is small, so PWC builds only that.

**Risks:**

- **Young software.** Both tools change several times a day. A release can break
  the CLI that `pwc spawn` calls. Pin a version on the Mac mini.
- **Client data (smarta).** A relay (T3 Connect, Orca's relay) adds one more
  processor of client data. [`data-handling.md`](data-handling.md) explains why
  that matters. For smarta work, use only the direct Tailscale route and turn the
  relay off.
- **Session tracking.** PWC records a harness session id at spawn time. With
  T3 Code, PWC records the T3 thread id instead, and `worker_status.py` asks
  `t3 thread show`. Cost tracking (`pwc cost`) reads the harness transcripts; the
  trial must confirm that the transcripts still land where `cost.py` reads them.

## Trial plan on the Mac mini (grumpyorange)

Installing anything is Shreyans' choice. These steps wait for his approval.

1. **Install and run as a service.** Install T3 Code with Homebrew and pin the
   version. Run `t3 service install`. Expose it with `t3 serve
   --tailscale-serve`. Leave T3 Connect off.
2. **Pair the phone.** Install the iOS or Android app. Pair it over Tailscale with
   `t3 pair --tailscale`.
3. **Use home-admin only.** Do the trial in the home-admin workspace, which has no
   client data. Open one home-admin repo as a T3 project.
4. **Test each harness by hand.** Start one thread each in Claude, Codex and
   Cursor from the phone. For each thread, check:
   - the thread uses the subscription login, not an API key;
   - the phone can send a message, approve a tool call and stop the thread;
   - the transcript lands where `pwc cost` reads it.
5. **Test the CLI bridge.** On the mini, run `t3 thread start` for a real PWC task
   and record the thread id on the task as a note. Do not change `pwc spawn` yet.
6. **Test resume.** Try to open a session that PWC started earlier in iTerm2. If
   T3 Code cannot do it, test the same step in Orca.
7. **Decide after one week.** Success means three things: Shreyans moved a Codex
   or Cursor task forward from the phone, the CLI bridge worked, and nothing broke
   on a T3 update. If the trial succeeds, the follow-up tasks are:
   - a `t3` launcher in `pwc spawn` next to iTerm2;
   - `worker_status.py` support for T3 threads;
   - the board page on the hub Worker.

## Sources

- T3 Code: https://github.com/pingdotgg/t3code,
  https://github.com/pingdotgg/t3code/releases,
  https://github.com/pingdotgg/t3code/blob/main/docs/user/remote-access.md,
  https://github.com/pingdotgg/t3code/blob/main/docs/user/outside-agents.md
- Orca: https://github.com/stablyai/orca, https://onorca.dev/,
  https://www.onorca.dev/docs/mobile, https://www.onorca.dev/docs/remote-servers,
  https://www.onorca.dev/docs/agents/session-history,
  https://www.onorca.dev/docs/cli/orchestration, https://www.onorca.dev/docs/ways-to-run
- Happy: https://github.com/slopus/happy
- CloudCLI: https://github.com/siteboon/claudecodeui
- Agent Deck: https://github.com/asheshgoplani/agent-deck
- Vibe Kanban: https://github.com/BloopAI/vibe-kanban
- Omnara: https://github.com/omnara-ai/omnara
- Codex in ChatGPT: https://help.openai.com/en/articles/6825453-chatgpt-release-notes
- Cursor remote control: https://cursor.com/docs/cloud-agent/mobile
