class OutcraftError(Exception):
    """A safe, actionable failure without credentials or response bodies."""


class AuthenticationRequired(OutcraftError):
    """The pre-operation session check needs browser authentication."""
