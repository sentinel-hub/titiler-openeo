"""Test titiler.openeo version."""

from titiler.openeo import __version__


def test_version_exists():
    """Test that __version__ is defined."""
    assert __version__ is not None
    assert isinstance(__version__, str)
    assert len(__version__) > 0


def test_version_format():
    """Test that __version__ follows expected format."""
    # Version should be a string that contains at least a digit
    assert any(char.isdigit() for char in __version__)
    # Version should contain a dot (semantic versioning)
    assert "." in __version__
