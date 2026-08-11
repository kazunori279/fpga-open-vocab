# Timing constraints for M6 gemm_top.
#
# 13.333 ns = 75 MHz, which is the rate M2 actually clocked the board at.
#
# M2's link.sdc asked for 40 ns and the board ran at 75 MHz anyway, on 37.263 ns
# of slack out of a 365 MHz Fmax. That was a shift register. This is an 8-lane
# int8 MAC array with a 256-bit-wide accumulator RAM and a two-stage pipeline in
# front of it, so the constraint has to state the real operating point or the
# report is decoration. If Efinity does not close at 13.333 ns, the honest
# response is to lower the PIO clock in firmware and record the slower frame
# time - not to relax this number.
#
# clk_32m is the Y2 oscillator, fixed at 32 MHz, and carries the heartbeat and
# the LEDs only.
#
# The two domains meet at exactly one place: gemm_top's seen_s/err_s
# synchronisers, which sample dbg_seen and dbg_err from the link domain. Both
# are single bits that change at most once per configuration, so the two flops
# are the whole crossing and there is nothing for the analyser to close. Cutting
# the path is what makes that explicit - left in, it is a false failure that
# trains you to ignore the report.

create_clock -period 13.333 -name link_clk [get_ports {link_clk}]
create_clock -period 31.250 -name clk_32m  [get_ports {clk_32m}]

set_false_path -from [get_clocks {link_clk}] -to [get_clocks {clk_32m}]
set_false_path -from [get_clocks {clk_32m}]  -to [get_clocks {link_clk}]
