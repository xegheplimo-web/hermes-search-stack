"""Test package for the hermes-search-stack offline suite.

Marking ``tests/`` as a package makes pytest import these modules as
``tests.test_*`` (instead of bare ``test_*``), so the in-test ``import
test_keyless_fallback`` resolves to the project-root script of the same name
rather than the test module itself.
"""
