# Typed follow-ups

When a generated lesson is on screen, the companion question field now asks about the current step. A nonblank submission sends one direct follow-up request using the already accepted screenshot, current step, bounded OCR text and recent in-memory session exchanges. It does not capture the screen, rerun OCR, rebuild lesson steps, relocate anchors or make repair calls.

The companion shows **ANSWERING** while the request runs. The lesson image and highlight stay in place. An accepted answer replaces the step text temporarily, becomes the latest session exchange and is read by the existing local voice when Voice is enabled. Replay repeats that answer. Next or Back clears the temporary answer and resumes the stored lesson steps.

**Ask screen** is the explicit recapture action when the learner's screen has changed. It uses the typed question or the existing default question and follows the original capture, OCR and lesson path. **Select area** remains available on a separate compact row. On native Windows Qt metrics, the 380 px companion leaves 202 px for the follow-up field.

Follow-ups use the same single background-worker slot as lessons. A newer request can wait behind a draining cancelled worker, but provider calls never overlap and only the latest owned request may update the interface. Ownership includes request serial, session identity, displayed lesson identity, step index and accepted image identity. Escape cancels a follow-up without clearing the lesson. Errors restore the base step with an inline notice; stale callbacks cannot append history, start speech or release a newer worker.

Each prompt field is deterministically clipped. Visible OCR text is bounded to 120 lines and 4,000 characters including separators; the step title, anchor, context, explanation, crop note, original lesson question and follow-up question have local character caps. At most five complete recent exchanges are included, within the existing 12,000-character session bound. The stored screenshot is sent because follow-up wording may refer to a visual element.

The complete deterministic suite, Ruff, configured Mypy and whitespace checks passed after integration. Focused UI and lifecycle checks passed after the final compact-layout adjustment. A native Windows PyQt6 offscreen smoke used fake capture, model and speech components to verify the layout, stored-context route, accepted result, navigation and cancellation without consuming API quota.

This version remains in memory only. It has no RAG, external document retrieval, cross-launch persistence, summarization, voice input or streaming. The native smoke does not establish live model answer quality, audible voice quality, physical hotkey behavior or arbitrary display-scaling support. Those require deliberate personal-use checks before another feature is chosen.
