#!/usr/bin/env bash
set -e

pipeline_file=$1
yq -i '.metadata.annotations.changes += "add new task"' "$pipeline_file"
