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
    #: The file we want to add to the repository
    template_file: pathlib.Path
    #: Possible locations where the file may be in the repository.
    #: The first will be used as a default if it's not found.
    possible_files: list[Union[str, pathlib.Path]]
    #: If missing in the repository, add it from the template
    add_if_missing: bool = True
    #: If existing in the repository, replace it with that of the template
    update_if_existing: bool = True


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
            add_if_missing=True,
            update_if_existing=True,
        ),
        TemplateFile(
            template_file=twincat_template_project_root / ".github" / "ISSUE_TEMPLATE.md",
            possible_files=[".github/ISSUE_TEMPLATE.md"],
            add_if_missing=True,
            update_if_existing=False,
        ),
        TemplateFile(
            template_file=twincat_template_project_root / ".github" / "PULL_REQUEST_TEMPLATE.md",
            possible_files=[".github/PULL_REQUEST_TEMPLATE.md"],
            add_if_missing=True,
            update_if_existing=False,
        ),
        TemplateFile(
            template_file=twincat_template_project_root / ".pre-commit-config.yaml",
            possible_files=[".pre-commit-config.yaml"],
            add_if_missing=True,
            update_if_existing=False,
        ),
        TemplateFile(
            template_file=twincat_template_project_root / ".gitignore",
            possible_files=[".gitignore"],
            add_if_missing=True,
            update_if_existing=False,
        ),
        TemplateFile(
            template_file=twincat_template_project_root / ".gitattributes",
            possible_files=[".gitattributes"],
            add_if_missing=True,
            update_if_existing=False,
        ),
        TemplateFile(
            template_file=bundled_templates_root / "README.md",
            possible_files=["README.md"],
            add_if_missing=True,
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
                possible_files=[".github/workflows/standard.yml"],
                update_if_existing=True,
                add_if_missing=True,
            )
        )

    for file in to_update:
        for dest_file in file.possible_files:
            if (repo.root / dest_file).exists():
                dest_file = repo.root / dest_file
                break
        else:
            dest_file = repo.root / file.possible_files[0]

        assert file.template_file.exists()

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
