# Stills, and why they are not benches

PNGs off the appliance's own camera, shot so that a question about the *encoder*
can be asked without spending a morning of daylight on a bench.

**Nothing in here is comparable to a row in [`../README.md`](../README.md)'s
manifest.** No cue schedule, no enrolment, no held-out set, no `.cues` sidecar,
no held-out percentage. A bench measures the whole appliance — camera, staging,
enrolment, decision rule — and costs a morning. A set in here measures one thing
about the model and costs about four minutes.

## Shooting a set

```sh
mkdir -p bench/stills/20260822-laptop
printf 'an opened laptop\na closed laptop\n' > bench/stills/20260822-laptop/queries.txt

sh bench/stills/shoot.sh 20260822-laptop open   1     # then close it by hand
sh bench/stills/shoot.sh 20260822-laptop closed 1
sh bench/stills/shoot.sh 20260822-laptop open   2     # and again
sh bench/stills/shoot.sh 20260822-laptop closed 2
```

**One set is one pair**, and `queries.txt` is what records which pair — beside
the pixels, rather than in a commit message or someone's memory.
`20260821-bisect/` predates the convention and holds two pairs; its README says
so.

**Alternate the rounds. This is not optional.** Twenty consecutive frames of one
scene followed by twenty of the other confounds the class with the AEC, the
daylight and the operator, which is the confound that made four glass benches
unreadable. Two rounds is the floor — below that `probe_bisect.py` cannot
measure its drift null, and it prints `n/a` rather than a zero.

`shoot.sh` grep-checks every run for #25's `enrolment:` and #26's `scene:`
flags, so a set shot through a bad exposure ramp says so at capture time instead
of after the analysis.

## Reading a set

```sh
uv run --script tools/probe_bisect.py \
    --a bench/stills/20260822-laptop/open --b bench/stills/20260822-laptop/closed \
    --pos "an opened laptop" --neg "a closed laptop"
```

Teacher 1152 → PCA 512 → student fp32, on the same pixels, in the board's own
`z` margin, quoted as effect size because AUC saturates. Archive the output next
to the stills.

**A control belongs in any set that might come back low.** A student row reading
0.2 sd means nothing until a pair the board is known to carry has gone through
the identical path — that is what the book pair does in `20260821-bisect/`, and
it is the reason that set's verdict is trustworthy.

## Sets

| set | pair | what it settled |
| --- | --- | --- |
| [`20260821-bisect/`](20260821-bisect/) | `a glass with tea` / `an empty glass`, plus a book control | the axis is lost at the student and nowhere earlier — not the resolution, not the projection ([#24](https://github.com/kazunori279/fpga-open-vocab/issues/24)) |

## What a set can and cannot answer

It can say **which stage of the encoder chain drops a distinction**, which is
what [#24](https://github.com/kazunori279/fpga-open-vocab/issues/24) needed and
what no bench can see.

It cannot say **what the appliance will score**. Sixteen runs of the same book
pair on the same desk span a margin of 1.000 to 0.579; the staging is a real
variable and a set of stills holds it still on purpose. A pair that reads well
here still has to be benched before any accuracy number is quoted.

And a **low reading is not permission to skip a bench** either — only a low
*teacher* reading is, because nothing downstream can recover what the teacher
never had.
