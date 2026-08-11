# Conference deck

[`index.html`](index.html) is a self-contained 50-minute talk about this project:
what CLIP is, what the board is, how the encoder was split between an MCU and an
FPGA, how the frame went from 3,359 ms to 304 ms, and how a 512-d embedding
becomes an answer on an LED. 38 slides, eight sections, aimed at a general
technical audience — each domain is taught from scratch before the deep-dive.

Open it in a browser. There is no build step and no server:

```
open slides/index.html
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

Every number in the deck is sourced from
[`../README.md`](../README.md), [`../docs/architecture.md`](../docs/architecture.md)
and [`../docs/history.md`](../docs/history.md) as of 2026-08-11 — the frame time,
the resource counts, the link rates, the ladder, the two-band solve, and the
M21 held-out tables. **Those files are the source of truth.** When one of them
changes, this deck is stale until someone says otherwise; it is not checked by
`tools/check_links.py`, which only reads markdown.
