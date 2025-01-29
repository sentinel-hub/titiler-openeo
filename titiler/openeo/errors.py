"""titiler.openeo errors."""

from starlette import status


class OpenEOException(Exception):
    """General Error."""

    pass


class ProcessParameterMissing(OpenEOException):
    """Invalid Parameters."""

    pass


class NoDataAvailable(OpenEOException):
    """There is no data available for the given extents."""

    pass


class InvalidProcessGraph(OpenEOException):
    """The process graph is invalid."""

    pass


class TemporalExtentEmpty(OpenEOException):
    """The temporal extent is empty. The second instant in time must always be greater/later than the first instant in time."""

    pass


DEFAULT_STATUS_CODES = {
    NoDataAvailable: status.HTTP_404_NOT_FOUND,
    InvalidProcessGraph: status.HTTP_400_BAD_REQUEST,
}
