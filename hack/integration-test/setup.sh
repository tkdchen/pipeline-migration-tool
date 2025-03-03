#!/usr/bin/env bash

set -euo pipefail

: "${BUILD_DEFS_REPO:?Missing repository path of build-definitions}"
: "${QUAY_NAMESPACE:?Missing QUAY_NAMESPACE. build-and-push.sh uses it to push task bundles}"

export QUAY_NAMESPACE

declare -r DEFAULT_RECIPES="
clone 0.2 -
clone 0.2 M
clone 0.2 M
clone 0.2 -
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

    local -r filename=${task_file##*/}
    local -r task_name=${filename%.*}

    cat >"$migration_file" <<EOF
#!/usr/bin/env bash
set -euo pipefail

declare -r pipeline_file=\${1:?Missing pipeline file}
declare -r history="Migration was created at $(date --iso-8601=s --utc)"
declare -r params_selector='.spec.tasks[] | select(.name == "${task_name}") | .params'
declare -r history_selector="\${params_selector}[] | select(.name == \"history\")"

if yq -e "\$history_selector" "\$pipeline_file" >/dev/null 2>&1
then
    yq -i "(\${history_selector} | .value) += [\"\${history}\"]" "\$pipeline_file"
else
    yq -i "(\${params_selector}) += [{\"name\": \"history\", \"value\": [\"\${history}\"]}]" "\$pipeline_file"
fi

EOF
    echo "$migration_file"
}

bump_task_version_and_commit() {
    local -r task_name=$1 task_version=$2
    local -r task_file="task/${task_name}/${task_version}/${task_name}.yaml"
    new_version=$(bump_task_version "$task_file")
    git add "$task_file"
    git commit -m "task(${task_name}): bump version to $new_version"
}

create_migration_for_task() {
    local -r task_name=$1 task_version=$2
    local -r task_file="task/${task_name}/${task_version}/${task_name}.yaml"
    local new_version migration_file
    new_version=$(bump_task_version "$task_file")
    migration_file=$(create_migration "$task_file" "$new_version")
    git add "$task_file" "$migration_file"
    git commit -m "task(${task_name}): bump version to ${new_version} with a migration"
}

image_to_renovate_upgrade() {
    local -r old=$1 new=$2
    local dep_name=${old%@*}  # remove digest
    dep_name=${dep_name%:*}  # remove tag
    local current_value=${old%@*}
    current_value=${current_value##*:}
    local current_digest=${old#*@}
    local new_value=${new%@*}
    new_value=${new_value##*:}
    local new_digest=${new#*@}
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
    curl -sL "https://quay.io/api/v1/repository/${QUAY_NAMESPACE}/task-${task_name}/tag?onlyActiveTags=true&limit=100" | \
    jq -r '.tags[] | .name + " " + .manifest_digest' | \
        while read -r tag_name manifest_digest; do
            if [ "${tag_name%-*}" == "sha256" ]; then
                continue  # skip artifact image tag
            fi
            echo "quay.io/${QUAY_NAMESPACE}/task-${task_name}:${tag_name%-*}@${manifest_digest}"
        done | \
            uniq | \
            sed -n '1p; $p' | \
            tr '\n' ' ' # the newest one, then the oldest one
}

produce_bundles() {
    cd "$DEFINITIONS_DIR" || return 1
    while read -r task_name task_version create_migration
    do
        if [ -z "$task_name" ]; then
            continue  # skip blank line
        fi
        echo "Handle recipe: task $task_name, version $task_version, create a migration? $create_migration"
        if [ "$create_migration" == "M" ]; then
            create_migration_for_task "$task_name" "$task_version"
        else
            bump_task_version_and_commit "$task_name" "$task_version"
        fi
        build_and_push
    done <<<"$RECIPES"
}


###### do the job ######

build_and_push  # set up bundles baseline

produce_bundles

{
    echo "["
    read -r newest oldest <<<"$(get_newest_oldest_image clone)"
    if [ "$newest" != "$oldest" ]; then
        image_to_renovate_upgrade "$oldest" "$newest"
    fi
    read -r newest oldest <<<"$(get_newest_oldest_image lint)"
    if [ "$newest" != "$oldest" ]; then
        echo ","
        image_to_renovate_upgrade "$oldest" "$newest"
    fi
    echo "]"
} >/tmp/pmt-test-upgrades.txt

echo "Then,

(
cd ${SCRIPTDIR}/app
PMT_LOCAL_TEST=1 pipeline-migration-tool -u \"\$(cat /tmp/pmt-test-upgrades.txt)\"
)
"
