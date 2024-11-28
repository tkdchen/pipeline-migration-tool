#!/usr/bin/env bash
pipeline_file=$1
yq -i '.metadata.annotations.changes += "change task execution order"' "$pipeline_file"
