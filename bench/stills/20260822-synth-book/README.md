# Superseded: whole COCO photographs, book open against closed

The first attempt, kept as the evidence for why
[`../20260822-synth-book-crop/`](../20260822-synth-book-crop/) crops the source
around a COCO instance box before editing it.

A COCO photograph is a *room*. The generator obeyed — a blind judge named the
open side correctly on 11 of 12 pairs and called the scene unchanged on 12 of
12 ([`judge-a.json`](judge-a.json)) — but the book is a few dozen pixels in a
128×128 frame, so `object_both` held on only 6. The appliance's object fills
its frame; this set does not, and it read 1.4 sd at the teacher for that reason
alone.

Fixing it by *asking* for a close-up is what
[`../20260822-synth-book-closeup/`](../20260822-synth-book-closeup/) did, and
that traded one fault for a worse one. Cropping first fixes it with no licence
granted to the generator.
