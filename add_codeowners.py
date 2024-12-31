import argparse
import base64
import csv
from dataclasses import dataclass, fields
from enum import Enum
from pathlib import Path
from typing import Dict, List, Optional
from urllib.error import HTTPError

from ghapi.all import GhApi

from helpers import DryRunner


class CodeownerGroup(str, Enum):
    ADMIN = "software-admin"
    PYTHON = "python-reviewers"
    TWINCAT = "twincat-reviewers"
    EPICS = "epics-reviewers"
    SHELL = "shell-reviewers"
    CPP = "cpp-reviewers"
    VACUUM_SME = "vacuum-sme"
    LASER_SME = "laser-sme"
    MOTION_SME = "motion-sme"
    PMPS_SME = "pmps-sme"
    UI_SME = "ui-sme"
    LCLS_NAMING = "lcls-naming-council"
    TMO = "tmo-members"
    CXI = "cxi-members"
    LAS = "las-members"
    MEC = "mec-members"
    MFX = "mfx-members"
    RIX = "rix-members"
    TXI = "txi-members"
    XCS = "xcs-members"
    XPP = "xpp-members"


@dataclass
class RepoOwnerSettings:
    owner: str
    repo_name: str
    sme_owners: List[CodeownerGroup]
    lang_owners: List[CodeownerGroup]
    area_owners: List[CodeownerGroup]

    @classmethod
    def from_dict(cls, source: dict):
        settings = cls(
            owner=source["owner"],
            repo_name=source["repo_name"],
            sme_owners=[CodeownerGroup(value.strip()) for value in
                        source["sme_owners"].split(',') if value],
            area_owners=[CodeownerGroup(value.strip()) for value in
                         source["area_owners"].split(',') if value],
            lang_owners=[CodeownerGroup(value.strip()) for value in
                         source["lang_owners"].split(',') if value],
        )

        settings.sme_owners.sort()
        settings.area_owners.sort()
        settings.lang_owners.sort()

        return settings


GROUP_TO_EXT: Dict[CodeownerGroup, str] = {
    CodeownerGroup.PYTHON: '*.py*',
    CodeownerGroup.TWINCAT: '*.{plcproj,sln,TcDUT,TcGVL,TcPOU,TcTTO,tmc,tsproj,xti}',
    CodeownerGroup.CPP: '*.cpp',
    CodeownerGroup.SHELL: '*.sh',
    CodeownerGroup.EPICS: '*.{archive,autosave,cmd,db,dbd,edl,ioc,proto,req,'
                          'sub-arch,sub-req,substitutions,tpl-arch,tpl-req}',
}


def create_codeowners_file(settings: RepoOwnerSettings) -> str:
    """
    Create codeowners file based on RepoOwnerSettings

    See https://confluence.slac.stanford.edu/x/u5Q9Hw for details.
    By default:
    - "default" = sme_owners + area_owners
    - .github owned by admins only
    - Default (*): default
    - language specific files: default + lang_owners

    Returns content encoded to and from base64 utf-8, in order to ensure the
    payload is appropriate for github's REST API
    """
    lines = []

    # gather groups
    default_owners = list(set(settings.sme_owners + settings.area_owners))
    default_groups = ["@pcdshub/" + grp for grp in default_owners]

    # .github admin group
    lines.append("# github folder holds administrative files")
    lines.append(f".github/** @pcdshub/{CodeownerGroup.ADMIN}")
    lines.append("")

    # default group
    lines.append("# default group")
    lines.append(f"* {' '.join(default_groups)}")
    lines.append("")

    # Language specific groups
    lines.append("# language-specific group(s)")
    for group in settings.lang_owners:
        specific_lang_group = ' '.join([f"@pcdshub/{group}"] + default_groups)
        lines.append(f"{GROUP_TO_EXT[group]} {specific_lang_group}")
    lines.append("")

    base_file = "\n".join(lines)

    # ensure file contents can be base64 encoded for submission to github API
    encoded_content = base64.b64encode(base_file.encode('utf-8'))
    return encoded_content.decode("ascii")


