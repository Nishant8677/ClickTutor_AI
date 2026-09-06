# Reviewer brief

> Historical handoff supplied before the desktop tutor checkpoint. The body below preserves that earlier brief; its status, quota figures, benchmark claims and proposed dependencies are not a statement of the current verified state. Current implemented scope and check evidence are in [the desktop tutor checkpoint](desktop-tutor-checkpoint.md); current decisions are in [the personal tutor roadmap](personal-tutor-roadmap.md). Historical benchmark-claim reconciliation remains open.

This file exists so a second AI can act as reviewer and decision-maker on this
project without being told the same context every session. It is written for
that reviewer. If you are reading it as a human, it is also the fastest
summary of where the project stands.

The split of work is:

- **Claude Code** writes the code, runs the measurements, and pushes.
- **The reviewer** decides trade-offs, sets acceptance thresholds *before* a
  measurement runs, reviews diffs, and says what to build next.
- **The repo owner** keeps the final say on anything irreversible or
  outward-facing: rotating keys, spending API quota, system installs, deleting
  things, and anything that leaves the machine.

---

## 1. Paste this into the reviewer to start it

> You are the reviewer and technical decision-maker for ClickTutor AI, a
> desktop tutor that watches the screen, generates a step-by-step lesson, and
> highlights what it is talking about. You have read access to the repo
> `Nishant8677/ClickTutor_AI`. Another agent (Claude Code) writes the code and
> runs the measurements; you do not write code unless asked for a snippet to
> illustrate a point.
>
> Start by reading, in order: `docs/reviewer-brief.md` (this brief),
> `docs/anchor-repair.md` (what has been measured and why), and
> `docs/narrated-lessons-plan.md` (what is being built now).
>
> Your job: decide trade-offs, set acceptance thresholds before a measurement
> is run rather than after, review diffs for correctness and for whether the
> claim matches the evidence, and say what to build next. Enforce the rules in
> section 5 of the brief. When you are missing evidence, ask for the specific
> file or command output rather than assuming. Be concrete and brief; a
> decision with one line of reasoning beats a survey of options.

---

## 2. What the project is

A desktop app (PyQt6, Windows) that:

1. Takes a question and a screenshot.
2. Sends both to Gemini, which returns a 3-to-6 step lesson in a line-based
   text format: `STEP / TITLE / ANCHOR / CONTEXT / ATTENTION / EMPHASIS /
   EXPLANATION`.
3. Locates each step's `ANCHOR` phrase in the screenshot with OCR.
4. Draws a circle, rectangle or underline on a transparent always-on-top
   overlay while a floating companion panel shows the step text.

The interesting engineering is step 3. An explanation that points at the wrong
words is worse than one that points at nothing, and the project's history is
mostly about finding out that this was happening and fixing it.

### The owner's vision

This matters more than the feature list, because it is what every decision
should be measured against. In the owner's own framing:

The inspiration is Clicky (`clicky_repo/`, MIT-licensed, since taken private
by its author): an AI teacher that sits next to your cursor, sees your screen,
talks to you and points at things. ClickTutor was started to be that kind of
tutor, **teaching in real time rather than only overlaying a lesson**.

The intended experience:

1. You have a problem on screen you cannot understand.
2. You call the tutor and tell it what you are stuck on, about the specific
   part you mean.
3. It takes a screenshot, or you select the part of the screen yourself.
4. It starts explaining like a live tutor sitting beside you: **speaking out
   loud, and highlighting on the screen while it speaks**, so the pointing and
   the explanation are one thing, not a panel of text next to a box.

What the codebase does today is the batch version of this: capture, generate a
3-to-6 step lesson, show it step by step with a highlight, silently. The gap
between that and the vision is the current milestone,
`docs/narrated-lessons-plan.md`: a question at capture time, region selection,
and narration synchronised to the highlight.

Two parts of the vision are **deliberately later**, not rejected: true live
conversation (interrupting mid-sentence, follow-up questions, speech input)
and a cursor-following avatar. They need a session model the engine does not
have, continuous streaming of screen and voice to a cloud model, and API quota
the owner's accounts do not have (section 6). The plan's position is to build
the narrated version first and use it for a week before deciding whether the
missing thing is conversation or just speed. Treat proposals in that
direction as premature rather than wrong.

---

## 3. Repo map and read order

| Read | Why |
|---|---|
| `docs/anchor-repair.md` | The full measurement story. Longest, most important. |
| `docs/narrated-lessons-plan.md` | The current milestone, four stages, stage 1 done. |
| `readme.md` | How to run it, on which interpreter, and why that matters. |
| `src/ocr_locator.py` | The six-pass locator and the trust boundary. |
| `src/lesson_engine.py` | Prompt, parsing, anchor repair, vision fallback. |
| `src/desktop/controller.py` | The desktop flow: capture, ask, teach. |
| `benchmarks/*.json` | Every number the docs claim, with its environment block. |

Other things worth knowing exist:

- `src/florence_ocr.py` and `src/nemotron_ocr.py` are **measurement code, not
  wired into the app**. Deliberately. See section 4.
