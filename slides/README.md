# Conference deck

[`index.html`](index.html) is a self-contained 50-minute talk about this project:
what CLIP is, what the board is, how the encoder was split between an MCU and an
FPGA, how the frame came down from 3,359 ms, and how a 512-d embedding becomes
an answer on an LED. 45 slides, eight sections, aimed at readers with basic FPGA and machine-learning knowledge.
It introduces project-specific terms before explaining the implementation.

**The frame time and the shipped clock are current** — both decks say 282 ms as
the sum of parts and **293 ms** by the board's own clock, 265 ms encode,
320 MHz sys / 160 MHz link, the same as the rest of the repo.
See [Keeping it true](#keeping-it-true) for which files to re-read from.

[`index.ja.html`](index.ja.html) is the same 45 slides in Japanese. It is a
translation, not a fork: the structure, the SVGs and every number are the same,
and a link in the top-right corner of each deck switches to the other one. The
type is set a few points smaller with the negative tracking relaxed, because
Japanese sets denser than English at the same size.

Both are published to GitHub Pages by [`../.github/workflows/pages.yml`](../.github/workflows/pages.yml)
on every push to `main`:

- <https://kazunori279.github.io/fpga-open-vocab/slides/>
- <https://kazunori279.github.io/fpga-open-vocab/slides/index.ja.html>

The deck detects the browser’s primary language on entry: Japanese (`ja` or
`ja-*`) opens the Japanese deck; other languages open the English deck. Both
entry files apply the same rule and preserve the query string and slide number.
The language toggle uses `?lang=en` or `?lang=ja` to override detection, so a
manual selection remains in effect on reload and when sharing that URL.

Or open either in a browser. There is no build step and no server:

```
open slides/index.html
open slides/index.ja.html
```

| key | |
|---|---|
| <kbd>→</kbd> <kbd>space</kbd> <kbd>PgDn</kbd> | next |
| <kbd>←</kbd> <kbd>PgUp</kbd> | previous |
| <kbd>Home</kbd> / <kbd>End</kbd> | first / last |
| <kbd>f</kbd> | fullscreen |

Clicking the right or left half of the window pages too, and the slide number is
in the URL fragment — `index.html#22` opens on slide 22, which is what to paste
when someone asks about a specific claim.

## What it depends on

Required local images:

- [`../docs/img/wire.svg`](../docs/img/wire.svg) — the WaveDrom timing diagram, on
  the "Link transaction format" slide. It is loaded by relative path, so the
  deck must stay at `slides/` for it to resolve.
- `assets/forgix-board.png` and `assets/arducam-mega-3mp.jpg` — product
  images on the introduction slide. Sources are linked on the slide and recorded
  in [`assets/README.md`](assets/README.md). The Arducam Mega 3MP SPI supplies the demo’s inference images.

Optional local image:

- `assets/me.jpg` — the speaker photo on the self-intro slide. **Not committed.**
  Drop a square image there and it appears; without it the slide renders with the
  avatar hidden and nothing else moves.

Slide 6 embeds the [35-second board demo](https://youtu.be/n6vrb4GZql0), immediately
after the hardware photographs. YouTube playback requires a network connection;
the slide also includes a direct watch link. Leaving the slide unloads the player
to stop playback.

Fonts come from Google Fonts over the network. Offline, the deck falls back to
the system sans-serif and the layout still holds.

## Keeping it true

The two decks are edited by hand and nothing checks that they agree. **A number
changed in one has to be changed in the other**, or the Japanese deck quietly
becomes a snapshot of an older talk.

Every number in the deck is sourced from
[`../README.md`](../README.md), [`../docs/architecture.md`](../docs/architecture.md)
and [`../docs/history.md`](../docs/history.md) — the frame time, the resource
counts, the link rates, the ladder, the two-band solve, and the M21 held-out
tables. **Those files are the source of truth.** When one of them changes, this
deck is stale until someone says otherwise; it is not checked by
`tools/check_links.py`, which only reads markdown.

The last reconciliation was 2026-11-14, against the current working tree.
The decks retain the hardware performance figures and the enrolled empty-scene
rule, replace the single-run 120/120 headline with the 2026-09-09 archive
comparison, and add the registration-free LED probe and the 2026-09-26 household
synthetic screen. The summary now reports the shipped 85% LE utilization.

These updates also draw on `bench/probe/20260909-noenrol/README.md`,
`bench/probe/20260909-pairs/README.md`, `bench/probe/20260909-cubes/README.md`,
and `bench/stills/20260926-home-screen/README.md`. The synthetic screen is
candidate selection, not board accuracy. The evidence links point to the repository, so the synthetic screen files must
be committed alongside the deck before publishing. The latest experiment date is 2026-09-26; the November
date records reconciliation, not a new hardware measurement.

## Language review

Both decks were revised on 2026-11-14 for readers with basic FPGA and ML
knowledge. Terms such as tile, scene registration and room calibration are
defined in context. Historical benchmark conditions are separated from current
application performance. Registration repeats after restaging an object, not
on a different day. The presence comparison labels 53.8% as the distance-band
fallback, as documented in `tools/probe_third.py`. Core 1’s current 149 ms
profile replaces the old 509 ms figure. That review covered 42 slides.

On 2026-11-15, the hardware slide was updated to show the Arducam Mega 3MP SPI
used in the demo, and the public YouTube demo was added immediately after it in
both languages.

A precision diagram after the Host/MCU/FPGA responsibilities slide distinguishes
8-bit activations, int4 weights, int32 running sums and 8-bit requantized output.
The example `255 × 7 = 1,785` illustrates why weight precision cannot also be
the accumulator precision.