def parse_repo_list(repo_data_path: str) -> List[RepoOwnerSettings]:
    """
    Read and parse a repository data list from ``repo_data_path``
    Expects a basic csv format (comma-delimited, first row are column headers)
    Expects the following header names
    - 'owner': repository owner, organization
    - 'repo_name': repository name
    - ......

    Parameters
    ----------
    repo_data_path : str
        path to the repo-data csv

    Returns
    -------
    List[ProtectionGroup]
        A list of ProtectionGroup's, holding settings for protection groups
    """
    data_path = Path(repo_data_path)
    if not data_path.exists:
        print('repo data file does not exist')
        return
    repo_data = []
    # Deal with extra columns
    with open(data_path, 'r') as csvfile:
        csv_reader = csv.DictReader(csvfile)
        for row in csv_reader:
            data_row = RepoOwnerSettings.from_dict(row)
            repo_data.append(data_row)
    return repo_data


@DryRunner()
def create_fork(
    repo_name: str,
    original_owner: str = "pcdshub",
    api: Optional[GhApi] = None,
) -> None:
    if api is None:
        api = GhApi()  # requires a token for access

    # create fork (verify who authenticated user is)
    # TODO: Is it even possible to create a fork for an interna/private repo?
    # - create fork action needs user PAT, but that doesn't have access to org internals
    # - pcdshub also forbids classic PAT access (though re-enabling it doesn't help)
    # - Will probably just need to make a branch on the pcdshub repo
    # succeeds even if fork exists.
    api.repos.create_fork(original_owner, repo_name, default_branch_only=True)


@DryRunner()
def create_branch(
    owner: str,
    repo_name: str,
    branch_name: str,
    default_branch_name: str = "master",
    api: Optional[GhApi] = None,
) -> None:
    """create branch on owner repo"""
    if api is None:
        api = GhApi()  # requires a token for access

    # Create branch from most recent sha
    try:
        api.repos.get_branch(owner, repo_name, branch_name)
        print(f"  >> found existing branch, deleting branch: {branch_name}")
        api.git.delete_ref(owner, repo_name, f"heads/{branch_name}")
    except HTTPError:
        # branch does not exist, continue to create
        pass

    head_sha = api.repos.get_branch(
        owner, repo_name, default_branch_name
    )['commit']['sha']

    print(f"  >> creating new branch: {owner}:{branch_name}")
    api.git.create_ref(
        owner,
        repo_name,
        f'refs/heads/{branch_name}',
        head_sha
    )


@DryRunner()
def add_codeowners_file_to_branch(
    owner: str,
    repo_name: str,
    branch_name: str,
    codeowner_data: str,
    commit_message: str = "MNT: Adding CODEOWNERS file",
    api: Optional[GhApi] = None,
) -> None:
    if api is None:
        api = GhApi()  # requires a token for access

    try:
        # if file exists, grab its sha so we can edit it
        current_file_sha = api.repos.get_content(
            owner,
            repo_name,
            ".github/CODEOWNERS",
            branch_name,
        ).sha
        print("  >> File exists, will modify existing CODEOWNERS")
    except HTTPError:
        # file not found, create a new file
        current_file_sha = None

    print(" >> Adding new codeowners data...")
    api.repos.create_or_update_file_contents(
        owner=owner,
        repo=repo_name,
        path=".github/CODEOWNERS",
        message=commit_message,
        content=codeowner_data,
        sha=current_file_sha,
        branch=branch_name,
    )


@DryRunner()
def create_codeowner_pr(
    repo_name: str,
    contributor: str,
    upstream: str = "pcdshub",
    base_branch_name: str = "master",
    branch_name: str = "mnt_add_codeowners",
    title: str = "MNT: Adding CODEOWNERS file",
    body: str = "(autogenerated) adding codeowners file",
    api: Optional[GhApi] = None,
) -> None:
    if api is None:
        api = GhApi()  # requires a token for access

    # Create pull request
    print(f"  >> Creating pull request: {title}")
    api.pulls.create(
        owner=upstream,
        repo=repo_name,
        title=title,
        head=f"{upstream}:{branch_name}",
        base=base_branch_name,
        body=body,
        maintainer_can_modify=True
    )


