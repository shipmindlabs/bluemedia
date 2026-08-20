"""Building and hashing the exact string that Blue Media signs.

The gateway does not sign a serialised document. It signs the *values* of a
fixed set of fields, joined by a separator, taken in the order declared by the
specification -- which is neither document order nor alphabetical order. Get
the order wrong and every digest is wrong, so the order lives here as data and
is pinned down by table-driven tests.

The digest is the hash of those values with the shared key appended as one more
part. XML and transport belong elsewhere.
"""

from __future__ import annotations

import hashlib
import hmac
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

__all__ = [
    "DEFAULT_ALGORITHM",
    "DEFAULT_SEPARATOR",
    "DuplicateFieldError",
    "HASH_FIELD_NAMES",
    "ITN_FIELD_ORDER",
    "TRANSACTION_FIELD_ORDER",
    "UnknownFieldError",
    "hash_values",
    "ordered_values",
    "sign",
    "string_to_sign",
    "verify",
]

DEFAULT_SEPARATOR = "|"

#: Blue Media configures the digest algorithm per service; SHA-256 is the usual
#: setting and the only one this client defaults to.
DEFAULT_ALGORITHM = "sha256"

#: Order of a transaction request sent to the gateway.
TRANSACTION_FIELD_ORDER: tuple[str, ...] = (
    "ServiceID",
    "OrderID",
    "Amount",
    "Description",
    "GatewayID",
    "Currency",
    "CustomerEmail",
    "CustomerNRB",
    "TaxCountry",
    "CustomerIP",
    "Title",
    "ValidityTime",
    "LinkValidityTime",
    "AuthorizationCode",
    "ScreenType",
    "BlikUIDKey",
    "BlikUIDLabel",
    "BlikAMKey",
    "BlikAliasKey",
    "BlikAliasLabel",
    "ReturnURL",
    "DefaultRegulationAcceptanceState",
    "DefaultRegulationAcceptanceID",
    "DefaultRegulationAcceptanceTime",
    "ReceiverNRB",
    "ReceiverName",
    "ReceiverAddress",
    "RecurringAcceptanceState",
    "RecurringAction",
    "ClientHash",
    "PaymentToken",
    "PaymentTokenExpirationDate",
)

#: Order of an ITN, the payment notification the gateway pushes to the shop.
#: The names repeat the transaction ones in camelCase and in a different order.
ITN_FIELD_ORDER: tuple[str, ...] = (
    "serviceID",
    "orderID",
    "remoteID",
    "amount",
    "currency",
    "gatewayID",
    "paymentDate",
    "paymentStatus",
    "paymentStatusDetails",
    "addressIP",
    "title",
    "customerNumber",
    "customerEmail",
    "customerNRB",
    "customerData",
    "verificationStatus",
    "invoiceNumber",
    "startAmount",
    "endAmount",
    "recurringData",
)

#: The digest travels inside the document it signs, so it is never an input.
HASH_FIELD_NAMES = frozenset({"hash"})


class UnknownFieldError(ValueError):
    """A field was supplied that the declared order does not mention."""

    def __init__(self, names: Iterable[str]) -> None:
        self.names = tuple(sorted(names))
        super().__init__(
            "fields missing from the declared order: " + ", ".join(self.names)
        )


class DuplicateFieldError(ValueError):
    """Two keys differ only in case, so their place in the string is unclear."""

    def __init__(self, name: str) -> None:
        self.name = name
        super().__init__(f"field {name!r} was supplied more than once")


def _key(name: str) -> str:
    return name.strip().lower()


def _flatten(value: Any) -> list[str]:
    """Expand one field value into the parts it contributes to the string."""
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, (bytes, bytearray)):
        raise TypeError("field values must be text, not bytes")
    if isinstance(value, Mapping):
        return [part for item in value.values() for part in _flatten(item)]
    if isinstance(value, Iterable):
        return [part for item in value for part in _flatten(item)]
    return [str(value)]


def ordered_values(fields: Mapping[str, Any], order: Iterable[str]) -> list[str]:
    """Return the values of ``fields`` laid out in the order given by ``order``.

    Names match case-insensitively, missing and empty values drop out, a field
    holding several values expands in place, and a hash field is ignored. A
    field outside the declared order raises: silently dropping it would produce
    a plausible-looking string that the gateway rejects.
    """
    order = tuple(order)
    supplied: dict[str, Any] = {}
    for name, value in fields.items():
        key = _key(name)
        if key in HASH_FIELD_NAMES:
            continue
        if key in supplied:
            raise DuplicateFieldError(name)
        supplied[key] = value

    unknown = set(supplied) - {_key(name) for name in order}
    if unknown:
        raise UnknownFieldError(unknown)

    values: list[str] = []
    for name in order:
        key = _key(name)
        if key in supplied:
            values.extend(part for part in _flatten(supplied[key]) if part != "")
    return values


def string_to_sign(
    fields: Mapping[str, Any],
    order: Iterable[str],
    *,
    separator: str = DEFAULT_SEPARATOR,
) -> str:
    """Join the ordered values into the string the gateway hashes."""
    return separator.join(ordered_values(fields, order))


def hash_values(
    values: Sequence[str],
    key: str,
    *,
    separator: str = DEFAULT_SEPARATOR,
    algorithm: str = DEFAULT_ALGORITHM,
) -> str:
    """Hash already ordered values with the shared key as the final part."""
    if not key:
        raise ValueError("the shared key must not be empty")
    payload = separator.join([*values, key])
    return hashlib.new(algorithm, payload.encode("utf-8")).hexdigest()


def sign(
    fields: Mapping[str, Any],
    order: Iterable[str],
    key: str,
    *,
    separator: str = DEFAULT_SEPARATOR,
    algorithm: str = DEFAULT_ALGORITHM,
) -> str:
    """Return the lowercase hex digest of ``fields`` signed with ``key``."""
    return hash_values(
        ordered_values(fields, order),
        key,
        separator=separator,
        algorithm=algorithm,
    )


def _supplied_digest(fields: Mapping[str, Any]) -> str | None:
    for name, value in fields.items():
        if _key(name) in HASH_FIELD_NAMES:
            return value if isinstance(value, str) else None
    return None


def verify(
    fields: Mapping[str, Any],
    order: Iterable[str],
    key: str,
    *,
    digest: str | None = None,
    separator: str = DEFAULT_SEPARATOR,
    algorithm: str = DEFAULT_ALGORITHM,
) -> bool:
    """Check the digest of an incoming message.

    The digest is taken from ``digest`` when given, otherwise from the hash
    field of ``fields``. A message without one is unverified, not an error, so
    a caller can treat every rejection the same way.
    """
    candidate = digest if digest is not None else _supplied_digest(fields)
    if candidate is None:
        return False
    expected = sign(
        fields, order, key, separator=separator, algorithm=algorithm
    )
    # Constant time: the comparison runs against an attacker-supplied digest.
    return hmac.compare_digest(
        expected.encode("ascii"), candidate.strip().lower().encode("utf-8")
    )
