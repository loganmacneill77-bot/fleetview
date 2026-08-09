# Notes

The source cites these by number (`D7`, `D21`, …). They are the decisions that
were not obvious, and the bugs that were expensive. Kept because the reasoning
is worth more than the conclusions, and because several of them will bite
anyone building something similar.

---

**D7 — the summariser must not discover itself.** FleetView summarises sessions
by calling `claude -p`, which is itself a session. Filtering on
`kind == "interactive"` is not enough: headless runs report that too. The
discriminator is `entrypoint != "sdk-cli"`. Without it, the dashboard
summarises its own summarisers, forever, at your expense.

**D9 — standard library only.** No dependencies, no build, no framework, no
`requirements.txt`. This is why the whole thing is one readable file and why
installing it is `git clone && python fleetview.py`. Every feature since has
been shaped by it, and none has been worse for it: the PNG icon is generated
with `zlib` and `struct`, the multipart upload to Whisper is fifteen lines.

**D11 / D16 — context windows are not on disk.** Nothing in the session files
or the transcript records a session's context limit. The first attempt inferred
it ("if we have seen more than 200k tokens, it must be a 1M model"), which
mis-rated every 1M session that was *under* 200k — showing "167k / 200k" for a
session that had used 153k of a million. It is now a hand-maintained table
keyed by model id. Hand-maintained is a real cost; guessing was worse.

**D19 — type into the session, don't fork it.** The first version of "send a
message" ran `claude -p --resume <id>`, which starts a *new* session from the
same history. You get a reply, but it is not in the terminal you were looking
at, and your terminal never sees it. Typing into the real console via
`WriteConsoleInput` means a message from your phone is indistinguishable from
one typed at the keyboard — the same session, the same transcript, and slash
commands work for free because they are just text.

**D20 — mirror the terminal.** Typing blind into a session that is showing a
permission prompt sends your message into a menu. Reading the console buffer
back makes that visible, and turns the prompt into something you can answer.

**D21 — curl is not a client.** A CSRF guard rejected VS Code's Simple Browser
(`Origin: vscode-webview://…`) and sandboxed frames (`Origin: null`) for days.
Every curl test passed, because curl sends no Origin at all. The rule now
allows non-http(s) origins and leans on requiring
`Content-Type: application/json`, which forces a CORS preflight the server
never answers — a hostile page cannot get past that, and form posts (the one
shape that skips preflight) cannot set that content type.
**If you test an auth or CSRF change only with curl, you have tested nothing.**

**D22 / D23 — only the tail of the screen counts.** Blocked-prompt detection
originally matched the option-list and footer patterns anywhere in the console
buffer, and immediately flagged a healthy session whose terminal happened to be
displaying a quoted example of a permission prompt. A live widget is the last
thing drawn; nothing follows it. Twelve non-blank lines is the window.

**D24 — the terminal *is* the detail view.** An earlier design reconstructed
the conversation from the transcript and showed that. It was a worse copy of
something the console already had, and it could not show the input box or a
picker. The parsed log is still there, folded away underneath.

**D25 — a button that does nothing visible has not responded.** Answering a
prompt takes a couple of seconds to show up. Without a pressed state and a
spinner the button reads as broken and gets pressed twice — which answers the
*next* prompt too.

**D26 / D28 — phone layout has to be measured, not read.** Two separate
width bugs, both invisible in the source. The first: a flex child with
`min-width:auto` refuses to shrink below its content, so one 120-column
terminal line set the width of the whole sheet. The second: a `max-width:7.5em`
in a *second*, narrower media query twenty lines below the first, left over
from when that element held a short name. Reading the CSS found neither;
querying the computed style in a real browser found both in minutes.
**Screenshot the layout, or query the computed style. Do not reason about it.**

**D30 — Safari Private Browsing throws on `localStorage.setItem`.** It does not
fail quietly, it throws — and the throw happened during startup, before the
first render, so the page came up blank. All storage access is now wrapped,
each startup step is individually guarded, and a window `error` handler paints
the message on the page. "It's gone down" is not a bug report; a red banner
saying `Cannot read properties of null` is.

**D31 — the same widget was rendered twice.** A blocking prompt was
interpolated both inside the compact row and as a direct child of the card, and
the compact CSS revealed both. Two identical prompts on every blocked session.
The guard is a test asserting the template interpolates it exactly once.

**D32 — closing a session is just typing `/exit`.** No endpoint, no process
kill, no special case. The card is *not* removed optimistically: `/exit` gets
swallowed if a picker is open, so the card greys out only when the process
actually dies.

**D33 — lead with what the session is for.** The compact row used to show the
session name — a folder plus a hash. It identifies nothing at a glance. It
shows the AI-written goal now; the name lives in the tooltip.

**D34 — "it feels slow" was IPv6.** Measured before changing anything:
`/data.json` took 2004 ms via `localhost` and 8 ms via `127.0.0.1` — and the
*static HTML page* took 2002 ms, which is proof the cost was not in any work
being done. `localhost` resolves to `::1` first on Windows and the server bound
IPv4 only, so every request made a doomed IPv6 connection first. Ten lines (a
second listener on `::1`) took it to 2 ms.
**Time a static request before optimising anything. If the floor is high, your
code is not the problem.**

**D35 — sort by what needs a human.** Blocked sessions to the top, ended ones
to the bottom, everything else left in discovery order. A list that reorders
itself every three seconds is harder to scan than one that holds still.

**D36 — multi-line without submitting.** A bare Enter submits, so messages used
to be flattened to a single line. Ending every line but the last with a
backslash — the TUI's own continuation — gives real newlines. Established by
typing `AAA\` ⏎ `BBB\` ⏎ `CCC` ⏎ into a live session and reading the
**transcript**, because the screen was ambiguous: it echoes both the input box
and the sent message, which looks like a duplicated line.

**D38 — the audio filename must match the audio.** Dictation posts the clip to
Whisper as a file, and Whisper rejects mp4 bytes named `.webm`. iOS Safari
records `audio/mp4`; desktop Chrome records `audio/webm`. Hardcoding either
breaks the other — and the phone is the only place anyone dictates. Also: Groq
sits behind Cloudflare, which returns `403 error code: 1010` for urllib's
default User-Agent. Any real one is accepted.

---

## The two rules that came out of all this

1. **Measure or observe before you conclude.** The IPv6 stall, the CSS cap, the
   backslash continuation, the Cloudflare block — none was findable by reading
   the code. All four took minutes to find once something was actually
   measured.

2. **A test that guards a specific bug is not finished when it goes green.**
   Break the fix, watch that exact test fail, restore it. Every behaviour in
   this repo was checked that way, and it caught a test that sorted its own
   list and would have passed no matter what the code did.