def main(
    owner: str = "pcdshub",
    user: str = "",
    repo_name: str = "",
    branch_name: str = "mnt_add_codeowners",
    commit_message: str = "MNT: Adding CODEOWNERS file",
    sme: Optional[List[str]] = None,
    area: Optional[List[str]] = None,
    lang: Optional[List[str]] = None,
    repo_data_path: str = "",
    write: bool = False
):
    """
    Requires fine grained token permissions as pcdshub (to see internal):
    - create fork: "Administration" (write), "Contents" (read)
    - get repo: "Metadata" (read)
    - get branch (create ref): "Contents" (write)
    - create/update contents: "Contents" (write)
    - create pull request: "Pull requests" (write)
    """
    # Set dry-run option
    DryRunner.set(run=write)
    api = GhApi()

    # parse csv with settings
    if repo_data_path:
        repo_data = parse_repo_list(repo_data_path)
        for settings in repo_data:
            print(f"--- working on {settings.repo_name}...")
            # create_fork(
            #     repo_name=settings.repo_name,
            #     original_owner=settings.owner,
            #     api=api,
            # )
            repo_info = api.repos.get(settings.owner, settings.repo_name)
            default_branch_name = repo_info["default_branch"]
            create_branch(
                owner=settings.owner,
                repo_name=settings.repo_name,
                branch_name=branch_name,
                default_branch_name=default_branch_name,
                api=api,
            )
            codeowner_file = create_codeowners_file(settings)
            print(base64.b64decode(codeowner_file).decode('utf-8'))
            add_codeowners_file_to_branch(
                owner=settings.owner,
                repo_name=settings.repo_name,
                branch_name=branch_name,
                codeowner_data=codeowner_file,
                commit_message=commit_message,
                api=api,
            )
            create_codeowner_pr(
                repo_name=settings.repo_name,
                contributor=user,
                upstream=settings.owner,
                base_branch_name=default_branch_name,
                branch_name=branch_name,
                api=api,
            )

    if not repo_name:
        return

    # apply
    print("End of function")


def _create_argparser() -> argparse.ArgumentParser:
    """
    Create an ArgumentParser for update_branch_protections

    Returns
    -------
    argparse.ArgumentParser
    """
    parser = argparse.ArgumentParser()
    # specify an owner, repo, and settings
    parser.add_argument("owner", type=str, default='pcdshub', nargs='?',
                        help='Organization or owner of the repository, "pcdshub"'
                             'by default')
    parser.add_argument("user", type=str,
                        help="Name of account to contribute from, the fork will "
                             "created on this github account")
    parser.add_argument("branch_name", type=str, default="mnt_add_codeowners",
                        help="Name of branch to create on fork and submit upstream")

    # manually specify settings
    parser.add_argument("repo_name", type=str, default='', nargs='?',
                        help='Name of the repository')
    parser.add_argument("--sme", type=str, nargs='*',
                        choices=[g.value for g in CodeownerGroup
                                 if "sme" in g.value],
                        help='Subject Matter Experts for the repository.')
    parser.add_argument("--area", type=str, nargs='*',
                        choices=[g.value for g in CodeownerGroup
                                 if "member" in g.value],
                        help='Area (Hutch) owners for the repository.')
    parser.add_argument("--lang", type=str, nargs='*',
                        choices=[g.value for g in CodeownerGroup
                                 if "reviewers" in g.value],
                        help='Language experts for the repository.')

    # Optionally specify everything at once
    parser.add_argument("--repo-data-path", type=str, dest='repo_data_path',
                        help='Path to repo data csv.  Expects columns for: '
                             f'{[f.name for f in fields(RepoOwnerSettings)]}')

    parser.add_argument("--write", action="store_true", dest="write")

    return parser


def _main(args=None):
    """CLI entrypoint."""
    parser = _create_argparser()
    return main(**vars(parser.parse_args(args=args)))


if __name__ == "__main__":
    _main()
