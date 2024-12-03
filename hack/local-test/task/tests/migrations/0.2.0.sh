#!/usr/bin/env bash

set -e

pipeline_file=$1

echo "info: set verbosity"

yq -i '
(.spec.tasks[] | select(.name == "test") | .params) += [{"name": "verbosity", "value": "-vv"}]
' "$pipeline_file"

