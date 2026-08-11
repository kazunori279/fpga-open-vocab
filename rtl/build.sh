#!/usr/bin/env bash
# Synthesize an M2 link bitstream with Efinity, orchestrated from macOS.
#
#   ./build.sh narrow          # M2 link, configuration A, no board modification
#   ./build.sh wide            # M2 link, configuration C, needs the jumper
#   ./build.sh gemm_top        # M6 GEMM, configuration A
#   ./build.sh gemm_top_wide   # M7f GEMM, configuration C
#   ./build.sh probe_a         # any other <top>.v + <top>_io.isf pair
#
# Efinity has no macOS build, so synthesis runs in a Linux/amd64 container under
# Rosetta. Only the bitstream and the reports come back.
#
# IMPORTANT: the build happens in /tmp, NOT in this project dir. Docker Desktop's
# default file sharing does not include ~/Documents (iCloud-synced), and
# bind-mounting a path it cannot share leaves the container stuck in "Created"
# forever. It is a Docker Desktop constraint, not an Efinity one.
set -euo pipefail

cd "$(dirname "$0")"
PROJ="$PWD"
CFG="${1:-narrow}"
# narrow/wide name the two link configurations; anything else is taken as a top
# module name directly, which is how the M2 config-failure probes are built.
case "$CFG" in
  narrow|wide) TOP="link_${CFG}" ;;
  *)           TOP="$CFG" ;;
esac
for f in "$TOP.v" "${TOP}_io.isf"; do
  [ -f "$PWD/$f" ] || { echo "$0: $f not found" >&2; exit 2; }
done

DEVICE=T8F49
TIMING=C2
WORK="/tmp/forgix-efinity/${TOP}"

# The installer is account-gated and not redistributable, so it lives outside the
# repo and the image is built once from it.
#
# EFINITY_TARBALL is checked inside the build branch, not here. The tarball is a
# 1 GB download that gets deleted once the image exists, and demanding it on
# every run makes an existing image unusable months later - which is exactly
# what happened between M2 and M6.
: "${EFINITY_VERSION:=2026.1}"
IMAGE="efinity-local:${EFINITY_VERSION}"

# `docker image inspect` is the obvious check and the wrong one: this image is
# linux/amd64 only, and on an arm64 host Docker Desktop's image store reports
# "No such image" for it while `docker run --platform linux/amd64` works fine
# and `docker image ls` lists it. Ask the question that matches how the image is
# used, not the one that reads better.
if [ -z "$(docker image ls -q "$IMAGE" 2>/dev/null)" ]; then
  : "${EFINITY_TARBALL:?image $IMAGE not found; set EFINITY_TARBALL to the downloaded efinity-*-linux-x64.tar.bz2 to build it}"
  echo "==> building $IMAGE (slow: unpacking ~1 GB of Efinity under emulation)"
  CTX=/tmp/forgix-efinity/ctx
  rm -rf "$CTX" && mkdir -p "$CTX"
  cp "$EFINITY_TARBALL" "$CTX/efinity.tar.bz2"
  cp "$PROJ/docker/Dockerfile" "$CTX/"
  docker build --platform linux/amd64 -t "$IMAGE" \
    --build-arg EFINITY_TARBALL=efinity.tar.bz2 \
    --build-arg EFINITY_VERSION="$EFINITY_VERSION" "$CTX"
  rm -rf "$CTX"
fi

echo "==> staging $TOP in $WORK"
rm -rf "$WORK" && mkdir -p "$WORK"
# The probes are self-contained, and handing Efinity a link_core.v they do not
# instantiate invites it to pick the wrong top module - it already warns that no
# top was specified and guesses.
SOURCES=("$TOP.v")
case "$TOP" in
  link_*)                    SOURCES=(link_core.v "$TOP.v") ;;
  gemm_top|gemm_top_wide)    SOURCES=(gemm_link.v gemm_tile.v im2col_feed.v "$TOP.v") ;;
  # M10 Stage 0. The tile without the link, to find out what it closes at on its
  # own clock - which is the whole of M10's value, since RUN's 314 ms is compute
  # at link_clk and not transport. gemm_link.v is deliberately absent: handing
  # Efinity a module the top does not instantiate invites it to guess the wrong
  # top, and its framing logic is the critical path in both shipped builds, so
  # leaving it in would answer a different question.
  tile_probe)                SOURCES=(gemm_tile.v im2col_feed.v "$TOP.v") ;;
esac

