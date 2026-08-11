M10-era images. Previous known-good; superseded by ../m11 on 2026-08-07.

gemm_top      seed=2  link_clk 64.737 MHz  slack -2.680 ns
gemm_top_wide seed=2  link_clk 58.630 MHz  slack -3.723 ns

These predate the D1 score meter, so they have no CMD_LED (0x07) and D1 keeps
its bring-up meanings only: green heartbeat, blue solid once link_clk has
ticked, red latched on a fault.

m7 and m8 behave identically on these and on ../m11 by construction -- neither
ever sends an LED command.

DO NOT RUN m9 AGAINST THESE. An unknown opcode is not ignored: cmd_known goes
low, frame_ok goes low, and the fabric drops the frame and latches bad_frame
(gemm_link.v:498,514). So every single frame would raise a fault, D1 would sit
solid red -- the one display that actively lies, because a fault rendered as
solid red reads as a confident detection -- and gh_led()'s deferred failure
would land on the *following* link call and outrank it. Use ../m11 for m9, or
build an M10-era m9 without the gh_led() call.

Kept as the fallback if an M11 image ever misbehaves, and as half the evidence
that a P&R seed does not carry across a netlist: seed 2 was the best of four
here and the worst of four on the M11 netlist. See ../README.md.

sha256:
  2df89dad17a7d7b716739a817d9555e1aeecc9dbf9e9e4ad2a191c86ba3f58a0  gemm_top.hex
  b860b4df9f1df9a7a80192a42e083a4f038142c35c74bdfa9c11a7aa53e05fa9  gemm_top_wide.hex
