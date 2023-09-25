from __future__ import annotations

import argparse
import collections
import dataclasses
import functools
import json
import logging
import pathlib
import subprocess
from dataclasses import field
from typing import Any, Generic, Optional, TypeVar, Union

import apischema
from apischema.metadata import alias

script_path = pathlib.Path(__file__).resolve().parent


logger = logging.getLogger(__name__)

default_required_status_checks = {
    "python": [
        "standard / Conda (3.10) / Python 3.10: conda",
        "standard / Conda (3.9, true) / Python 3.9: conda",
        "standard / Documentation / Python 3.9: documentation building",
        "standard / Pip (3.10) / Python 3.10: pip",
        "standard / Pip (3.9, true) / Python 3.9: pip",
        "standard / pre-commit checks / pre-commit",
    ],
    "twincat": [
        "standard / Documentation / Python 3.9: documentation building",
        "standard / pragma linting / Pragma Linting",
        "standard / pytmc summary / Project Summary",
        # "standard / Documentation / Documentation deployment",
        # "standard / Style check / Leading tabs",
        # "standard / Style check / Line IDs (TwinCAT misconfiguration)",
        # "standard / Style check / Trailing whitespace",
        # "standard / Syntax check / Experimental Syntax Check",
        # "standard / pre-commit checks / pre-commit",
    ],
    "none": []
}


@functools.lru_cache()
def get_packaged_graphql(filename: str) -> str:
    # Ref: https://graphql.org/learn/queries/
    # Ref: https://gist.github.com/duboisf/68fb6e22ac0a2165ca298074f0e3b553

    with open(script_path / "graphql" / filename, "rt") as fp:
        return fp.read().strip()


def run_gh(*command: str) -> bytes:
    return subprocess.check_output(["gh", *command])


def gh_api(*command: str, hostname: str = "github.com") -> dict:
    raw_json = run_gh("api", "--hostname", hostname, *command)
    return json.loads(raw_json)


def gh_api_graphql(
    query: str,
    hostname: str = "github.com",
    **params: list[str] | str | bool | int,
) -> dict:

    args = []

    def find_params():
        yield "query", query
        for name, value in params.items():
            if isinstance(value, list):
                for item in value:
                    yield f"{name}[]", item
            else:
                yield name, value

    for name, value in find_params():
        if isinstance(value, str):
            value = value.replace("'", r"\'")
            # -f is a raw field - a string parameter
            args.extend(["-f", f"{name}={value}"])
        elif isinstance(value, bool):
            value = "true" if value else "false"
            args.extend(["-F", f"{name}={value}"])
        else:
            # -F is a typed field
            args.extend(["-F", f"{name}={value!r}"])

    raw_json = run_gh("api", "graphql", "--hostname", hostname, *args)
    return json.loads(raw_json)


def gh_api_graphql_paginated(
    query: str,
    key: tuple[str, ...],
    hostname: str = "github.com",
    **params: list[str] | str | bool | int,
) -> list:

    results = []

    params.pop("endCursor", None)
    while True:
        result = gh_api_graphql(
            query=query, hostname=hostname, **params
        )["data"]
        for key_part in key:
            result = result[key_part]

        page_info = apischema.deserialize(Pagination, result.pop("pageInfo"))
        result = result["nodes"]

        assert isinstance(result, list)
        results.extend(result)

        if not page_info.hasNextPage:
            break
        params["endCursor"] = page_info.endCursor

    return results


def gh_graphql_describe(type_: str):
    return gh_api_graphql(query='''\
        query {
          __type(name: "''' + type_ + '''") {
            name
            kind
            description
            fields {
                name
                type {
                    name
                    kind
                    ofType {
                        name
                        kind
                    }
                }
                description
            }
          }
        }
''')


class DeserializationError(Exception):
    def __init__(self, message: str, info: dict[str, Any]):
        super().__init__(message)
        self.info = info


class Serializable:
    @classmethod
    def from_dict(cls, info: dict[str, Any]):
        try:
            return apischema.deserialize(cls, info)
        except Exception as ex:
            raise DeserializationError(
                f"Failed to deserialize JSON dictionary for {cls.__name__}",
                info
            ) from ex


@dataclasses.dataclass
class Actor(Serializable):
    login: str = ""


