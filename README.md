# FleetView

A dashboard for every Claude Code session running on your machine — and a way
to drive them from your phone.

One Python file. No dependencies. Nothing leaves your computer (with one
clearly-marked exception: dictation).

```
┌─────────────────────────────────────────────────────┐
│ FleetView   4 sessions · 2 busy      + New   Compact│
├─────────────────────────────────────────────────────┤
│  ▸ (72) Fix the auth redirect loop            ●     │
│         Running the integration suite               │
│                                                     │
│  ▸ (26) Migrate the billing tables            !  ×  │
│         Waiting on you — answer the prompt          │
│         ┌───────────────────────────────────────┐   │
│         │ Do you want to run this migration?    │   │
│         │  [1 Yes]  [2 Yes, always]  [3 No]     │   │
│         └───────────────────────────────────────┘   │
│                                                     │
│  ▸ (10) Rewrite the onboarding email          ✓  ×  │
│         All tasks done — time to /clear             │
└─────────────────────────────────────────────────────┘
```

## What it does

- **See every session at a glance** — a context-usage ring, an AI-written
  one-line summary of what each session is *for*, and a state glyph: `●`
  working, `!` blocked on a prompt, `✓` all tasks done (time to `/clear`),
  `…` idle. Blocked sessions sort to the top.
- **Answer prompts from your phone.** When a session hits a permission or
  choice prompt, the options appear as buttons. Tap one; it gets typed into
  the real terminal.
- **Watch the actual terminal.** Not a reconstruction — the live console
  buffer, colour-coded, updated every couple of seconds.
- **Type into a session.** Your message goes into the terminal you were
  already using. It does *not* fork a new session — no `--resume`, no second
  transcript. Multi-line works (Shift+Enter), and slash commands work because
  they are just text typed at the prompt.
- **Send a photo.** Pick one from your phone; FleetView saves it and drops the
  path into the message box, which is how Claude Code takes images.
- **Dictate** a message instead of typing it (optional — see below).
- **Start and close sessions** — `+ New` opens one in a folder you choose, `×`
  types `/exit`.
- **Install it to your home screen.** It is a PWA with an icon; over Tailscale
  it works from anywhere.

## Requirements