cp "$PROJ/${TOP}_io.isf" "$PROJ/mk_peri.py" "$WORK/"
for s in "${SOURCES[@]}"; do cp "$PROJ/$s" "$WORK/"; done
# efx_run.py auto-detects constraints by filename, so the constraints have to
# arrive under the design's name or they are silently ignored - and "ignored"
# looks like success, with every clock defaulting to a 1 ns period. A design
# with its own <top>.sdc uses that; the link configurations share link.sdc.
cp "$PROJ/${TOP}.sdc" "$WORK/${TOP}.sdc" 2>/dev/null || cp "$PROJ/link.sdc" "$WORK/${TOP}.sdc"

# Two container invocations rather than one, because the periphery design only
# has to be rebuilt when the .isf changes, and keeping it separate makes it
# obvious in the log which half failed.
echo "==> interface: ${TOP}_io.isf -> ${TOP}.peri.xml"
docker run --rm --platform linux/amd64 -v "$WORK":/work -w /work "$IMAGE" \
  python3 mk_peri.py "$TOP" "$DEVICE"

# mode=passive matches how the board is strapped. On Trion T8 it happens to
# produce a byte-identical bitstream to mode=active - verified by diffing the
# two - so this is a statement of intent rather than a functional switch.
#
# generate_header=off keeps the .hex to pure payload. Read the history before
# changing it back: the 256-byte ASCII banner Efinity prepends is not inert
# padding, it is the lead-in the T8 has to be clocked through before it starts
# matching the synchronization pattern (AN 006 Figure 15 draws it on CDI0 as
# "Header, D, D, D, ..."). Suppressing it here while fpga_config.c waited 100 us
# without clocking is why no bitstream from this script would configure. The
# firmware now supplies the lead-in itself, which is the right place for it -
# configuration should not depend on a bitstream-generation flag - so the header
# stays off and the images stay reproducible.
# Place-and-route effort, empty by default. Every critical path in gemm_top is
# 100% routing delay and 0% logic delay - the design uses 33% of the LEs but
# 87.5% of the memory blocks, so the placer spreads the tile across the die and
# scatters the link control logic through what is left.
#
# The obvious lever does not work, and neither does the less obvious one. All
# six named levels were measured against the default on an identical netlist:
#
#   default        64.973 MHz   (seed 2)
#   CONGESTION_1   61.440
#   CONGESTION_2   61.143
#   CONGESTION_3   60.096
#   TIMING_3       49.579       (seed default; default was 53.975 on that netlist)
#
# Every one is worse. The levels are not a monotonic effort dial - they select
# different cost functions, and on a design that uses 87.5% of the memory blocks
# and 33% of the LEs, both the timing-weighted and the congestion-weighted
# functions pack worse than the balanced default. Levels are TIMING_1..3 and
# CONGESTION_1..3 (efx_run_pnr_sweep.py:658).
#
# `PNR_OPTS="seed=N"` re-rolls the initial placement and is the only knob that
# helps: four seeds on one netlist spread 60.7 to 65.4 MHz, so a single build's
# delta under ~3 MHz is noise and not evidence.
#
# AND A SEED IS NOT PORTABLE ACROSS NETLISTS, which M11 learned the expensive
# way. Adding the D1 meter re-rolled gemm_top like this:
#
#   seed 2   59.934 MHz      <- the shipped choice up to M10, now the worst roll
#   seed 3   61.904
#   seed 1   63.243
#   seed 4   63.922          <- shipped from M11
#
# Read against the 64.737 MHz that seed 2 gave on the M10 netlist, seed 2 alone
# looks like a 4.8 MHz regression and a reason to back the feature out. The
# band says otherwise: the whole spread moved down by about 1 MHz and seed 2
# went from the top of it to the bottom. gemm_top_wide is the control - the same
# RTL delta moved it 58.630 -> 58.555 MHz, which is nothing.
#
# So: re-roll three or four seeds before believing any single number, and pin
# the seed per top rather than globally. gemm_top ships seed 4, gemm_top_wide
# ships seed 2.
: "${PNR_OPTS:=}"

