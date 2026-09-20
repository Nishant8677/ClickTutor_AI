# ClickTutor: personal tutor roadmap

Status: area selection and local Windows narration are implemented and locally reviewed in the desktop tutor checkpoint. The owner has confirmed that narration works in their normal setup; no measured latency or exhaustive control check was reported. These acceptance targets are decisions to agree before testing, not measured results. Authoritative location propagation, Escape coordination and stale-worker cancellation are now implemented and locally reviewed in the desktop tutor checkpoint. The owner confirmed that the area-selection flow works in their Windows trial. The particular display-scaling and monitor configuration was not reported; historical benchmark-claim reconciliation remains open. No new performance or accuracy measurements have been made.

Priority: a tutor useful in actual personal study, followed by a portfolio demonstration of that same working experience. Code and algorithm problems are the first evaluation context; the broader screen-aware tutor vision remains the destination.

The first complete experience is: select a problem, ask what is confusing, hear a short explanation while the relevant phrase is highlighted, take time to think, and choose Next or Replay.

The inspected baseline is main at f1b7e20. See [the desktop tutor checkpoint](desktop-tutor-checkpoint.md) for the implemented scope and recorded functional checks. The existing milestone is docs/narrated-lessons-plan.md. This proposal makes its open decisions concrete and adds a reliability gate and a use-based decision point.

## 0. Restore trust in the current app

The location, Escape and request-ownership corrections have passed local review. The owner has confirmed the exercised area-selection interaction. Broader native hotkey/display coverage and historical evidence reconciliation remain open.

- Carry each authoritative location from the lesson engine through to the desktop renderer. An explicit unresolved result must remain unresolved. Desktop rendering must not repeat permissive OCR matching after trusted routing rejected a match.
- Reuse accepted locations when navigating steps. Navigation must not make locator API calls.
- Coordinate Escape between the companion and global hotkey path. While composing, Escape abandons the draft and resets the armed hotkey. Outside composition, Escape cancels the lesson.
- Ensure cancelling or replacing a request prevents late worker output from restoring an old lesson or replacing the current one.
- Reconcile benchmark tables with existing per-anchor evidence. Recover missing pass labels where existing records permit; otherwise qualify the unsupported claim. Do not regenerate API results to obtain convenient counts.

Acceptance: controller-level regression cases pass for trusted matches, weak-match rejection, successful and failed vision fallback, navigation, cancellation and late completion. Windows checks cover real hotkeys, focus and exclusion of the companion from capture. Run the repository's existing lint, type and unit checks. Record the tested commit and outcomes.

Implementation references: src/lesson_engine.py:378, src/desktop/controller.py:524, src/locator/ocr_locator_engine.py:63, src/desktop/companion.py:335 and src/input/hotkeys.py:30.

## 1. Select exactly what needs explaining

Keep region selection before narration: it lets the learner specify the referent as well as potentially reducing OCR work.

- Support both a companion Select area button and Ctrl+Shift+S.
- Selection prepares the area only; Ask or Enter captures it and starts a lesson. Preserve the typed question through selection and cancellation. Use the existing default question when the field is blank.
- Keep the selected area for later asks until it is replaced or cleared with Use full screen. Clearing the next capture area must not move highlights in the currently displayed lesson.
- If the crop omits definitions or surrounding code needed to explain it, ask the learner to widen the selection or supply context. Do not guess missing code or silently capture a larger area.
- Escape or a zero-area drag returns to question entry without capture or an API request.
- Define capture origin and scale explicitly. Preserve the existing demo transform separately from mapping a real screen crop.
- Initially support selection within the overlay's current monitor. Cross-monitor dragging and claims of arbitrary mixed-DPI support wait for their own evidence.
- Treat a lesson as referring to its captured screen state. Provide an explicit recapture path; do not describe this version as tracking moving content.

Acceptance: normalised drags in all directions; no capture on cancel; correct target placement at 100% and 150% display scaling; no selection panel or companion in the image. Verify nonzero capture origins and reset of the origin when returning to full-screen capture.

