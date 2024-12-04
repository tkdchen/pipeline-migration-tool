#!/usr/bin/env python
set -e

pipeline_file=$1

echo "info: use an alternative clone url"

yq -i '
(.spec.tasks[] | select(.name == "clone") | .params[] | select(.name == "url") | .value)
|= "https://github.com/konflux-ci/pipeline-migration-tool.git"
' "$pipeline_file"

