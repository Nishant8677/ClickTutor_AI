# Study-session foundation

The desktop app now starts with a fresh in-memory study session. When a lesson is accepted, its question and model answer become context for later questions in that app run. Failed, cancelled, empty, stale-screen and retired-worker results are not remembered.

Each request receives a frozen snapshot of the earlier accepted exchanges and the current session identity. The worker passes that snapshot through the existing lesson-engine history input. Session identity is checked alongside request identity, so output from an ended session cannot redraw the overlay, speak, open an error dialog or enter the new session's history.

The companion's **New session** control invalidates old ownership first, then stops narration, retires active and pending lesson work, stops a demo, clears the displayed lesson and capture state, removes the prepared area and draft, and returns to Ready. It keeps the learner's Voice preference. A running model worker is asked to stop and allowed to drain off the UI thread; a new request waits behind it under the existing one-worker limit.

History is bounded without another dependency: at most eight complete exchanges and 12,000 content characters are retained, evicting the oldest complete exchange first. A request holds immutable exchange values and creates fresh role/content dictionaries for the model boundary. Nothing is saved to disk, so exiting and reopening starts clean.

The existing lesson engine currently formats only the latest ten role messages, so ordinary new questions use at most five of the eight retained exchanges. The larger bounded snapshot remains available for the typed follow-up path. No summarization or retrieval call is made.

The full deterministic suite, Ruff, configured Mypy and whitespace checks passed. A native Windows PyQt6 offscreen check verified the control layout and reset behavior without capture or model calls. Exact commands and limitations are in [the check record](checks/study-session-foundation.json).

Typed follow-up questions remain the next separate ticket. RAG is still deferred until the tutor needs to retrieve information from external notes, documents or older saved sessions.
