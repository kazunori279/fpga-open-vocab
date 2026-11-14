# Home-object screen with camera-referenced synthetic images

**Provisional shortlist: folded/crumpled towel and upright/tipped mug. Neither is validated on the board.** The towel distinguishes the two object states well but often overlaps the synthetic empty-scene reference. The mug has fewer empty-scene collisions but is sensitive to lighting. This experiment narrows what to try; it does not establish a finished appliance use case.

Generated on 2026-09-26 using the built-in image generation tool. No board, camera acquisition, firmware flash, or model retraining was used.

[Image gallery](index.html) · [All numerical results](results.json) ·
[Use-case guide](../../../docs/fit.md#household-candidates-from-the-2026-09-26-synthetic-screen) ·
[Stills index](../README.md)

## What was tested

| Candidate and possible application | State A / B | Native nearest-reference, /6 | Worst image perturbation, /6 | Frozen registration under changed lighting, /8 | Native with an empty reference, /6 |
|---|---|---:|---:|---:|---:|
| Laundry tidiness | Folded / crumpled towel | 6 | 5 | 8 | 3 |
| A mug knocked onto its side | Upright / tipped mug | 6 | 5 | 6 | 6 |
| Food-preparation progress | Unpeeled / peeled banana | 5 | 3 | 4 | 4 |
| Glasses put away | Unfolded / folded eyeglasses | 4 | 4 | 6 | 3 |
| Entryway belongings | Keys / wallet on a tray | 3 | 3 | 5 | 3 |
| Visible leaf posture | Upright / drooping basil leaves | 2 | 2 | 5 | 2 |
| **Known-use positive control** | Open hand / fist | 5 | 3 | 8 | 4 |

These are counts on a tiny synthetic dataset, **not accuracy percentages for real deployment**. Each case is one generation, one physical scene, three simulated capture conditions, and two images of each state per condition. Images within a contact sheet are correlated and often nearly duplicates. The six test images are withheld from reference fitting, but are not independent generations. Perturbed versions do not increase the sample size.

- Each lighting row supplies one registration image per state and one withheld image per state: six withheld images altogether. The nearest-reference rule is fit separately in each lighting row.
- The frozen-registration column instead fits on the first A/B pair in the first row and tests all eight images in the other two rows, without adapting to the new lighting.
- Adding the empty reference measures whether the held-out **object** images are stolen by that reference. It does **not** measure empty-scene recall or unknown-object rejection. Empty-scene and bowl panels are not independent negative test sets.
- The brightness-only baseline gets 4/6 on towels, 4/6 on mugs, 3/6 on bananas, 4/6 on glasses, 3/6 on the tray and 5/6 on plants. The plant result offers no reason to prefer this model to a trivial visual cue.

**The control is a warning:** raw text ranking on generated hands is correct only 1/6 times for the shipped student (the teacher gets 6/6). Registration recovers 5/6. The user reports that hands work on the real board; synthetic scores do not reliably reproduce that behavior. This prevents interpreting this table as a calibrated ranking of real-world success. Likewise, a failure on these images is not proof that a real object can never work.

## Camera matching

The generator was given an actual empty frame, [the reference](../../cue/m9_cue-20260908-0610-f0286.png), and instructions to preserve its low viewpoint, ribbed pale wall, narrow brown desktop, darker right edge, uneven indoor light, softness and sensor-like texture. No commercial/product-photography aesthetic was requested. The complete prompts are in [generation-plan.json](generation-plan.json) and [manifest.json](manifest.json).

Each 4×4 sheet uses A/B/B/A, B/A/A/B, A/B/B/A across its first three rows. The last row provides three empty scenes and one bowl distractor. Label positions are counterbalanced. Lighting should change only between rows; subtle restaging should change between repeated states. All sheets were visually inspected before model scoring. Scissors were rejected because the generator stood them upright without support.

For evaluation, panels are separated and box-downsampled to **128×128**, packed as **RGB565**, and processed by the actual `cam_frame_to_chw()` and C encoder kernels. This is dataset/input preprocessing; it does not make the generated images genuine sensor samples.

Measured empty-frame diagnostics ([camera-profile.json](camera-profile.json)):

| Diagnostic | Three real empty frames | 21 synthetic empty panels |
|---|---:|---:|
| Mean luminance, range on 0–255 scale | 125.1–130.3 | 93.2–159.7 |
| Spatial luminance standard deviation, mean | 43.6 | 41.4 |
| Sum of mean horizontal/vertical neighbor differences | 6.57 | 4.88 |

The synthetic images remain smoother despite the prompt. Their exposure range is deliberately broader. Neighbor differences contain wall texture, blur and noise; they are not a sensor-noise estimate. These three real frames are insufficient to fit a camera simulator. No camera response curve, automatic-exposure history, read-noise distribution, focus model or temporal drift model is claimed.

Sensitivity checks apply exposure ×0.75/×1.25, red/blue gains (1.10,1.00,0.85)/(0.85,1.00,1.10), 0.6-pixel Gaussian blur, and zero-mean RGB noise with standard deviation 4/255 to the withheld input images. These are fixed stress assumptions, not parameters measured from the camera. Registrations remain unperturbed.

## What is exact, and what is a surrogate

- **Exact encoder:** committed `so400m-full-a05/export/weights.bin`, CRC32 `0xF368CC6E`, through `firmware/encoder_fast.c` with DSP shim. Before processing images, the tool requires all **2,048 shipped golden embedding floats** to match bit for bit.
- **Exact text construction:** the export's SigLIP2 teacher, its seven prompt templates, and its frozen PCA basis. Prompt pairs are fixed in the evaluation script. They were not selected to maximize these test results.
- **Surrogate decision:** normalized image cosine against the two query vectors, using their difference as a scalar, then nearest registered reference. This corresponds to the two-query centered direction when the query scales are equal. It does not reproduce the firmware's unequal room standard deviations, 30-frame background calibration, temporal smoothing, 20-frame registration windows, presence hysteresis or unknown rejection. Thus the complete board decision is **not** simulated.
- **Teacher diagnostic:** native 128px RGB888 panels are resized by the teacher's own transform. The C student sees RGB565; this is not a strict per-stage bit-exact image comparison.

The raw embeddings, exact prompts and results are retained so the surrogate can be replaced by a full firmware replay later. Do not quietly tune thresholds or wording on these sheets and report the result as new held-out evidence.

## Breadth and generation failures

The plan covered twelve home-object distinctions plus hand and red/green-cube controls. Eight sheets were generated; seven passed the pre-score visual screen. Six requested sheets failed with a service authentication error, and a retry also failed: cardboard box open/shut, whole/halved apple, intact/crushed plastic bottle, fork/spoon, rice-filled/empty jar, and the cube control. They are **untested**, not rejected by the encoder. The existing Gemini generation path was offered as an alternative; it was not silently substituted.

Useful next candidates, if generation resumes, span parcel handling, recycling preparation, utensil sorting, pantry replenishment and food preparation. Further deployment claims such as a detected spill, missing keys, watering need or food safety cannot be inferred from the visual states tested here.

## Reproduce

```sh
uv run --script tools/screen_home.py --set bench/stills/20260926-home-screen
```

Requires a C compiler and the teacher download/cache, but no student checkpoint or board. The tool compiles [encode_stills.c](../../../tools/encode_stills.c), verifies the existing golden vectors, and then runs the input frames through the firmware kernels. Package versions and artifact identity are saved in [results.json](results.json). Teacher cache identity includes the actual panel pixels, prompts, model spec and PCA hash.

Files:

- `sheets/`: original generated sheets, including rejected scissors.
- `inputs/`: native 128×128 panels used as preprocessing inputs.
- `manifest.json`: source paths, full prompts, pre-score keep/exclude decisions and limitations.
- `generation-plan.json`: all planned candidates, including those that could not be generated.
- `student_embeddings.npy`, `records.json`: C embeddings and their image/profile indexing.
- `text_vectors.npz`, `text_vectors.json`: teacher image/text vectors and cache identity.
- `results.json`: all metrics, not just the strongest candidates.
- `camera-profile.json`: real/synthetic empty-frame diagnostics.
