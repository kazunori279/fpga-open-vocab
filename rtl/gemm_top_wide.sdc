# Timing constraints for M7f gemm_top_wide, configuration C.
#
# Identical to gemm_top.sdc, and it has to be: the point of the jumper is to
# move three times the forward data at the same clock, not to move it faster.
# 13.333 ns = 75 MHz is the rate M2 clocked configuration C at, over all ten
# operating points with zero errors.
#
# What changed is where link_clk enters. gemm_top.sdc constrains it on F3,
# general fabric; here it is on B3, a global-clock ball. That should make the
# clock network cheaper, not the logic - the receive front end grew a three-way
# hunt and a walking byte boundary - so the two numbers to read in the report
# were whether the global clock bought back more skew than the regrouper cost,
# and whether the critical path is still in the MAC array.
#
# Both have now been answered, and both answers are no.
#
#   - The global clock did not pay for the regrouper. gemm_top_wide closes at
#     58.630 MHz with -3.723 ns of slack; gemm_top, on the F3 general-fabric
#     clock, closes at 62.449 MHz with -2.680 ns. B3 is a global ball but the
#     buffer is not free: 0.420 ns of pad, 2.640 ns of net to reach it and
#     3.318 ns through CLKBUF gives a 6.378 ns launch path, and that is paid on
#     the capture side too, so it cancels rather than helps.
#   - The critical path is not in the MAC array, and in gemm_top it never was.
#     Here it is u_link/rx_bc[1] -> u_link/frame_ok, five levels; in gemm_top it
#     is u_link/state[2] -> u_link/tx_en, four. Both are the link state machine.
#     link_wide alone closed at 253.421 MHz on this same B3 clock, so it is not
#     the widening either - it is gemm_link's framing logic in both builds, and
#     that is where a timing-closure project would have to start.
#
# The 13.333 ns stays anyway, because the board does. Both configurations run
# bit-exact at 75 MHz through six rungs of the rate ladder with zero CRC errors
# - M7g moved 16.791 MB through this one in 643 ms of wire, 36.5 ns/B - so the
# analyser's -3.723 ns is pessimism against this silicon at this temperature,
# not a rate the hardware fails at. Relaxing the constraint to match the report
# would only stop the report from saying anything.
#
# If a future revision genuinely does not close, the honest response is still to
# lower the PIO clock in firmware and record the slower frame time - not to
# relax this number.
#
# clk_32m is the Y2 oscillator, fixed at 32 MHz, and carries the LEDs only. It
# no longer carries a heartbeat output: A4 is return data in this configuration.
#
# The two domains meet at exactly one place: gemm_top_wide's seen_s/err_s
# synchronisers, which sample dbg_seen and dbg_err from the link domain. Both
# are single bits that change at most once per configuration, so the two flops
# are the whole crossing and there is nothing for the analyser to close. Cutting
# the path is what makes that explicit - left in, it is a false failure that
# trains you to ignore the report.

create_clock -period 13.333 -name link_clk [get_ports {link_clk}]
create_clock -period 31.250 -name clk_32m  [get_ports {clk_32m}]

set_false_path -from [get_clocks {link_clk}] -to [get_clocks {clk_32m}]
set_false_path -from [get_clocks {clk_32m}]  -to [get_clocks {link_clk}]
