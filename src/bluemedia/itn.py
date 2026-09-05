"""ITN: the notification the gateway pushes, and the answer it expects back.

A notification arrives base64-encoded in a form field and carries every payment
the gateway has to report in one ``<transactionList>``. ``serviceID`` is written
once, on the list, but signs each notification inside it, so it is filled into
every one of them while parsing and each notification verifies on its own.

The answer belongs to the same request: one ``CONFIRMED`` or ``NOTCONFIRMED``
per order, signed with the same key. A notification whose digest does not check
out is answered ``NOTCONFIRMED`` rather than dropped -- an unanswered
notification is only retried, while a forged one must never be booked.
"""

from __future__ import annotations

import base64
import binascii
from collections.abc import Callable, Iterator
from dataclasses import dataclass, fields, replace
from typing import ClassVar
from xml.etree import ElementTree as ET

from bluemedia.models import (
    DuplicateElementError,
    MissingElementError,
    UnexpectedRootError,
    UnknownElementError,
    XMLParseError,
    _Document,
    _element,
    _optional,
    _parse,
)
from bluemedia.signing import (
    DEFAULT_ALGORITHM,
    DEFAULT_SEPARATOR,
    ITN_FIELD_ORDER,
    hash_values,
)

__all__ = [
    "CONFIRMATION_ROOT",
    "CONFIRMED",
    "ITN_ROOT",
    "NOTCONFIRMED",
    "Base64DecodeError",
    "Confirmation",
    "ConfirmationList",
    "Notification",
    "NotificationList",
    "decode_base64",
]

#: The shop took the payment and does not want to hear about it again.
CONFIRMED = "CONFIRMED"

#: The shop did not take it; the gateway sends the notification again.
NOTCONFIRMED = "NOTCONFIRMED"

_ANSWERS = frozenset({CONFIRMED, NOTCONFIRMED})

ITN_ROOT = "transactionList"
CONFIRMATION_ROOT = "confirmationList"


class Base64DecodeError(XMLParseError):
    """The notification did not survive base64 decoding."""


def decode_base64(payload: str | bytes) -> bytes:
    """Decode the base64 a notification travels in.

    Line breaks and surrounding whitespace drop out, because form fields carry
    them; anything else outside the alphabet is an error rather than something
    to skip, since skipping it would hand a truncated document to the parser.
    """
    try:
        data = payload.encode("ascii") if isinstance(payload, str) else bytes(payload)
        return base64.b64decode(b"".join(data.split()), validate=True)
    except (UnicodeEncodeError, binascii.Error, ValueError) as exc:
        raise Base64DecodeError(f"the payload is not base64: {exc}") from exc


@dataclass(frozen=True, slots=True, kw_only=True)
class Notification(_Document):
    """One payment the gateway reports, with the digest that vouches for it.

    ``service_id`` signs the notification but is written on the enclosing list,
    so parsing a list fills it in here.
    """

    XML_ROOT: ClassVar[str] = "transaction"
    FIELD_ORDER: ClassVar[tuple[str, ...]] = ITN_FIELD_ORDER

    service_id: str = _element("serviceID")
    order_id: str = _element("orderID")
    remote_id: str = _element("remoteID")
    amount: str = _element("amount")
    currency: str = _element("currency")
    gateway_id: str | None = _optional("gatewayID")
    payment_date: str = _element("paymentDate")
    payment_status: str = _element("paymentStatus")
    payment_status_details: str | None = _optional("paymentStatusDetails")
    address_ip: str | None = _optional("addressIP")
    title: str | None = _optional("title")
    customer_number: str | None = _optional("customerNumber")
    customer_email: str | None = _optional("customerEmail")
    customer_nrb: str | None = _optional("customerNRB")
    customer_data: str | None = _optional("customerData")
    verification_status: str | None = _optional("verificationStatus")
    invoice_number: str | None = _optional("invoiceNumber")
    start_amount: str | None = _optional("startAmount")
    end_amount: str | None = _optional("endAmount")
    recurring_data: str | None = _optional("recurringData")
    hash: str | None = _optional("hash")


@dataclass(frozen=True, slots=True, kw_only=True)
class Confirmation:
    """What the shop answers about one order."""

    order_id: str
    confirmation: str = CONFIRMED

    def __post_init__(self) -> None:
        if self.confirmation not in _ANSWERS:
            raise ValueError(
                f"an answer is {CONFIRMED} or {NOTCONFIRMED}, "
                f"not {self.confirmation!r}"
            )


@dataclass(frozen=True, slots=True, kw_only=True)
class ConfirmationList:
    """The document the shop returns, signed in turn.

    Its digest runs over the service id followed by each order and its answer,
    so the gateway can tell the shop's reply from a replayed one.
    """

    service_id: str
    confirmations: tuple[Confirmation, ...] = ()
    hash: str | None = None

    def values(self) -> list[str]:
        """The parts the reply's digest is taken over, in order."""
        parts = [self.service_id]
        for entry in self.confirmations:
            parts.append(entry.order_id)
            parts.append(entry.confirmation)
        return parts

    def sign(
        self,
        key: str,
        *,
        separator: str = DEFAULT_SEPARATOR,
        algorithm: str = DEFAULT_ALGORITHM,
    ) -> str:
        """Return the digest this reply belongs to be signed with."""
        return hash_values(
            self.values(), key, separator=separator, algorithm=algorithm
        )

    def signed(
        self,
        key: str,
        *,
        separator: str = DEFAULT_SEPARATOR,
        algorithm: str = DEFAULT_ALGORITHM,
    ) -> ConfirmationList:
        """Return a copy carrying its own digest in the hash field."""
        return replace(
            self, hash=self.sign(key, separator=separator, algorithm=algorithm)
        )

    def to_xml(self) -> str:
        """Serialise the reply into the document the gateway reads."""
        root = ET.Element(CONFIRMATION_ROOT)
        ET.SubElement(root, "serviceID").text = self.service_id
        container = ET.SubElement(root, "transactionsConfirmations")
        for entry in self.confirmations:
            element = ET.SubElement(container, "transactionConfirmed")
            ET.SubElement(element, "orderID").text = entry.order_id
            ET.SubElement(element, "confirmation").text = entry.confirmation
        if self.hash is not None:
            ET.SubElement(root, "hash").text = self.hash
        return ET.tostring(root, encoding="unicode")


