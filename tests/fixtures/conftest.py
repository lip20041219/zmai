"""Prevent pytest from collecting test files inside fixture project directories."""

collect_ignore_glob = ["*/project/*.py", "*/project/**/*.py"]
