# Efinity synthesis container.
#
# Efinix ships Linux/x86_64 and Windows builds only, so on Apple Silicon this
# runs under Rosetta amd64 emulation. Synthesis of a design this small is a few
# seconds of real work, so the emulation penalty does not matter.
#
# The installer tarball is NOT redistributable and is not baked into the image
# by this Dockerfile from the network - it has to be downloaded from an Efinix
# account and passed in as build context. See build.sh.
FROM --platform=linux/amd64 ubuntu:22.04

ARG EFINITY_TARBALL
ARG EFINITY_VERSION

ENV DEBIAN_FRONTEND=noninteractive

# Efinity bundles its own Python, libstdc++, libgcc, libgomp, tcl and crypto,
# so this list is only what its binaries still pull from the system.
#
# The X and Qt packages are needed even though no window is ever opened:
#   - efx_pnr and efx_sta link libX11 unconditionally
#   - the Interface Designer is a Qt application, and the headless API in
#     pt/bin/api_service imports PyQt6 on the way in, which drags in libGL
# QT_QPA_PLATFORM=offscreen below keeps Qt from looking for a display.
RUN apt-get update && apt-get install -y --no-install-recommends \
        python3 \
        libglib2.0-0 libxml2 \
        libx11-6 libgl1 libegl1 libxkbcommon0 libxkbcommon-x11-0 libdbus-1-3 \
        libxcb1 libxcb-cursor0 libxcb-icccm4 libxcb-image0 libxcb-keysyms1 \
        libxcb-randr0 libxcb-render-util0 libxcb-shape0 libxcb-sync1 \
        libxcb-xfixes0 libxcb-xkb1 \
        libfontconfig1 libfreetype6 libxrender1 libsm6 libice6 \
        bzip2 ca-certificates make \
    && rm -rf /var/lib/apt/lists/*

# The tarball's top level is already efinity/<version>/, so extract into /opt.
COPY ${EFINITY_TARBALL} /tmp/efinity.tar.bz2
RUN tar -xjf /tmp/efinity.tar.bz2 -C /opt \
    && rm /tmp/efinity.tar.bz2

# bin/setup.sh sets all of this, but it is written for an interactive shell and
# guesses EFINITY_HOME from its own location. Setting the variables here means
# any `docker run ... bash -lc` gets a working environment.
#
# Two that setup.sh gets wrong or omits for headless use:
#   - PYTHONPATH must include pt/bin, or `import api_service` fails and the
#     interface step is silently skipped with a one-line warning.
#   - EFINITY_USER_DIR_INI must point somewhere writable, or the flow scripts
#     die with KeyError: 'EFINITY_USER_DIR_INI'.
ENV EFINITY_HOME=/opt/efinity/${EFINITY_VERSION}
ENV EFXPT_HOME=${EFINITY_HOME}/pt
ENV EFXPGM_HOME=${EFINITY_HOME}/pgm
ENV EFXDBG_HOME=${EFINITY_HOME}/debugger
ENV EFXIPM_HOME=${EFINITY_HOME}/ipm
ENV PATH=${EFINITY_HOME}/bin:${EFINITY_HOME}/scripts:${PATH}
ENV PYTHONHOME=${EFINITY_HOME}
ENV PYTHONPATH=${EFINITY_HOME}/pt/bin:${EFINITY_HOME}/lib
ENV PYTHONNOUSERSITE=1
ENV EFINITY_USER_DIR_INI=/tmp/efinity_user_dir.ini
ENV QT_QPA_PLATFORM=offscreen
ENV QT_PLUGIN_PATH=${EFINITY_HOME}/lib/plugins
ENV QT_LOGGING_CONF=${EFINITY_HOME}/bin/lc.ini

WORKDIR /work
