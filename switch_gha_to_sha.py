"""
Goals:
- Convert GHA versions to SHAs
- Add a dependabot config

I'm expecting this to generate about 200 PRs- not every repo has a GHA config.

I won't bother trying to avoid cloning repos.
"""

from dataclasses import dataclass, asdict
from pathlib import Path
import base64
import json
import re
import subprocess
import typing

from ghapi.all import GhApi

# It's not so important that latest is picked, but pick a real SHA
COMMON_SHA = {
    "pcdshub/pcds-ci-helpers/.*": "2e7e5ec8fb8afca5fa0cdb60b82b0f1b99cd2647",
    "actions/checkout": "9c091bb21b7c1c1d1991bb908d89e4e9dddfe3e0",
    "actions/configure-pages": "45bfe0192ca1faeb007ade9deae92b16b8254a0d",
    "actions/create-release": "0cb9c9b65d5d1901c1f53e5e66eaf4afd303e70e",
    "actions/deploy-pages": "cd2ce8fcbc39b97be8ca5fce6e763baed58fa128",
    "actions/download-artifact": "3e5f45b2cfb9172054b4087a40e8e0b5a5461e7c",
    "actions/github-script": "3a2844b7e9c422d3c10d287c895573f7108da1b3",
    "actions/setup-python": "a309ff8b426b58ec0e2a45f0f869d46889d02405",
    "actions/upload-artifact": "043fb46d1a93c77aae656e7c1c64a875d1fc6a0a",
    "actions/upload-pages-artifact": "fc324d3547104276b827a68afc52ff2a11cc49c9",
    "actions/upload-release-asset": "e8f9f06c4b078e705bd2ea027f0926603fc9b4d5",
    "docker/build-push-action": "f9f3042f7e2789586610d6e8b85c8f03e5195baf",
    "docker/login-action": "650006c6eb7dba73a995cc03b0b2d7f5ca915bee",
    "docker/setup-buildx-action": "d7f5e7f509e45cec5c76c4d5afdd7de93d0b3df5",
}

banned_repos = ["archiverappliance-datasource", "epicsmacrolib", "ioc-whatrecord-example", "pcds-ci-test-repo-python", "plc-summary"]

# Stash intermediates to speed up debug of later steps
HERE = Path(__file__).parent
BUILD = HERE / "sha_build"
REPO_LIST = BUILD / "repo_list.json"
WORKFLOWS = BUILD / "workflow_contents.json"
CLONES = BUILD / "clones"

GhResponseElem = dict[str, str | int]
GhListResponse = list[GhResponseElem]


def stash_github_response(filename: str | Path, response: GhListResponse):
    """Store the github reponse in a json file to limit the requests we need to make."""
    print(f"Saving info to {filename}")
    with open(filename, "w") as fd:
        json.dump(response, fd, default=lambda _: None)


def retrieve_github_response(
    filename: str | Path,
) -> GhListResponse:
    """Bring back the github reponse from a previous github request"""
    with open(filename, "r") as fd:
        print(f"Retrieving info from {filename}")
        return json.load(fd)


def get_repo_list_from_github(
    org: str = "pcdshub", api: GhApi | None = None
) -> GhListResponse:
    """Return information about all repos that are neither archived nor disabled."""
    if api is None:
        api = GhApi()
    repos: GhListResponse = []
    print("Retrieving repo list from github.")
    for page in range(1, 100):
        print(f"Checking repo page {page}")
        page_repos = api.repos.list_for_org(  # type: ignore
            org, per_page=100, page=page, sort="full_name"
        )
        page_repos = typing.cast(GhListResponse, page_repos)
        for repo_info in page_repos:
            if repo_info["archived"] or repo_info["disabled"]:
                continue
            if repo_info["name"] in banned_repos:
                continue
            repos.append(repo_info)
        if len(page_repos) < 100:
            break
    print("Done retrieving repo list from github.")
    return repos


@dataclass
class RepoWorkflowInfo:
    name: str
    workflow_contents: dict[str, str]


@dataclass
class OrgWorkflowInfo:
    name: str
    repos: list[RepoWorkflowInfo]


def stash_workflow_info(filename: str | Path, info: OrgWorkflowInfo):
    """Store the fully processed information from github to limit the requests we need to make."""
    print(f"Saving workflow info to {filename}")
    with open(filename, "w") as fd:
        json.dump(asdict(info), fd)