@dataclasses.dataclass
class Pagination(Serializable):
    hasNextPage: bool
    endCursor: str


@dataclasses.dataclass
class BranchProtection(Serializable):
    creator: Actor = field(default_factory=Actor)
    id: str = ""

    allows_deletions: bool = field(default=False, metadata=alias("allowsDeletions"))
    allows_force_pushes: bool = field(
        default=False, metadata=alias("allowsForcePushes")
    )
    is_admin_enforced: bool = field(default=False, metadata=alias("isAdminEnforced"))
    required_status_checks: list[str] = field(
        default_factory=default_required_status_checks["python"].copy,
        metadata=alias("requiredStatusCheckContexts"),
    )
    required_approving_review_count: Optional[int] = field(
        default=1, metadata=alias("requiredApprovingReviewCount")
    )
    requires_approving_reviews: bool = field(
        default=True, metadata=alias("requiresApprovingReviews")
    )
    requires_code_owner_reviews: bool = field(
        default=False, metadata=alias("requiresCodeOwnerReviews")
    )
    requires_status_checks: bool = field(
        default=True, metadata=alias("requiresStatusChecks")
    )
    restricts_pushes: bool = field(default=True, metadata=alias("restrictsPushes"))
    blocks_creations: bool = field(default=True, metadata=alias("blocksCreations"))
    restricts_review_dismissals: bool = field(
        default=False, metadata=alias("restrictsReviewDismissals")
    )
    dismisses_stale_reviews: bool = field(
        default=True, metadata=alias("dismissesStaleReviews")
    )
    pattern: str = field(default="master")

    def create(self, repo: Repository) -> BranchProtection:
        info = gh_api_graphql(
            get_packaged_graphql("branch_protection.graphql"),
            operationName="addBranchProtection",
            repositoryId=repo.id,
            requiredStatusChecks=self.required_status_checks,
            allowsDeletions=self.allows_deletions,
            allowsForcePushes=self.allows_force_pushes,
            blocksCreations=self.blocks_creations,
            dismissesStaleReviews=self.dismisses_stale_reviews,
            isAdminEnforced=self.is_admin_enforced,
            requiresApprovingReviews=self.requires_approving_reviews,
            requiredApprovingReviewCount=self.required_approving_review_count,
            requiresCodeOwnerReviews=self.requires_code_owner_reviews,
            requiresStatusChecks=self.requires_status_checks,
            restrictsPushes=self.restricts_pushes,
            restrictsReviewDismissals=self.restricts_review_dismissals,
            branchPattern=self.pattern,
        )
        return self.from_dict(
            info["data"]["createBranchProtectionRule"]["branchProtectionRule"]
        )

    def delete(self) -> str:
        info = gh_api_graphql(
            get_packaged_graphql("branch_protection.graphql"),
            operationName="deleteBranchProtection",
            ruleId=self.id,
        )
        return info["data"]["deleteBranchProtectionRule"]["clientMutationId"]

    @classmethod
    def from_repository(cls, repo: Repository) -> list[BranchProtection]:
        info = gh_api_graphql(
            get_packaged_graphql("branch_protection.graphql"),
            operationName="showBranchProtection",
            owner=repo.owner,
            repo=repo.repo,
        )["data"]["repository"]
        return [
            cls.from_dict(node)
            for node in info["branchProtectionRules"].get("nodes", [])
        ]


T = TypeVar("T")


@dataclasses.dataclass
class NodeList(collections.UserList, Generic[T]):
    nodes: list[T]

    @property
    def data(self):
        return self.nodes

    def __str__(self):
        return repr(self.data)

    def __repr__(self):
        return repr(self.data)


@dataclasses.dataclass
class User(Serializable):
    login: str
    name: str


@dataclasses.dataclass
class Team(Serializable):
    combined_slug: str = field(metadata=alias("combinedSlug"))


@dataclasses.dataclass
class ProtectionRule(Serializable):
    timeout: int
    reviewers: NodeList[Union[User, Team]]


@dataclasses.dataclass
class Environment(Serializable):
    id: str
    name: str
    protectionRules: NodeList[ProtectionRule]


