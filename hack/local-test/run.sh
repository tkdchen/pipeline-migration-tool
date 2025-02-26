#!/usr/bin/env bash

# BUILD_DEFS_REPO=~cqi/code/konflux/build-definitions QUAY_NAMESPACE=mytestworkload/build-definitions-test bash -x ./hack/local-test/run.sh

set -euo pipefail

: "${BUILD_DEFS_REPO:?Missing repository path of build-definitions}"
: "${QUAY_NAMESPACE:?Missing QUAY_NAMESPACE. build-and-push.sh uses it to push task bundles}"

export QUAY_NAMESPACE

declare -r DEFAULT_RECIPES="clone 0.2 -
clone 0.2 M
clone 0.2 -
clone 0.2 M
clone 0.2 M
lint 0.2 -
lint 0.2 M
lint 0.2 M
lint 0.2 M
lint 0.2 -
"

: "${RECIPES:=$DEFAULT_RECIPES}"

SCRIPTDIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" >/dev/null 2>&1 && pwd )"
cd "$SCRIPTDIR" || exit 1

declare -r DEFINITIONS_DIR="${SCRIPTDIR}/definitions"


build_and_push() {
    HERE="$DEFINITIONS_DIR" \
    SKIP_INSTALL=1 \
    SKIP_BUILD=1 \
    BUILD_TAG=$(git rev-parse HEAD) \
    OUTPUT_TASK_BUNDLE_LIST=/tmp/pmt-test-task-bundle-list \
    OUTPUT_PIPELINE_BUNDLE_LIST=/tmp/pmt-test-pipeline-bundle-list \
        bash "${BUILD_DEFS_REPO}/hack/build-and-push.sh"
}

bump_task_version() {
    local -r task_file=$1
    local patch
    local new_version

    IFS=. read -r major minor patch < <(
        yq '.metadata.labels."app.kubernetes.io/version"' "$task_file"
    )
    patch=$((patch+1))
    new_version="${major}.${minor}.${patch}"
    # app.kubernetes.io/version: "0.3.1"
    sed -i "s|^\( \+app\.kubernetes\.io/version\): .\+$|\1: \"${new_version}\"|" "$task_file"
    echo "$new_version"
}

create_migration() {
    local -r task_file=$1
    local -r new_version=$2
    local -r versioned_dir=${task_file%/*}

    local -r migration_dir="${versioned_dir}/migrations"
    [ -e "$migration_dir" ] || mkdir -p "$migration_dir"

    local -r migration_file="${migration_dir}/${new_version}.sh"

    cat >"$migration_file" <<EOF
#!/usr/bin/env bash
set -euo pipefail
declare -r pipeline_file=\${1:?Missing pipeline file}
EOF
    echo "$migration_file"
}

bump_task_version_and_commit() {
    local -r task_name=$1 task_version=$2
    local -r task_file="task/${task_name}/${task_version}/${task_name}.yaml"
    bump_task_version "$task_file"
    git add "$task_file"
    git commit -m "bump version of task $task_name"
}

update_task_with_migration() {
    local -r task_name=$1 task_version=$2
    local -r task_file="task/${task_name}/${task_version}/${task_name}.yaml"
    local new_version migration_file
    new_version=$(bump_task_version "$task_file")
    migration_file=$(create_migration "$task_file" "$new_version")
    git add "$task_file" "$migration_file"
    git commit -m "Create a migration for task $task_name"
    build_and_push
}

image_to_renovate_upgrade() {
    local -r old=$1 new=$2
    local dep_name=${old%@*}
    dep_name=${dep_name%:*}
    current_value=${old%@*}; current_value=${current_value##*:}
    current_digest=${old#*@}
    new_value=${new%@*}; new_value=${new_value##*:}
    new_digest=${new#*@}
    echo "{
    \"depName\": \"${dep_name}\",
    \"currentValue\": \"${current_value}\",
    \"currentDigest\": \"${current_digest}\",
    \"newValue\": \"${new_value}\",
    \"newDigest\": \"${new_digest}\",
    \"packageFile\": \".tekton/ci-pipeline.yaml\",
    \"parentDir\": \".tekton\",
    \"depTypes\": [\"tekton-bundle\"]
}
"
}

get_newest_oldest_image() {
    local -r task_name=$1
    curl -sL "https://quay.io/api/v1/repository/${QUAY_NAMESPACE}/task-${task_name}/tag/?onlyActiveTags=true&limit=30" | \
    jq -r '.tags[] | .name + " " + .manifest_digest' | \
        while read -r tag_name manifest_digest; do
            echo "quay.io/${QUAY_NAMESPACE}/task-clone:${tag_name%-*}@${manifest_digest}"
        done | \
            unique | \
            sed -n '1p; $p' | \
            tr '\n' ' ' # the newest one, then the oldest one
}

produce_bundles() {
    cd "$DEFINITIONS_DIR" || return 1
    while read -r task_name task_version create_migration
    do
        echo "Handle recipe: task $task_name, version $task_version, create a migration? $create_migration"
        if [ "$create_migration" == "M" ]; then
            update_task_with_migration "$task_name" "$task_version"
        else
            bump_task_version_and_commit "$task_name" "$task_version"
        fi
    done <<<"$RECIPES"
}


###### do the job ######

produce_bundles

{
    echo "["
    read -r newest oldest < <(get_newest_oldest_image clone)
    image_to_renovate_upgrade "$oldest" "$newest"
    echo ","
    read -r newest oldest < <(get_newest_oldest_image lint)
    image_to_renovate_upgrade "$oldest" "$newest"
    echo "]"
} >/tmp/pmt-test-upgrades.txt

echo "Then,
cd app
pipeline-migration-tool -u '$(cat /tmp/pmt-test-upgrades.txt)'
"
