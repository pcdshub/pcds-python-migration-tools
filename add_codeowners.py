"""
Adds a codeowners file to a repository via pull request.

At a high level, this helper:
- Creates a feature branch for a repo on pcdshub
- Generates a CODEOWNERS file given the requested settings
- Commits CODEOWNERS file directly to branch
- Opens a pull request to the default branch


Some notes:
- All of this is done via the github API to avoid local file manipulation.
- A personal access token (PAT) is needed for API access.  It should have access
  to the organization's resources, and provide the following scopes:
    - get repo: "Metadata" (read)
    - get branch (create ref): "Contents" (write)
    - create/update contents: "Contents" (write)
    - create pull request: "Pull requests" (write)
- Because it seems impossible to scope a PAT to fork an internal / private repo
  into a personal github accounts, everything is performed on the organization
    - The token would need access to both the org and user's resources, currently
    you can only choose one
    - To do this on pcdshub currently you need bypass powers
"""
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


class LanguageOwners(str, Enum):
    PYTHON = "python-reviewers"
    TWINCAT = "twincat-reviewers"
    EPICS = "epics-reviewers"
    SHELL = "shell-reviewers"
    C = "c-reviewers"


class SMEOwners(str, Enum):
    VACUUM_SME = "vacuum-sme"
    LASER_SME = "laser-sme"
    MOTION_SME = "motion-sme"
    PMPS_SME = "pmps-sme"
    UI_SME = "ui-sme"
    LCLS_NAMING = "lcls-naming-council"


class AreaOwners(str, Enum):
    CXI = "cxi-members"
    LAS = "las-members"
    MEC = "mec-members"
    MFX = "mfx-members"
    RIX = "rix-members"
    TMO = "tmo-members"
    TXI = "txi-members"
    UED = "ued-members"
    XCS = "xcs-members"
    XPP = "xpp-members"


@dataclass
class RepoOwnerSettings:
    owner: str
    repo_name: str
    sme_owners: List[SMEOwners]
    lang_owners: List[LanguageOwners]
    area_owners: List[AreaOwners]

    @classmethod
    def from_dict(cls, source: dict):
        settings = cls(
            owner=source["owner"],
            repo_name=source["repo_name"],
            sme_owners=[SMEOwners(value.strip()) for value in
                        source["sme_owners"].split(',') if value],
            area_owners=[AreaOwners(value.strip()) for value in
                         source["area_owners"].split(',') if value],
            lang_owners=[LanguageOwners(value.strip()) for value in
                         source["lang_owners"].split(',') if value],
        )

        settings.sme_owners.sort()
        settings.area_owners.sort()
        settings.lang_owners.sort()

        return settings


