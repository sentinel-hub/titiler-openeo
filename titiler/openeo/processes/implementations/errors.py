"""titiler.openeo processes implementation errors."""


class OpenEOException(Exception):
    """General Error."""

    pass


class ProcessParameterMissing(OpenEOException):
    """Invalid Parameters."""

    pass
