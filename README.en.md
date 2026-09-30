# second-brain

[한국어](README.md) · English · [![Tests](https://github.com/sedurm85/second-brain/actions/workflows/tests.yml/badge.svg)](https://github.com/sedurm85/second-brain/actions/workflows/tests.yml)

![second-brain](web/assets/banner.png)

> A Claude Code plugin that turns "remember this" into plain-markdown notes in a local vault, then grows into a living assistant: calendar, tasks, itineraries, a speaking "core" screen and a pixel office where your automations work as employees.
> No accounts, no API keys, no servers. Obsidian-compatible markdown on your own disk, a 127.0.0.1-only dashboard, Python standard library only.

**Say "remember this" while talking to Claude and it lands in your vault. Ask "why did we decide that?" and it finds the decision and its reasons.** On top of that sits a calendar, tasks, trip itineraries, a morning briefing, a speaking core and an office full of employee characters, growing into an **assistant that moves on its own**.

Note: the UI, prompts and voice are Korean-first. The vault format, CLI and APIs are language-neutral.

## Install in 30 seconds

Two lines in Claude Code:

```
/plugin marketplace add sedurm85/second-brain
/plugin install second-brain@second-brain
```

The first "remember this" asks once whether to create the vault at `~/brain/`. The only requirement is Python 3.9+ (preinstalled on macOS and most Linux).

## Three screens

| Board `/` | Core `/core` | Office `/office` |
|---|---|---|
| ![Board](web/preview-live-desktop.png) | ![Core](web/preview-core.png) | ![Office](web/preview-office.png) |
| The assistant's report in sentences, today's timeline, tasks in four columns, this week, knowledge graph, automations, decisions, timeline, projects | A living orb with bands of light, minimal HUD, spoken briefing (Korean TTS), ask by microphone or text | One automation = one employee, rooms per team, typing while running, red lamp on failure, a workshop for Claude's background jobs |

"Show me the dashboard" (or `/second-brain:brain-view`) starts `brain.py serve`, which listens on your machine only. No external services or libraries: three HTML files and one Python script. `/report` is a printable one-page weekly report. Without a vault, `python3 scripts/brain.py serve --demo` shows a demo with fabricated data, fully isolated from your real `~/.cache/second-brain` cache (offline): three events today, five tasks including an overdue one and a waiting item, a meeting with a prep checklist, a trip to a located airport (Gimpo) with a four-step itinerary and a seeded weather cache, four automations (one failing, with a week of history), two pending suggestion cards, a staff brief, and a core-chat log.

![Demo board](web/preview-demo.png)

![Weekly report](web/preview-report.png)

## What the assistant does

- **Time**: reads Google Calendar (a private ICS URL kept in a file) or the macOS Calendar app (EventKit) and shows today's timeline, this week and upcoming events. Recurring events, exceptions, moved instances and time zones are handled, with a 15-minute cache. Events with a location (or an all-day event whose title is a place, like "Jeju Island") get a weather summary attached, and the KakaoTalk brief warns ahead if tomorrow needs an umbrella.
- **Event notes**: click an event and write memos, a prep checklist and an itinerary (`14:00 leave home (car, 50 min)`) right in the detail panel. They live as markdown under `events/` in the vault. A multi-day trip is one note attached to all of its days, and on the day the itinerary is overlaid on the timeline.
- **Tasks**: checkboxes in `inbox.md` are your tasks. `@due(2026-10-02)`, `@someday`, `@waiting(accountant)` and `@project(health)` sort them into today, this week, someday and waiting. Decisions due for review, failing automations and unfinished event prep join the list automatically. Say "carry the rest to tomorrow" in the evening and they move in one step.
- **Speaks first**: with a KakaoTalk helper configured it sends a 07:00 briefing, departure and start reminders every 10 minutes (10 minutes before an itinerary step, 30 before an event) and a 21:30 evening wrap-up, all as launchd agents.
- **Meeting prep**: for every event within three days it searches your notes by title, attendees and location and attaches related records, opened from the "events to prepare" card.
- **Speaking core**: the core screen reads the briefing aloud and handles "this week", "automation status", "add task: buy milk tomorrow" or "call yourself Jarvis" instantly. Anything else goes to headless Claude together with today's state and a vault search, answered in two or three sentences. Answers can be kept with "remember this conversation", which writes a note.
- **Automation watch**: register cron logs, status JSON, CSV metrics or markdown results in `~/.config/second-brain/widgets.json` and they appear as board cards and office employees: alive or not (ok / fail / stale), last result, trend. In the office each employee carries a status line ("working", "idle, all done", "pretending to work", "meltdown", "on leave") and a thought bubble that changes with the hour and weekday. Finished automations are folded away with `"state": "paused"`. On day one this found three crons that had quietly died. With `"allow_run": true` you can run an automation once from its row or desk and pause or resume it. With `"allow_hire": true` the office lets you hire, transfer or let go of automations directly, without opening widgets.json by hand.
- **Things Claude writes for you**: cleaning up imported notes with better titles, summaries, tags and links (`enrich`); prep checklists and itinerary drafts for upcoming events (`prepare`, applied only when you accept them on the board); a five-line daily journal at 21:30 (`journal`); a Monday weekly retrospective with three questions worth revisiting (`retro`); content-based link suggestions (`review --semantic`); and a one-line weekly summary of any office employee from its log. Every suggestion needs a human click before it reaches the vault.

```bash
python3 scripts/brain.py calendar add ics google --url-file ~/.config/second-brain/google.ics.url
python3 scripts/brain.py reminders on --lists Groceries,Work  # read macOS Reminders (off by default, one-time permission prompt)
python3 scripts/brain.py agenda                        # today and the next 7 days
python3 scripts/brain.py today                         # briefing (human + 200-char Kakao version)
python3 scripts/brain.py brief --kakao                 # morning briefing to KakaoTalk
python3 scripts/brain.py brief --evening --journal --kakao   # evening wrap-up + daily journal
python3 scripts/brain.py remind --kakao                # upcoming itinerary steps and events (run every 10 min)
python3 scripts/brain.py task add "Report draft" --due 2026-10-02
python3 scripts/brain.py task list                     # today / this week / someday / waiting
python3 scripts/brain.py task carry                    # move everything left today to tomorrow
python3 scripts/brain.py event todo "2026-10-03|Jeju" "Confirm rental car"
python3 scripts/brain.py event step "2026-10-03|Jeju" "14:00 leave home (car, 50 min)"
python3 scripts/brain.py config set assistant_name Jarvis
python3 scripts/brain.py backup                        # zip backup of the vault (keeps 14)
python3 scripts/brain.py restore [zip] [--dry-run]     # restore from a backup (auto safety backup before overwrite)
python3 scripts/brain.py enrich [--dry-run]            # Claude refines titles, summaries, tags, links
python3 scripts/brain.py prepare [--days 7]            # Claude drafts prep lists for upcoming events
python3 scripts/brain.py journal [--dry-run]           # five-line journal for today
python3 scripts/brain.py retro [--days 7]              # weekly retrospective note + 3 questions
python3 scripts/brain.py review --semantic             # content-based link suggestions (apply with `link A B`)
python3 scripts/brain.py agents install                # launchd: 07:00 brief, 10-min remind, 21:30 evening, 23:00 backup
python3 scripts/brain.py agents install prepare retro  # optional: 06:40 prep suggestions, Monday 09:00 retro
python3 scripts/brain.py doctor                        # health check: vault, Claude CLI, calendar, Kakao, widgets, agents
python3 scripts/brain.py config init-widgets           # example widgets.json
python3 scripts/brain.py widget add "Hiring Scout" ~/.local/k-skill-cron/scout.log --team hiring --ok "done|OK" --fail "fail|Error"
python3 scripts/brain.py widget list [--team NAME]     # add · move · rename · remove · pause · resume · run · brief · show too (works without allow_hire/allow_run)
```

Writes (memos, checks, tasks, settings) require the session token the dashboard mints on every start and are accepted from 127.0.0.1 only. External data such as calendar events is never copied into the vault; it is cached briefly under `~/.cache/second-brain/`.

## How it is used: five scenes

**1. Save an idea (brain-capture)**
```
me:     What if we sent the onboarding mail again on day 3? Remember this.
Claude: Saved to notes/2026/09/onboarding-day3-mail.md.
        Ask "what did I say about X?" later and I'll find it.
```

**2. A link to read later (brain-capture, source)**
```
me:     https://example.com/rag-eval  save this for later
Claude: Saved to notes/2026/09/rag-evaluation-guide.md (title, three key lines, original link).
```

**3. Record a decision (brain-decide)**
```
me:     Let's go with Loki instead of OpenSearch for logs. A third of the cost, and our queries are mostly label lookups.
Claude: Recorded in decisions/007-log-store-loki.md.
        Review date 2026-12-29 · linked to 2 related notes
```

**4. Find past context (brain-ask)**
```
me:     Why did we pick Loki again?
Claude: Cost was about a third of OpenSearch and the main queries were label lookups,
        so full-text search wasn't needed [[007-log-store-loki]].
        You agreed to revisit if full-text search becomes necessary [[007-log-store-loki]].
        No actual cost comparison table is on record.
```

**5. Weekly review (brain-review)**
```
me:     /second-brain:brain-review
Claude: 6 new notes (3 ideas · 2 sources · 1 meeting)
        1 decision due for review: [[003-weekly-deploy-day]] — keep it or change it?
        2 orphan notes · 3 link suggestions (connected when you approve)
        BRAIN.md updated
```

Also available: `/second-brain:brain-today` (today's briefing), `/second-brain:brain-event` (memos, prep and itinerary on an event), `/second-brain:brain-import <path>` (import an Obsidian vault or Claude Code memory), `/second-brain:brain-setup` (vault location, git auto-commit), `/second-brain:brain-journal` (daily journal, weekly retro), `/second-brain:brain-doctor` (install/connection health check), `/second-brain:brain-office` (hire, move, retire or run automation widgets by chat, no dashboard needed).

## Vault layout

```
~/brain/
├── BRAIN.md                    auto index (recent · by project · open decisions)
│                               → Claude reads the top 40 lines at session start
├── inbox.md                    quick-capture inbox = tasks (checkboxes + @due/@someday/@waiting)
├── notes/YYYY/MM/<slug>.md     note · idea · source · meeting
├── events/YYYY/<date>-<slug>.md event notes: prep checklist · itinerary · memos (attached to calendar events)
├── journal/YYYY/<date>.md      daily journal (five lines) and <date>-weekly.md retrospectives
├── decisions/<NNN>-<slug>.md   ADR: situation · options · decision · reasons · review date
├── projects/<slug>.md          project hubs (related notes and decisions collected automatically)
└── people/<slug>.md            people and context
```

Every file is front matter (title, type, created, tags …) plus free markdown and `[[wikilinks]]`. Reversed decisions are not deleted; they are marked `superseded` so the "why we changed" survives.

## With Obsidian

Obsidian → **Open folder as vault** → pick `~/brain`. Graph view, backlinks and search work as-is. Notes written directly in Obsidian are searched by Claude too, as long as they have front matter. An existing Obsidian vault can be copied in with `/second-brain:brain-import <vault path>` without touching the original.

## FAQ

**Where is my data?**
Only in the vault folder on your machine (default `~/brain/`). The plugin itself sends nothing to any server. Free-form questions on the core screen go to headless Claude (your own account) together with today's state and search excerpts. The calendar URL file lives outside the vault under `~/.config/second-brain/`.

**What if I uninstall the plugin?**
The vault is outside the plugin and stays put. Reinstall and continue.

**Several machines?**
Turn on git auto-commit in `/second-brain:brain-setup` and push the vault to a private repository, or move the vault into an iCloud or Dropbox folder.

**KakaoTalk notifications?**
Point `config set kakao_cmd <path>` at a "message to myself" helper script and `brief --kakao` (alias `--notify`) and `remind --kakao` will call it. Without a helper, macOS falls back to Notification Center (`osascript`) automatically; if that's not available either, output stays in the terminal. Run `notify test` to check which channel actually fires.

**Embeddings / semantic search?**
Search is keyword based (weighted title and tags plus recency, Korean 2-grams). Embedding search would need an external API key, which conflicts with the "zero keys" rule, so it is deferred as an option. `review --semantic` uses headless Claude instead.

## Inspiration

- hongik.man's reel "3 AI projects you can build over a weekend with Claude", item 2 (personal knowledge base)
- reznikov_engineering's reels (a living orb and a speaking agent) → the core screen
- godseng.mom's reel "my own employees who work automatically" → the office screen

Design notes are in [SPEC.md](SPEC.md) (Korean), the phase-2 plan in [docs/v2-plan.md](docs/v2-plan.md), the vault format in [docs/vault-format.md](docs/vault-format.md), and a contributor code map in [docs/architecture.md](docs/architecture.md) (Korean).

## License

MIT — [LICENSE](LICENSE)
