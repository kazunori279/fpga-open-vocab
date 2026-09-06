# Conference deck

[`index.html`](index.html) is a self-contained 50-minute talk about this project:
what CLIP is, what the board is, how the encoder was split between an MCU and an
FPGA, how the frame came down from 3,359 ms, and how a 512-d embedding becomes
an answer on an LED. 39 slides, eight sections, aimed at a general technical
audience — each domain is taught from scratch before the deep-dive.

**The frame time and the shipped clock are current** — both decks say 282 ms,
265 ms encode, 320 MHz sys / 160 MHz link, the same as the rest of the repo.
What they do not yet say is that 282 ms is the sum of parts and the board's own
clock reads **293**; the decks quote one number where the repo now quotes two.
See [Keeping it true](#keeping-it-true) for which files to re-read from.

[`index.ja.html`](index.ja.html) is the same 39 slides in Japanese. It is a
translation, not a fork: the structure, the SVGs and every number are the same,
and a link in the top-right corner of each deck switches to the other one. The
type is set a few points smaller with the negative tracking relaxed, because
Japanese sets denser than English at the same size.

Both are published to GitHub Pages by [`../.github/workflows/pages.yml`](../.github/workflows/pages.yml)
on every push to `main`:

- <https://kazunori279.github.io/fpga-open-vocab/slides/>
- <https://kazunori279.github.io/fpga-open-vocab/slides/index.ja.html>

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

Two files outside this directory, both optional:

- [`../docs/img/wire.svg`](../docs/img/wire.svg) — the WaveDrom timing diagram, on
  the "One transaction on the wire" slide. It is loaded by relative path, so the
  deck must stay at `slides/` for it to resolve.
- `assets/me.jpg` — the speaker photo on the self-intro slide. **Not committed.**
  Drop a square image there and it appears; without it the slide renders with the
  avatar hidden and nothing else moves.

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

The last reconciliation was 2026-08-16, which is why the shipped 320/160 and the
282 ms frame are in there. Don't trust that date in the abstract — `git log
slides/` is the only honest answer to "how far behind is it", and the two decks
have to be checked against each other as well as against the sources.
