from praetor import __version__


def test_package_importable():
    assert isinstance(__version__, str)
