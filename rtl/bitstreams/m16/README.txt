M16 (int4 unpack at the write port, requantize in the tile, kernel taps paired
on kx). Built 2026-08-09 at TOP_PARAMS="RQ=1,KPACK=1". Current shipped images.

gemm_top      seed=default  link_clk 56.654 MHz  slack -4.318 ns
gemm_top_wide seed=default  link_clk 52.874 MHz  slack -5.580 ns

                        gemm_top          gemm_top_wide
  Logic Elements        6265 / 7384       6232 / 7384      (84.9% / 84.4%)
  LE: LUTs/Adders       5018              4995
  LE: Registers         3265              3265
  Memory Blocks         21 / 24           21 / 24
  Multipliers           8 / 8             8 / 8

These carry the whole arithmetic stack the M11 images predate: M14's int4
weights, M15's in-tile requantize, M16's paired taps. Firmware must be built at
GP_KPACK=1 to drive them -- see gemm_plan.h for which of the two mismatches
hangs the board and which merely returns nonsense.

VALIDATED ON HARDWARE. These two images are what every board result after M15
was taken on; the harness defaults in host/m6.py, m7.py, m8.py and demo.py all
pointed at them (as rtl/build/*_m16.hex) before they were promoted here.
  M16          config C 569 ms at 150 MHz sys / 75 MHz link, bit-exact
  after M17    m6 2048/2048 at 280/140, three boots; m7 three clean runs at
               304 / 304 / 303 ms, config A pinned at 718 in every one
  M18          m6 31 rows bit-exact 2048/2048; m7 all 8 layers exact in all six
               modes of both link configurations at 303 ms/frame; m9 hashing
               its 780,720 B export to 0xF368CC6E
The measurements and their conditions are in docs/milestones.md -- the M16
section, "After M17 -- the audit, and a ladder that is not monotonic", and M18.

THE SHIPPED IMAGES ARE THE DEFAULT-SEED ROLLS, AND THEY ARE NOT THE BEST OF THE
FOUR. Four seeds were rolled per top:

  gemm_top       default 56.654   s2 55.316   s7 56.996   s13 55.586
  gemm_top_wide  default 52.874   s2 54.927   s7 53.056   s13 51.840

so s7 beats gemm_top's shipped roll by 0.34 MHz and s2 beats the wide's by
2.05 -- and neither was ever flashed. This directory ships what the board has
run, not what the analyser preferred, which is the same rule the m10 and m11
directories were kept under. Issue #4 is where the unflashed margin is tracked.

The wide top's band is the one uncomfortable number in M16: its worst seed is
51.840 against M15's 53.6, and unlike gemm_top's the band no longer overlaps
M15's at the top. At 84% occupancy that is small enough to be seed noise, and
the shipped image is bit-exact at a 140 MHz link regardless -- which is the
usual gap between this analyser and this board, discussed in the clock rows of
docs/history.md.

sha256:
  1cda7fd67949328859075b37c53516c3f740debeb2c12f9f168f1388236dc4a7  gemm_top.hex
  cfdd0c3d02b74366b7d81b866125cfebd3f84a70a80761dacabdcd692f727ef1  gemm_top_wide.hex
