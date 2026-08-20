# 2026-08-21 — the degenerate-enrolment guard, and the three sets it was checked on

Three short runs, not a soak and not a bench. They are the verification for
[#25](https://github.com/kazunori279/fpga-open-vocab/issues/25): a query set
whose level axis is identically zero used to be invisible, and it had already
cost two benches before anyone noticed.

## What the failure was

`host/demo.py` sends `normalize(e_pos - mean(e_neg))` for a contrast query, so
`"A / B"` and `"B / A"` are **bitwise negatives** of each other. Then `cos[A] ==
-cos[B]` on every frame, their COCO background statistics are measured on the
same 5000 photos and come out mirrored too, and `lvl = mean(z)` is a constant.
`level` cannot move, M21's centring subtracts nothing, and both of M21's axes
collapse to the raw pair — while the frame lines look exactly as they always do.

The three enrolment guards all read `qref[]`, which is downstream of the
collapse, so none of them can see it. The degeneracy is a property of `qvec[]`.

## What the guard prints

One line on **every** set, because a line that only appears when something is
wrong cannot be used to confirm that nothing is:

```
            level axis carries 0.95 of one query's swing; most opposed pair +0.810
```

`level axis carries` is `|mean(qvec/qsd)| / mean(|qvec|/qsd)` — 1.0 when every
query points the same way, 0.0 when they cancel. The weight is `1/qsd` because
that is how `z` is built, so this is the mean `report()` will actually take. It
is reported and never judged; the bar is on the pair cosine, which in the
failure case is exactly −1.

## The three sets

### `degenerate-pair.log` — the failure, caught at load

```
uv run host/demo.py "an opened book / a closed book" "a closed book / an opened book"
```

```
queries   : 2 accepted, 512-d, crc ok
            level axis carries 0.00 of one query's swing; most opposed pair -1.000
            'an opened book~' AND 'a closed book~' ARE EXACT NEGATIVES, so the LEVEL AXIS IS DEAD: ...
```

Printed **before a single frame is captured**, not six minutes in at the second
enrolment. The frame lines then confirm it from the other side — the two queries
are exact mirrors and `lvl` never moves:

```
frame     0 :  a closed book~ +0.32  an opened book~ -0.32   led  13/132 h0.26 lvl+0.00   -
frame     1 :  a closed book~ +0.08  an opened book~ -0.08   led   1/221 h0.06 lvl+0.00   -
```

and the stopped summary says it again, for the reader who has the harness
redirecting to `--out`:

```
            enrolment: 'an opened book~' and 'a closed book~' are exact negatives, so every presence and
            level number above is void - a margin figure is not (#25)
```

### `two-plain-queries.log` — the healthy control

```
uv run host/demo.py "an opened book" "a closed book"
```

```
            level axis carries 0.95 of one query's swing; most opposed pair +0.810
frame     0 :  a closed book +0.69  an opened book +0.57   led  71/ 42 h0.56 lvl+0.63   -
```

No warning, and `lvl` moves. 0.95 is itself worth reading: two plain queries
about books are almost all common mode, which is what the level axis is for.

### `contrast-plus-two-plain.log` — three queries, no false positive

```
uv run host/demo.py "an opened book / a closed book" "a glass with tea" "a hand"
```

```
            level axis carries 0.72 of one query's swing; most opposed pair -0.011
```

A mean-subtracted contrast query is near-orthogonal to unrelated plain ones,
which is the case that would have tripped a naive "any negative cosine"
rule. It also exercises `nq > 2`; the figure is computed off the Gram matrix, so
it generalises past pairs.

## Why it reports rather than refuses

The scores a degenerate set produces are not wrong, they are narrower than they
look. The 2026-08-20 14:22 bench asked a margin question and its AUC is valid —
refusing would have thrown a real measurement away. What is void is every
presence and level number, and that is what the warning names.