# Top-module parameter overrides, comma-separated, e.g.
#
#   TOP_PARAMS="DPIPE=1,APACK=2,WNIB=1,ADEPTH=128" ./build.sh tile_probe
#
# M14 uses this to build the int4 variants of gemm_tile from an unmodified
# source tree. That matters more than convenience: a seed is not portable across
# netlists (see above), so a variant and its control have to differ by the
# command line and nothing else, or the comparison is measuring the edit.
#
# **Not** via efx_map's --top-params, which is the option that exists for this
# and cannot be reached. efx_run.py takes --map_opts with argparse nargs='+',
# efx_run_map.py takes --opt the same way, and argparse treats any value
# beginning with "-" as the next option rather than as an argument. Neither the
# "--map_opts=--top-params=..." form nor a leading space survives both layers -
# the space makes it through argparse and arrives at efx_map as the single token
# "-- --top-params=...", which boost::program_options rejects.
#
# So the override is applied to the *staged* copy under /tmp, which is a
# rewrite of the parameter's default and is exactly what --top-params would
# have done. The repo tree is never modified, and the diff is echoed below so
# the build log says precisely what was built.
: "${TOP_PARAMS:=}"

# Where the reports land. Successive variants of one top would otherwise
# overwrite each other in rtl/build/, and the whole point of a sweep is to have
# all of them side by side afterwards.
: "${TAG:=}"

if [ -n "$TOP_PARAMS" ]; then
  echo "==> top params on the staged $TOP.v"
  IFS=',' read -ra _kv <<< "$TOP_PARAMS"
  for kv in "${_kv[@]}"; do
    k="${kv%%=*}"; v="${kv#*=}"
    grep -qE "^ *parameter integer +$k +=" "$WORK/$TOP.v" \
      || { echo "$0: no 'parameter integer $k' in $TOP.v" >&2; exit 2; }
    # Written back through `cat >`, which truncates the existing inode, and not
    # with `sed -i`, which renames a new one into place. The bind mount is
    # virtiofs and it caches by inode: the first run of this edit produced
    # "[EFX-0010 VERI-ERROR] cannot open Verilog file '/work/tile_probe.v'" for
    # a file that was plainly there and that a second, identical container run
    # read without complaint. Keeping the inode keeps the mount coherent.
    sed -E "s/^( *parameter integer +$k +=[[:space:]]*)[0-9]+/\1$v/" \
      "$WORK/$TOP.v" > "$WORK/.$TOP.v.tmp"
    cat "$WORK/.$TOP.v.tmp" > "$WORK/$TOP.v"
    rm -f "$WORK/.$TOP.v.tmp"
    grep -E "^ *parameter integer +$k +=" "$WORK/$TOP.v" | sed 's/^/      /'
  done
fi

echo "==> compile: map -> interface -> pnr -> bitstream  (pnr: ${PNR_OPTS:-default}, params: ${TOP_PARAMS:-default})"
docker run --rm --platform linux/amd64 -v "$WORK":/work -w /work "$IMAGE" \
  efx_run.py "$TOP" --family Trion -d "$DEVICE" --timing_model "$TIMING" \
    -f compile --work_dir . -v "${SOURCES[@]}" \
    ${PNR_OPTS:+--pnr_opts $PNR_OPTS} \
    --pgm_opts mode=passive width=1 generate_header=off timestamp=off

mkdir -p "$PROJ/build"
cp "$WORK/outflow/${TOP}.hex" "$PROJ/build/${TOP}${TAG}.hex"
# place.rpt is the only file that carries LE / register / memory-block /
# multiplier counts. M2 never needed them - 38 LEs out of 7,384 - but M6 lives
# or dies on whether 2,048 int32 accumulators fit in 24 memory blocks, so the
# report that answers that has to come out of the container with the rest.
for r in timing pinout res place; do
  for f in "$WORK/outflow/${TOP}."*"${r}"*; do
    [ -e "$f" ] || continue
    cp "$f" "$PROJ/build/${TOP}${TAG}.${f#"$WORK/outflow/${TOP}."}"
  done
done

echo "==> done: rtl/build/${TOP}${TAG}.hex"
ls -l "$PROJ/build/${TOP}${TAG}.hex"
echo
sed -n '/Maximum possible analyzed clocks frequency/,/^$/p' "$PROJ/build/${TOP}${TAG}.timing.rpt"
echo "Now re-run cmake in firmware/ so hex2c.py embeds it:"
echo "  cmake -S firmware -B firmware/build -G Ninja -DLINK_CFG=$(echo "$CFG" | tr a-z A-Z) \\"
echo "    -DPICO_TOOLCHAIN_PATH=\$HOME/toolchains/arm-gnu-toolchain-14.2.rel1-darwin-arm64-arm-none-eabi"
echo "  ninja -C firmware/build"
