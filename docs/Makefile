# Render the one figure GitHub cannot render for itself.
#
# Everything else in these docs is Mermaid, which GitHub renders from the source
# in the markdown, so it cannot drift from what is committed. A bit-level wire
# timing diagram is the one thing Mermaid has no notation for, so wire.svg is a
# build artifact that lives in git - and a committed artifact that nothing
# regenerates is a claim nobody is checking. `uv run tools/check_links.py
# --check-figures` re-renders it and fails if the result differs, which is the
# only reason this Makefile is worth having over a note saying "run wavedrom".
#
#   make -C docs
#
# Needs node; wavedrom-cli is fetched by npx and not vendored.

SVG  := img/wire.svg
JSON := diagrams/wire.json

all: $(SVG)

$(SVG): $(JSON)
	@mkdir -p $(dir $@)
	npx --yes wavedrom-cli -i $< -s $@

clean:
	rm -f $(SVG)

.PHONY: all clean
