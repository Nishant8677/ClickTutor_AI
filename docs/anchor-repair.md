# Getting an AI tutor to point at the right thing

ClickTutor watches your screen, answers a question about it, and draws a circle
around the thing it is talking about. The circle is the whole product. An
explanation without one is just a chat window.

To draw it, the model has to name something that is actually on screen — an
"anchor" — which OCR then locates so the overlay knows where to paint. When the
anchor cannot be found, the step renders with no highlight at all: an
explanation pointing at nothing.

This is how that went from failing one step in five to not failing, and what I
got wrong on the way.

Every figure below names the file or commit it came from. Where a number cannot
be reproduced from this repo, it is marked as such rather than quoted.

---

## Measuring before changing anything

I started by assuming the OCR matcher was too strict. It runs six passes —
exact phrase, exact word, fuzzy, partial — and the fuzzy threshold looked like
an obvious tuning knob.

So I measured first. Over 33 steps on three OCR-readable screenshots, **79% of
anchors resolved**. Then I classified every miss:

| Failure category | Count |
|---|---|
| Anchor text absent from OCR output entirely | 7 |
| Matcher failed on text that *was* in the OCR | **0** |

Zero matcher failures. The knob I was about to turn had nothing to do with it.

The misses were also concentrated: six of seven were the same phrase,
`"in-place"`, across two LeetCode screenshots. The visible text on those pages
says *"modify the input 2D matrix directly"*. The word "place" appears nowhere.

The model was not misreading the screen. It was answering from its knowledge of
the canonical problem — "rotate the image **in-place**" is how that problem is
always described — and naming something it expected to be there.

## Attempt one: tell the model what is on screen

The prompt already said *"Copy the visible text EXACTLY."* Clearly not enough,
since the model was reading the image visually and paraphrasing.

So I put the OCR output in the prompt: every line Tesseract extracted, with an
instruction to copy anchors character-for-character from that list. The appeal
is that it makes anchors findable *by construction* — the model picks from
exactly the strings the locator will search. It also makes OCR errors
self-consistent: if Tesseract misreads `count` as `cou nt`, the model copies the
misreading, which still matches at lookup time.

**Result: 79% → 82%.**

On a sample that size, that is noise. The model kept choosing `"in-place"`
regardless.

I kept the change, but not for the reason I made it. It is still the right
shape — it removes a whole class of failure — it just does not fix the specific
failure I had.

Reporting this is the point. It would have been easy to ship "prompt grounding
raised accuracy" and never mention that the number barely moved.

## Attempt two: reject and re-ask

If the model will not stay inside the vocabulary when asked nicely, check its
answer and ask again.

After parsing, every anchor is tested against the OCR data. Any that does not
resolve triggers a second call naming the rejected phrase:

> The phrase "in-place" does NOT appear in the text on screen, so it cannot be
> highlighted. Here is every line of text actually on screen: […] Reply with ONE
> short phrase copied character-for-character from the lines above.

**Result: every anchor resolved.**

The replacements were better than the originals:

| Rejected | Replacement |
|---|---|
| `in-place` | `modify the input 2D matrix directly` |
| `in-place` | `DO NOT allocate another 2D matrix` |

Both point at where the constraint is actually stated on the page. The
correction produced a better anchor than the first attempt would have.

It is bounded to three repairs per lesson so a badly-read screen cannot fan out
into many calls, and it only fires on misses, so typical lessons pay nothing.

## What I rejected, and why

The obvious cheap alternative is a similarity fallback: if the anchor is not
found, snap to the closest OCR line.

I tested it before building it. The closest line to `"in-place"` scores 0.33,
and it is **`"Example 1:"`**. That would have confidently highlighted the wrong
thing — worse than highlighting nothing, because a wrong circle is a wrong
claim about the code.

Semantic matching would have worked. That needs embeddings or another model
call, which is what the repair loop already is.

## Where the results stand

Across eight screens — C++ source in an editor, three LeetCode problems, a
hardware diagram, a long-form article, a GitHub profile, a chat UI —
**33 of 33 anchors located**. Reproducible:

```
python tools/benchmark.py --accuracy-only
```

