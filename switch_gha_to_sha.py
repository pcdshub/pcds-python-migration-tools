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
import typing

from ghapi.all import GhApi

# It's not so important that latest is picked, but pick a real SHA
COMMON_SHA = {
    "pcdshub/pcds-ci-helpers/*": "2e7e5ec8fb8afca5fa0cdb60b82b0f1b99cd2647",
    "actions/checkout": "9c091bb21b7c1c1d1991bb908d89e4e9dddfe3e0",
    "actions/download-artifact": "3e5f45b2cfb9172054b4087a40e8e0b5a5461e7c",
    "actions/upload-artifact": "043fb46d1a93c77aae656e7c1c64a875d1fc6a0a",
    "actions/setup-python": "a309ff8b426b58ec0e2a45f0f869d46889d02405",
    "actions/github-script": "3a2844b7e9c422d3c10d287c895573f7108da1b3",
}

# Stash intermediates to speed up debug of later steps
HERE = Path(__file__).parent
BUILD = HERE / "build"
REPO_LIST = BUILD / "repo_list.json"
WORKFLOWS = BUILD / "workflow_contents.json"

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


if __name__ == "__main__":
    main()
