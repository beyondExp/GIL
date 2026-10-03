from __future__ import annotations

import hashlib
import hmac
import secrets

from gil.core.config import GilSettings, Role


class AuthError(PermissionError):
    pass


def _digest(token: str, pepper: str) -> str:
    material = f"{pepper}:{token}".encode("utf-8")
    return hashlib.sha256(material).hexdigest()


class AuthContext:
    def __init__(self, settings: GilSettings | None = None):
        self.settings = settings or GilSettings()

    def resolve(self, token: str | None, *, hardware: bool) -> Role:
        supplied = (token or "").strip()
        operator = (self.settings.operator_token or "").strip()
        autonomy = (self.settings.autonomy_token or "").strip()
        if not hardware and not operator and not autonomy:
            return Role.operator
        if hardware and not operator and not autonomy:
            raise AuthError("Hardware mode requires GIL_OPERATOR_TOKEN or GIL_AUTONOMY_TOKEN.")
        if not supplied:
            if hardware:
                raise AuthError("Hardware mode requires a valid operator or autonomy token.")
            return Role.observer
        if autonomy and hmac.compare_digest(_digest(supplied, "autonomy"), _digest(autonomy, "autonomy")):
            return Role.autonomy
        if operator and hmac.compare_digest(_digest(supplied, "operator"), _digest(operator, "operator")):
            return Role.operator
        if hardware:
            raise AuthError("Hardware mode requires a valid operator or autonomy token.")
        return Role.observer

    def require(self, role: Role, minimum: Role) -> None:
        order = {Role.observer: 0, Role.operator: 1, Role.autonomy: 2}
        if order[role] < order[minimum]:
            raise AuthError(f"Role {role.value} is below required {minimum.value}.")


def new_token() -> str:
    return secrets.token_urlsafe(32)
