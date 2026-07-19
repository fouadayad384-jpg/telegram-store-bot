from __future__ import annotations

import hashlib
import hmac
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from cryptography.fernet import Fernet, InvalidToken

MONEY_QUANTUM = Decimal("0.01")


class CredentialCipher:
    """Encrypts inventory secrets before they are persisted."""

    def __init__(self, key: str) -> None:
        self._fernet = Fernet(key.encode("ascii"))
        self._fingerprint_key = hashlib.sha256(key.encode("ascii")).digest()

    def encrypt(self, value: str) -> str:
        return self._fernet.encrypt(value.encode("utf-8")).decode("ascii")

    def decrypt(self, token: str) -> str:
        try:
            return self._fernet.decrypt(token.encode("ascii")).decode("utf-8")
        except InvalidToken as exc:
            raise ValueError("Inventory credential cannot be decrypted") from exc

    def fingerprint(self, product_id: int, email: str) -> str:
        normalized = f"{product_id}:{email.strip().casefold()}".encode()
        return hmac.new(self._fingerprint_key, normalized, hashlib.sha256).hexdigest()


def parse_money(value: str | Decimal) -> Decimal:
    try:
        amount = Decimal(str(value)).quantize(MONEY_QUANTUM, rounding=ROUND_HALF_UP)
    except (InvalidOperation, ValueError) as exc:
        raise ValueError("Invalid money amount") from exc
    if not amount.is_finite():
        raise ValueError("Invalid money amount")
    return amount


def mask_display_name(name: str) -> str:
    """Masks a name while keeping just enough characters for a public feed."""

    clean = " ".join(name.strip().split()) or "مستخدم"
    if clean.startswith("@"):
        clean = clean[1:]
    length = len(clean)
    if length == 1:
        return "*"
    if length == 2:
        return f"{clean[0]}*"
    if length <= 5:
        return f"{clean[0]}{'*' * (length - 2)}{clean[-1]}"
    return f"{clean[:3]}{'*' * (length - 4)}{clean[-1]}"
