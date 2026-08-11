# Timing constraints for M10 Stage 0, tile_probe.
#
# 6.0 ns = 166.7 MHz, and it is meant to fail.
#
# The number this build exists to produce is `Maximum possible analyzed clocks
# frequency`, which the analyser reports whether the constraint is met or not.
# What the constraint controls is how hard place-and-route tries. Ask for 13.333
# ns, as the shipped designs do, and the placer stops optimising the moment it
# has enough slack - so a tile that could reach 150 MHz reports 80 and the gate
# reads as a fail. Ask for something out of reach and every path stays on the
# critical list until routing runs out of tricks.
#
# 6.0 ns rather than 1 ns for the same reason in the other direction: an
# unreachable constraint makes the whole netlist critical and the placer spends
# its budget spreading effort evenly instead of on the paths that matter. 6.0 ns
# is roughly 2x the best plausible answer, which is the usual place to sit.
#
# NOT the constraint M10 would ship. gemm_top_tclk.sdc will name whatever
# frequency this build turns out to support, with margin, and it will have three
# clocks and an asynchronous clock group rather than one clock and nothing.
#
# There is no I/O constraint here on purpose. seed_in feeds the LFSR feedback
# and probe_out comes off a flop; both are timing sinks for a design that is
# never loaded onto hardware, and constraining them would add paths to the
# report that have nothing to do with the question.

create_clock -period 6.000 -name tile_clk [get_ports {tile_clk}]
