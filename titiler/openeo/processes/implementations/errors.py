"""titiler.openeo processes implementation errors."""


class OpenEOException(Exception):
    """General Error."""

    pass


class ProcessParameterMissing(OpenEOException):
    """Invalid Parameters."""

    pass


class NoDataAvailable(OpenEOException):
    """There is no data available for the given extents."""

    pass


class TemporalExtentEmpty(OpenEOException):
    """The temporal extent is empty. The second instant in time must always be greater/later than the first instant in time."""

    pass
