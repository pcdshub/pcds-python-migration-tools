#!/usr/bin/env python

from __future__ import annotations

import argparse
import dataclasses
import logging
import pathlib
from typing import Optional, Union

import update_python_repository as helpers

logger = logging.getLogger(__name__)

script_path = pathlib.Path(__file__).parent.resolve()
twincat_template_project_root = script_path / "lcls-twincat-template-project"
bundled_templates_root = script_path / "templates" / "twincat"


@dataclasses.dataclass
class TemplateFile:
    template_file: pathlib.Path
    possible_files: list[Union[str, pathlib.Path]] = dataclasses.field(
        default_factory=list
    )
    add_if_missing: bool = True
    update_if_existing: bool = True

    def __post_init__(self):
        if not self.possible_files:
            self.possible_files = [self.template_file]


def get_fixes(repo: helpers.Repository) -> list[helpers.Fix]:
    """
    Update a TwinCAT repository to the latest standards.
    """
    if not (repo.root / ".git").exists():
        raise RuntimeError("Not a repository root?")

    fixes = []
    to_remove = [
    ]
    for file in to_remove:
        if file.exists():
            fixes.append(
                helpers.DeleteFiles(name=f"remove-{file.name}", repo=repo, files=[file])
            )

    to_update = [
        TemplateFile(
            template_file=twincat_template_project_root / "LICENSE",
            possible_files=["LICENSE", "LICENSE.md", "LICENSE.rst"],
        ),
        TemplateFile(
            template_file=twincat_template_project_root / ".github" / "ISSUE_TEMPLATE.md",
            update_if_existing=False,
        ),
        TemplateFile(
            template_file=twincat_template_project_root / ".github" / "PULL_REQUEST_TEMPLATE.md",
            update_if_existing=False,
        ),
        TemplateFile(
            template_file=twincat_template_project_root / ".pre-commit-config.yaml",
            update_if_existing=False,
        ),
        TemplateFile(
            template_file=twincat_template_project_root / ".gitignore",
            update_if_existing=False,
        ),
        TemplateFile(
            template_file=twincat_template_project_root / ".gitattributes",
            update_if_existing=False,
        ),
        TemplateFile(
            template_file=bundled_templates_root / "README.md",
            update_if_existing=False,
        ),
    ]

    travis = repo.root / ".travis.yml"
    gha = repo.root / ".github" / "workflows" / "standard.yml"
    if travis.exists():
        fixes.append(
            helpers.GitHubActionsMigration(
                name="gha",
                repo=repo,
            )
        )
    elif not gha.exists():
        to_update.append(
            TemplateFile(
                template_file=twincat_template_project_root / ".github" / "workflows" / "standard.yml",
                update_if_existing=False,
                add_if_missing=True,
            )
        )

    for file in to_update:
        for dest_file in file.possible_files:
            if (repo.root / dest_file).exists():
                break
        else:
            if not file.add_if_missing:
                continue
            dest_file = file.template_file

        dest_file = repo.root / dest_file
        if dest_file.exists() and not file.update_if_existing:
            continue

        fixes.append(
            helpers.AddFileFromTemplate(
                f"add_{file.template_file.name}",
                template_file=pathlib.Path(file.template_file.parts[-1]),
                dest_file=dest_file,
                repo=repo,
                source_base_path=file.template_file.parent,
            )
        )

    remove_docs_tokens = helpers.DeleteFiles(
        name="remove_docs_tokens",
        repo=repo,
        files=list(repo.root.glob("*.enc")),
    )

    if remove_docs_tokens.files:
        fixes.append(remove_docs_tokens)

    fixes.extend(
        [
            helpers.PrecommitAutoupdate(name="run_precommit", repo=repo),
            # NOTE: don't run pre-commit; users will rebel
            # RunPrecommit(name="run_precommit", repo=repo),
        ]
    )

    return fixes


def main(
    repo_root: str,
    dry_run: bool = True,
    skip: Optional[list[str]] = None,
    only: Optional[list[str]] = None,
):
    root = pathlib.Path(repo_root).expanduser().resolve()
    repo = helpers.Repository(
        root=root,
        template_defaults={
            "repo_name": root.name,
        },
    )

    # repo.template_defaults["cookiecutter"].import_name = repo.import_name
    fixes = get_fixes(repo)
    return helpers.run_fixes(fixes, dry_run=dry_run, skip=skip, only=only)


def _create_argparser() -> argparse.ArgumentParser:
    """
    Create an ArgumentParser for detravisify.

    Returns
    -------
    argparse.ArgumentParser
    """
    parser = argparse.ArgumentParser()
    parser.add_argument("repo_root", type=str)
    parser.add_argument("--write", action="store_false", dest="dry_run")
    parser.add_argument("--skip", type=str, action="append")
    parser.add_argument("--only", type=str, action="append")
    return parser


def _main(args=None):
    """CLI entrypoint."""
    parser = _create_argparser()
    return main(**vars(parser.parse_args(args=args)))


if __name__ == "__main__":
    _main()
