#!/usr/bin/env bash
pipeline_file=$1
yq -i '.metadata.annotations.changes += "remove task from pipeline"' "$pipeline_file"
