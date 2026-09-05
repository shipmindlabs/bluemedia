"""Reading an ITN and answering it: base64, XML, digests, confirmations."""

from __future__ import annotations

import base64
import dataclasses
import hashlib

import pytest

from bluemedia import (
    CONFIRMED,
    NOTCONFIRMED,
    Base64DecodeError,
    Confirmation,
    ConfirmationList,
    DuplicateElementError,
    MalformedXMLError,
    MissingElementError,
    Notification,
    NotificationList,
    UnexpectedRootError,
    UnknownElementError,
    XMLParseError,
    hash_values,
)

KEY = "9dfc5eb3d1b2f4e0"
SERVICE_ID = "123456"

ITN = """<?xml version="1.0" encoding="UTF-8"?>
<transactionList>
  <serviceID>123456</serviceID>
  <transactions>
    <transaction>
      <orderID>5555</orderID>
      <remoteID>AB-1</remoteID>
      <amount>10.99</amount>
      <currency>PLN</currency>
      <gatewayID>21</gatewayID>
      <paymentDate>20260818120000</paymentDate>
      <paymentStatus>SUCCESS</paymentStatus>
      <hash>{digest}</hash>
    </transaction>
  </transactions>
</transactionList>
"""


def notification(**overrides) -> Notification:
    message = Notification(
        service_id=SERVICE_ID,
        order_id="5555",
        remote_id="AB-1",
        amount="10.99",
        currency="PLN",
        gateway_id="21",
        payment_date="20260818120000",
        payment_status="SUCCESS",
    )
    return dataclasses.replace(message, **overrides) if overrides else message


def listing(*notifications: Notification) -> NotificationList:
    return NotificationList(service_id=SERVICE_ID, notifications=notifications)


def pushed(*notifications: Notification) -> str:
    document = listing(*notifications).to_xml()
    return base64.b64encode(document.encode("utf-8")).decode("ascii")


def signed_itn() -> str:
    return ITN.format(digest=notification().sign(KEY))


def test_the_notification_signs_in_the_declared_order():
    payload = f"123456|5555|AB-1|10.99|PLN|21|20260818120000|SUCCESS|{KEY}"
    expected = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    assert notification().sign(KEY) == expected


def test_a_pushed_notification_decodes_and_parses():
    encoded = base64.b64encode(signed_itn().encode("utf-8")).decode("ascii")
    parsed = NotificationList.from_base64(encoded)
    assert parsed.service_id == SERVICE_ID
    assert len(parsed) == 1
    assert parsed[0].order_id == "5555"
    assert parsed[0].remote_id == "AB-1"
    assert parsed[0].payment_status == "SUCCESS"


def test_the_service_id_of_the_list_reaches_every_notification():
    parsed = NotificationList.from_xml(signed_itn())
    assert parsed[0].service_id == SERVICE_ID
    assert parsed[0].verify(KEY) is True


def test_a_notification_carrying_its_own_service_id_keeps_it():
    payload = (
        "<transactionList><serviceID>123456</serviceID><transactions>"
        "<transaction><serviceID>654321</serviceID><orderID>5555</orderID>"
        "<remoteID>AB-1</remoteID><amount>10.99</amount><currency>PLN</currency>"
        "<paymentDate>20260818120000</paymentDate>"
        "<paymentStatus>SUCCESS</paymentStatus></transaction>"
        "</transactions></transactionList>"
    )
    assert NotificationList.from_xml(payload)[0].service_id == "654321"


def test_base64_survives_the_line_breaks_a_form_field_adds():
    raw = base64.b64encode(signed_itn().encode("utf-8")).decode("ascii")
    wrapped = "\n".join(raw[i : i + 60] for i in range(0, len(raw), 60))
    assert NotificationList.from_base64(f"  {wrapped}\n") == (
        NotificationList.from_base64(raw)
    )


def test_non_ascii_text_survives_the_whole_journey():
    encoded = pushed(notification(title="Zapłata za zamówienie").signed(KEY))
    parsed = NotificationList.from_base64(encoded)
    assert parsed[0].title == "Zapłata za zamówienie"
    assert parsed[0].verify(KEY) is True


@pytest.mark.parametrize("payload", ["not base64!", "żółć", "YWJj====="])
def test_a_payload_that_is_not_base64_is_reported(payload):
    with pytest.raises(Base64DecodeError):
        NotificationList.from_base64(payload)


def test_a_base64_failure_is_a_parse_failure():
    assert issubclass(Base64DecodeError, XMLParseError)


def test_base64_of_something_else_still_fails_to_parse():
    encoded = base64.b64encode(b"<html>maintenance</html>").decode("ascii")
    with pytest.raises(UnexpectedRootError):
        NotificationList.from_base64(encoded)


def test_a_list_round_trips_through_xml():
    original = listing(notification().signed(KEY), notification(order_id="5556"))
    assert NotificationList.from_xml(original.to_xml()) == original


def test_the_service_id_is_written_once_on_the_list():
    assert listing(notification().signed(KEY)).to_xml().count("<serviceID>") == 1


def test_an_unknown_element_on_the_list_is_reported():
    payload = (
        "<transactionList><serviceID>123456</serviceID><transactions/>"
        "<transactionz/></transactionList>"
    )
    with pytest.raises(UnknownElementError) as excinfo:
        NotificationList.from_xml(payload)
    assert excinfo.value.name == "transactionz"


def test_something_other_than_a_transaction_in_the_container_is_reported():
    payload = (
        "<transactionList><serviceID>123456</serviceID>"
        "<transactions><confirmation/></transactions></transactionList>"
    )
    with pytest.raises(UnknownElementError):
        NotificationList.from_xml(payload)


