"""helper snippets"""


import functools
from typing import Any, Optional


class DryRunner:
    """
    Dry run implementation inspired by dryable https://github.com/haarcuba/dryable/

    to use:

    .. code:: python
        @DryRunner()
        def my_func(*args):
            print("I ran!")

        my_func()  # will not run

        DryRunner.set(run=True)

        my_func()  # will run


    """
    _run: bool = False

    def __init__(self, ret_val: Optional[Any] = None, msg: str = ''):
        self._ret_val = ret_val
        self._msg = msg

    @classmethod
    def set(cls, run: bool = False):
        cls._run = run

    def __call__(self, func):
        """
        Decorator that runs the enclosed snippet if dry_run=False
        Logs (prints) the action in either case

        This does prevent static analysis from detecting the signature of the
        wrapped function.  This may be alleviated in 3.10 with ParamSpec
        """
        @functools.wraps(func)
        def _decorated(*args, **kwargs):
            if not self._run:
                print(f"(dry run) {func.__name__}({self._msg})")
                return self._ret_val
            else:
                return func(*args, **kwargs)

        return _decorated
