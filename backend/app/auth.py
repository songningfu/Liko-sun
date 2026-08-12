from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class CurrentUser:
    """Server-verified identity. The public API must never trust a client-supplied user id."""

    user_id: str | None = None
    authenticated: bool = False
    memory_consent: bool = False

    @property
    def can_use_long_term_memory(self) -> bool:
        return bool(self.authenticated and self.user_id and self.memory_consent)


def get_current_user() -> CurrentUser:
    """Return a guest until the real login/session service is connected."""

    return CurrentUser()
