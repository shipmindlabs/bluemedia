"""Typed transaction messages: strict parsing, serialisation and signing."""

from __future__ import annotations

import dataclasses

import pytest

from bluemedia import (
    TRANSACTION_FIELD_ORDER,
    TRANSACTION_INIT_FIELD_ORDER,
    DuplicateElementError,
    MalformedXMLError,
    MissingElementError,
    TransactionInit,
    TransactionStart,
    UnexpectedRootError,
    UnknownElementError,
    XMLParseError,
)

KEY = "9dfc5eb3d1b2f4e0"

RESPONSE = """<?xml version="1.0" encoding="UTF-8"?>
<transaction>
  <serviceID>123456</serviceID>
  <orderID>5555</orderID>
  <remoteID>AB-1</remoteID>
  <amount>10.99</amount>
  <currency>PLN</currency>
  <gatewayID>21</gatewayID>
  <paymentDate/>
  <paymentStatus>PENDING</paymentStatus>
  <redirecturl>https://pay.example.com/AB-1</redirecturl>
  <hash>deadbeef</hash>
</transaction>
"""


def spec_names(model: type) -> list[str]:
    return [f.metadata["spec"] for f in dataclasses.fields(model)]


def request() -> TransactionStart:
    return TransactionStart(
        service_id="123456",
        order_id="5555",
        amount="10.99",
        gateway_id="21",
        currency="PLN",
    )


def response() -> TransactionInit:
    return TransactionInit(
        service_id="123456",
        order_id="5555",
        remote_id="AB-1",
        amount="10.99",
        currency="PLN",
        gateway_id="21",
        payment_status="PENDING",
        redirect_url="https://pay.example.com/AB-1",
    )


def test_declared_fields_are_exactly_the_signed_transaction_fields():
    declared = [name for name in spec_names(TransactionStart) if name != "hash"]
    assert [name.lower() for name in declared] == [
        name.lower() for name in TRANSACTION_FIELD_ORDER
    ]


def test_declared_response_fields_are_exactly_the_signed_response_fields():
    declared = [name for name in spec_names(TransactionInit) if name != "hash"]
    assert declared == list(TRANSACTION_INIT_FIELD_ORDER)


def test_a_response_parses_into_typed_attributes():
    parsed = TransactionInit.from_xml(RESPONSE)
    assert parsed.service_id == "123456"
    assert parsed.order_id == "5555"
    assert parsed.remote_id == "AB-1"
    assert parsed.amount == "10.99"
    assert parsed.currency == "PLN"
    assert parsed.gateway_id == "21"
    assert parsed.payment_status == "PENDING"
    assert parsed.hash == "deadbeef"


def test_the_lowercase_redirect_element_reaches_a_readable_attribute():
    assert TransactionInit.from_xml(RESPONSE).redirect_url == (
        "https://pay.example.com/AB-1"
    )


def test_an_empty_optional_element_reads_as_absent():
    assert TransactionInit.from_xml(RESPONSE).payment_date is None


def test_bytes_are_accepted_as_well_as_text():
    assert TransactionInit.from_xml(RESPONSE.encode("utf-8")) == (
        TransactionInit.from_xml(RESPONSE)
    )


def test_element_names_match_regardless_of_case():
    parsed = TransactionStart.from_xml(
        "<transaction><SERVICEID>123456</SERVICEID>"
        "<orderid>5555</orderid><Amount>10.99</Amount></transaction>"
    )
    assert (parsed.service_id, parsed.order_id, parsed.amount) == (
        "123456",
        "5555",
        "10.99",
    )


def test_a_request_serialises_in_the_declared_order():
    assert request().to_xml() == (
        "<transaction>"
        "<serviceID>123456</serviceID>"
        "<orderID>5555</orderID>"
        "<amount>10.99</amount>"
        "<gatewayID>21</gatewayID>"
        "<currency>PLN</currency>"
        "</transaction>"
    )


def test_unset_fields_stay_out_of_the_document():
    assert "description" not in request().to_xml()


@pytest.mark.parametrize(
    "title", ["Order 1 & 2", "a <b> c", "Zapłata za zamówienie", 'quote "here"']
)
def test_awkward_text_survives_a_round_trip(title):
    original = dataclasses.replace(request(), title=title)
    assert TransactionStart.from_xml(original.to_xml()) == original


