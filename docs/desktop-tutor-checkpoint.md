# Desktop tutor checkpoint

This checkpoint saves the working desktop tutoring flow after the owner confirmed area selection and narration in normal use. It follows baseline `f1b7e20`.

## Included behavior

- Select an area using the companion or Ctrl+Shift+S; Ask captures it. Cancellation preserves the draft and prior selection.
- Keep crop coordinates tied to the captured image, and reject results whose capture or display context is stale.
- Render accepted OCR/vision locations consistently. An unresolved target stays unhighlighted.
- Coordinate Escape and request cancellation so retired workers cannot restore old lessons.
- Speak the accepted step using installed Windows SAPI through Qt. Keep its highlight visible, then wait for Next. Back and Replay read stored explanations; Stop preserves the lesson; Voice off stops and Voice on starts nothing by itself.
- Retire old speech before replacement, selection or cancellation, and ignore stale speech callbacks. A voice failure leaves the text lesson usable.
- Resolve bundled demo paths independently of the working directory. Demo controls remain development tools.

## Validation

The complete deterministic unit suite, Ruff and the configured Mypy modules passed on the implementation recorded by this checkpoint. Commands and their actual outputs are in [the check record](checks/desktop-tutor-checkpoint.json). Qt tests used the offscreen platform; speech backends were silent test doubles.

Earlier native Windows checks used muted SAPI and silent controller fixtures. The owner subsequently confirmed that the exercised area-selection and narration flow works. These checks do not establish numerical latency, exhaustive control coverage or arbitrary display-scaling support. No live tutor request, installation or benchmark was needed for this checkpoint.

## Next work and limits

The next product step is personal studying to identify useful improvements. The [personal tutor roadmap](personal-tutor-roadmap.md) records the proposed session behavior: fresh on launch, conversation context within that session, bounded model context, and explicit reset. Session memory and typed follow-ups are not yet implemented.

The proposed OCR-speed and audible-latency targets remain unmeasured. This is a functional checkpoint, not acceptance of those performance claims. Historical benchmark-document reconciliation remains open. Captures describe a snapshot; this version does not track moving content.