The full result, including the per-image breakdown and the environment it was
measured in, is committed at `benchmarks/benchmark_results.json`.

### On latency, carefully

The same file records **9.73s mean end-to-end** over ten iterations
(capture 81ms, OCR 4,734ms, lesson API 4,893ms, anchor lookup 15ms).

Accuracy did cost latency: grounding puts the OCR lines into every prompt, and
repair adds a call whenever an anchor misses. That trade was deliberate. A fast
lesson that points at nothing is not a cheaper version of the product; it is a
different, broken one.

But I cannot tell you the size of that cost, and it would be easy to pretend
otherwise. The pre-grounding baseline was measured by a benchmark harness whose
results file was never committed — an unanchored `.gitignore` pattern kept it
out of the repo — so there is no file to open. The direction is known. The
magnitude is not, and a number I cannot reproduce does not belong here.

Two further cautions on the 9.73s itself:

- Latency is measured against **whatever is on screen**, because the pipeline
  starts with a live capture. A denser screen means more OCR *and* a longer
  prompt. An earlier run on the same code measured 8.62s; the split moved
  (OCR 2.6s → 4.7s, API 5.9s → 4.9s) more than the total did.
- Two samples of ten, taken against different screen contents, are not a
  comparison. Quote the figure with its environment or not at all.

**Anchor lookup is 15ms, and that is not render time.** It is the six-pass
matcher searching OCR output; it touches no Qt and paints nothing. An earlier
version of the chart labelled this bar "Render", which invited exactly the
wrong reading. Nothing in this benchmark measures rendering.

---

## The part I would put first in an interview

The metric is measuring less than it appears to.

Grounding means the model picks anchors *from* OCR output, so the locator will
nearly always find them. Testing a blackboard of handwritten equations made this
obvious. Tesseract read the chalk as garbage — `el-etoh a) MO Coa RdRp` — and
the pipeline still scored three anchors out of three, because it anchored on
fragments like `x<0`, `K,>` and `10"`.

(That blackboard run was exploratory and is not in the committed corpus, so
treat it as an observation rather than a measurement.)

Those anchors resolved. They are also meaningless. Pointing at `K,>` on a
physics blackboard teaches nobody anything.

So `anchors_located / anchors_total` measures **whether the lookup resolved**,
not **whether the anchor was worth pointing at**. On readable screens those
coincide. On a blackboard they come apart completely, and the metric cannot
tell the difference.

That is not a bug in the implementation. It is a limit of the measurement, and
it means "100%" is a weaker claim than it sounds. Knowing *which* claim a number
supports matters more than the number.

The fix is not a better threshold. It is localisation that does not route
through OCR at all — pixel coordinates from a vision model — which is the next
architectural step rather than a tuning exercise.

---

## What generalises

**Classify failures before fixing them.** The six-pass matcher was my prime
suspect and was responsible for none of it. Ten minutes of categorising misses
saved a day of tuning the wrong component.

**Publish the intermediate that did not work.** Grounding moved 79% to 82%.
Reporting only the final 100% would describe a straight line that never
happened.

**Test the cheap fix before building it.** Similarity fallback took five
minutes to disprove and would have shipped a confidently wrong highlight.

**Ask what the metric actually measures.** Mine was closer to "did the string
lookup succeed" than "did the tutor point at the right thing", and grounding
quietly moved it further in that direction.

**A number without a file is a guess.** Three figures in the first draft of this
document had drifted from the artifact they claimed to describe, and a fourth
could not be traced to any file at all. All four read as authoritative.

---

## Provenance

| Claim | Where to check it |
|---|---|
| 79% baseline, 82% after grounding, failure classification | `git show 45f1d67` |
| 33/33 anchors over 8 screens | `benchmarks/benchmark_results.json` |
| 9.73s end-to-end, stage breakdown | same file, `latency` section |
| SDK and Tesseract versions for both | same file, `*_environment` sections |
| Benchmark is re-runnable | `python tools/benchmark.py` |

Commits: `45f1d67` (measurement and repair loop), `74449f7` (reproducible
benchmark), `5099799` and `46c658b` (corpus), `54bcddd` (latency), `4001f06`
(SDK recorded in results).
