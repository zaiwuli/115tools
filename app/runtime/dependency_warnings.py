"""Narrow filters for verified third-party import warnings."""
import warnings


def filter_iterdir_warnings():
    # iterdir intentionally distinguishes integer traversal sentinels from bools.
    # Keep unrelated SyntaxWarnings and all runtime failures visible.
    for message in (
        r'^"is" with (?:an? )?[\x27\"]?int[\x27\"]? literal\.',
        r'^"is not" with (?:an? )?[\x27\"]?int[\x27\"]? literal\.',
    ):
        warnings.filterwarnings("ignore", message=message,
                                category=SyntaxWarning, module=r"^(?:iterdir(?:\..*)?|.*[/\\]iterdir[/\\]__init__)$")
