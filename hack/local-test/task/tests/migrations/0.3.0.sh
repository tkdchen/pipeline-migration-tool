#!/usr/bin/env bash

set -e

pipeline_file=$1

echo "info: fix execution order: run test after clone"

yq -i '
(.spec.tasks[] | select(.name == "test") | .runAfter) += ["clone"]
' "$pipeline_file"

