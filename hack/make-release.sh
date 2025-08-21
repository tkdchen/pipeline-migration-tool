#!/usr/bin/env bash

set -eoxu pipefail

if ! which gh >/dev/null 2>&1; then
    printf "GitHub CLI is required to run this script.\n"
    printf "Follow instructions to install: %s\n" "https://github.com/cli/cli/blob/trunk/docs/install_linux.md"
    exit 1
fi

func_name=$1
if [[ -z "$func_name" ]]; then
    printf "Missing command to run. Doing release in the order of make_release_for_review and merge_and_publish_release\n"
    exit 1
fi

new_version=$2
if [[ -z "$new_version" ]]; then
    printf "Please provide a version in form of major.minor.patch\n"
    printf "Existing versions:\n"
    git tag
    exit 1
fi

issue_key=${3:-""}

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" >/dev/null 2>&1 && pwd )"
cd "$SCRIPT_DIR/.." || exit 1


make_release_for_review() {
    git checkout main
    if git fetch upstream; then
        git merge upstream/main
    else
        git pull
    fi
    git checkout -b "release-${new_version}"
    sed "s/^__version__ = \"[0-9]\+\.[0-9]\+\.[0-9]\+\"$/__version__ = \"${new_version}\"/" src/pipeline_migration/__init__.py
    git add src/pipeline_migration/__init__.py
    commit_opts=(-s -m "Release ${new_version}")
    if [[ -n "$issue_key" ]]; then
        commit_opts+=(-m "$issue_key")
    fi
    git commit "${commit_opts[@]}"
    git push -u origin HEAD
    gh pr create --title "Release ${new_version}"
}


merge_and_publish_release() {
    local num_of_approves
    local pr_state
    pr_state=$(gh pr view --json state -t "{{.state}}")
    if [[ $pr_state == MERGED ]]; then
        printf "Pull request has been merged already.\n"
    else
        num_of_approves=$(gh pr view --json reviews | jq '[.reviews[] | select(.state == "APPROVED")] | length')
        if [[ $num_of_approves -eq 0 ]]; then
            printf "Pull request has not been approved yet.\n"
            return 0
        fi
        gh pr merge --merge
    fi
    gh release create "v${new_version}" --latest --generate-notes
}

"$func_name"