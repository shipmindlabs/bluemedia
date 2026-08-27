"""Typed models for the transaction endpoint.

The specification names its elements in a casing that is awkward in Python and
not consistent with itself -- ``redirecturl`` sits in a document otherwise
written in camelCase -- so every attribute carries the specification's name in
its field metadata, once, and nowhere else.

Parsing is strict, because a payment document is untrusted input: an element the
model does not declare, an element sent twice, a required element left out or
left empty, all raise instead of producing a half-filled object that would fail
later and further away, at the digest.

Values stay text from the wire to the digest. The gateway signs the exact
characters it sent, so ``10.90`` must not become a number that renders back as
``10.9``.
"""

from __future__ import annotations

import re
from dataclasses import MISSING, dataclass, field, fields, replace
from typing import Any, ClassVar, TypeVar
from xml.etree import ElementTree as ET

from bluemedia.signing import (
    DEFAULT_ALGORITHM,
    DEFAULT_SEPARATOR,
    TRANSACTION_FIELD_ORDER,
    TRANSACTION_INIT_FIELD_ORDER,
)
from bluemedia.signing import sign as _sign
from bluemedia.signing import verify as _verify

__all__ = [
    "DuplicateElementError",
    "MalformedXMLError",
    "MissingElementError",
    "TransactionInit",
    "TransactionStart",
    "UnexpectedRootError",
    "UnknownElementError",
    "XMLParseError",
]


class XMLParseError(ValueError):
    """A payload could not be read as the document a model expects."""


class MalformedXMLError(XMLParseError):
    """The payload is not well-formed XML."""


class UnexpectedRootError(XMLParseError):
    """The document is well-formed but is not the one being parsed."""

    def __init__(self, found: str, expected: str) -> None:
        self.found = found
        self.expected = expected
        super().__init__(f"expected a <{expected}> document, got <{found}>")


class UnknownElementError(XMLParseError):
    """The document carries an element the model does not declare."""

    def __init__(self, name: str) -> None:
        self.name = name
        super().__init__(f"<{name}> is not part of this document")


class DuplicateElementError(XMLParseError):
    """One element appeared twice, so its value is ambiguous."""

    def __init__(self, name: str) -> None:
        self.name = name
        super().__init__(f"<{name}> appeared more than once")


class MissingElementError(XMLParseError):
    """A required element is absent or carries no text."""

    def __init__(self, names: Any) -> None:
        self.names = tuple(sorted(names))
        super().__init__(
            "required elements missing or empty: " + ", ".join(self.names)
        )


_DECLARATION = re.compile(r"^\s*<\?xml[^>]*\?>")


def _parse(source: str | bytes) -> ET.Element:
    # Text has already been decoded, so its encoding declaration is spent;
    # ElementTree refuses to parse a string that still carries one.
    payload = _DECLARATION.sub("", source, count=1) if isinstance(source, str) else source
    try:
        return ET.fromstring(payload)
    except ET.ParseError as exc:
        raise MalformedXMLError(str(exc)) from exc


def _element(spec: str) -> Any:
    """Declare a required element under its specification name."""
    return field(metadata={"spec": spec})


def _optional(spec: str) -> Any:
    """Declare an optional element under its specification name."""
    return field(default=None, metadata={"spec": spec})


_D = TypeVar("_D", bound="_Document")


class _Document:
    """XML reading and writing shared by the transaction messages."""

    __slots__ = ()

    XML_ROOT: ClassVar[str] = "transaction"
    FIELD_ORDER: ClassVar[tuple[str, ...]] = ()

    @classmethod
    def from_xml(cls: type[_D], source: str | bytes | ET.Element) -> _D:
        """Parse a document into a model, refusing anything unexpected.

        Element names match regardless of case, surrounding whitespace is
        stripped, and an element with no text counts as absent.
        """
        root = source if isinstance(source, ET.Element) else _parse(source)
        if root.tag != cls.XML_ROOT:
            raise UnexpectedRootError(root.tag, cls.XML_ROOT)

        declared = {f.metadata["spec"].lower(): f for f in fields(cls)}
        values: dict[str, str | None] = {}
        for child in root:
            declaration = declared.get(child.tag.strip().lower())
            if declaration is None:
                raise UnknownElementError(child.tag)
            if len(child):
                raise XMLParseError(
                    f"<{child.tag}> must hold text, not further elements"
                )
            if declaration.name in values:
                raise DuplicateElementError(child.tag)
            values[declaration.name] = (child.text or "").strip() or None

        missing = [
            f.metadata["spec"]
            for f in fields(cls)
            if f.default is MISSING and values.get(f.name) is None
        ]
        if missing:
            raise MissingElementError(missing)
        return cls(**{name: value for name, value in values.items() if value is not None})

    def to_xml(self) -> str:
        """Serialise the model back into the document the gateway reads."""
        root = ET.Element(self.XML_ROOT)
        for f in fields(self):
            value = getattr(self, f.name)
            if value is not None:
                ET.SubElement(root, f.metadata["spec"]).text = value
        return ET.tostring(root, encoding="unicode")

    def to_fields(self) -> dict[str, str]:
        """Return the set values keyed by their specification names."""
        return {
            f.metadata["spec"]: getattr(self, f.name)
            for f in fields(self)
            if getattr(self, f.name) is not None
        }

    def sign(
        self,
        key: str,
        *,
        separator: str = DEFAULT_SEPARATOR,
        algorithm: str = DEFAULT_ALGORITHM,
    ) -> str:
        """Return the digest this message belongs to be signed with."""
        return _sign(
            self.to_fields(),
            self.FIELD_ORDER,
            key,
            separator=separator,
            algorithm=algorithm,
        )

    def signed(
        self: _D,
        key: str,
        *,
        separator: str = DEFAULT_SEPARATOR,
        algorithm: str = DEFAULT_ALGORITHM,
    ) -> _D:
        """Return a copy carrying its own digest in the hash field."""
        return replace(
            self, hash=self.sign(key, separator=separator, algorithm=algorithm)
        )

    def verify(
        self,
        key: str,
        *,
        separator: str = DEFAULT_SEPARATOR,
        algorithm: str = DEFAULT_ALGORITHM,
    ) -> bool:
        """Check the digest the message carries; no digest means unverified."""
        return _verify(
            self.to_fields(),
            self.FIELD_ORDER,
            key,
            separator=separator,
            algorithm=algorithm,
        )


