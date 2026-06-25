"""
Goals:
- Convert GHA versions to SHAs
- Add a dependabot config

I'm expecting this to generate about 200 PRs- not every repo has a GHA config.

I won't bother trying to avoid cloning repos.
"""
from dataclasses import dataclass
import base64
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


@dataclass
class RepoWorkflowInfo:
    name: str
    workflow_contents: dict[str, str]


@dataclass
class OrgWorkflowInfo:
    name: str
    repos: list[RepoWorkflowInfo]


GhResponseElem = dict[str, str | int]
GhListResponse = list[GhResponseElem]

def get_org_workflow_info(org: str = "pcdshub", api: GhApi | None = None) -> OrgWorkflowInfo:
    """Return information on all workflows in the org."""
    if api is None:
        api = GhApi()
    repos: GhListResponse = []
    for page in range(1, 100):
        page_repos = api.repos.list_for_org(org, per_page=100, page=page, sort="full_name") # type: ignore
        page_repos = typing.cast(GhListResponse, page_repos)
        for repo_info in page_repos:
            if repo_info["archived"] or repo_info["disabled"] or repo_info["fork"]:
                continue
            repos.append(repo_info)
        repos.extend(page_repos)
        if len(page_repos) < 100:
            break
    org_workflow_info = OrgWorkflowInfo(name=org, repos=[])
    for repo_info in repos:
        name = typing.cast(str, repo_info["name"])
        try:
            workflow_file_info = api.repos.get_content("pcdshub", name, ".github/workflows") # type: ignore
        except Exception:
            # No workflows
            continue
        # Yes workflows, accumulate the files
        workflow_file_info = typing.cast(GhListResponse, workflow_file_info)
        workflow_contents = {}
        for file_info in workflow_file_info:
            file_content = api.repos.get_content("pcdshub", name, file_info["path"]) # type: ignore
            b64_content = typing.cast(str, file_content["content"])
            workflow_contents[file_info["name"]] = base64.b64decode(b64_content).decode("utf-8", errors="replace")
        repo_workflow_info = RepoWorkflowInfo(name=name, workflow_contents=workflow_contents)
        org_workflow_info.repos.append(repo_workflow_info)
    return org_workflow_info
