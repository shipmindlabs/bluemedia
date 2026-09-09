"""BLIK: the authorization code flow and the gateway it runs on.

BLIK is not a second protocol. It is a gateway -- ``gatewayID`` 509 -- so a
BLIK payment is the ordinary transaction request with one field more: the six
digits the payer reads out of a banking application. Sent along, the gateway
authorises the payment itself and answers with a payment status rather than a
URL to send anybody to; left out, the request behaves like a card one and the
payer types the code on the gateway's own screen.

The code is signed where ``AuthorizationCode`` sits in the declared order, like
any other field, which is why the grouping a payer copies with it has to go
before the digest is taken.
"""

from __future__ import annotations

import re
from typing import Any

from bluemedia.models import TransactionStart

__all__ = [
    "AUTHORIZATION_CODE_LENGTH",
    "BLIK_GATEWAY_ID",
    "AuthorizationCodeError",
    "authorization_code",
    "blik_transaction",
]

#: The gateway id BLIK is reached under.
BLIK_GATEWAY_ID = "509"

#: How many digits the payer's code has.
AUTHORIZATION_CODE_LENGTH = 6

_CODE = re.compile(rf"\A[0-9]{{{AUTHORIZATION_CODE_LENGTH}}}\Z")
_GROUPING = str.maketrans("", "", " \t\n\r-")


class AuthorizationCodeError(ValueError):
    """The code is not the digits BLIK asks the payer for."""

    def __init__(self, value: Any) -> None:
        self.value = value
        super().__init__(
            f"a BLIK authorization code is {AUTHORIZATION_CODE_LENGTH} digits, "
            f"not {value!r}"
        )


def authorization_code(value: Any) -> str:
    """Return the code the way the gateway reads it: the digits and nothing else.

    Grouping copied along with the code -- ``777 123``, ``777-123`` -- drops
    out, because the value is signed and sent exactly as given and the gateway
    would not recognise the separators. Anything else raises here rather than
    one round trip later.
    """
    if not isinstance(value, str):
        raise AuthorizationCodeError(value)
    digits = value.translate(_GROUPING)
    if not _CODE.match(digits):
        raise AuthorizationCodeError(value)
    return digits


def blik_transaction(
    *,
    code: str | None = None,
    gateway_id: str = BLIK_GATEWAY_ID,
    **fields: Any,
) -> TransactionStart:
    """Build an unsigned BLIK transaction request.

    With ``code``, the gateway charges the payer straight away and its answer
    reports a status; without one, the answer redirects to the screen where the
    payer enters the code. The remaining fields are a transaction's ordinary
    ones, the BLIK alias fields among them.
    """
    if code is not None:
        fields["authorization_code"] = authorization_code(code)
    return TransactionStart(gateway_id=gateway_id, **fields)
