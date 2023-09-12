===============================
pcds-python-migration-tools
===============================

Usage
-----
1. Clone the repository to migrate and make a new branch

    ::

        $ git clone https://github.com/pcdshub/my-repo-name-here
        $ cd my-repo-name-here
        $ git checkout -b ref_update_repository

2. Clone the migration tools in your home directory

    ::

        $ git clone https://github.com/pcdshub/pcds-python-migration-tools

3. Perform a dry-run update of the repository with

    ::

        $ cd pcds-python-migration-tools
        $ python update_repository.py /path/to/repository

4. Review the updates that are proposed. Assuming all looks good, add `--write` to the arguments and re-run update_repository:

    ::

        $ python update_repository.py --write /path/to/repository

5. If you find that the update performed something incorrectly or incompletely, you should manually fix it at this point.

    ::

        $ cd /path/to/repository
        $ pre-commit run --all-files

6. If you find that the update failed for some reason, please let the PD team know.
If you want to revert your branch and try again, you can destructively reset your branch to the state of the master branch by way of ``git reset --hard origin/master``
(Warning: Make sure you followed the above step and are on a branch specifically for migration purposes!).


