# Simulation only. Bitstream builds need Efinity, which is not installable
# unattended - see rtl/README.md.

IVFLAGS := -g2005 -Wall
SRC     := link_core.v link_narrow.v link_wide.v
GEMM    := gemm_tile.v im2col_feed.v
TOPS    := gemm_top.v gemm_link.v $(GEMM)
WTOPS   := gemm_top_wide.v gemm_link.v $(GEMM)
BUILD   := build
VEC     := $(BUILD)/vec
# Must track firmware/CMakeLists.txt's FGX_EXPORT. $(VEC)/cases.hex depends on
# $(MODEL)/weights.bin, so the two pointing at different runs is not a mismatch
# that shows up as an error - it is a testbench that passes against a model the
# board is not running.
MODEL   ?= ../model/runs/so400m-full-a05/export

# M16. `make tb_gemm KPACK=1` pairs two kernel taps per lane. Both values have to
# stay green against the *same* goldens, so this is a knob and not a new target:
# 0 is the regression that the parameter folds away, 1 is the claim. The PASS
# line prints the busy-cycle total, which is where the 1.5x is visible - a run
# that paired nothing would still be bit-exact.
KPACK   ?= 0

.PHONY: sim tb_gemm tb_gemm_link tb_gemm_link_wide vec test_wire test_plan clean
sim: $(BUILD)/tb_link
	@$(BUILD)/tb_link

$(BUILD)/tb_link: tb_link.v $(SRC)
	@mkdir -p $(BUILD)
	iverilog $(IVFLAGS) -o $@ tb_link.v $(SRC)

# M6a. The golden accumulators are fgx_conv_acc() from the firmware, so this
# target builds a host binary against the real encoder.c and runs it over the
# real weights.bin before simulating anything.
tb_gemm: $(BUILD)/tb_gemm_k$(KPACK)
	@$(BUILD)/tb_gemm_k$(KPACK)

# The vectors are a compile-time dependency, not just a runtime one: the
# testbench includes vecsizes.vh to size its memories exactly.
#
# KPACK is in the binary's name because it is not in any dependency: flipping it
# alone leaves every prerequisite older than the target, and one shared name
# would re-run the previous build's binary while printing the new number.
$(BUILD)/tb_gemm_k$(KPACK): tb_gemm.v $(GEMM) $(VEC)/cases.hex
	@mkdir -p $(BUILD)
	iverilog $(IVFLAGS) -DVECDIR=\"$(VEC)\" -I$(VEC) -Ptb.KPACK=$(KPACK) \
	   -o $@ tb_gemm.v $(GEMM)

# The same vectors, but reaching gemm_top through link_mosi/link_miso only -
# one bit per clock, framing and CRCs included. Slower than tb_gemm by design.
tb_gemm_link: $(BUILD)/tb_gemm_link_k$(KPACK)
	@$(BUILD)/tb_gemm_link_k$(KPACK)

$(BUILD)/tb_gemm_link_k$(KPACK): tb_gemm_link.v $(TOPS) $(VEC)/cases.hex
	@mkdir -p $(BUILD)
	iverilog $(IVFLAGS) -DVECDIR=\"$(VEC)\" -I$(VEC) -Ptb.KPACK=$(KPACK) \
	   -o $@ tb_gemm_link.v $(TOPS)

# M7f. Configuration C: the same testbench, the same vectors, three forward
# lanes into gemm_top_wide. Both targets have to stay green - the point of the
# shared source is that the wide path cannot drift away from the measured one.
tb_gemm_link_wide: $(BUILD)/tb_gemm_link_wide_k$(KPACK)
	@$(BUILD)/tb_gemm_link_wide_k$(KPACK)

$(BUILD)/tb_gemm_link_wide_k$(KPACK): tb_gemm_link.v $(WTOPS) $(VEC)/cases.hex
	@mkdir -p $(BUILD)
	iverilog $(IVFLAGS) -DLINK_WIDE -DVECDIR=\"$(VEC)\" -I$(VEC) \
	   -Ptb.KPACK=$(KPACK) -o $@ tb_gemm_link.v $(WTOPS)

vec: $(VEC)/cases.hex

# gemm_block.c is the layout the *firmware* uses. Linking it here rather than
# letting gen_gemm_vec.c carry its own copy is what makes a tb_gemm PASS
# evidence about the code that ships to the MCU.
# gemm_wire.c joined at M15: gb_rqp() builds the RQP payload through
# gw_rqp_pack(), so the entry layout the vectors carry is the wire's own and not
# a transcription of it.
$(VEC)/cases.hex: ../firmware/gen_gemm_vec.c ../firmware/gemm_block.c \
                  ../firmware/gemm_block.h ../firmware/encoder.c \
                  ../firmware/encoder.h ../firmware/gemm_wire.c \
                  ../firmware/gemm_wire.h $(MODEL)/weights.bin
	@mkdir -p $(VEC)
	cc -O2 -Wall -Wextra -o $(BUILD)/gen_gemm_vec \
	   ../firmware/gen_gemm_vec.c ../firmware/gemm_block.c \
	   ../firmware/gemm_wire.c ../firmware/encoder.c -lm
	@$(BUILD)/gen_gemm_vec $(MODEL) $(VEC)

# M7a. Not RTL, but it belongs beside the vectors for the same reason they do:
# it is the part of the firmware that can be proved right without a strap.
test_wire: $(BUILD)/test_gemm_wire
	@$(BUILD)/test_gemm_wire

$(BUILD)/test_gemm_wire: ../firmware/test_gemm_wire.c ../firmware/gemm_wire.c \
                         ../firmware/gemm_wire.h ../firmware/gemm_host.h
	@mkdir -p $(BUILD)
	cc -O2 -Wall -Wextra -o $@ \
	   ../firmware/test_gemm_wire.c ../firmware/gemm_wire.c

# M7c. The blocking table for every layer, chosen from the real weights.bin and
# checked for exact tiling of each output tensor - the one property that a
# bit-exact result per block does not establish. Same argument as test_wire: a
# strap is expensive, so this is settled on the laptop.
test_plan: $(BUILD)/test_gemm_plan
	@$(BUILD)/test_gemm_plan $(MODEL)

$(BUILD)/test_gemm_plan: ../firmware/test_gemm_plan.c ../firmware/gemm_plan.c \
                         ../firmware/gemm_plan.h ../firmware/gemm_block.c \
                         ../firmware/gemm_block.h ../firmware/gemm_wire.c \
                         ../firmware/encoder.c ../firmware/encoder.h
	@mkdir -p $(BUILD)
	cc -O2 -Wall -Wextra -o $@ \
	   ../firmware/test_gemm_plan.c ../firmware/gemm_plan.c \
	   ../firmware/gemm_block.c ../firmware/gemm_wire.c \
	   ../firmware/encoder.c -lm

clean:
	rm -rf $(BUILD)