@dataclasses.dataclass
class Repository(Serializable):
    id: str
    name: str
    description: Optional[str]
    full_name: str = field(metadata=alias("nameWithOwner"))
    homepage_url: Optional[str] = field(metadata=alias("homepageUrl"))
    is_archived: bool = field(metadata=alias("isArchived"))
    collaborators: Optional[dict] = None
    environments: Optional[NodeList[Environment]] = None

    @property
    def owner(self) -> str:
        return self.full_name.split("/", 1)[0]

    @property
    def repo(self) -> str:
        return self.full_name.split("/", 1)[1]

    @classmethod
    def from_name(
        cls, owner: str, repo: str, hostname: str = "github.com"
    ) -> Repository:
        info = gh_api_graphql(
            get_packaged_graphql("repository_info.graphql"),
            hostname=hostname,
            operationName="showRepositoryInfo",
            owner=owner,
            repo=repo,
        )["data"]["repository"]
        print(json.dumps(info, indent=2))
        return cls.from_dict(info)

    def create_environment(self, name: str):
        data = gh_api_graphql(
            get_packaged_graphql("repository_info.graphql"),
            operationName="createEnvironment",
            repositoryId=self.id,
            name=name,
        )["data"]
        env = data["createEnvironment"]["environment"]
        return Environment.from_dict(env)


def find_repositories(owner: str) -> list[Repository]:
    repositories = gh_api_graphql_paginated(
        get_packaged_graphql("repository_info.graphql"),
        key=("organization", "repositories"),
        operationName="listAllReposInOrg",
        # operationName="allOrgRepoDirectCollaborators",
        orgLogin=owner,
    )
    return [
        Repository.from_dict(repo)
        for repo in repositories
    ]


def main(
    owner: str,
    repo_name: str = "",
    repo_type: str = "python",
    write: bool = False,
    list_repos: bool = False,
    create_environments: bool = True,
    update_branch_protection: bool = True,
):
    if list_repos:
        for repo in find_repositories(owner=owner):
            print(repo.name, repo.description)

    if not repo_name:
        if not list_repos:
            raise RuntimeError(
                "Repository name required for anything but --list-repositories"
            )
        return

    repo = Repository.from_name(owner=owner, repo=repo_name)
    if create_environments:
        print("Creating environment gh-pages")
        if write:
            repo.create_environment("gh-pages")

    if update_branch_protection:
        print("Repository:", repo)
        for prot in BranchProtection.from_repository(repo):
            if write:
                print("Deleting branch protection setting")
                prot.delete()
            else:
                print("(dry run) Deleting branch protection setting")
        master_prot = BranchProtection()
        master_prot.required_status_checks = default_required_status_checks[repo_type]

        gh_pages_prot = BranchProtection(pattern='gh-pages', allows_force_pushes=True,
                                         dismisses_stale_reviews=False,
                                         required_status_checks=[],
                                         requires_status_checks=False,
                                         required_approving_review_count=0,
                                         requires_approving_reviews=False,
                                         )
        all_prot = BranchProtection(pattern='*', required_status_checks=[],
                                    dismisses_stale_reviews=False,
                                    requires_status_checks=False,
                                    required_approving_review_count=0,
                                    requires_approving_reviews=False,
                                    allows_deletions=True,)
        prot_rules = [master_prot, gh_pages_prot, all_prot]
        if write:
            for prot in prot_rules:
                new_rule = prot.create(repo)
                print("Created rule")
                print(new_rule)
        else:
            print("(dry run) Created rule")
            print(prot_rules)


def _create_argparser() -> argparse.ArgumentParser:
    """
    Create an ArgumentParser for update_github_settings.

    Returns
    -------
    argparse.ArgumentParser
    """
    parser = argparse.ArgumentParser()
    parser.add_argument("owner", type=str)
    parser.add_argument("repo_name", type=str, default="", nargs="?")
    parser.add_argument("--list-repos", action="store_true", dest="list_repos")
    parser.add_argument(
        "--repo-type",
        type=str,
        default="python",
        choices=sorted(default_required_status_checks),
    )
    parser.add_argument(
        "--write", action="store_true", dest="write"
    )
    parser.add_argument(
        "--no-branch-protection", action="store_false", dest="update_branch_protection"
    )
    parser.add_argument(
        "--no-environments", action="store_false", dest="create_environments"
    )
    return parser


def _main(args=None):
    """CLI entrypoint."""
    parser = _create_argparser()
    return main(**vars(parser.parse_args(args=args)))


if __name__ == "__main__":
    _main()