- `tools/` holds the harnesses: `benchmark.py`, `locator_experiment.py`,
  `hostile_locator_experiment.py`, `ocr_engine_comparison.py`,
  `router_validation.py`, `demo_drive.py`.
- `tests/unit/` is the suite CI runs, 248 tests, offscreen Qt, no network, no
  Tesseract binary needed.
- `archive/` and `clicky_repo/` are not part of the build. `clicky_repo` is
  the third-party project that inspired this one.

---

## 4. What has already been measured

Every number below traces to a committed file. Cite the file, not the doc,
when you rely on one.

**Anchor location**
- Baseline: 79% of anchors resolved; grounding the prompt in visible OCR text
  moved it to 82%; adding a repair pass took it to 33/33 on the accuracy
  corpus. (`git show 45f1d67`, `benchmarks/benchmark_results.json`)
- **The metric was measuring the wrong thing.** It counted whether a lookup
  *resolved*, not whether the box was on the right words. Nine highlights were
  confidently wrong and counted as successes.
- Which of the locator's six passes fired predicts correctness exactly:
  phrase-level 40 right / 0 wrong, word-level 0 right / 9 wrong. The locator
  now trusts only phrase-level matches (`TRUSTED_PASSES` in
  `src/ocr_locator.py`). (`benchmarks/locator_comparison.json`)

**OCR versus a vision model** (`benchmarks/hostile_locator.json`)
- Readable screens: OCR 33/34 (97%), vision 26/34 (76%).
- Screens OCR cannot read: vision 23/24 (96%), OCR 7/24 (29%).
- Conclusion: neither wins outright, so the answer is routing, not
  replacement. The vision locator is a fallback, capped at
  `MAX_VISION_FALLBACKS = 2` per lesson; anchor repair is capped at
  `MAX_ANCHOR_REPAIRS = 3`.

**OCR engines** (`benchmarks/ocr_engine_comparison*.json`)

| | Tesseract | Florence-2 (hosted) | Nemotron OCR v2 (local) |
|---|---|---|---|
| Readable, 21 phrases | 0.886 mean, 16/21 usable | 0.912, 18/21 | 0.891, 17/21 |
| Unreadable, 23 phrases | 0.598 mean, 7/23 usable | 0.832, 19/23 | **0.914, 21/23** |

Florence costs ~13 s per image and sends the screen to a third party.
Nemotron runs locally in 140–600 ms warm, but needs Python 3.12, a Linux-only
build with a CUDA extension, torch 2.9 pinned, and an NVIDIA GPU — against an
app that runs on Python 3.11 on Windows. Both are therefore unwired: the
first for privacy, the second for packaging.

**Latency** (`benchmarks/benchmark_results.json`, Windows, 10 iterations)
- capture 81 ms, OCR 4,734 ms, lesson 4,893 ms, end to end 9,725 ms.
- OCR is half the wait and scales with image area, which is the measured
  argument for region selection in stage 2 of the current plan.

---

## 5. Rules that are not up for negotiation

These come from the repo owner. Enforce them in review.

1. **History is append-only.** No squash, rebase, amend, or force-push.
   Corrections are forward commits.
2. **Every number must trace to a committed file.** A claim in a doc, commit
   message or resume bullet without a file behind it is a defect.
3. **Never write "100% accuracy" unqualified.** The accuracy metric measures
   whether a lookup resolved, not whether the highlight was correct. This
   distinction is the whole point of the project's second act.
4. **Agree the acceptance threshold before the measurement runs.** A number
   that misses the threshold is a finding to commit, not a reason to re-run.
   No re-running until it looks good.
5. **Ask before system installs, large downloads, deletes, or spending API
   quota.**
6. **No `verify=False`, ever**, even though the campus network intercepts TLS.
7. **No AI co-author trailers in commits, and no self-mention in commits or
   docs.** The commit log reads as the owner's work because it is.
8. **Comments say why, not what.** This codebase's comments carry the
   reasoning and the measured evidence for a decision. A comment restating
   the code is noise; a decision without its reason is worse.

---

## 6. Environment: what can be tested where

| | Windows (`D:\venvs\clicktutor`, py3.11) | WSL Ubuntu (`venv/`, py3.12) |
|---|---|---|
| Runs the desktop app (`run.ps1`) | yes | no — overlay draws on the wrong display |
| Global hotkeys, screen capture, focus | yes | no |
| Unit tests, ruff, mypy | possible | yes, this mirrors CI |
| Pushing to GitHub | no SSH key | yes |

CI (GitHub Actions, Ubuntu, py3.12) runs `ruff check .`, `mypy` on four
incrementally-typed modules (`src/attention/coordinates.py`,
`src/lesson_validator.py`, `src/box_metrics.py`, `src/ocr_occurrences.py`),
and `pytest` with `QT_QPA_PLATFORM=offscreen`.

**Constraints that shape decisions**
- API quota is the binding one: the consumer Pro accounts have **no** API
  quota (`gemini-3.1-pro` limit 0, `gemini-3.6-flash` 20/day). Any proposal
  that needs repeated live calls to test is expensive in a way that is easy to
  miss. The shipping model is `gemini-3.1-flash-lite`.
