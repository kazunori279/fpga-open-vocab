M11 (D1 score meter). Current shipped images.

gemm_top      seed=4  link_clk 63.922 MHz  slack -2.311 ns  (M10: 64.737 / -2.680)
gemm_top_wide seed=2  link_clk 58.555 MHz  slack -3.745 ns  (M10: 58.630 / -3.723)

Critical path in both is pre-existing and does not touch the LED logic:
  gemm_top      u_tile/state[0] -> u_link/tph[0]|CE

VALIDATED ON HARDWARE 2026-08-07.
  m7 ladder, both bitstreams in one boot: RESULT : PASS
    all 8 layers bit-exact in all six modes of both link configurations,
    512/512 floats exact.  config A 1,074 ms;  config C 845 ms at 75.0 MHz
  m8, 50 frames: cos 0.995-0.999, no faults, D1 in legacy mode throughout
  m9, 90 frames: meter glides green->red and back; fault display confirmed

Note both run well past their reported fmax and are bit-exact anyway, which is
how this design has behaved since M6c. The slack numbers above are for tracking
drift between respins, not a prediction of whether the board works.

Note the seed. gemm_top's was rolled from 2 to 4 because seed 2 -- which had
been the shipped choice through M10 -- came out WORST of four on this netlist at
59.934 MHz, against 61.904 for seed 3, 63.243 for seed 1 and 63.922 for seed 4.
That is the whole reason ../m10 is kept: a P&R seed does not carry across a
netlist, so a future respin cannot assume these settings reproduce. The wide
build was left on seed 2 deliberately, as the control -- 58.555 against M10's
58.630 is the evidence that M11's RTL did not cost anything.

sha256:
  6bb77a8126cd212748f96bad10502eb47b408e27a90f49aa63cb177a60327608  gemm_top.hex
  1ce62071cd60c47a3e650e71d37a64287f10a64081b76627fd067270576e6eb5  gemm_top_wide.hex
