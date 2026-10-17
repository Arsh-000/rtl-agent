#!/usr/bin/env bash
# Installs the open-source EDA tools rtl-agent needs into /opt/eda (change with PREFIX=...).
#
#   Yosys    - synthesis         built from source at a fixed version (YOSYS_TAG): Ubuntu's package
#                                is often too old and gives different timing numbers
#   OpenSTA  - timing analysis   built from source at a fixed commit: there is no common package
#   CUDD     - a library OpenSTA needs
#   Icarus   - simulation        Ubuntu package (built from source if the package is missing)
#
# Written for Ubuntu 24.04 (also WSL2 Ubuntu). Ubuntu 22.04 works too: a newer cmake is fetched
# automatically. It is also what the Docker image runs (see Dockerfile).
#
# Usage:   sudo bash scripts/install_eda_tools.sh
# Then:    export PATH=/opt/eda/bin:$PATH        (add it to ~/.bashrc)
#
# Safe to run again: every step checks first and skips what is already installed at the right
# version. Takes 20-40 minutes the first time (Yosys is the slow part).
#
# Options (environment variables):
#   PREFIX=/opt/eda   where to install          JOBS=4   parallel compile jobs (lower = less memory)
#   YOSYS_TAG=v0.69   Yosys version to build    FORCE_YOSYS=1  rebuild Yosys even if a good one exists
#   SKIP_APT=1        do not install packages   DRY_RUN=1      only print what would be done
set -euo pipefail

PREFIX=${PREFIX:-/opt/eda}
SRC=${SRC:-$HOME/eda-src}
NPROC=$(nproc 2>/dev/null || echo 2)
JOBS=${JOBS:-$(( NPROC < 4 ? NPROC : 4 ))}
YOSYS_TAG=${YOSYS_TAG:-v0.69}
OPENSTA_COMMIT=${OPENSTA_COMMIT:-d1e43c6f9f4e66cb59c3d7958a4aa7d1626b4614}   # OpenSTA 3.1.0, Sep 2026
DRY_RUN=${DRY_RUN:-0}
export PATH="$PREFIX/bin:$PATH"

say()  { echo "== $*"; }
run()  { if [ "$DRY_RUN" = 1 ]; then echo "   [dry run] $*"; else "$@"; fi; }
# version_ge A B  ->  true if version A >= version B   (e.g. version_ge 0.52 0.69 is false)
version_ge() { [ "$(printf '%s\n%s\n' "$2" "$1" | sort -V | sed -n 1p)" = "$2" ]; }

if [ "$DRY_RUN" != 1 ] && [ "$(id -u)" != 0 ]; then
  echo "Please run with sudo:  sudo bash scripts/install_eda_tools.sh" >&2; exit 1
fi
run mkdir -p "$PREFIX/bin" "$SRC"

# ---- 1. packages -----------------------------------------------------------------------
if [ "${SKIP_APT:-0}" != 1 ] && command -v apt-get >/dev/null; then
  say "installing build packages with apt"
  run apt-get update
  run env DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
    build-essential git cmake bison flex libfl-dev gperf gawk autoconf automake libtool pkg-config \
    tcl tcl-dev swig libeigen3-dev zlib1g-dev libreadline-dev libffi-dev \
    python3 python3-pip python3-venv iverilog ca-certificates
fi

# Yosys needs cmake 3.28+ (Ubuntu 22.04 has 3.22): fetch a newer one into $PREFIX if needed.
CMAKE_VER=$(cmake --version 2>/dev/null | grep -oE '[0-9]+\.[0-9]+(\.[0-9]+)?' | sed -n 1p || true)
if [ -z "$CMAKE_VER" ] || ! version_ge "$CMAKE_VER" 3.28; then
  say "cmake ${CMAKE_VER:-missing} is too old for Yosys: installing a newer cmake into $PREFIX/cmake-venv"
  run python3 -m venv "$PREFIX/cmake-venv"
  run "$PREFIX/cmake-venv/bin/pip" install --quiet cmake
  run ln -sf "$PREFIX/cmake-venv/bin/cmake" "$PREFIX/bin/cmake"
fi

