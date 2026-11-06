"""Narrow filtering while importing verified third-party code."""
import re
import warnings
from contextlib import contextmanager


@contextmanager
def filter_iterdir_warnings():
    # Compilation warnings can be attributed to the importing module, so match
    # their source filename rather than suppressing all warnings in that module.
    with warnings.catch_warnings(record=True) as captured:
        warnings.simplefilter("always", SyntaxWarning)
        try:
            yield
        finally:
            pass
    for item in captured:
        known = (issubclass(item.category, SyntaxWarning)
                 and re.search(r"(?:^|[/\\])iterdir[/\\]__init__\.py$", item.filename)
                 and re.match(r'''^"is(?: not)?" with (?:an? )?['"]?int['"]? literal\.''', str(item.message)))
        if not known:
            warnings.warn_explicit(item.message, item.category, item.filename, item.lineno)
