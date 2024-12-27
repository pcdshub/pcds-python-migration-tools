import argparse
import base64
import csv
from dataclasses import dataclass, fields
from enum import Enum
from pathlib import Path
from typing import Dict, List, Optional
from urllib.error import HTTPError

from ghapi.all import GhApi


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
    default_owners: List[CodeownerGroup]
    lang_owners: List[CodeownerGroup]
    area_owners: List[CodeownerGroup]

    @classmethod
    def from_dict(cls, source: dict):
        settings = cls(
            owner=source["owner"],
            repo_name=source["repo_name"],
            repo_type=source["repo_type"],
            default_owners=[CodeownerGroup(value.strip) for value in
                            source["default_owners"].split(',') if value],
            area_owners=[CodeownerGroup(value.strip) for value in
                         source["area_owners"].split(',') if value],
            lang_owners=[CodeownerGroup(value.strip) for value in
                         source["lang_owners"].split(',') if value],
        )

        settings.default_owners.sort()
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
    """Create codeowners file based on RepoOwnerSettings"""
    lines = []

    # gather groups
    default_owners = list(set(settings.default_owners + settings.area_owners))
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

    return "\n".join(lines)


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


def create_codeowner_pr(
    settings: RepoOwnerSettings,
    contributor_acct: str,
    branch_name: str = 'mnt_add_codeowners',
    commit_message: str = "Adding CODEOWNERS file",
    api: Optional[GhApi] = None,
):
    """

    Requires fine grained token permissions:
    - create fork: "Administration" (write), "Contents" (read)
    - get repo: "Metadata" (read)
    - get branch (create ref): "Contents" (write)
    - create/update contents: "Contents" (write)
    - create pull request: "Pull requests" (write)

    Parameters
    ----------
    settings : RepoOwnerSettings
        _description_
    contributor_acct : str
        _description_
    branch_name : str, optional
        _description_, by default 'mnt_add_codeowners'
    commit_message : str, optional
        _description_, by default "Adding CODEOWNERS file"
    api : Optional[GhApi], optional
        _description_, by default None
    """

    if api is None:
        api = GhApi()  # requires a token for access

    # parse csv with settings
    # for repo in repos:
    repo_name = settings.repo_name
    # create relevant codeowner file
    # create fork (verify who authenticated user is)
    api.repos.create_fork("pcdshub", repo_name, default_branch_only=False)
    # Create branch from most recent sha
    repo_info = api.repos.get(contributor_acct, repo_name)
    head_sha = api.repos.get_branch(
        contributor_acct, repo_name, repo_info['default_branch']
    )['commit']['sha']

    try:
        api.repos.get_branch("tangkong", repo_name, "mnt_add_codeowners")
        api.git.delete_ref("tangkong", repo_name, "heads/mnt_add_codeowners")
    except HTTPError:
        # branch does not exist, continue to create
        pass

    api.git.create_ref(
        'tangkong',
        repo_name,
        'refs/heads/mnt_add_codeowners',
        head_sha
    )

    codeowner_file = create_codeowners_file(settings)
    # ensure file contents can be base64 encoded
    encoded_content = base64.b64encode(codeowner_file.encode('utf-8'))
    decoded_content = encoded_content.decode("ascii")

    try:
        # if file exists, grab its sha so we can edit it
        current_file_sha = api.repos.get_content(
            "tangkong",
            repo_name,
            ".github/CODEOWNERS",
            branch_name,
        ).sha
    except HTTPError:
        # file not found, create a new file
        current_file_sha = None

    api.repos.create_or_update_file_contents(
        owner=contributor_acct,
        repo=repo_name,
        path=".github/CODEOWNERS",
        message=commit_message,
        content=decoded_content,
        sha=current_file_sha,
        branch=branch_name,
    )

    # Create pull request
    api.pulls.create(
        owner="pcdshub",
        repo=repo_name,
        title="MNT: Creating CODEOWNER file",
        head=f"{contributor_acct}:{branch_name}",
        base=repo_info['default_branch'],
        body="(autogenerated) Adding CODEOWNERS file",
        maintainer_can_modify=True
    )


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
    parser.add_argument("repo_name", type=str, default='', nargs='?',
                        help='Name of the repository')

    # Optionally specify everything at once
    parser.add_argument("--repo-data-path", type=str, dest='repo_data_path',
                        help='Path to repo data csv.  Expects columns for: '
                             f'{[f.name for f in fields(RepoOwnerSettings)]}')

    parser.add_argument("--write", action="store_true", dest="write")

    return parser


def _main(args=None):
    """CLI entrypoint."""
    return
    # parser = _create_argparser()
    # return main(**vars(parser.parse_args(args=args)))


if __name__ == "__main__":
    _main()
