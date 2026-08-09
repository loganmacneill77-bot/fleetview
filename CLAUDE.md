# FleetView — instructions for Claude Code

You are working in FleetView: a single-file, stdlib-only Python dashboard of
the Claude Code sessions running on this machine. Read `README.md` first.

## If the user asks you to set it up

Do these in order and **report what you actually observed** at each step, not
what you expected. Every one of them is checkable.

1. **Python.** `python --version` — needs 3.9+. If it is missing, stop and say
   so; do not try to install it.
2. **Start it.** `python fleetview.py` in the background, then
   `curl http://localhost:7936/data.json`. You should get JSON with a `cards`
   array. If port 7936 is busy, FleetView exits with a message — find what is
   on the port rather than changing the port silently.
3. **Check it can see sessions.** The `cards` array should have an entry per
   running Claude Code session. If it is empty, confirm
   `~/.claude/sessions/*.json` exists — if that directory is missing, this
   version of Claude Code does not record sessions the way FleetView expects,
   and you should say that plainly instead of debugging around it.
4. **Autostart.** `.\install.ps1`. It registers a Scheduled Task and verifies
   the port answers.
5. **Phone access** — only if they want it. Requires Tailscale:
   `tailscale serve --bg 7936`, then `tailscale serve status` for the URL.
   Tell them to check Tailscale is on *on the phone* before debugging DNS.
6. **Dictation** — only if they want it. Needs `GROQ_API_KEY` set at **User**
   level, then a FleetView restart so the task inherits it. Say plainly that
   this is the one feature that sends data (audio) off the machine.

Then tell them the URL and stop. Do not tune, refactor, or "improve" anything
that is working.

## If the user asks you to change it

- **Python standard library only.** No pip installs, no `requirements.txt`, no
  build step. This constraint is the reason the whole thing is one file you
  can read in an afternoon; do not relax it to save yourself effort.
- **Run the tests before and after**: `python test_fleetview.py` prints
  `ALL OK`. If you add a test that guards a specific bug, **break the fix,
  watch that exact test fail, restore it, watch it pass — and say that you
  did.** A test that passes for the wrong reason is worse than no test.
- **Never read a whole transcript.** They reach hundreds of MB. Tail the last
  200KB, plus the head for the opening message.
- **Filter sessions** to `kind == "interactive"` AND `entrypoint != "sdk-cli"`.
  FleetView's own summariser calls are headless `claude -p` runs that also
  report `kind: "interactive"` — without the second condition it summarises
  itself in a loop and burns tokens.
- **Console I/O must stay in a child process.** `AttachConsole` is
  process-wide and a process can hold only one console, so calling it in the
  server would detach the server from its own console and break every
  subsequent read.
- **Measure before optimising.** The one time this dashboard "felt slow" it
  was `localhost` resolving to `::1` against an IPv4-only listener — 2 seconds
  per request, including for the static page. Time a static request first; if
  the floor is high, the cost is in the transport, not your code.
- **Verify layout in a browser, not by reading CSS.** Mobile bugs here have
  twice been caused by a rule in a *second* media query that no amount of
  reading found. Screenshot it, or query the computed style.

## Things that are deliberate, not oversights

- **No authentication.** It binds loopback only and is meant to be reached
  over a tailnet. If you are tempted to add auth, ask first — the user may
  simply not want the complexity.
- **No dependencies, no framework, no build.** See above.
- **The card does not disappear when you close a session.** It greys out when
  the process actually dies, because `/exit` gets swallowed if a picker is
  open, and a card that vanished anyway would be lying.
- **Summaries are cached and rate-limited by transcript growth.** They cost
  tokens. Do not make them more frequent without being asked.
