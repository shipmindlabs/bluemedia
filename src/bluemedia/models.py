"""Typed models for the transaction endpoint.

The specification names its elements in a casing that is awkward in Python and
not consistent with itself -- ``redirecturl`` sits in a document otherwise
written in camelCase -- so every attribute carries the specification's name in
its field metadata, once, and nowhere else.

Parsing is strict, because a payment document is untrusted input: an element the
model does not declare, an element sent twice, a required element left out or
left empty, all raise instead of producing a half-filled object that would fail
later and further away, at the digest.

A few elements hold elements rather than text. ``_nested`` declares one as a
document of its own, which parses and serialises like any other and whose parts
take the single place its element has in the signing order.

The transaction root carries two different documents back: the answer to a
started payment and the refusal of one. They are separate models, told apart by
the element only a refusal has, so a refusal is read and verified rather than
reported as an element the answer does not declare.

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
    TRANSACTION_REFUSAL_FIELD_ORDER,
)
from bluemedia.signing import ordered_values as _ordered_values
from bluemedia.signing import sign as _sign
from bluemedia.signing import verify as _verify

__all__ = [
    "DuplicateElementError",
    "MalformedXMLError",
    "MissingElementError",
    "TransactionInit",
    "TransactionRefusal",
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


def _nested(spec: str, model: type[_Document]) -> Any:
    """Declare an optional element that holds a document rather than text."""
    return field(default=None, metadata={"spec": spec, "model": model})


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
        stripped, and an element with no text counts as absent. An element the
        model declares as a document of its own is read by that model, just as
        strictly.
        """
        root = source if isinstance(source, ET.Element) else _parse(source)
        if root.tag.strip().lower() != cls.XML_ROOT.lower():
            raise UnexpectedRootError(root.tag, cls.XML_ROOT)

        declared = {f.metadata["spec"].lower(): f for f in fields(cls)}
        values: dict[str, Any] = {}
        for child in root:
            declaration = declared.get(child.tag.strip().lower())
            if declaration is None:
                raise UnknownElementError(child.tag)
            model = declaration.metadata.get("model")
            if model is None and len(child):
                raise XMLParseError(
                    f"<{child.tag}> must hold text, not further elements"
                )
            if declaration.name in values:
                raise DuplicateElementError(child.tag)
            values[declaration.name] = (
                model.from_xml(child)
                if model is not None
                else (child.text or "").strip() or None
            )

        missing = [
            f.metadata["spec"]
            for f in fields(cls)
            if f.default is MISSING and values.get(f.name) is None
        ]
        if missing:
            raise MissingElementError(missing)
        return cls(**{name: value for name, value in values.items() if value is not None})

    def to_element(self, tag: str | None = None) -> ET.Element:
        """Build the element this model is written as."""
        root = ET.Element(tag or self.XML_ROOT)
        for f in fields(self):
            value = getattr(self, f.name)
            if value is None:
                continue
            if isinstance(value, _Document):
                root.append(value.to_element(f.metadata["spec"]))
            else:
                ET.SubElement(root, f.metadata["spec"]).text = value
        return root

    def to_xml(self) -> str:
        """Serialise the model back into the document the gateway reads."""
        return ET.tostring(self.to_element(), encoding="unicode")

    def to_fields(self) -> dict[str, Any]:
        """Return the set values keyed by their specification names.

        A nested document contributes its own ordered parts, which the signing
        string then expands in the place its element has.
        """
        supplied: dict[str, Any] = {}
        for f in fields(self):
            value = getattr(self, f.name)
            if value is None:
                continue
            supplied[f.metadata["spec"]] = (
                value.values() if isinstance(value, _Document) else value
            )
        return supplied

    def values(self) -> list[str]:
        """The parts this message's digest is taken over, in order."""
        return _ordered_values(self.to_fields(), self.FIELD_ORDER)

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


@dataclass(frozen=True, slots=True, kw_only=True)
class TransactionRefusal(_Document):
    """The gateway's refusal to start a transaction.

    It shares the transaction root with the answer but reports an outcome for
    the order rather than a payment: ``confirmation`` says it was not taken and
    ``reason`` says why. There is no ``remoteID`` and nowhere to send a payer,
    so this is a document of its own rather than an answer with elements
    missing, signed in its own order and verified like any other.
    """

    XML_ROOT: ClassVar[str] = "transaction"
    FIELD_ORDER: ClassVar[tuple[str, ...]] = TRANSACTION_REFUSAL_FIELD_ORDER

    #: The element no answer carries, which is what tells the two apart.
    MARKER: ClassVar[str] = "confirmation"

    service_id: str = _element("serviceID")
    order_id: str = _element("orderID")
    confirmation: str = _element("confirmation")
    reason: str | None = _optional("reason")
    hash: str | None = _optional("hash")

    @classmethod
    def refuses(cls, source: str | bytes | ET.Element) -> bool:
        """Whether a transaction document is a refusal rather than an answer."""
        root = source if isinstance(source, ET.Element) else _parse(source)
        if root.tag.strip().lower() != cls.XML_ROOT.lower():
            return False
        return any(child.tag.strip().lower() == cls.MARKER for child in root)