def test_a_response_round_trips_through_xml():
    original = response()
    assert TransactionInit.from_xml(original.to_xml()) == original


def test_an_unknown_element_is_reported_not_ignored():
    payload = (
        "<transaction><serviceID>123456</serviceID><orderID>5555</orderID>"
        "<amount>10.99</amount><amout>10.99</amout></transaction>"
    )
    with pytest.raises(UnknownElementError) as excinfo:
        TransactionStart.from_xml(payload)
    assert excinfo.value.name == "amout"


def test_a_response_element_is_unknown_to_a_request():
    payload = (
        "<transaction><serviceID>123456</serviceID><orderID>5555</orderID>"
        "<amount>10.99</amount><redirecturl>https://x</redirecturl></transaction>"
    )
    with pytest.raises(UnknownElementError):
        TransactionStart.from_xml(payload)


@pytest.mark.parametrize("second", ["orderID", "ORDERID"])
def test_a_repeated_element_is_ambiguous(second):
    payload = (
        "<transaction><serviceID>123456</serviceID><orderID>5555</orderID>"
        f"<amount>10.99</amount><{second}>6666</{second}></transaction>"
    )
    with pytest.raises(DuplicateElementError):
        TransactionStart.from_xml(payload)


def test_missing_required_elements_are_named():
    with pytest.raises(MissingElementError) as excinfo:
        TransactionStart.from_xml("<transaction><serviceID>123456</serviceID></transaction>")
    assert excinfo.value.names == ("amount", "orderID")


def test_a_required_element_without_text_counts_as_missing():
    payload = (
        "<transaction><serviceID>123456</serviceID><orderID>5555</orderID>"
        "<amount>  </amount></transaction>"
    )
    with pytest.raises(MissingElementError) as excinfo:
        TransactionStart.from_xml(payload)
    assert excinfo.value.names == ("amount",)


def test_a_nested_element_is_refused():
    payload = (
        "<transaction><serviceID>123456</serviceID><orderID>5555</orderID>"
        "<amount><value>10.99</value></amount></transaction>"
    )
    with pytest.raises(XMLParseError):
        TransactionStart.from_xml(payload)


def test_another_document_is_refused_by_its_root():
    with pytest.raises(UnexpectedRootError) as excinfo:
        TransactionStart.from_xml("<transactionList><transaction/></transactionList>")
    assert excinfo.value.found == "transactionList"


@pytest.mark.parametrize("payload", ["<transaction>", "not xml at all", ""])
def test_malformed_xml_raises_a_parse_error(payload):
    with pytest.raises(MalformedXMLError):
        TransactionStart.from_xml(payload)


def test_every_parse_failure_is_one_kind_of_error():
    for error in (
        MalformedXMLError,
        UnexpectedRootError,
        UnknownElementError,
        DuplicateElementError,
        MissingElementError,
    ):
        assert issubclass(error, XMLParseError)


def test_fields_are_keyed_by_their_specification_names():
    assert request().to_fields() == {
        "serviceID": "123456",
        "orderID": "5555",
        "amount": "10.99",
        "gatewayID": "21",
        "currency": "PLN",
    }


def test_a_request_signs_with_the_transaction_order():
    from bluemedia import sign

    message = request()
    assert message.sign(KEY) == sign(
        message.to_fields(), TRANSACTION_FIELD_ORDER, KEY
    )


def test_signing_fills_the_hash_field_and_verifies():
    signed = request().signed(KEY)
    assert signed.hash == request().sign(KEY)
    assert signed.verify(KEY) is True


def test_a_signed_response_verifies_after_a_round_trip():
    signed = response().signed(KEY)
    assert TransactionInit.from_xml(signed.to_xml()).verify(KEY) is True


def test_a_tampered_amount_fails_verification():
    signed = response().signed(KEY)
    assert dataclasses.replace(signed, amount="1.99").verify(KEY) is False


def test_a_response_without_a_digest_is_unverified():
    assert response().verify(KEY) is False


def test_the_wrong_key_fails_verification():
    assert response().signed(KEY).verify("another-shared-key") is False


def test_messages_are_immutable():
    with pytest.raises(dataclasses.FrozenInstanceError):
        request().amount = "1.00"
