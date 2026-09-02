# Narrated lessons: from overlay to tutor

The goal of the project was never a lesson generator with highlights. It was a
tutor: call it on a problem you are stuck on, tell it which part, and have it
explain out loud while pointing. This is the plan for the milestone that gets
most of the way there without touching the two walls that stop the full
vision today: API quota and continuous streaming of screen and voice to a
cloud model.

## What "narrated lessons" means

After this milestone, the flow is:

1. Press the hotkey. A small box appears asking what you want explained.
2. Optionally drag a rectangle over the part of the screen you mean.
3. The tutor reads that area, builds the lesson, and then speaks each step
   while the highlight sits on the thing it is talking about. The highlight
   moves when the speech for a step ends. You can pause, skip, or go back.

Not in this milestone: interrupting mid-sentence with a follow-up question,
speech input, and a cursor-following avatar. Those are the live-conversation
layer and depend on a session model the current engine does not have. They are
decided after a week of using the narrated version, not before.

## Where the code is today

The facts the plan has to fit, from the code as of `08f0d5e`:

- **Flow** in `src/desktop/controller.py`: hotkey → capture the whole overlay
  screen → a `QInputDialog` asks the question → `LessonWorker` runs OCR and
  Gemini off the GUI thread → steps are shown one at a time through
  `show_current_step`, with the companion showing title and explanation and
  the overlay drawing the shape.
- **States** in `src/input/state_machine.py`: IDLE, CAPTURING, ANALYZING,
  TEACHING, FINISHED. Actions in `src/input/events.py`: capture, toggle debug,
  cancel, next, prev. Every action goes through `InputManager`, which guards
  by state.
- **The overlay is transparent to input** (`WindowTransparentForInput`). It
  cannot receive a drag. Region selection needs its own widget.
- **The companion** (`src/desktop/companion.py`) is a draggable always-on-top
  panel that renders from state. It already has next, prev and dismiss
  buttons and a "thinking" animation.
- **A lesson is text** in a STEP / TITLE / ANCHOR / CONTEXT / ATTENTION /
  EXPLANATION block format, parsed by `parse_lesson_steps`, 3 to 6 steps,
  validated by `src/lesson_validator.py`. Explanations are one to three
  sentences.
- **Latency** (`benchmarks/benchmark_results.json`, Windows, 10 iterations):
  capture 0.08 s, OCR 4.7 s, lesson 4.9 s, end to end 9.7 s. OCR is half the
  wait, and it scales with image area.
- **Demo mode** (`src/desktop/demo_manager.py`) already auto-advances steps on
  a `QTimer`. Narration is the same loop with "audio finished" replacing the
  timer.
- **The Windows venv** (Python 3.11) has no audio library of any kind.
- **Constraints that do not move**: Gemini calls are 20 a day on flash and
  zero on pro from the consumer accounts; non-Google endpoints fail TLS on
  the campus network; the app runs on Windows from `run.ps1`.

## The stages

Each stage ships on its own, is measured before it is accepted, and lands as
forward commits with the number in a committed file. Order matters: each one
makes the next cheaper to test.

### Stage 1: ask at capture time

**What changes.** The `QInputDialog` after capture goes away. The companion
gains an input row: a single-line question field and a Go button, shown when
the state is IDLE or TEACHING. The hotkey focuses that field instead of
capturing immediately. Enter captures and asks. A second hotkey press with an
empty field captures with a default question ("Explain what I am looking at").

**Why first.** It is the smallest change, it removes a modal dialog that
breaks the flow on every use, and every later stage needs a place on screen
for controls. Stage 2 adds a "select area" button to the same row; stage 3
adds pause and skip.

**Code.** `companion.py` (input row, a `question_submitted` signal),
`controller.py` (the CAPTURE_SCREEN branch reads the question from the
signal instead of the dialog; the capture still hides the companion first).
State machine unchanged. New `InputAction.ASK` carrying the text is cleaner
than overloading CAPTURE_SCREEN, since the guard rules are the same.