Predeclare a local comparison: freeze the scene, crop rectangle, in-region anchors and their intended occurrences before running. Use ten paired full-screen/quarter-screen OCR timings with alternating order. Report every sample and failed read. The speed target is quarter-screen mean OCR time at most half the full-screen mean. On the fixed in-region anchors, require no loss of baseline-correct locations and no newly incorrect locations; report unresolved, wrong and correct separately.

This local experiment measures OCR and localisation, not full lesson quality or end-to-end latency. A later live pipeline comparison needs a separate approved API budget and fixed protocol.

Decision on a miss: commit the result. Region selection may still be worth shipping for control over context, but it has not passed the speed claim. Record an explicit scope/acceptance decision rather than changing the threshold after seeing the result. Investigate before authorising a new experiment.

## 2. Make explanation and pointing work as one experience

Implemented locally: QtTextToSpeech through the installed Windows SAPI plugin, using the existing pinned PyQt6 dependency. No additional installation or cloud speech service was needed. Voice, Replay and Stop are in the normal companion. Full-token callback ownership and backend cleanup were reviewed; full integration checks and final focused checks passed. Native muted SAPI and silent controller checks passed. The owner subsequently confirmed that narration works in normal use. Detailed audio-quality assessment, overlap checks and the timing targets below remain unmeasured or separately unverified.

- Begin the first spoken step automatically after the learner explicitly asks.
- Keep one highlight visible throughout its spoken step.
- Wait at the end of a step for Next. Replay speaks the same step again. Revisit initial autoplay after the personal-use period if it interrupts studying.
- Keep text available. Provide Stop and speech on/off; avoid taking a global Space shortcut away from the learner's editor.
- Keep display, speech and the current step synchronised on Next, Previous and cancellation.
- A missing target stays unhighlighted. Narration must not imply a verified on-screen location when none was found.
- Test on the existing offline demo lessons before spending quota on live questions.

Proposed Windows timing targets: first audible speech within one second of lesson readiness, and audible playback stopped within 200 ms of Stop. Use a fixed, documented protocol on the existing demos. Start the first-speech clock when the GUI accepts the completed lesson and end it at the first audible output; measure Stop from the GUI receiving that action to the last audible output. Record cold and warm conditions separately, retain all samples, and distinguish audible output timing from merely queuing a synthesis request. If timings cannot be measured reliably, report the missing evidence instead of claiming the target passed.

Acceptance also requires no overlapping utterances, no old completion callback advancing a newer lesson, correct replay/back navigation, and zero model requests caused by replay or navigation.

This is the first portfolio demonstration milestone. Record the actual interaction and state honestly which parts use a prerecorded lesson, which use live generation, and what is currently limited.

## 3. Use it during one week of real studying

The week is an observation period, not a development deadline. Choose code or algorithm tasks that naturally arise. Freeze a modest usage budget before live sessions; do not treat the historical quota figures in the brief as a current account entitlement.

Keep a small manual record for each attempt:

- What was confusing, and which screen/region was involved.
- Whether it pointed at the intended occurrence.
- Whether the explanation addressed the confusion or merely restated visible text.
- Whether the learner could explain the reasoning or attempt the next step afterward.
- Whether waiting, voice, navigation or capture interrupted studying.
- Model request count, including repair and fallback, and whether the tool was voluntarily used again.

Record failures and abandoned attempts. Use the existing assistant or usual learning method on comparable material; avoid presenting a small personal trial as a controlled educational study.

Decision gate: identify recurring situations where the tool helped, the most common failure, and whether it was chosen again without a reminder. If helpful use is not yet evident, improve the existing interaction before adding features. If too little studying occurred, report insufficient evidence rather than a success or failure verdict.

## 4. Add a conversation turn that solves the observed problem

If repeated use shows the missing interaction is asking Why?, make typed follow-ups the next increment.

Foundation status: bounded in-memory sessions and the explicit New session reset are implemented on `codex/study-sessions`. Accepted lesson questions and answers are snapshotted into later requests; ended-session callbacks are rejected. See [the study-session foundation](study-session-foundation.md) and its [check record](checks/study-session-foundation.json). Typed follow-up questions are still a separate next ticket, and no RAG or cross-launch persistence has been added.