@dataclass(frozen=True, slots=True, kw_only=True)
class TransactionStart(_Document):
    """A transaction request sent to the gateway.

    Only the service, the order and the amount are required; everything else is
    optional and simply stays out of the document and out of the digest.
    """

    XML_ROOT: ClassVar[str] = "transaction"
    FIELD_ORDER: ClassVar[tuple[str, ...]] = TRANSACTION_FIELD_ORDER

    service_id: str = _element("serviceID")
    order_id: str = _element("orderID")
    amount: str = _element("amount")
    description: str | None = _optional("description")
    gateway_id: str | None = _optional("gatewayID")
    currency: str | None = _optional("currency")
    customer_email: str | None = _optional("customerEmail")
    customer_nrb: str | None = _optional("customerNRB")
    tax_country: str | None = _optional("taxCountry")
    customer_ip: str | None = _optional("customerIP")
    title: str | None = _optional("title")
    validity_time: str | None = _optional("validityTime")
    link_validity_time: str | None = _optional("linkValidityTime")
    authorization_code: str | None = _optional("authorizationCode")
    screen_type: str | None = _optional("screenType")
    blik_uid_key: str | None = _optional("blikUIDKey")
    blik_uid_label: str | None = _optional("blikUIDLabel")
    blik_am_key: str | None = _optional("blikAMKey")
    blik_alias_key: str | None = _optional("blikAliasKey")
    blik_alias_label: str | None = _optional("blikAliasLabel")
    return_url: str | None = _optional("returnURL")
    default_regulation_acceptance_state: str | None = _optional(
        "defaultRegulationAcceptanceState"
    )
    default_regulation_acceptance_id: str | None = _optional(
        "defaultRegulationAcceptanceID"
    )
    default_regulation_acceptance_time: str | None = _optional(
        "defaultRegulationAcceptanceTime"
    )
    receiver_nrb: str | None = _optional("receiverNRB")
    receiver_name: str | None = _optional("receiverName")
    receiver_address: str | None = _optional("receiverAddress")
    recurring_acceptance_state: str | None = _optional("recurringAcceptanceState")
    recurring_action: str | None = _optional("recurringAction")
    client_hash: str | None = _optional("clientHash")
    payment_token: str | None = _optional("paymentToken")
    payment_token_expiration_date: str | None = _optional(
        "paymentTokenExpirationDate"
    )
    hash: str | None = _optional("hash")


@dataclass(frozen=True, slots=True, kw_only=True)
class TransactionInit(_Document):
    """The gateway's answer to a transaction request.

    ``remote_id`` is the gateway's own identifier for the payment and
    ``redirect_url`` is where the payer has to be sent; the digest is optional
    here only so that an unsigned answer is something to reject rather than
    something that fails to parse.
    """

    XML_ROOT: ClassVar[str] = "transaction"
    FIELD_ORDER: ClassVar[tuple[str, ...]] = TRANSACTION_INIT_FIELD_ORDER

    service_id: str = _element("serviceID")
    order_id: str = _element("orderID")
    remote_id: str = _element("remoteID")
    amount: str = _element("amount")
    currency: str = _element("currency")
    gateway_id: str | None = _optional("gatewayID")
    payment_date: str | None = _optional("paymentDate")
    payment_status: str = _element("paymentStatus")
    redirect_url: str | None = _optional("redirecturl")
    hash: str | None = _optional("hash")
