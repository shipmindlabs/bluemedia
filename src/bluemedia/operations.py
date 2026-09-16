"""What is done to a payment that already exists: refund, cancel, status.

The three requests share a shape. Each names a payment the gateway already
knows -- by the gateway's own ``remoteID`` or by the shop's ``orderID`` -- and
each carries a ``messageID`` the gateway echoes back, so an answer can be
matched to the call that caused it. A request naming neither payment, or
naming one with an identifier that holds nothing, raises here rather than at
the gateway, where it would apply to nothing.

A refund is partial when it names an amount and whole when it does not: the
amount is a field like any other, so leaving it out keeps it out of the
document and out of the digest, and the gateway sends back everything it took.
That the difference between the two is one absent field is also why a blank
amount is refused: an empty form field must not turn a partial refund into a
full one.

A status query is answered with the document an ITN arrives in, which
``bluemedia.itn`` already reads; only the request lives here.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import ClassVar

from bluemedia.models import _Document, _element, _optional

__all__ = [
    "CANCEL_FIELD_ORDER",
    "CANCEL_RESPONSE_FIELD_ORDER",
    "FAILURE",
    "PENDING",
    "REFUND_FIELD_ORDER",
    "REFUND_RESPONSE_FIELD_ORDER",
    "SUCCESS",
    "TRANSACTION_STATUS_FIELD_ORDER",
    "CancelRequest",
    "CancelResponse",
    "MissingReferenceError",
    "RefundAmountError",
    "RefundRequest",
    "RefundResponse",
    "StatusRequest",
    "new_message_id",
]

#: Order of a refund request. The amount sits where the specification puts it,
#: and a full refund simply leaves it out.
REFUND_FIELD_ORDER: tuple[str, ...] = (
    "ServiceID",
    "MessageID",
    "RemoteID",
    "OrderID",
    "Amount",
    "Currency",
)

#: Order of the gateway's answer to a refund request.
REFUND_RESPONSE_FIELD_ORDER: tuple[str, ...] = (
    "serviceID",
    "messageID",
    "remoteID",
    "remoteOutID",
    "orderID",
    "amount",
    "currency",
    "refundStatus",
    "refundReason",
)

#: Order of a cancellation request.
CANCEL_FIELD_ORDER: tuple[str, ...] = (
    "ServiceID",
    "MessageID",
    "RemoteID",
    "OrderID",
)

#: Order of the gateway's answer to a cancellation request.
CANCEL_RESPONSE_FIELD_ORDER: tuple[str, ...] = (
    "serviceID",
    "messageID",
    "remoteID",
    "orderID",
    "cancelStatus",
    "cancelReason",
)

#: Order of a status query. Its answer is an ITN document, so it verifies with
#: ``ITN_FIELD_ORDER`` like a pushed notification.
TRANSACTION_STATUS_FIELD_ORDER: tuple[str, ...] = (
    "ServiceID",
    "MessageID",
    "RemoteID",
    "OrderID",
)

#: The gateway carried the refund or the cancellation out.
SUCCESS = "SUCCESS"

#: It refused; the reason field says why.
FAILURE = "FAILURE"

#: It accepted the call but has not finished; the outcome arrives as an ITN.
PENDING = "PENDING"


class MissingReferenceError(ValueError):
    """The request does not say which payment it acts on."""

    def __init__(self) -> None:
        super().__init__("name the payment by remote_id or order_id")


class RefundAmountError(ValueError):
    """The refund carries an amount field that names no amount."""

    def __init__(self, value: str) -> None:
        self.value = value
        super().__init__(
            "a refund is for an amount or for the whole payment, "
            f"not {value!r}"
        )


def new_message_id() -> str:
    """Return a fresh id for one call to the gateway."""
    return uuid.uuid4().hex


def _blank(value: str | None) -> bool:
    return value is None or not value.strip()


class _Operation(_Document):
    """A request acting on a payment the gateway already knows."""

    __slots__ = ()

    remote_id: str | None
    order_id: str | None

    def __post_init__(self) -> None:
        if _blank(self.remote_id) and _blank(self.order_id):
            raise MissingReferenceError()


@dataclass(frozen=True, slots=True, kw_only=True)
class RefundRequest(_Operation):
    """A request to send a payment back, whole or in part."""

    XML_ROOT: ClassVar[str] = "transactionRefundRequest"
    FIELD_ORDER: ClassVar[tuple[str, ...]] = REFUND_FIELD_ORDER

    service_id: str = _element("serviceID")
    message_id: str = _element("messageID")
    remote_id: str | None = _optional("remoteID")
    order_id: str | None = _optional("orderID")
    amount: str | None = _optional("amount")
    currency: str | None = _optional("currency")
    hash: str | None = _optional("hash")

    def __post_init__(self) -> None:
        # slots=True rebuilds the class, which leaves the cell a zero-argument
        # super() reads pointing at the class it replaced.
        _Operation.__post_init__(self)
        if self.amount is not None and _blank(self.amount):
            raise RefundAmountError(self.amount)

    @property
    def partial(self) -> bool:
        """Whether this refund names an amount rather than the whole payment."""
        return self.amount is not None


@dataclass(frozen=True, slots=True, kw_only=True)
class RefundResponse(_Document):
    """The gateway's answer to a refund request.

    ``remote_out_id`` identifies the refund itself, which is not the identifier
    of the payment it came out of.
    """

    XML_ROOT: ClassVar[str] = "transactionRefundResponse"
    FIELD_ORDER: ClassVar[tuple[str, ...]] = REFUND_RESPONSE_FIELD_ORDER

    service_id: str = _element("serviceID")
    message_id: str = _element("messageID")
    remote_id: str | None = _optional("remoteID")
    remote_out_id: str | None = _optional("remoteOutID")
    order_id: str | None = _optional("orderID")
    amount: str | None = _optional("amount")
    currency: str | None = _optional("currency")
    refund_status: str = _element("refundStatus")
    refund_reason: str | None = _optional("refundReason")
    hash: str | None = _optional("hash")

    @property
    def succeeded(self) -> bool:
        """Whether the money is on its way back; ``PENDING`` is not yet."""
        return self.refund_status.strip().upper() == SUCCESS


@dataclass(frozen=True, slots=True, kw_only=True)
class CancelRequest(_Operation):
    """A request to call off a payment the gateway has not settled."""

    XML_ROOT: ClassVar[str] = "transactionCancelRequest"
    FIELD_ORDER: ClassVar[tuple[str, ...]] = CANCEL_FIELD_ORDER

    service_id: str = _element("serviceID")
    message_id: str = _element("messageID")
    remote_id: str | None = _optional("remoteID")
    order_id: str | None = _optional("orderID")
    hash: str | None = _optional("hash")


@dataclass(frozen=True, slots=True, kw_only=True)
class CancelResponse(_Document):
    """The gateway's answer to a cancellation request."""

    XML_ROOT: ClassVar[str] = "transactionCancelResponse"
    FIELD_ORDER: ClassVar[tuple[str, ...]] = CANCEL_RESPONSE_FIELD_ORDER

    service_id: str = _element("serviceID")
    message_id: str = _element("messageID")
    remote_id: str | None = _optional("remoteID")
    order_id: str | None = _optional("orderID")
    cancel_status: str = _element("cancelStatus")
    cancel_reason: str | None = _optional("cancelReason")
    hash: str | None = _optional("hash")

    @property
    def succeeded(self) -> bool:
        """Whether the payment is called off for good."""
        return self.cancel_status.strip().upper() == SUCCESS


@dataclass(frozen=True, slots=True, kw_only=True)
class StatusRequest(_Operation):
    """A question about where a payment stands.

    The answer is a ``transactionList``: the same document an ITN arrives in,
    read by ``NotificationList`` and verified notification by notification.
    """

    XML_ROOT: ClassVar[str] = "transactionStatusRequest"
    FIELD_ORDER: ClassVar[tuple[str, ...]] = TRANSACTION_STATUS_FIELD_ORDER

    service_id: str = _element("serviceID")
    message_id: str = _element("messageID")
    remote_id: str | None = _optional("remoteID")
    order_id: str | None = _optional("orderID")
    hash: str | None = _optional("hash")
