"""Repository utility scripts.

A package (rather than a loose directory) so `scripts.build_skills_pack` is an
ordinary import for both mypy and the tests, instead of relying on namespace-package
resolution and pytest's rootdir injection.
"""
