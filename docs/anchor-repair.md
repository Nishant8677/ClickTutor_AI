# Getting an AI tutor to point at the right thing

ClickTutor watches your screen, answers a question about it, and draws a circle
around the thing it is talking about. The circle is the whole product. An
explanation without one is just a chat window.

To draw it, the model has to name something that is actually on screen — an
"anchor" — which OCR then locates so the overlay knows where to paint. When the
anchor cannot be found, the step renders with no highlight at all: an
explanation pointing at nothing.

This is how that went from failing one step in five to not failing, why the
metric that said so was measuring less than it looked like, and what I got wrong
on the way — which was a lot, and is the more useful half.

Every figure below names the file or commit it came from. Where a number cannot
be reproduced from this repo, it is marked as such rather than quoted.

The short version, if you read nothing else:

- Repairing rejected anchors took localisation from 79% to 33/33.
- That metric measured whether a *lookup resolved*, not whether the box was
  right. Nine highlights were confidently on the wrong words and counted as
  successes.
- Which of the locator's six passes fired predicts correctness exactly —
  phrase-level 40 right and 0 wrong, word-level 0 right and 9 wrong — and the
  locator already knew, it just wasn't saying.
- On readable screens OCR beats a vision model 97% to 76%. On screens OCR cannot
  read, the vision model wins 96% to 29%. Neither wins outright.
- Replacing Tesseract with Florence-2 helps more than replacing the locator
  does, and costs 12.8 seconds an image.
- Three of my predictions along the way were wrong, including one where I
  recommended skipping the experiment that turned out to matter most.

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

## Part two: the metric was measuring the wrong thing

Everything above is true and was, for a while, where the story stopped. Then I
measured the thing the metric could not see, and the conclusion moved three
times.

### The lookup was succeeding on the wrong words

`find_text` runs six passes, loosest last. It returns a box and nothing else,
so a caller cannot tell an exact phrase match from a desperate one. Some of
those passes are desperate. `exact_word` matches **any single word** of a
multi-word target, anywhere on screen.

That is how `"Moral: Intelligence is strength"` resolved to an 18×7 pixel box
around the lone word `strength` on a different line. How `"Angle: 45"` and
`"Angle: 90"` both matched `angle` and returned *the same box*. How the single
character `k` returned a 199-pixel box spanning most of a line.

Every one of those was reported to the caller as a successful lookup. The
benchmark counted them as located, which they were. They were also wrong.

**A wrong highlight is worse than no highlight**, because a circle is a claim
about what the explanation refers to. The pipeline was making that claim
confidently and silently.

### Which pass fired predicts whether the box was right

I expected the split to be exact versus fuzzy matching. It is not. Recording
the pass and cross-tabulating against hand verdicts, over 52 matches on two
corpora:

| Pass | On target | Wrong |
|---|---|---|
| `line_substring` | 39 | 0 |
| `exact_phrase` | 1 | 0 |
| `exact_word` | 0 | 6 (+3 partial) |
| `partial` | 0 | 1 |

**Phrase-level 40/0. Word-level 0/9.** Total separation, and not along the line
I predicted — `exact_word` is an exact match, of the wrong thing.

That is a routing signal available for free: the locator already knew which
pass fired, it just wasn't saying. So `locate_trusted` returns a box only on a
whole-phrase match, a word-level match is treated as a miss, and a miss reaches
the repair loop that already existed for exactly that case — an anchor that
isn't on screen as written.

Re-running accuracy under the stricter rule: **33/33, unchanged.** Grounding
means the model quotes anchors out of OCR lines, so they match as phrases
anyway. Same number, stronger claim.

### It held up on screens it was not derived from

A rule with perfect separation on the 16 images it came from is fitted, not
validated. Seven screenshots taken afterwards, not selected for difficulty — a
Reddit thread, Google image results, the Windows start menu, VS Code, a Hugging
Face Space, a job application form, an event listing:

**26 anchors, all located by the phrase-level pass, 25 correct, 0 wrong, 1
uncertain.** No spurious fallbacks, which was the live risk: a rule that
refuses good matches breaks the common case to fix a rare one.

The other half is untested. **Not one anchor took a word-level pass**, so those
seven screens say nothing about whether such matches are wrong; that still
rests on 12 samples from the original 16 images. Ordinary screens apparently
don't produce word-level matches at all — the failure being guarded against is
rare, and the guard costs nothing when it is.

The uncertain one is `"CC"`. The box landed on a glyph I could not confidently
read as those letters, and the string occurs elsewhere on the page. Calling it
correct would have made the headline 26/26, which is why it isn't called
correct.

## Part three: OCR versus a vision model, three times