**Tests.** `tests/unit/test_companion.py` already drives the widget offscreen.
Add: the field is visible in IDLE and TEACHING and hidden while ANALYZING;
Enter with an empty field submits the default question; the submitted text
reaches the worker unchanged.

**Measure.** Nothing numeric. Verify by hand on Windows that the companion is
not in the screenshot after the change, since it now stays visible longer
before capture. The existing `_companion_hidden` context manager covers it,
but the check is what proves it.

### Stage 2: select the part of the screen

**What changes.** A "select area" button, or holding the hotkey, brings up a
full-screen `SelectionOverlay`: a new widget that does accept input, dims
the screen, and lets the user drag a rectangle. On release it emits the
rectangle in physical pixels, hides, and the normal capture runs with that
region. Escape cancels back to a full-screen capture.

The rectangle becomes the capture region passed to
`ScreenCapture.capture(region=...)`, which already exists. The overlay's
`set_source_size` and `CoordinateMapper` then need an origin offset as well
as a size, so a box at (0, 0) in the cropped image lands at the region's
top-left on screen. That offset is the only geometry change, and it is where
the bugs will be, so it gets its own tests against `coordinates.py`, which
is already one of the strictly typed modules.

**Why it matters beyond UX.** OCR is 4.7 of the 9.7 seconds and Tesseract's
time scales with area. A quarter-screen region should cut that by more than
half. It also raises the share of screens in the regime where OCR wins: a
cropped code panel has no menu bars, tabs, or thumbnails to confuse the
reading, and fewer anchors resolve to the wrong window. The router still
falls back to vision on the region when OCR misses.

**Code.** New `src/desktop/selection.py` (the widget), `src/attention/
coordinates.py` (origin offset), `controller.py` (region held between
selection and capture; cleared on cancel), `companion.py` (button).
`InputAction.SELECT_REGION`. The state machine gains SELECTING between IDLE
and CAPTURING so a hotkey during a drag is dropped rather than starting a
second capture.

**Tests.** Mapper with offset: a box at the region origin maps to the region's
screen position under scale 1 and under 1.5 display scaling. Selection
widget offscreen: drag produces a normalised rectangle regardless of drag
direction; a zero-area drag is a cancel; Escape is a cancel.

**Measure.** Extend `tools/benchmark.py` with a `--region` option and run the
latency section on the same 10 iterations at full screen and at a fixed
quarter-screen region on the same content. Report OCR ms and end-to-end ms
for both. Also re-run the anchor accuracy section on the region to confirm
resolution does not drop, since anchors now have less context text. Agree on
what counts as acceptable before running: the proposal is OCR time at least
halved and anchor resolution unchanged.

### Stage 3: narration synchronised to highlights

**What changes.** A `Narrator` behind a small interface: `speak(text) ->
handle`, `stop()`, `finished` signal. Two backends:

- **Local, default.** Windows SAPI through `pyttsx3`. No network, no quota,
  no privacy question, works offline for demos. The voice quality is what it
  is; the point is that the mechanism is right before the voice is good.
- **Cloud, opt-in.** Gemini's TTS model, which is a Google endpoint so it
  passes the campus network. It costs a call per step against a 20-a-day
  budget, so it is a flag, not the default, and every call is logged with
  its byte count.

Playback: when a lesson arrives, the controller enters TEACHING and starts
narrating step 1. When the narrator's `finished` signal fires, it advances
to the next step, which moves the highlight, and speaks that step. Pressing
next or prev stops the current audio and jumps; the lesson keeps playing
from there. A pause button and a spacebar toggle in the companion. Cancel
stops audio immediately. The synthesis for step n+1 starts while step n is
playing, so there is no gap at the boundary.

What is spoken: the step's explanation, prefixed by its title. Not the anchor
or context, which are instructions to the locator, not to the learner.

**Why it is the milestone's centre.** This is the moment the app changes
kind. Everything else is polish around this. It reuses the demo manager's
advance loop, the companion's controls, and the existing step shape, so the
new code is the narrator and the wiring.