def retrieve_workflow_info(
    filename: str | Path,
) -> OrgWorkflowInfo:
    """Bring back the workflow info from a previous run."""
    with open(filename, "r") as fd:
        print(f"Retrieving workflow info from {filename}")
        workflow_dict = json.load(fd)

    return OrgWorkflowInfo(
        name=workflow_dict["name"],
        repos=[
            RepoWorkflowInfo(
                name=iter_repo["name"], workflow_contents=iter_repo["workflow_contents"]
            )
            for iter_repo in workflow_dict["repos"]
            if iter_repo["name"] not in banned_repos
        ],
    )


def get_org_workflow_info(
    repo_list: GhListResponse, org: str = "pcdshub", api: GhApi | None = None
) -> OrgWorkflowInfo:
    """Return information on all workflows in the org."""
    if api is None:
        api = GhApi()
    org_workflow_info = OrgWorkflowInfo(name=org, repos=[])
    for repo_info in repo_list:
        name = typing.cast(str, repo_info["name"])
        try:
            workflow_file_info = api.repos.get_content(  # type: ignore
                "pcdshub", name, ".github/workflows"
            )
        except Exception:
            print(f"Repo {name} has no gha workflows")
            continue
        # Yes workflows, accumulate the files
        print(f"Repo {name} has gha workflows")
        workflow_file_info = typing.cast(GhListResponse, workflow_file_info)
        workflow_contents = {}
        for file_info in workflow_file_info:
            print(f"Downloading {file_info['name']}")
            file_content = api.repos.get_content("pcdshub", name, file_info["path"])  # type: ignore
            b64_content = typing.cast(str, file_content["content"])
            workflow_contents[file_info["name"]] = base64.b64decode(b64_content).decode(
                "utf-8", errors="replace"
            )
        print(f"All downloads complete for {name}")
        repo_workflow_info = RepoWorkflowInfo(
            name=name, workflow_contents=workflow_contents
        )
        org_workflow_info.repos.append(repo_workflow_info)
    return org_workflow_info


USES_REGEX = re.compile(r"uses:(.*)@(.*)\n")


def needs_pinning(gha_contents: str) -> bool:
    """Return True if the file contents indicate that SHA pinning needs to be done."""
    act_and_ver = USES_REGEX.findall(gha_contents)
    for _, ver in act_and_ver:
        if len(ver.strip()) != 40:
            return True
    return False


def update_file(file_contents: list[str]) -> list[str]:
    """Given the old file contents, return what the file should contain after the update."""
    output_lines = []
    fixed_something = False
    for line in file_contents:
        did_replace = False
        found_action = False
        if (mch:=USES_REGEX.search(line)) is None:
            continue
        act, ver = mch.groups()
        act = act.strip()
        ver = ver.strip()
        for action_name, sha in COMMON_SHA.items():
            if re.match(action_name, act):
                found_action = True
                if ver != sha:
                    output_lines.append(line.replace(ver, sha))
                    print(f"replace '{line.strip()}' with '{output_lines[-1].strip()}'")
                    did_replace = True
                    fixed_something = True
                    break
        if not found_action:
            raise RuntimeError(f"Found unknown action: {act}")
        if not did_replace:
            output_lines.append(line)
    if not fixed_something:
        raise RuntimeError("Did not find anything to fix!")
    return output_lines


def main():
    BUILD.mkdir(exist_ok=True)
    try:
        workflow_info = retrieve_workflow_info(WORKFLOWS)
    except OSError:
        try:
            repo_list = retrieve_github_response(REPO_LIST)
        except OSError:
            repo_list = get_repo_list_from_github()
            stash_github_response(REPO_LIST, repo_list)
        workflow_info = get_org_workflow_info(repo_list)
        stash_workflow_info(WORKFLOWS, workflow_info)

    repos_to_update: list[str] = []

    for repo_workflow_info in workflow_info.repos:
        for file_content in repo_workflow_info.workflow_contents.values():
            if needs_pinning(file_content):
                repos_to_update.append(repo_workflow_info.name)

    CLONES.mkdir(exist_ok=True)

    for repo_name in repos_to_update:
        if (CLONES / repo_name).exists():
            continue
        subprocess.run(["git", "clone", f"git@github.com:pcdshub/{repo_name}", "--depth",  "1", str(CLONES / repo_name)], check=True)
        subprocess.run(["git", "checkout", "-b", "auto/ci_pin_gha_sha"], check=True, cwd=CLONES / repo_name)

    for repo_name in repos_to_update:
        workflows_dir = CLONES / repo_name / ".github" / "workflows"
        for workflow_file in workflows_dir.glob("*.y*ml"):
            with open(workflow_file, "r") as fd:
                lines = fd.readlines()
            print(f"Updating {workflow_file}")
            new_lines = update_file(lines)


if __name__ == "__main__":
    main()