The obvious next step was to stop routing through OCR and ask the model for
pixel coordinates directly. The lesson request already sends the screenshot, so
the model has the image either way — the only question is whether it returns a
string or a box.

### First answer: OCR wins

Same rule applied to both locators on the eight readable screens:

| | Localised |
|---|---|
| OCR + repair | **33/34 (97%)** |
| Vision (`gemini-3.1-flash-lite`) | 26/34 (76%) |

Vision never refused — it returned a box for all 34 — but eight landed on the
wrong words, four of those on C++ statements with punctuation. Boxes were also
loose: only 3% reached IoU 0.75 against the matching OCR occurrence, and a
highlight that overlaps roughly still looks wrong.

Cost went the same way. Vision needs **one call per anchor**; the OCR path
needs **1.12 calls per lesson**, measured, because repair only fires on a miss.
The "two calls per lesson" figure I had been quoting for the OCR path was never
measured and was wrong.

**I had to fix my own method mid-experiment.** The first version scored IoU
against the single box `find_text` returned, which conflated three different
outcomes: vision being wrong, vision finding a *different but equally valid*
occurrence, and the reference box itself being wrong. All three were present —
`arr` and `missing` each appear several times, and the `k` reference was that
199-pixel box. On one image the flawed method reported mean IoU **0.192** where
the honest figure was **0.537**.

The fix was to stop using a reference at all: read the OCR words inside the
returned box and ask whether they contain the phrase, capped at two extra words
so a full-screen box cannot pass. That rule then had to be applied to OCR too,
which is why OCR scores 97% here and not the 100% the accuracy benchmark
reports. Different questions — "did a lookup return anything" versus "was the
box right".

### Second answer: the corpus was wrong

Eight readable screens is the wrong evidence for a question about screens OCR
*cannot* read. Eight of those — handwriting, a whiteboard, photographed book
pages, printed maths, a rotated-label diagram, dark-mode UI, text over
photography — hand-scored from rendered overlays:

| | Correct |
|---|---|
| Vision | **23/24 (96%)** |
| OCR, actually correct | 7/24 (29%) |
| OCR, *reported* a hit | 16/24 (67%) |

Read the last two rows together. Nine of twenty-four times, OCR returned a
confident box on the wrong thing. Vision missed once, by about fifty pixels.

So neither wins outright, and the earlier conclusion held only for its own
corpus. Readable screens: OCR 97%, vision 76%. Unreadable: vision 96%, OCR 29%.
The architecture that follows is routing, not replacement.

### A trigger that looked obvious and wasn't

Mean OCR confidence separates the two corpora almost perfectly — every readable
screen scores 81.6 or above, most hostile ones below 79. It does not work.
`text_over_image` scores **94.8** and OCR still failed two of its three targets,
because the average is dominated by a readable paragraph elsewhere on the page.
A whole-image average cannot see a region that failed.

The threshold was also read off the same sixteen images it would be judged on.
Fitted, not validated. The signal that does work is the per-target one above:
which pass matched.

### Third answer: the model tier mattered more than expected

All of that was measured on `gemini-3.1-flash-lite`, the cheapest tier, which
confounds "vision doesn't work" with "the cheap model doesn't do this well".
Holding the lesson and anchors fixed and swapping only the locator, over the
same 33 anchors:

| | Localised | IoU median | ≥0.75 | Latency |
|---|---|---|---|---|
| `gemini-3.1-flash-lite` | 25/33 (76%) | 0.375 | 3% | 1,661ms |
| `gemini-3.6-flash` | **29/33 (88%)** | **0.641** | **30%** | 4,378ms |
| OCR | **32/33 (97%)** | — | — | — |

A better model closes about half the gap, and improves *tightness* far more
than accuracy — boxes tight enough to look right go from 3% to 30%. It still
does not overtake OCR on readable screens, so the routing order stands.

`gemini-3.1-pro` is untested rather than rejected: it reports a free-tier quota
of zero on three separate keys, which is an account-type limitation, not a
per-account one.

## Part four: the OCR engine is also a variable

Every comparison above treats OCR quality as fixed. It isn't — it's one
component, and swapping it changes which regime a screen falls into.
Florence-2, scored against hand-verified phrases:

| | Tesseract | Florence-2 |
|---|---|---|
| Readable (21 phrases) | 0.886 mean, 16/21 usable, **0 failures** | 0.912 mean, 18/21 usable, **0 failures** |
| Unreadable (23 phrases) | 0.598 mean, 7/23 usable, **10 failures** | **0.832 mean, 19/23 usable**, 4 failures |