**Code.** New `src/desktop/narrator.py` (interface, SAPI backend, Gemini
backend, a `NullNarrator` for tests and for `--mute`), `controller.py`
(narration loop, replacing the manual advance as the default; manual advance
remains), `companion.py` (pause and a speaking indicator),
`InputAction.PAUSE_NARRATION`. The state machine keeps TEACHING; whether
audio is playing is narrator state, not tutor state, so pause does not need
a new tutor state. `requirements.txt` gains `pyttsx3` with a pin.

**Tests.** With `NullNarrator`, which emits `finished` on a timer: the
controller advances through all steps in order, stops on cancel, and jumps
correctly on prev during playback. Backend selection from a flag. Nothing
calls a real TTS engine in CI.

**Measure.** For each backend, on the two demo lessons: time from lesson
ready to first audio, and the per-step gap between one step's audio ending
and the next beginning. Proposed acceptance: first audio under one second
for local, gaps under 200 ms. For the cloud backend also the daily call
count a five-step lesson costs, which decides whether it can ever be the
default on these accounts.

### Stage 4, after the first three are in use: show step 1 early

The wait is still 9.7 seconds of silence before the first word. Region
selection cuts the OCR half; this cuts the lesson half. The lesson format is
line-based and step 1's block is complete the moment the "STEP 2" line
arrives, so streaming the model response and parsing incrementally lets step
1 be repaired, highlighted, and spoken while steps 2 to 6 are still being
generated. `repair_anchors` already works per step.

This stage is listed so its design is not painted out by stage 3, and
deferred because it changes `tutor.generate_content` from a single response
to a stream, which touches retry and error handling that currently work.
Do it once the narrated version has been used on real problems and the wait
is the thing that hurts most. Measure: time to first spoken word, before and
after.

## Cross-cutting rules for the whole milestone

- **Every stage lands on its own**, tests green in CI, before the next
  starts. No long-lived branch.
- **Measure before accept, once.** Each stage names its number above. The
  acceptance threshold is agreed before the run. If it misses, that is a
  finding to commit, not a reason to re-run.
- **No new Gemini calls by default.** Stages 1 to 3 add zero API calls on
  their default paths. The cloud narrator is opt-in and counted.
- **Nothing leaves the machine that does not already.** Screen and question
  already go to Gemini. Local narration adds nothing. Region selection sends
  less.
- **Windows is the target.** Selection overlay, SAPI, and hotkeys are tested
  by hand on Windows through `run.ps1`. Unit tests run offscreen in WSL and
  CI as now. The WSL capture fallback ignores regions today; it keeps doing
  so, and selection is simply unavailable there.
- **Docs.** The writeup gets a short "Part five" once stage 3 is measured,
  with the latency-before-and-after table. Not before.

## Order and size

| Stage | Sessions | Ships |
|---|---|---|
| 1. Ask at capture | 1 | Question in the companion, no modal |
| 2. Region selection | 2 | Drag to select, faster OCR, measured |
| 3. Narration | 2 to 3 | Spoken steps synced to highlights, local by default |
| 4. First step early | 1 to 2, later | Under-five-second time to first word |

## Decisions for you before stage 1 starts

1. **Default question** when the hotkey is pressed with an empty field.
   Proposal: "Explain what I am looking at."
2. **Selection trigger.** A button on the companion, or a second hotkey
   (Ctrl+Shift+S), or both. Proposal: both, since the button is
   discoverable and the hotkey is fast.
3. **Whether the cloud narrator is built at all in stage 3**, or only the
   local one with the interface left ready. Building it costs about a
   session and lets the voice quality be compared; skipping it keeps the
   milestone off the quota entirely. Proposal: local only in stage 3, cloud
   as a separate small commit if the local voice turns out to be the thing
   that makes the demo feel wrong.
4. **Auto-play or press-to-start.** When the lesson is ready, does narration
   begin immediately or wait for the learner? Proposal: begin immediately,
   with pause one key away.
