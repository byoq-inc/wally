class AdapterError(Exception):
    """Base class for expected, safe-to-report adapter failures."""


class ConfigurationError(AdapterError):
    pass


class CredentialError(AdapterError):
    pass


class AuthenticationError(AdapterError):
    pass


class RedirectError(AdapterError):
    pass


class BoundaryError(AdapterError):
    pass


class BoundsError(AdapterError):
    pass


class TransportError(AdapterError):
    pass