- Implemented: start a fresh in-memory study session on each app launch. Keep accepted questions and answers from that session available until the learner explicitly ends it or exits the app. New session stops speech, cancels/disowns pending work, clears conversation and capture/lesson state, and starts fresh. There is no automatic cross-launch conversation persistence.
- Carry useful conversation context across questions and lessons within the active session. Preserve the current question/step and recent relevant exchanges under an explicit input-token budget; use a compact older-context summary when needed. Retaining session history does not mean sending the entire transcript on every turn, and a summary must not be described as perfect recall.
- Bind each conversation turn's visual references to its capture identity. Recapture can retain conceptual discussion while invalidating old screen locations; starting a new session rejects late model and speech callbacks from the previous session.
- Retain the current question, step and a bounded amount of lesson context.
- Accept a custom follow-up about the current step.
- Cancel speech immediately when starting a new turn and reject late results from the old turn.
- Allow explicit recapture when the screen changes. Never reuse old boxes on a new capture.
- Keep per-turn request caps and a visible user-controlled session budget. Starting further paid work after the budget is exhausted requires a new decision.

Acceptance: a follow-up refers to the correct step; a new capture invalidates previous geometry; cancellation cannot resurrect old speech or shapes; all request fan-out remains bounded.

Add push-to-talk after the typed version works and actual use shows typing interrupts study. It is an input improvement over the same session flow. Choose a speech recognition backend after reviewing its local hardware cost or approved service budget.

Continuous cloud video is not a prerequisite for these follow-ups. A later full-duplex implementation can be evaluated separately; provider APIs support such sessions, but they do not remove the need for application-side cancellation and screen freshness handling. Reference: https://ai.google.dev/gemini-api/docs/live-api/capabilities

## 5. Improve the bottleneck the usage record identifies

| Observed problem | Next investment |
|---|---|
| Wrong or stale pointing | Repair geometry, disambiguation or capture freshness before adding presentation features |
| Explanations are generic or unhelpful | Improve prompts and the teaching interaction; test explanation of the next reasoning step |
| Waiting causes abandonment | Separate OCR, model and playback delays; evaluate streaming only against the measured dominant delay |
| Typing breaks the study flow | Push-to-talk on the existing follow-up session |
| Local voice makes explanations hard to follow | Evaluate a better voice with a bounded, approved comparison |
| Learners want a stronger sense of presence after the interaction is useful | Add the cursor companion without changing the tutor engine |
| The app is useful and stable | Improve setup, record a representative demo, update the README and prepare a release for owner approval |

The cursor companion is part of the intended experience. It follows a useful spoken tutor because it improves presence without proving comprehension or target correctness.

## Working rules

One stage at a time. Implementation lands as forward commits; no history rewriting. Agree a measurement's corpus, metric, threshold, environment, sample count and request budget before running it. Retain failures and raw results, record the measured commit and any dirty state, and commit evidence before citing it as a project result.

Build and exercise UI/controller behaviour with mocks, synthetic data and offline lessons first. Obtain owner approval before API spending, installations, large downloads, deletion, publication or other outward-facing actions. Never expose secrets.

A failed measurement leads to a recorded finding, an explanation and a new decision. An explicit change in goal or scope is allowed; silently lowering a target to call the old experiment a success is not.

Deferred until supported by use: continuous observation, broad app coverage, a new OCR runtime, cloud narration, long-term learner profiles, accounts and billing.

Immediate next steps: begin the personal-use observation before choosing more features, following the owner confirmation that narration works. Record the specific interactions exercised and any problems rather than inferring exhaustive validation from this confirmation. Local narration is implemented and reviewed; no actual audible timing measurement has been made. Geometry, capture safeguards, selection lifecycle, crop rendering and stale-screen recovery have passed local review and automated checks; native offscreen smoke checks used synthetic events and fake capture/model callbacks. Demo controls remain development tools; further demo polishing is deferred. The location, Escape and stale-worker corrections have passed local review and checks; broader native Windows focus and global-hotkey validation remains open. Reconciliation of historical documentation claims with committed benchmark evidence remains open.