# ---- 2. CUDD (library needed by OpenSTA) -------------------------------------------------
if [ ! -f "$PREFIX/lib/libcudd.a" ]; then
  say "building CUDD"
  run rm -rf "$SRC/cudd"
  run git clone --depth 1 --branch cudd-3.0.0 https://github.com/ivmai/cudd.git "$SRC/cudd"
  if [ "$DRY_RUN" != 1 ]; then
    cd "$SRC/cudd"
    touch aclocal.m4 configure Makefile.am Makefile.in config.h.in
    ./configure --prefix="$PREFIX" && make -j"$JOBS" && make install
  fi
else
  say "CUDD already installed"
fi

# ---- 3. OpenSTA ---------------------------------------------------------------------------
if command -v sta >/dev/null; then
  say "OpenSTA already installed: $(command -v sta) ($(sta -version 2>/dev/null || echo '?'))"
else
  say "building OpenSTA (commit ${OPENSTA_COMMIT:0:10})"
  run rm -rf "$SRC/OpenSTA"
  run git clone --filter=blob:none https://github.com/The-OpenROAD-Project/OpenSTA.git "$SRC/OpenSTA"
  if [ "$DRY_RUN" != 1 ]; then
    cd "$SRC/OpenSTA"
    git checkout --quiet "$OPENSTA_COMMIT"
    cmake -S . -B build -DCMAKE_BUILD_TYPE=Release -DCMAKE_INSTALL_PREFIX="$PREFIX" -DCUDD_DIR="$PREFIX" \
          -DBUILD_TESTS=OFF -DUSE_TCL_READLINE=OFF
    cmake --build build -j"$JOBS"
    cp build/sta "$PREFIX/bin/sta"
  fi
fi

# ---- 4. Yosys -----------------------------------------------------------------------------
WANT=${YOSYS_TAG#v}
HAVE=$(yosys -V 2>/dev/null | grep -oE 'Yosys [0-9]+\.[0-9]+' | grep -oE '[0-9]+\.[0-9]+' || true)
if [ -n "$HAVE" ] && version_ge "$HAVE" "$WANT" && [ "${FORCE_YOSYS:-0}" != 1 ]; then
  say "Yosys $HAVE already installed ($(command -v yosys)) - OK"
else
  if [ -n "$HAVE" ]; then
    say "Yosys $HAVE found at $(command -v yosys), older than $WANT: building $YOSYS_TAG into $PREFIX"
    echo "   (the old one is left alone; $PREFIX/bin comes first in PATH, so the new one is used)"
  else
    say "building Yosys $YOSYS_TAG (the slow step)"
  fi
  run rm -rf "$SRC/yosys"
  run git clone --depth 1 --branch "$YOSYS_TAG" --recurse-submodules --shallow-submodules \
      https://github.com/YosysHQ/yosys.git "$SRC/yosys"
  if [ "$DRY_RUN" != 1 ]; then
    cd "$SRC/yosys"
    cmake -S . -B build -DCMAKE_BUILD_TYPE=Release -DCMAKE_INSTALL_PREFIX="$PREFIX" \
          -DYOSYS_ENABLE_UNIT_TESTS=OFF -DYOSYS_WITHOUT_SLANG=ON
    cmake --build build -j"$JOBS"
    cmake --install build
  fi
fi

# ---- 5. Icarus Verilog (only if the package was not available) ------------------------------
if command -v iverilog >/dev/null; then
  say "Icarus Verilog already installed: $(iverilog -V 2>&1 | sed -n 1p)"
else
  say "building Icarus Verilog"
  run rm -rf "$SRC/iverilog"
  run git clone --depth 1 --branch v13_0 https://github.com/steveicarus/iverilog.git "$SRC/iverilog"
  if [ "$DRY_RUN" != 1 ]; then
    cd "$SRC/iverilog" && sh autoconf.sh && ./configure --prefix="$PREFIX" && make -j"$JOBS" && make install
  fi
fi

echo
say "done. Tools that will be used:"
for t in yosys sta iverilog; do printf '   %-9s %s\n' "$t" "$(command -v $t || echo MISSING)"; done
[ "$DRY_RUN" = 1 ] || { yosys -V; sta -version; iverilog -V 2>&1 | sed -n 1p; } || true
echo "Add this line to ~/.bashrc so new terminals find them first:  export PATH=$PREFIX/bin:\$PATH"
