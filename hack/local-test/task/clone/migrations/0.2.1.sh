#!/usr/bin/env bash
set -e

pipeline_file=$1

echo "info: set param revision to devel branch"
yq -i '
(.spec.tasks[] | select(.name == "clone") | .params) += [{"name": "revision", "value": "devel"}]
' "$pipeline_file"