GROUP_TO_EXT: Dict[LanguageOwners, str] = {
    LanguageOwners.PYTHON: '*.py*',
    LanguageOwners.TWINCAT: '*.{tsproj,plcproj,tmc,tpr,xti,TcTTO,TcPOU,TcDUT,TcGVL,'
                            'TcVis,TcVMO,TcGTLO}',
    LanguageOwners.C: '*.{c,cpp,cc,h,h++,hh,hpp}',
    LanguageOwners.SHELL: '*.{sh,zsh,csh,bash}',
    LanguageOwners.EPICS: '*.{archive,autosave,cmd,db,dbd,edl,ioc,proto,req,'
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

    Returns content encoded as base64 utf-8, in order to ensure the
    payload is appropriate for github's REST API
    """
    lines = []

    # gather groups
    default_owners = list(set(settings.sme_owners + settings.area_owners))
    if not default_owners:
        default_owners = [CodeownerGroup.ADMIN]
    default_groups = ["@pcdshub/" + grp for grp in default_owners]

    # default group
    lines.append("# default group")
    lines.append(f"* {' '.join(default_groups)}")
    lines.append("")

    # Language specific groups
    if settings.lang_owners:
        lines.append("# language-specific group(s)")
        for group in settings.lang_owners:
            specific_lang_group = ' '.join([f"@pcdshub/{group}"] + default_groups)
            lines.append(f"{GROUP_TO_EXT[group]} {specific_lang_group}")
        lines.append("")

    # .github admin group
    lines.append("# github folder holds administrative files")
    lines.append(f".github/** @pcdshub/{CodeownerGroup.ADMIN}")
    lines.append("")

    base_file = "\n".join(lines)

    # ensure file contents can be base64 encoded for submission to github API
    encoded_content = base64.b64encode(base_file.encode('utf-8'))
    return encoded_content.decode("utf-8")


def parse_repo_list(repo_data_path: str) -> List[RepoOwnerSettings]:
    """
    Read and parse a repository data list from ``repo_data_path``
    Expects a basic csv format (comma-delimited, first row are column headers)
    Expects the following header names
    - 'owner': repository owner, organization
    - 'repo_name': repository name
    - 'sme_owners': subject matter experts
    - 'area_owners': hutch/area owners
    - 'lang_owners': programming language experts

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
    """
    Creates a fork of `original_owner`/`repo_name`on whatever account the
    GITHUB_TOKEN has access to.
    (The github api does not let you specify the fork destination, as making a
    fork only makes sense on your account, not someone else's)
    """
    if api is None:
        api = GhApi()  # requires a token for access

    print(f" > Creating fork of {original_owner}/{repo_name}")
    api.repos.create_fork(original_owner, repo_name, default_branch_only=True)


@DryRunner()
def create_branch(
    owner: str,
    repo_name: str,
    branch_name: str,
    default_branch_name: str = "master",
    api: Optional[GhApi] = None,
) -> None:
    """
    Create branch (`branch_name`) from `default_branch_name` on `owner`/`repo_name`

    To do this we must identify the sha of the default branch, so that we can
    specify the new branch be created with that sha as its head.  This requires
    a few extra calls to the github API

    This will also first delete the branch named `branch_name` if it exists
    before creating a new one.
    """
    if api is None:
        api = GhApi()  # requires a token for access

    print(f" > Create branch: {branch_name}")
    # Create branch from most recent sha
    try:
        api.repos.get_branch(owner, repo_name, branch_name)
        print(" >> found existing branch, deleting branch...")
        api.git.delete_ref(owner, repo_name, f"heads/{branch_name}")
    except HTTPError:
        # branch does not exist, continue to create
        pass

    head_sha = api.repos.get_branch(
        owner, repo_name, default_branch_name
    )['commit']['sha']

    print(f" >> Creating ref: {owner}/{repo_name}:{branch_name} from sha: {head_sha}")
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
    """
    Add a CODEOWNERS file defined by `codeowner_data` to `branch_name`.

    NOTE: This expects `codeowner_data` to be base64 encoded.

    This could probably be generalized but I don't care enough right now.
    """
    if api is None:
        api = GhApi()  # requires a token for access

    print(f" > adding CODEOWNERS file to branch: {branch_name}")
    try:
        # if file exists, grab its sha so we can edit it
        current_file_sha = api.repos.get_content(
            owner,
            repo_name,
            ".github/CODEOWNERS",
            branch_name,
        ).sha
        print(" >> File exists, will modify existing CODEOWNERS")
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
def create_pull_request(
    repo_name: str,
    contributor: str,
    branch_name: str,
    reviewers: List[str],
    upstream: str = "pcdshub",
    base_branch_name: str = "master",
    title: str = "AUTOGENERATED: Pull Request Title",
    body: str = "(autogenerated) Pull Request body",
    api: Optional[GhApi] = None,
) -> None:
    """
    Create a pull request to merge:
    `contributor`/`branch_name` --> `upstream`/`base_branch_name`
    """
    if api is None:
        api = GhApi()  # requires a token for access

    # Create pull request
    print(f" > Creating pull request: {title}")
    resp = api.pulls.create(
        owner=contributor,
        repo=repo_name,
        title=title,
        head=f"{upstream}:{branch_name}",  # This could also point to contributor
        base=base_branch_name,
        body=body,
        maintainer_can_modify=True
    )

    print(f" > requesting reviews from {reviewers}")

    api.pulls.request_reviewers(
        owner=upstream,
        repo=repo_name,
        pull_number=resp['number'],
        reviewers=reviewers,
    )


def add_codeowners_from_setting(
    settings: RepoOwnerSettings,
    user: str,
    branch_name: str,
    reviewers: List[str],
    commit_message: str,
    pr_title: str,
    pr_body: str,
    api: Optional[GhApi],
):
    """
    Add a CODEOWNERS file from the specified `RepoOwnerSettings` object.

    Also requires information about the actor (`user`), and the contribution
    description metadata (branch_name, commit_message, pr_title, pr_body)
    """
    if api is None:
        api = GhApi()  # requires a token for access

    if reviewers is None:
        reviewers = []

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
    print("")
    print(base64.b64decode(codeowner_file).decode('utf-8'))
    add_codeowners_file_to_branch(
        owner=settings.owner,
        repo_name=settings.repo_name,
        branch_name=branch_name,
        codeowner_data=codeowner_file,
        commit_message=commit_message,
        api=api,
    )
    create_pull_request(
        repo_name=settings.repo_name,
        contributor=user,
        branch_name=branch_name,
        reviewers=reviewers,
        upstream=settings.owner,
        base_branch_name=default_branch_name,
        title=pr_title,
        body=pr_body,
        api=api,
    )


def main(
    owner: str = "pcdshub",
    user: str = "pcdshub",
    repo_name: str = "",
    branch_name: str = "mnt_add_codeowners",
    reviewers: Optional[List[str]] = None,
    commit_message: str = "MNT: Adding CODEOWNERS file",
    title="MNT: Adding CODEOWNERS file",
    body="(autogenerated) adding codeowners file",
    sme: Optional[List[str]] = None,
    area: Optional[List[str]] = None,
    lang: Optional[List[str]] = None,
    repo_data_path: str = "",
    write: bool = False
):
    """
    Create codeowners files and submit them via pull request
    """
    # Set dry-run option
    DryRunner.set(run=write)
    api = GhApi()

    print(owner, user, repo_name, branch_name, reviewers)
    # parse csv with settings
    if repo_data_path:
        repo_data = parse_repo_list(repo_data_path)
        for settings in repo_data:
            print(f"--- working on {settings.repo_name}...")
            add_codeowners_from_setting(
                settings, user, branch_name, reviewers,
                commit_message, title, body,
                api=api
            )

        return

    if not repo_name:
        print("Neither repo name nor repo data path provided, nothing to do")
        return

    # apply on a per-repo basis
    sme_owners = [SMEOwners(grp) for grp in sme]
    area_owners = [AreaOwners(grp) for grp in area]
    lang_owners = [LanguageOwners(grp) for grp in lang]

    print(sme_owners, area_owners, lang_owners)

    settings = RepoOwnerSettings(
        owner=owner,
        repo_name=repo_name,
        sme_owners=sme_owners,
        area_owners=area_owners,
        lang_owners=lang_owners,
    )

    print(f"--- working on {settings.repo_name}...")
    add_codeowners_from_setting(
        settings, user, branch_name, reviewers,
        commit_message, title, body,
        api=api
    )


def _create_argparser() -> argparse.ArgumentParser:
    """
    Create an ArgumentParser for add_codeowners

    If we ever reuse this we should probably let users specify their commit
    message, pr title, etc.  But this is already an incredibly long cli call.

    Returns
    -------
    argparse.ArgumentParser
    """
    parser = argparse.ArgumentParser()
    # specify an owner, repo, and settings
    parser.add_argument("owner", type=str, default='pcdshub', nargs='?',
                        help='Organization or owner of the repository, "pcdshub"'
                             'by default')
    parser.add_argument("user", type=str, default='pcdshub', nargs='?',
                        help="Name of account to contribute from, the fork will "
                             "created on this github account if required.")
    parser.add_argument("branch_name", type=str, default="mnt_add_codeowners", nargs='?',
                        help="Name of branch to create and submit to upstream")

    # manually specify settings
    parser.add_argument("repo_name", type=str, default='', nargs='?',
                        help='Name of the repository')
    parser.add_argument("--sme", type=str, nargs='*',
                        choices=[g.value for g in SMEOwners],
                        help='Subject Matter Experts for the repository.')
    parser.add_argument("--area", type=str, nargs='*',
                        choices=[g.value for g in AreaOwners],
                        help='Area (Hutch) owners for the repository.')
    parser.add_argument("--lang", type=str, nargs='*',
                        choices=[g.value for g in LanguageOwners],
                        help='Language experts for the repository.')

    # Optionally specify everything at once
    parser.add_argument("--repo-data-path", type=str, dest='repo_data_path',
                        help='Path to repo data csv.  Expects columns for: '
                             f'{[f.name for f in fields(RepoOwnerSettings)]}')

    # reviewers
    parser.add_argument("--reviewers", type=str, nargs='*',
                        help="Reviewers to be added to the generated Pull Request")

    # dry run?
    parser.add_argument("--write", action="store_true", dest="write",
                        help="(by default False) Apply the changes.  "
                             "If not specified, this will dry run and display the "
                             "proposed CODEOWNER file.")

    return parser


def _main(args=None):
    """CLI entrypoint."""
    parser = _create_argparser()
    return main(**vars(parser.parse_args(args=args)))


if __name__ == "__main__":
    _main()
