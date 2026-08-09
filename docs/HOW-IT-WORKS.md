# How FleetView works

One file, `fleetview.py`, about 1,500 lines including the HTML and JS. No
dependencies. This document is the map.

## The four data sources

Everything FleetView knows comes from files Claude Code already writes, plus
the Windows console API. It writes nothing of its own except `summaries.json`,
`fleetview.log` and `uploads/`.

### 1. `~/.claude/sessions/*.json` — which sessions exist

One small JSON file per session, named after its pid. The fields that matter:

| Field | Use |
|---|---|
| `pid` | the console to type into and read from; also liveness |
| `sessionId` | joins to the transcript |
| `cwd` | which project it is in |
| `status` | `busy` / `idle` |
| `name` | a short auto-generated label (folder + hash) |
| `kind`, `entrypoint` | filtering — see below |

**Filter to `kind == "interactive"` AND `entrypoint != "sdk-cli"`.** Headless
`claude -p` runs also report `kind: "interactive"`, so `kind` alone does not
discriminate — and FleetView's own summariser *is* a headless run. Without the
second condition it discovers itself, summarises itself, and loops.

A session sitting on the folder-trust prompt has not written its file yet, so
FleetView cannot see it. That is a real gap, not a bug you can fix from here.

### 2. `~/.claude/projects/<slug>/<sessionId>.jsonl` — what happened

The transcript. **Never read it whole** — these reach hundreds of megabytes.
FleetView reads the last 200KB (for recent events, tool calls, todo state and
token counts) plus the first line (for the opening message, which is the best
available statement of what the session is *for* before an AI summary exists).

Assistant "thinking" is **not** available: the field exists but is empty on
disk, so a thinking view cannot be built from this.

### 3. `claude -p` — the summaries

A Haiku call turns the tail of a transcript into `{goal, activity, done_tasks,
advice}`. Cached in `summaries.json`, keyed by session, and only refreshed once
the transcript has grown 15KB since the last one. The cache survives restarts.

This is the only part that costs money, and the growth threshold is what keeps
it to pennies.

### 4. The Win32 console — typing and mirroring

The interesting part.

```
AttachConsole(pid)                 attach to another process's console
  CreateFileW("CONIN$")            → WriteConsoleInputW    (typing)
  CreateFileW("CONOUT$")           → GetConsoleScreenBufferInfo
                                     ReadConsoleOutputCharacterW  (mirroring)
FreeConsole()
```

**Both must run in a child process.** `AttachConsole` is process-wide and a
process can hold exactly one console; calling it inside the server would
detach the server from its own console and poison every later call. So the
server shells out to itself:

```
python fleetview.py --type   <pid> <text>
python fleetview.py --screen <pid>
```

Both are spawned with `CREATE_NO_WINDOW`, or a console flashes on screen every
few seconds — intolerable if you are doing anything else on the machine.

Two details that are not obvious:

- **Text and Enter must be separate writes**, with a pause between. Sent in one
  buffer, the text lands but is never submitted.
- **A newline in the payload means "press Enter here".** To put a newline
  *inside* a message, the line is ended with a backslash first — the TUI's own
  continuation. Verified against the transcript, not the screen.

Screen reads cost about 65ms each, which is cheap enough to poll every 2s.

## Request flow

```
browser ──GET /              one HTML page, inline CSS + JS, no build step
        ──GET /data.json     every card, every 3s
        ──GET /detail.json   one session: wider caps + a fresh console read
        ──POST /send         type text into a session's console
        ──POST /new          start a session in a folder
        ──POST /upload       save a photo, return its path
        ──POST /dictate      audio → Groq Whisper → text
```

Three background threads, all daemons:

| Thread | Every | Job |
|---|---|---|
| `refresh_loop` | 3s | rescan sessions, parse transcripts |
| `summarize_loop` | 10s | summarise anything that grew 15KB |
| `screen_loop` | 2s | mirror every live console |

The HTTP server is a `ThreadingHTTPServer` on **both** `127.0.0.1` and `::1`.
Both, because `localhost` resolves to `::1` first on Windows and a v4-only
listener makes every request pay a doomed IPv6 connect — 2 seconds each from
some clients. See NOTES D34.

## State detection

What turns a session into a glyph:

```python
if needs_attention(screen):  "attention"   # ! — a choice widget on screen
elif status == "busy":       "working"     # ●
elif todos and all done:     "done"        # ✓ — time to /clear
else:                        "waiting"     # …
```

`needs_attention` looks for a numbered option list *and* a footer reading
"Enter to confirm" / "Esc to cancel" — but **only in the last 12 non-blank
lines**. Matching anywhere on screen turns any terminal that happens to be
displaying a quoted prompt into a false positive. A real widget is the last
thing drawn.

`prompt_block` then parses that widget into `{question, options}` so the
options can be rendered as buttons. Answering one types the digit.

## The UI

One page, two modes, all state in JS:

- **Compact** — one row per session: context ring, the session's *goal* as the
  headline, state glyph, close button. Blocked sessions sort to the top.
- **Expanded** — the full card: todos, event feed, advice chip, message box.
- **The sheet** — tap a session: the live terminal, colour-coded by the TUI's
  own line prefixes, with the parsed event log folded away beneath it, and the
  message box with photo, mic and send buttons.

The grid is rebuilt with `innerHTML` every 3s, which destroys anything
half-typed — so drafts live in JS and are written back after each render, and
while a *card* box has focus the grid holds still (rebuilding the focused
element closes the keyboard on iOS).