- The campus network intercepts TLS for non-Google endpoints, so third-party
  APIs need a phone hotspot.
- GPU is an RTX 3050 Laptop with 4 GB, visible from WSL, with CUDA 12.6
  installed.
- Secrets live in `.env`, which is gitignored. Values are never printed.

---

## 7. Where things stand

`main` at `f1b7e20`, clean, pushed, CI green, 248 tests, 104 commits.

The application is functionally complete. Current work is the **narrated
lessons** milestone (`docs/narrated-lessons-plan.md`):

| Stage | State |
|---|---|
| 1. Question typed in the companion, not a modal dialog | **done**, `f1b7e20`, awaiting hand test on Windows |
| 2. Drag to select a screen region | next |
| 3. Spoken steps synchronised to the highlight | after that |
| 4. Show step 1 while the rest generates | deferred until the wait is the thing that hurts |

Also open, unrelated to the milestone: rotating the Gemini API key, and
writing resume bullets from this work.

---

## 8. Decisions waiting for a decider

Proposals in brackets are Claude Code's; they were accepted by the owner for
stage 1 and are open again for later stages.

1. **Stage 2 selection trigger.** Companion button, second hotkey, or both.
   [both]
2. **Stage 3 voice backend.** Local Windows SAPI only, or also a cloud voice
   that costs a call per step against a 20-a-day budget. [local only first]
3. **Auto-play or press-to-start** when a lesson is ready. [auto-play, pause
   one key away]
4. **Whether the Nemotron result changes anything.** It is measured and
   unwired. Wiring it is a sidecar-process or Linux-only project, not a
   routing change. [leave unwired]
5. **Whether the project should stop taking features and start being
   presented** — release tag, readme rewrite that leads with the measurements
   rather than a feature list.

---

## 9. How to review this codebase

What good looks like here, in rough order of how often it matters:

- **The claim matches the evidence.** If a commit message, comment or doc
  states a number, the file it came from should exist and say that. Check the
  `environment` block: a number measured under a different Tesseract or model
  version is not comparable to one that was not.
- **The failure mode is named.** This project's defining bug was silent: no
  crash, no log, a wrong highlight that the benchmark counted as a success.
  Ask of any new code: if this were wrong, how would anyone find out?
- **Caps and budgets on anything that fans out.** Repair calls, vision
  fallbacks, retries. An unbounded loop that makes API calls is a defect here
  regardless of how unlikely the path is.
- **Tests name behaviour, not methods.** Existing examples:
  `test_position_survives_alternating_content_heights`,
  `test_empty_field_submits_the_default_question`. A test named after the
  function it calls tells a later reader nothing.
- **Qt threading.** Hotkeys arrive on the `keyboard` library's thread;
  everything downstream touches widgets. Signals must cross to the GUI thread
  (see the comment on `InputManager`). This is a recurring source of bugs that
  look like random crashes.
- **Coordinate spaces.** There are four: OCR's upscaled image pixels, captured
  image pixels, physical screen pixels, and logical widget pixels. Display
  scaling makes the last two differ. Most highlight bugs are a missing
  conversion; `src/attention/coordinates.py` is typed and tested for this
  reason, and stage 2 adds an origin offset to it, which is the riskiest part
  of that stage.
- **Scope.** The plan's stages ship one at a time on `main`, tests green
  before the next starts. Push back on a diff that quietly does two stages.

---

## 10. What the reviewer cannot see

Ask for evidence rather than assuming. In particular:

- **Anything that needs a real screen.** Focus behaviour, global hotkeys,
  whether the companion appears in its own screenshot, whether a highlight
  lands on the right pixels under display scaling. CI runs Qt offscreen, where
  focus never lands and `raise()` is a no-op. Hand tests on Windows are the
  only evidence here, and they come back as the owner's description, not a log.
- **Whether a number is fresh.** Ask which commit it was measured at.
- **The `.env` contents.** Keys are never printed.

---

## 11. Closed. Do not reopen without new evidence

- **Replacing OCR with a vision locator.** Measured twice; the answer is
  routing, and it is implemented.
- **Wiring Florence-2.** Privacy decision, made.
- **Wiring Nemotron OCR v2.** Packaging decision, made, and re-openable only
  by someone volunteering to build the sidecar.
- **Logging and streaming subsystems.** Deferred by the owner. (Voice was on
  this list and has come off it: it is stage 3 of the current plan.)
- **Other repos.** Out of scope.

---

## 12. Handing work to Claude Code

The most useful instruction names the decision, the acceptance threshold and
the boundary. For example:

> Do stage 2. Selection is both a companion button and Ctrl+Shift+S. Accept it
> if OCR time on a quarter-screen region is at most half the full-screen time
> on the same content and anchor resolution does not drop; measure once and
> report before committing. Do not touch the narration path.

Less useful: "make the region selection better", which cannot be measured or
refused.

If you want a diff reviewed, ask for the commit range and read it from the
repo rather than having it pasted; the surrounding code is usually where the
answer is.
