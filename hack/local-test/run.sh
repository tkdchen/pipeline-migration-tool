#!/usr/bin/env bash

set -e
set -o pipefail

# podman login quay.io
# cp "${XDG_RUNTIME_DIR}/containers/auth.json" ~/.docker/config.json
REGISTRY_AUTH_JSON=${REGISTRY_AUTH_JSON:-"$HOME/.docker/config.json"}

MIGRATION_ANNOTATION=dev.konflux-ci.task.migration
MIGRATION_ARTIFACT_TYPE=text/x-shellscript

TASK_NAMES=(clone tests)

IMAGE_PREFIX=${IMAGE_NS:-quay.io/mytestworkload/pmt-playground}

if [[ ! -e README.md ]]; then
    echo "error: script must run from the root of the repository." >&2
    exit 1
fi


retry() {
  local status
  local retry=0
  local -r interval=${RETRY_INTERVAL:-5}
  local -r max_retries=5
  while true; do
    "$@" && break
    status=$?
    ((retry+=1))
    if [ $retry -gt $max_retries ]; then
      return $status
    fi
    echo "Waiting for a while, then retry ..."
    sleep "$interval"
  done
}


generate_sha1_hash() {
  echo "$1 $(date)" | sha1sum - | (read -r checksum _; echo "$checksum")
}


declare -a clone_upgrades_data=()
declare -a tests_upgrades_data=()


build_and_push() {
  local -r task_name="$1"
  local -r task_file="hack/local-test/task/${task_name}/${task_name}.yaml"
  local -r image="${IMAGE_PREFIX}/task-${task_name}"
  local -a task_versions=()

  if [[ "$task_name" == "clone" ]]; then
    task_versions+=(0.1.0 0.2.0 0.2.1 0.2.4 0.3.0 0.4.0)
  elif [[ "$task_name" == "tests" ]]; then
    task_versions+=(0.1.0 0.2.0 0.2.1 0.3.0 0.3.2)
  else
    echo "error: script does not work for task name $task_name" >&2
    return 1
  fi

  for task_version in "${task_versions[@]}"
  do
    echo "info: handle task version ${task_version}"
    modified_task_file="/tmp/${task_file##*/}-modified"
    migration_file="hack/local-test/task/${task_name}/migrations/${task_version}.sh"

    yq ".metadata.labels.\"app.kubernetes.io/version\" |= \"${task_version}\"" "$task_file" | \
      yq ".metadata.labels.\"dev.konflux-ci/updated-datetime\" |= \"$(date)\"" \
      >"$modified_task_file"
    annotations=()
    if [[ -e "$migration_file" ]]; then
      echo "info: task $task_name has a migration $migration_file"
      annotations+=(--annotate "${MIGRATION_ANNOTATION}=true")
    fi

    sha1_hash=$(generate_sha1_hash "$task_version")
    image_tag="${task_version%.*}-${sha1_hash}"
    image_with_tag="$image:$image_tag"
    echo "info: push task bundle $image_with_tag"
    output=$(retry tkn bundle push "${image_with_tag}" -f "$modified_task_file" "${annotations[@]}")
    echo "$output"
    bundle_ref=$(echo "$output" | grep "sha256:[0-9a-f]\+" | cut -d' ' -f5)

    if [[ -e "$migration_file" ]]; then
      echo "info: attach migration file ${migration_file} to task bundle ${bundle_ref}"
      pushd "${migration_file%/*}"
      retry oras attach --registry-config "$REGISTRY_AUTH_JSON" \
        --annotation "${MIGRATION_ANNOTATION}=true" \
        --artifact-type "$MIGRATION_ARTIFACT_TYPE" \
        --distribution-spec v1.1-referrers-tag \
        "$bundle_ref" \
        "${migration_file##*/}"
      popd
    fi

    if [[ "$task_name" == "clone" ]]; then
      clone_upgrades_data+=("$image $image_tag ${bundle_ref##*@}")
    elif [[ "$task_name" == "tests" ]]; then
      tests_upgrades_data+=("$image $image_tag ${bundle_ref##*@}")
    fi
  done
}


remove_task_bundles() {
  local image
  local -a tags
  for task_name in "${TASK_NAMES[@]}"; do
    image="${IMAGE_PREFIX}/task-${task_name}"
    echo "info: listing tags from image repository ${image}"
    mapfile -t tags < <(skopeo list-tags "docker://${image}" | jq -r '.Tags[]')
    for tag_name in "${tags[@]}"; do
      echo "info: remove image tag ${image}:${tag_name}"
      skopeo delete "docker://${image}:${tag_name}"
    done
  done
}


make_renovate_upgrades() {
  local upgrade_from upgrade_to
  local n
  local dep_name current_value current_digest new_value new_digest

  local -r data_file="/tmp/pmt-upgrades-data.txt"

  echo -n "[" | tee "$data_file"

  upgrade_from=${clone_upgrades_data[0]}
  dep_name=$(cut -d' ' -f1 <<<"$upgrade_from")
  current_value=$(cut -d' ' -f2 <<<"$upgrade_from")
  current_digest=$(cut -d' ' -f3 <<<"$upgrade_from")
  n=${#clone_upgrades_data[@]}
  n=$((n-1))
  upgrade_to=${clone_upgrades_data[$n]}
  dep_name=$(cut -d' ' -f1 <<<"$upgrade_to")
  new_value=$(cut -d' ' -f2 <<<"$upgrade_to")
  new_digest=$(cut -d' ' -f3 <<<"$upgrade_to")

    echo "{
\"depName\": \"${dep_name}\",
\"currentValue\": \"${current_value}\",
\"currentDigest\": \"${current_digest}\",
\"newValue\": \"${new_value}\",
\"newDigest\": \"${new_digest}\",
\"packageFile\": \"hack/local-test/ci-pipeline.yaml\",
\"parentDir\": \"local-tests\",
\"depTypes\": [\"tekton-bundle\"]
}," | tee -a "$data_file"

  upgrade_from=${tests_upgrades_data[0]}
  dep_name=$(cut -d' ' -f1 <<<"$upgrade_from")
  current_value=$(cut -d' ' -f2 <<<"$upgrade_from")
  current_digest=$(cut -d' ' -f3 <<<"$upgrade_from")
  n=${#tests_upgrades_data[@]}
  n=$((n-1))
  upgrade_to=${tests_upgrades_data[$n]}
  dep_name=$(cut -d' ' -f1 <<<"$upgrade_to")
  new_value=$(cut -d' ' -f2 <<<"$upgrade_to")
  new_digest=$(cut -d' ' -f3 <<<"$upgrade_to")

    echo -n "{
\"depName\": \"${dep_name}\",
\"currentValue\": \"${current_value}\",
\"currentDigest\": \"${current_digest}\",
\"newValue\": \"${new_value}\",
\"newDigest\": \"${new_digest}\",
\"packageFile\": \"hack/local-test/ci-pipeline.yaml\",
\"parentDir\": \"local-tests\",
\"depTypes\": [\"tekton-bundle\"]
}" | tee -a "$data_file"

  echo "]" | tee -a "$data_file"

  echo
  echo "Sample Renovate upgrades is written into $data_file"
}


main() {
  local -r op=$1
  if [[ "$op" == "build-and-push" ]]; then
    for task_name in "${TASK_NAMES[@]}"; do
      echo "info: build and push task $task_name"
      build_and_push "$task_name"
    done
    make_renovate_upgrades
  elif [[ "$op" == "remove-task-bundles" ]]; then
    remove_task_bundles
  else
    echo "info: I don't know what to do for operation $op." >&2
    return 1
  fi
}

main "$@"