Near parity on readable screens, far ahead on unreadable ones. The worry that a
scene-text model would fall over on dense code did not materialise.

This matters more than a locator swap would, because grounding quotes OCR
output into the prompt. An engine that *reads* these screens makes the whole
existing pipeline work on them — grounding, repair, the router — with no
per-anchor calls and no new code paths. A vision locator buys localisation
while leaving the lesson grounded on noise.

Against it: **12.8 seconds per image**, against Tesseract running locally in a
fraction of that, and it sends the screen to a third party. Which argues for a
second read rather than a first — and the trigger for "the first read was poor"
is the phrase-level failure the router already detects.

Ground truth for the readable half is transcribed by reading the images, in
`benchmarks/readable_ground_truth.json`. The hostile half took its phrases from
a vision model, which is fine for ranking two engines but tilts the absolute
numbers toward vision-family readers — and Florence is one.

Nothing here is wired into the application. Sending screenshots to a
third-party host is a privacy decision, not a benchmark's call.

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

The fix is not a better threshold. I thought it was localisation that does not
route through OCR at all — pixel coordinates from a vision model — and said so
here before measuring it.

Measuring it gave a different answer. Vision localisation loses on readable
screens and wins on unreadable ones, so the fix is not a replacement but a
routing decision, with a per-target signal the locator already had and wasn't
reporting. And the deeper fix isn't in localisation at all: it's in the *reading*,
because grounding means a lesson built on garbage OCR is meaningless however
precisely it is highlighted.

The blackboard case above is still the sharpest illustration, and it now has a
measured counterpart — on eight OCR-hostile screens Tesseract's output was
usable for 7 of 23 phrases, and OCR returned a confident box on the wrong thing
nine times out of twenty-four while reporting success.

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

**Check what the corpus can answer before trusting what it says.** "Vision loses
to OCR" was measured entirely on screens OCR reads well. The evidence was sound;
it just could not speak to the case the change was meant to address.

**A rule with perfect separation on its own data has not been tested.** 40/0
against 0/9 looked conclusive on the sixteen images it came from. Seven
unrelated screens confirmed half of it and could not exercise the other half at
all, because the failure it guards against never occurred.

**Hold one thing fixed.** Swapping the model on a full run would have changed
the anchors too, leaving two runs different in both the thing under test and the
thing it was tested on. Re-running only the localisation call made the tier
comparison mean something.

**Silent failure is worse than loud failure, and only measurement finds it.**
Nine confidently wrong highlights had never thrown, never logged, and counted as
successes in the benchmark. No crash would ever have surfaced them.

**My predictions were wrong three times.** I expected grounding to fix the
anchors (it moved 79% to 82%). I expected the trust boundary to be exact versus
fuzzy (it is phrase versus word). I put the Florence experiment below 50% and
advised skipping it (it was the largest effect measured). Each is in the git
history with the reasoning that produced it.

---

## Provenance

| Claim | Where to check it |
|---|---|
| 79% baseline, 82% after grounding, failure classification | `git show 45f1d67` |
| 33/33 anchors over 8 screens, 9.73s end-to-end | `benchmarks/benchmark_results.json` |
| Pass-vs-verdict table, 1.12 repair calls per lesson | `benchmarks/locator_comparison.json` |
| Vision 23/24 on unreadable screens, OCR 7/24 | `benchmarks/hostile_locator.json` |
| Model-tier comparison on identical anchors | `benchmarks/locator_comparison_flash36.json` |
| Florence-2 vs Tesseract, both corpora | `benchmarks/ocr_engine_comparison*.json` |
| Hand-transcribed ground truth | `benchmarks/readable_ground_truth.json` |
| Held-out validation, 26 anchors on 7 unseen screens | `benchmarks/router_validation.json` |
| SDK, Tesseract and model versions for every run | each file's `environment` block |

Re-runnable:

```
python tools/benchmark.py --accuracy-only
python tools/locator_experiment.py
python tools/hostile_locator_experiment.py
python tools/ocr_engine_comparison.py --ground-truth benchmarks/readable_ground_truth.json
python tools/router_validation.py
```

The last three need hand-scored verdicts to mean anything; the verdicts already
in those files are mine, and the overlays they were scored from regenerate with
`--redraw`.

Commits: `45f1d67` (measurement and repair loop), `74449f7` (reproducible
benchmark), `54bcddd` (latency), `4001f06` (SDK recorded in results), `8c92eff`
(locator comparison), `17622dd` (unreadable screens), `9e1c0df` (pass reporting
and the routing signal), `d192a5f` (router wired), `51a68cc` (model tier),
`d8d35fe` and `0ca105d` (OCR engines), `db2010b` (held-out validation).
