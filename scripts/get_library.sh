#!/usr/bin/env bash
# Downloads the Nangate 45nm open cell library (typical corner) used by the
# examples. It is taken from the OpenSTA repository's examples folder.
set -euo pipefail
cd "$(dirname "$0")/.."
mkdir -p libs
if [ -f libs/nangate45_typ.lib ]; then echo "libs/nangate45_typ.lib already exists"; exit 0; fi
tmp=$(mktemp -d)
git clone --depth 1 --filter=blob:none --sparse https://github.com/The-OpenROAD-Project/OpenSTA.git "$tmp/OpenSTA"
git -C "$tmp/OpenSTA" sparse-checkout set examples
gunzip -c "$tmp/OpenSTA/examples/nangate45_typ.lib.gz" > libs/nangate45_typ.lib
rm -rf "$tmp"
echo "wrote libs/nangate45_typ.lib"
