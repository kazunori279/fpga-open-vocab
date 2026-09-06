# The cold run #30 asked for, second attempt — written before the first frame

*2026-09-07, Monday. Board powered down at 05:24 JST and left unpowered; the
session starts no earlier than 06:00. Scene unchanged from yesterday — the blank
wall about 30 cm from the lens. Nothing is shown to the camera at any point.*

**Everything in this file was committed before any run was launched.** That is
the entire point of this directory, and it is the second directory to make the
claim: [`../20260906-camlock-cold/`](../20260906-camlock-cold/) was aborted after
one pair when the instrument turned out to be broken, and its design was never
spent.

## The design is incorporated by hash, not by copy

The hypothesis, the protocol, the three pre-registered tests, the discard rule,
the timing limit and the two original amendments are the ones in
`../20260906-camlock-cold/README.md` **as of commit `de22117`**, together with
Amendment 3 in the same file at that commit. They are not restated here. A git
hash cannot drift and a copy can, and the thing being protected is precisely
that the design did not move once numbers existed.

`run.sh` in this directory is a copy of that directory's with two differences,
both of them recording and neither of them design: `DIR` points here, and each
run's line in `session.log` now carries `relit=N`, the number of times #33's
ramp rescue fired during that boot. Nothing about the order, the arms, the frame
count or the discard rule differs. `diff` the two files.

In one line, so this file is readable on its own: **eight pairs, sixteen runs of
600 frames, `free` and `lock` alternating with the leading arm swapping every
pair, scored by `tools/probe_camlock.py` on `common` and `margin`.** The primary
test is a one-sided Mann–Whitney U on `common`, locked against free, over all
eight pairs.

## What is different today, and all of it is a precondition rather than a change

**1. The board was warm and was deliberately cooled.** Yesterday's session left
it running, and at 05:21 today it was still enumerated on hub port 1 and looping
frames — powered for about twenty-one hours, which is the opposite of the
condition the hypothesis needs. VBUS was dropped at **05:24** and the board left
unpowered.

This is a weaker cold start than 09-06's, which found the board idle since
08-25, and the difference is recorded rather than glossed: **twelve days off
against roughly forty minutes off.** What the hypothesis needs is a sensor that
is still re-deciding, which a genuine power-down supplies; what it also wants is
a cold room at dawn, which 06:00 JST in September supplies about as well as
06:41 did. If the session shows nothing, "not cold enough" is available as an
excuse and is therefore written down *now*, before it can be reached for later.

**2. The firmware is pinned.** `firmware/build-280/forgix_m9.uf2`, rebuilt at
05:24 today from `firmware/` at commit `1d51090`, md5
`41296ef69f431ec063ba3f0118c3cd38`. This is the build with #33's ramp rescue and
its instrumentation in, as Amendment 3 requires. Yesterday's `.uf2` on disk
predated the commit and was replaced; that it needed rebuilding at all is the
reason the md5 is written here.

The board is expected to be running this already. It will be confirmed from the
first banner rather than assumed — the instrumented build prints a `reset gate:`
line that no earlier build has — and if the line is absent the board is flashed
before pair 1 and this paragraph is amended, not quietly satisfied.

**3. `run.sh` now catches both of `ft_acquire()`'s doubts.** It grepped only for
`EXPOSURE NEVER SETTLED`; the quiet form, *the exposure never moved from its
first reading*, is the one that let `m9_cue-20260816-172256.log` be scored for
three weeks. Commit `90a2189`.

## Recorded for #32, with no test pre-registered

`last mean RGB` per run, as 09-06 planned, and now also **the number of acquires
the rescue fired on**. Sixteen cold boots is the first sample of what the rescue
is worth on a cold board rather than on one that has been running all day. It is
an observation this session gets for free, not a question it was designed to
answer, and no test is pre-registered for it.

## The one number that would make this session void

If **every** run in a session is flagged by `run.sh`, there is no measurement
here at all and the discard rule cannot dig it out — that is 09-06's abort
happening again, and the response is to stop and say so rather than to score
what is left. Stated in advance because the temptation at run twelve is to keep
going.