def test_a_repeated_service_id_is_ambiguous():
    payload = (
        "<transactionList><serviceID>123456</serviceID>"
        "<serviceID>654321</serviceID><transactions/></transactionList>"
    )
    with pytest.raises(DuplicateElementError):
        NotificationList.from_xml(payload)


@pytest.mark.parametrize(
    ("payload", "missing"),
    [
        ("<transactionList><transactions/></transactionList>", ("serviceID",)),
        (
            "<transactionList><serviceID>123456</serviceID></transactionList>",
            ("transactions",),
        ),
    ],
)
def test_a_list_without_its_own_parts_is_refused(payload, missing):
    with pytest.raises(MissingElementError) as excinfo:
        NotificationList.from_xml(payload)
    assert excinfo.value.names == missing


def test_a_notification_missing_a_required_element_is_refused():
    payload = (
        "<transactionList><serviceID>123456</serviceID><transactions>"
        "<transaction><orderID>5555</orderID></transaction>"
        "</transactions></transactionList>"
    )
    with pytest.raises(MissingElementError):
        NotificationList.from_xml(payload)


def test_malformed_xml_raises_a_parse_error():
    with pytest.raises(MalformedXMLError):
        NotificationList.from_xml("<transactionList>")


def test_an_empty_notification_list_parses():
    payload = (
        "<transactionList><serviceID>123456</serviceID>"
        "<transactions/></transactionList>"
    )
    parsed = NotificationList.from_xml(payload)
    assert len(parsed) == 0
    assert parsed.confirm(KEY).confirmations == ()


def test_verified_keeps_only_the_notifications_the_key_signed():
    parsed = listing(
        notification().signed(KEY),
        notification(order_id="5556").signed("another-shared-key"),
    )
    assert [message.order_id for message in parsed.verified(KEY)] == ["5555"]


def test_a_verified_notification_is_confirmed():
    reply = NotificationList.from_xml(signed_itn()).confirm(KEY)
    assert reply.service_id == SERVICE_ID
    assert reply.confirmations == (
        Confirmation(order_id="5555", confirmation=CONFIRMED),
    )


@pytest.mark.parametrize(
    "broken",
    [
        notification(),
        notification(hash="deadbeef"),
        notification().signed("another-shared-key"),
        dataclasses.replace(notification().signed(KEY), amount="1.99"),
    ],
    ids=["unsigned", "bogus digest", "another key", "tampered amount"],
)
def test_a_notification_that_does_not_verify_is_not_confirmed(broken):
    reply = listing(broken).confirm(KEY)
    assert reply.confirmations[0].confirmation == NOTCONFIRMED


def test_accept_decides_between_verified_notifications():
    parsed = listing(
        notification().signed(KEY), notification(order_id="5556").signed(KEY)
    )
    reply = parsed.confirm(KEY, accept=lambda message: message.order_id == "5555")
    assert [entry.confirmation for entry in reply.confirmations] == [
        CONFIRMED,
        NOTCONFIRMED,
    ]


def test_accept_is_never_asked_about_a_notification_that_does_not_verify():
    asked = []

    def accept(message):
        asked.append(message.order_id)
        return True

    reply = listing(notification()).confirm(KEY, accept=accept)
    assert asked == []
    assert reply.confirmations[0].confirmation == NOTCONFIRMED


def test_every_order_is_answered_in_the_order_it_arrived():
    parsed = listing(
        notification(order_id="1").signed(KEY),
        notification(order_id="2"),
        notification(order_id="3").signed(KEY),
    )
    reply = parsed.confirm(KEY)
    assert [entry.order_id for entry in reply.confirmations] == ["1", "2", "3"]
    assert [entry.confirmation for entry in reply.confirmations] == [
        CONFIRMED,
        NOTCONFIRMED,
        CONFIRMED,
    ]


def test_the_reply_is_signed_over_the_service_id_and_every_answer():
    reply = listing(
        notification().signed(KEY), notification(order_id="5556")
    ).confirm(KEY)
    assert reply.values() == [SERVICE_ID, "5555", CONFIRMED, "5556", NOTCONFIRMED]
    assert reply.hash == hash_values(reply.values(), KEY)


def test_the_reply_serialises_the_way_the_gateway_reads_it():
    reply = NotificationList.from_xml(signed_itn()).confirm(KEY)
    assert reply.to_xml() == (
        "<confirmationList>"
        "<serviceID>123456</serviceID>"
        "<transactionsConfirmations>"
        "<transactionConfirmed>"
        "<orderID>5555</orderID>"
        f"<confirmation>{CONFIRMED}</confirmation>"
        "</transactionConfirmed>"
        "</transactionsConfirmations>"
        f"<hash>{reply.hash}</hash>"
        "</confirmationList>"
    )


def test_an_unsigned_reply_carries_no_hash_element():
    reply = ConfirmationList(
        service_id=SERVICE_ID, confirmations=(Confirmation(order_id="5555"),)
    )
    assert "<hash>" not in reply.to_xml()


def test_the_algorithm_reaches_both_directions():
    parsed = listing(notification().signed(KEY, algorithm="sha512"))
    reply = parsed.confirm(KEY, algorithm="sha512")
    assert reply.confirmations[0].confirmation == CONFIRMED
    assert len(reply.hash) == 128


def test_an_answer_outside_the_two_the_gateway_knows_is_refused():
    with pytest.raises(ValueError):
        Confirmation(order_id="5555", confirmation="MAYBE")


def test_notifications_are_immutable():
    with pytest.raises(dataclasses.FrozenInstanceError):
        notification().amount = "1.00"
