# Parse the same information as in the branch protection, but format RestAPI calls
# Currently github does not have graphql support for custom properties
# https://docs.github.com/en/rest/repos/custom-properties?apiVersion=2022-11-28


from __future__ import annotations

import argparse
import csv
from dataclasses import dataclass, fields
from enum import Enum
from pathlib import Path
from typing import List, Optional

# need this new fangled thinger for rest API
# needs a fine grained PAT to work
from ghapi.all import GhApi

api = None


def get_gh_api() -> GhApi:
    global api
    if api is None:
        api = GhApi()

    return api


class RepoType(str, Enum):
    """Enum where string values match the exact property values on Github"""
    NONE = 'None'
    PYTHON_LIBRARY = 'Python Library'
    TWINCAT_LIBRARY = 'TwinCAT Library'
    PLC = 'PLC'
    BACKUP = 'Backup'
    EPICS_IOC = 'EPICS IOC'
    EXTERNAL = 'External'
    PYTHON_DEV = 'Python Dev'
    EPICS_MODULE = 'EPICS module'
    OTHER = 'Other'


class StatusChecks(str, Enum):
    """Enum where string values match the exact property values on Github"""
    PYTHON_STANDARD = 'Python Standard'
    TWINCAT_STANDARD = 'TwinCAT Standard'
    NONE = 'None'


@dataclass
class RepoProperty:
    """field names match table headers exactly"""
    repo_name: str
    owner: str = 'pcdshub'
    repo_type: Optional[RepoType] = None
    default: Optional[bool] = None
    master: Optional[bool] = None
    gh_pages: Optional[bool] = None
    status_checks: Optional[StatusChecks] = None

    CHECK_MAP = {
        'Python Standard': StatusChecks.PYTHON_STANDARD,
        'TwinCAT Standard': StatusChecks.TWINCAT_STANDARD,
        'None': StatusChecks.NONE,
    }

    # maybe in the future we make the table match the properties, but for now
    # I don't want to break past scripts
    FIELD_TO_PROP_NAME = {
        'repo_type': 'type', 'default': 'protect_default', 'master': 'protect_master',
        'gh_pages': 'protect_gh_pages', 'status_checks': 'required_checks'
    }

    def apply_properties(self, write: bool = False) -> None:
        prop_list = [
            # Default owner property for all pcdshub repos
            {'property_name': 'owner', 'value': 'ECS (Experiment Control Systems)'},
        ]

        # Add actual mutable properties
        for field, prop_name in self.FIELD_TO_PROP_NAME.items():
            val = getattr(self, field)
            if val is not None:
                if isinstance(val, bool):
                    val = str(val).lower()
                elif isinstance(val, Enum):
                    val = val.value
                prop_list.append({
                    'property_name': prop_name,
                    'value': val,
                })

        if write:
            api = get_gh_api()
            print(f'Applying: {prop_list}')
            api.repos.create_or_update_custom_properties_values(
                owner=self.owner, repo=self.repo_name,
                properties=prop_list
            )
        else:
            print(f'(dry run): {prop_list}')

    @classmethod
    def from_dict(cls, source: dict) -> RepoProperty:
        try:
            bool_flags = {
                field: str_to_bool(source[field])
                for field in ('default', 'master', 'gh_pages')
            }

        except KeyError as e:
            print("source data malformed, aborting data=", source)
            raise e

        return cls(
            owner=source["owner"],
            repo_name=source["repo_name"],
            repo_type=RepoType(source["repo_type"]),
            status_checks=cls.CHECK_MAP[source["status_checks"]],
            **bool_flags
        )


def str_to_bool(val: str):
    return val.lower() in ('y', 'yes', 'true')


def parse_repo_list_properties(repo_data_path: str) -> List[RepoProperty]:
    data_path = Path(repo_data_path)
    if not data_path.exists:
        print('repo data file does not exist')
        return
    repo_data = []
    # Deal with extra columns
    with open(data_path, 'r') as csvfile:
        csv_reader = csv.DictReader(csvfile)
        for row in csv_reader:
            data_row = RepoProperty.from_dict(row)
            repo_data.append(data_row)

    return repo_data


def main(
    owner: str = "pcdshub",
    repo_name: str = "",
    repo_type: Optional[str] = None,
    prot_master: Optional[bool] = None,
    prot_pages: Optional[bool] = None,
    prot_default: Optional[bool] = None,
    status_checks: Optional[str] = None,
    repo_data_path: str = "",
    write: bool = False
):
    if repo_data_path:
        # run for each repo in list
        repo_data = parse_repo_list_properties(repo_data_path)
        for repo_prop in repo_data:
            repo_prop.apply_properties(write=write)
        return

    # Apply properties for a single repo
    if not repo_name:
        return

    if repo_type is not None:
        repo_type = RepoType(repo_type)
    if status_checks is not None:
        status_checks = StatusChecks(status_checks)

    prop = RepoProperty(
        owner=owner, repo_name=repo_name, repo_type=repo_type,
        master=prot_master, default=prot_default,
        gh_pages=prot_pages,
        status_checks=status_checks
    )

    prop.apply_properties(write=write)


def _create_argparser() -> argparse.ArgumentParser:
    """
    Create an ArgumentParser for update_repo_properties

    Returns
    -------
    argparse.ArgumentParser
    """
    parser = argparse.ArgumentParser()
    # specify an owner, repo, and settings
    parser.add_argument("owner", type=str, default='pcdshub', nargs='?',
                        help='Organization or owner of the repository, "pcdshub" '
                             'by default')
    parser.add_argument("repo_name", type=str, default='', nargs='?',
                        help='Name of the repository')
    parser.add_argument("--repo_type", type=str, nargs='?',
                        choices=list(t.value for t in RepoType),
                        help='Type of the repository. (Default: Other)')
    parser.add_argument("--status_checks", type=str, nargs='?',
                        choices=list(t.value for t in StatusChecks),
                        help="Status checks to require before merging a Pull Request."
                             " (Default: None)")
    parser.add_argument("--protect-master", action=argparse.BooleanOptionalAction,
                        dest='prot_master',
                        help="Add master protection.  This should be applied as "
                             "much as possible. This rule requires pull requests and "
                             "disallows force pushes. This protection also adds required "
                             "status checks depending on the repo_type")
    parser.add_argument("--protect-pages", action=argparse.BooleanOptionalAction,
                        dest='prot_pages',
                        help="Add gh-pages branch protection, allowing force pushes "
                             "to the gh-pages branch specifically")
    parser.add_argument("--protect-default", action=argparse.BooleanOptionalAction,
                        dest='prot_default',
                        help="Add default (*) branch protection, disallows "
                             "the creation of new branches")

    # Optionally specify everything at once
    parser.add_argument("--repo-data-path", type=str, dest='repo_data_path',
                        help='Path to repo data csv.  Expects columns for: '
                             f'{[f.name for f in fields(RepoProperty)]}')

    parser.add_argument("--write", action="store_true", dest="write",
                        help="Apply changes.  Default is a dry run")

    return parser


def _main(args=None):
    """CLI entrypoint"""
    parser = _create_argparser()
    return main(**vars(parser.parse_args(args=args)))


if __name__ == "__main__":
    _main()
