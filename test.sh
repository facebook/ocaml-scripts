#!/bin/bash

# Copyright (c) Meta Platforms, Inc. and affiliates.
#
# This source code is licensed under the MIT license found in the
# LICENSE file in the root directory of this source tree.

if [ -z ${OPAM_SWITCH_PREFIX+x} ]; then
  echo "OPAM_SWITCH_PREFIX is not set."
  echo "Please use opam to correctly set your environment."
  exit 1
fi

echo "Location of opam switch: $OPAM_SWITCH_PREFIX"

# TODO: make this configurable
INPUT="data.json"
OUTPUT="BUCK"
ERRLOG="rules.err"

# First generate the raw json data from the opam repo
echo "Gathering information from opam switch..."
python3 meta2json.py -o "$INPUT"

# Then process them
echo "Generating $OUTPUT..."
echo "Warnings and errors will be logged in $ERRLOG"
python3 rules.py -i "$INPUT" -o "$OUTPUT" -s "$OPAM_SWITCH_PREFIX" 2> "$ERRLOG"

echo "Done !"