- **Windows.** The core mechanism is Win32 console I/O (`AttachConsole` +
  `WriteConsoleInput` / `ReadConsoleOutputCharacter`), so macOS and Linux are
  not supported. See [Porting](#porting) if you want to change that.
- **Python 3.9+** — standard library only, nothing to `pip install`.
- **Claude Code**, with at least one session running.
- Optional: **Tailscale** for phone access, and a **Groq API key** for
  dictation.

## Quick start

```powershell
git clone https://github.com/<you>/fleetview.git
cd fleetview
python fleetview.py
```

Open <http://localhost:7936>. That is the whole install.

To have it start with Windows and run without a console window:

```powershell
.\install.ps1            # registers a Scheduled Task called "FleetView"
.\install.ps1 -Uninstall
```

`install.ps1` finds your `pythonw.exe` rather than hardcoding a path — but it
does store the absolute path in the task, so **re-run it after a Python
upgrade**.

### Setting it up with Claude Code

This repo ships a `CLAUDE.md` written for *your* Claude Code. Open the folder
and say:

> set up FleetView

It will start it, register the Scheduled Task, offer to expose it over
Tailscale, and check dictation. Everything it does is also written out below,
so you never have to take its word for it.

## Using it from your phone

FleetView binds to loopback only. To reach it from a phone, put it on your
tailnet — this does **not** expose it to the internet:

```powershell
tailscale serve --bg 7936
tailscale serve status          # prints the https://<machine>.ts.net URL
```

Open that URL on your phone and **Add to Home Screen**. Tailscale terminates
TLS, so it is HTTPS, which is also what lets the microphone work.

If the name will not resolve, check Tailscale is actually *on* on the phone —
`tailscale serve` is tailnet-only by design, so with it off there is nothing to
resolve.

## Dictation (optional)

The mic button records, sends the clip to Groq's `whisper-large-v3`, and puts
the text in the message box for you to read before sending.

```powershell
[Environment]::SetEnvironmentVariable('GROQ_API_KEY','gsk_…','User')
```

Restart FleetView afterwards so it inherits the variable. Without a key the
button simply reports that it is not configured; everything else works.

> **This is the only part of FleetView that leaves your machine.** Audio goes
> to Groq. Nothing else — transcripts, terminals, photos, summaries — ever
> leaves. If that is not acceptable, don't set the key and don't use the mic.

## Security model

Read this before you expose it anywhere.

**FleetView can type into your terminals and start processes.** That is the
point of it, and it means anyone who can reach the port can do the same. There
is no login.

What protects you:

- It binds **127.0.0.1 and ::1 only**. Nothing else can connect unless you
  deliberately proxy it.
- `tailscale serve` keeps it inside your tailnet — your devices, no one else's.
- A `Host` allow-list (localhost / `*.ts.net`) blocks DNS rebinding.
- `POST` requires `Content-Type: application/json`, which forces a CORS
  preflight this server never answers, so a hostile web page cannot drive it.
- `/new` takes a **folder**, never a command line, and only ever launches
  `claude`.
- Uploads are written under `uploads/` with a name FleetView chooses
  (timestamp + content hash); the client's filename is used for nothing but
  its extension, checked against a whitelist.

What does **not** protect you: putting it behind a plain port-forward, a
reverse proxy on the open internet, or a tunnel with public ingress. Don't.

Everything it reads is read-only: `~/.claude/sessions/*.json` and the session
transcripts. The only files it writes are its own `summaries.json`,
`fleetview.log`, and `uploads/`.

## Costs

Summaries are written by `claude -p` using Haiku, and a session is only
re-summarised once its transcript has grown by 15KB. The cache survives
restarts. In practice this is pennies a day, but it is not zero, and it does
consume your Claude quota.

## How it works

| Layer | Mechanism |
|---|---|
| Which sessions exist | `~/.claude/sessions/*.json` — pid, id, cwd, status |
| What they are doing | tail of `~/.claude/projects/*/<id>.jsonl` (never the whole file) |
| Summaries | `claude -p` with Haiku, cached in `summaries.json` |
| Typing into a session | `AttachConsole(pid)` + `WriteConsoleInputW` on `CONIN$` |
| Mirroring the terminal | `ReadConsoleOutputCharacterW` on `CONOUT$` |
| Serving the UI | `ThreadingHTTPServer`, one HTML page, polled every 3s |

`AttachConsole` is process-wide and a process may hold one console at a time,
so every console read and write happens in a short-lived **child process** —
`fleetview.py --type <pid> <text>` and `--screen <pid>`. That is also why they
pass `CREATE_NO_WINDOW`: without it, a console flashes on screen every few
seconds.

More detail, and the things that were surprising enough to write down, are in
[docs/HOW-IT-WORKS.md](docs/HOW-IT-WORKS.md) and
[docs/NOTES.md](docs/NOTES.md). The `D<n>` references scattered through the
source point at the numbered entries in NOTES.

## Tests

```powershell
python test_fleetview.py        # prints ALL OK
```

Assert-based, no framework, no fixtures beyond one sample transcript. Every
test that guards a specific defect was checked by breaking the fix and
watching that exact test fail — if you add one, do the same, or you are
writing a test that passes for the wrong reason.

## Porting

The Windows-only part is small and well isolated: `type_into_console`,
`read_console_screen`, and the two `--type` / `--screen` child modes. On
macOS/Linux the equivalent is a pty — but Claude Code sessions you started in
a normal terminal aren't attached to one you control, so the honest answer is
that it needs `tmux` (or similar) and a different discovery step. Everything
else — session discovery, transcript parsing, the UI, uploads, dictation — is
platform-neutral.

## Licence

MIT. See [LICENSE](LICENSE).
