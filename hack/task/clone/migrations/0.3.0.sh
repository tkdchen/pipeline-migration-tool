#!/usr/bin/env python
set -e

pipeline_file=$1
yq -i '.metadata.annotations.changes += "remove task params"' "$pipeline_file"
