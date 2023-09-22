from __future__ import annotations

import argparse
import csv
from pathlib import Path
from typing import Any, Dict, List

from update_github_settings import BranchProtection, Repository

master_protect = BranchProtection()
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


# Map column names to protection rules
RULE_MAP = {'master': master_protect, 'gh-pages': gh_pages_prot, 'default': all_prot}


def str_to_bool(val: str):
    return val.lower() in ('y', 'yes', 'true')


def parse_repo_list(repo_data_path: str) -> List[Dict[str, Any]]:
    """ Returns {reponame: {master: bool, gh_pages: bool, all: bool}}"""
    data_path = Path(repo_data_path)
    if not data_path.exists:
        print('repo data file does not exist')
        return
    repo_data = []
    # Deal with extra columns
    with open(data_path, 'r') as csvfile:
        csv_reader = csv.reader(csvfile)
        for row in csv_reader:
            try:
                repo_data.append(
                    {'owner': row['owner'], 'repo_name': row['repo_name'],
                     'master': str_to_bool(row['master']),
                     'pages': str_to_bool(row['pages']),
                     'default': str_to_bool(row['default'])}
                )
            except KeyError as e:
                print('CSV File malformed, aborting')
                raise e

    return repo_data


def apply_protections(owner: str, repo_name: str, prot_master: bool, prot_pages: bool, prot_default: bool,
                      write: bool):
    repo = Repository.from_name(owner=owner, repo=repo_name)

    for prot in BranchProtection.from_repository(repo):
        if write:
            print("Deleting branch protection setting")
            prot.delete()
        else:
            print("(dry run) Deleting branch protection setting")

    if prot_master:
        print(f'created master rule for repo: {repo_name}')
        if write:
            master_protect.create(repo)
            print('-- master rule applied')
    if prot_pages:
        print(f'created gh-pages rule for repo: {repo_name}')
        if write:
            master_protect.create(repo)
            print('-- pages rule applied')
    if prot_default:
        print(f'created default (*) rule for repo: {repo_name}')
        if write:
            master_protect.create(repo)
            print('-- default (*) rule applied')
    return


def main(
    owner: str = "pcdshub",
    repo_name: str = "",
    prot_master: bool = False,
    prot_pages: bool = False,
    prot_default: bool = False,
    repo_data_path: str = "",
    write: bool = False
):
    print(owner, repo_name, prot_master, prot_pages, prot_default,
          repo_data_path, write)

    if repo_data_path:
        # run for each repo in list
        repo_data = parse_repo_list(repo_data_path)
        for repo_details in repo_data:
            apply_protections(repo_details['owner'], repo_details['repo_name'],
                              repo_details['master'], repo_details['pages'],
                              repo_details['default'], write=write)
        return

    # apply settings for a single repo
    apply_protections(owner, repo_name, prot_master=prot_master,
                      prot_pages=prot_pages, prot_default=prot_default,
                      write=write)

    return


def _create_argparser() -> argparse.ArgumentParser:
    """
    Create an ArgumentParser for update_branch_protections

    Returns
    -------
    argparse.ArgumentParser
    """
    parser = argparse.ArgumentParser()
    # specify an owner, repo, and settings
    parser.add_argument("owner", type=str)
    parser.add_argument("repo_name", type=str)
    parser.add_argument("--protect-master", action="store_true", dest='prot_master')
    parser.add_argument("--protect-pages", action="store_true", dest='prot_pages')
    parser.add_argument("--protect-default", action="store_true", dest='prot_default')

    # Optionally specify everything at once
    parser.add_argument("--repo-data-path", type=str, dest='repo_data_path',
                        help='Path to repo data csv.  Expects columns for: '
                             '[orgname, reponame, apply_master, apply_pages, '
                             'apply_default]')

    parser.add_argument("--write", action="store_true", dest="write")

    return parser


def _main(args=None):
    """CLI entrypoint."""
    parser = _create_argparser()
    return main(**vars(parser.parse_args(args=args)))


if __name__ == "__main__":
    _main()