@dataclass(frozen=True, slots=True, kw_only=True)
class NotificationList:
    """One ITN: the service it belongs to and the payments it reports."""

    service_id: str
    notifications: tuple[Notification, ...] = ()
    hash: str | None = None

    @classmethod
    def from_base64(cls, payload: str | bytes) -> NotificationList:
        """Decode the form field the gateway posts and parse what it holds."""
        return cls.from_xml(decode_base64(payload))

    @classmethod
    def from_xml(cls, source: str | bytes | ET.Element) -> NotificationList:
        """Parse a decoded notification, refusing anything unexpected."""
        root = source if isinstance(source, ET.Element) else _parse(source)
        if root.tag != ITN_ROOT:
            raise UnexpectedRootError(root.tag, ITN_ROOT)

        service_id: str | None = None
        container: ET.Element | None = None
        digest: str | None = None
        seen: set[str] = set()
        for child in root:
            name = child.tag.strip().lower()
            if name in seen:
                raise DuplicateElementError(child.tag)
            seen.add(name)
            if name == "serviceid":
                service_id = (child.text or "").strip() or None
            elif name == "transactions":
                container = child
            elif name == "hash":
                digest = (child.text or "").strip() or None
            else:
                raise UnknownElementError(child.tag)

        missing = [
            spec
            for spec, value in (("serviceID", service_id), ("transactions", container))
            if value is None
        ]
        if missing:
            raise MissingElementError(missing)

        notifications = tuple(
            Notification.from_xml(_with_service_id(child, service_id))
            for child in container
        )
        return cls(
            service_id=service_id, notifications=notifications, hash=digest
        )

    def to_xml(self) -> str:
        """Serialise the notification back the way the gateway wrote it."""
        root = ET.Element(ITN_ROOT)
        ET.SubElement(root, "serviceID").text = self.service_id
        container = ET.SubElement(root, "transactions")
        for notification in self.notifications:
            container.append(_transaction_element(notification, self.service_id))
        if self.hash is not None:
            ET.SubElement(root, "hash").text = self.hash
        return ET.tostring(root, encoding="unicode")

    def verified(
        self,
        key: str,
        *,
        separator: str = DEFAULT_SEPARATOR,
        algorithm: str = DEFAULT_ALGORITHM,
    ) -> tuple[Notification, ...]:
        """The notifications whose digest the shared key produces."""
        return tuple(
            notification
            for notification in self.notifications
            if notification.verify(key, separator=separator, algorithm=algorithm)
        )

    def confirm(
        self,
        key: str,
        *,
        accept: Callable[[Notification], bool] | None = None,
        separator: str = DEFAULT_SEPARATOR,
        algorithm: str = DEFAULT_ALGORITHM,
    ) -> ConfirmationList:
        """Answer every notification and sign the answer.

        A notification is confirmed when its digest verifies and ``accept``, if
        given, returns true for it: booking the payment is the shop's business,
        vouching for it is this library's. ``accept`` is only asked about
        notifications that already verify, so a forged one cannot slip through
        a callback that trusts what it is handed.
        """
        answers = []
        for notification in self.notifications:
            taken = notification.verify(
                key, separator=separator, algorithm=algorithm
            ) and (accept is None or bool(accept(notification)))
            answers.append(
                Confirmation(
                    order_id=notification.order_id,
                    confirmation=CONFIRMED if taken else NOTCONFIRMED,
                )
            )
        return ConfirmationList(
            service_id=self.service_id, confirmations=tuple(answers)
        ).signed(key, separator=separator, algorithm=algorithm)

    def __len__(self) -> int:
        return len(self.notifications)

    def __iter__(self) -> Iterator[Notification]:
        return iter(self.notifications)

    def __getitem__(self, index: int) -> Notification:
        return self.notifications[index]


def _with_service_id(element: ET.Element, service_id: str) -> ET.Element:
    """Copy one transaction, giving it the service id of its list."""
    if element.tag != Notification.XML_ROOT:
        raise UnknownElementError(element.tag)
    copy = ET.Element(Notification.XML_ROOT)
    copy.extend(list(element))
    if not any(child.tag.strip().lower() == "serviceid" for child in element):
        ET.SubElement(copy, "serviceID").text = service_id
    return copy


def _transaction_element(notification: Notification, service_id: str) -> ET.Element:
    element = ET.Element(Notification.XML_ROOT)
    for f in fields(notification):
        value = getattr(notification, f.name)
        if value is None:
            continue
        if f.name == "service_id" and value == service_id:
            continue
        ET.SubElement(element, f.metadata["spec"]).text = value
    return element
