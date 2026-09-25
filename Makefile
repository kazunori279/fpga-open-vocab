# Hardware-independent checks. Run `uv run make test-host` after `uv sync`.
PYTHON ?= python3
CC ?= cc
TEST_BUILD := rtl/build/host-tests
EXPORT := model/runs/so400m-full-a05/export

.PHONY: test test-host test-rtl

test: test-host test-rtl

test-host:
	$(PYTHON) -m unittest discover -s tests -p test_host.py -v
	$(PYTHON) tools/check_links.py
	$(MAKE) -C rtl test_wire test_plan
	mkdir -p $(TEST_BUILD)
	$(CC) -O2 -Wall -Wextra -o $(TEST_BUILD)/encoder firmware/test_encoder.c firmware/encoder.c -lm
	$(TEST_BUILD)/encoder $(EXPORT)
	$(CC) -O2 -Wall -Wextra -o $(TEST_BUILD)/fast firmware/test_encoder_fast.c firmware/encoder.c firmware/encoder_fast.c -lm
	$(TEST_BUILD)/fast $(EXPORT)
	$(CC) -O2 -Wall -Wextra -DFGX_DSP_SHIM -o $(TEST_BUILD)/dsp firmware/test_encoder_fast.c firmware/encoder.c firmware/encoder_fast.c -lm
	$(TEST_BUILD)/dsp $(EXPORT)

test-rtl:
	$(MAKE) -C rtl vec
	$(PYTHON) tests/test_rtl.py
	$(MAKE) -C rtl sim
	$(MAKE) -C rtl tb_gemm tb_gemm_link tb_gemm_link_wide KPACK=0
	$(MAKE) -C rtl tb_gemm tb_gemm_link tb_gemm_link_wide KPACK=1
