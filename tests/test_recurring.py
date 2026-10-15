"""Recurring notifications: nested recurring data, parsed and signed in place."""

from __future__ import annotations

import base64
import dataclasses
import hashlib

import pytest

from bluemedia import (
    CONFIRMED,
    ITN_FIELD_ORDER,
    RECURRING_DATA_FIELD_ORDER,
    MissingElementError,
    Notification,
    NotificationList,
    RecurringData,
    UnknownElementError,
    XMLParseError,
    string_to_sign,
)

KEY = "9dfc5eb3d1b2f4e0"
SERVICE_ID = "123456"

RECURRING_ITN = """<?xml version="1.0" encoding="UTF-8"?>
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
      <recurringData>
        <recurringAction>INIT</recurringAction>
        <clientHash>CLIENT-HASH</clientHash>
      </recurringData>
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
        recurring_data=RecurringData(
            recurring_action="INIT", client_hash="CLIENT-HASH"
        ),
    )
    return dataclasses.replace(message, **overrides) if overrides else message


def listing(*notifications: Notification) -> NotificationList:
    return NotificationList(service_id=SERVICE_ID, notifications=notifications)


def digest_of(payload: str) -> str:
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def itn_holding(recurring: str) -> str:
    return (
        "<transactionList><serviceID>123456</serviceID><transactions>"
        "<transaction><orderID>5555</orderID><remoteID>AB-1</remoteID>"
        "<amount>10.99</amount><currency>PLN</currency>"
        "<paymentDate>20260818120000</paymentDate>"
        "<paymentStatus>SUCCESS</paymentStatus>"
        f"{recurring}</transaction></transactions></transactionList>"
    )


def test_the_parts_are_declared_in_their_own_order():
    assert RECURRING_DATA_FIELD_ORDER == ("recurringAction", "clientHash")
    assert [f.metadata["spec"] for f in dataclasses.fields(RecurringData)] == list(
        RECURRING_DATA_FIELD_ORDER
    )
    assert ITN_FIELD_ORDER[-1] == "recurringData"


def test_the_parts_take_the_place_the_element_has():
    assert notification().values() == [
        SERVICE_ID,
        "5555",
        "AB-1",
        "10.99",
        "PLN",
        "21",
        "20260818120000",
        "SUCCESS",
        "INIT",
        "CLIENT-HASH",
    ]
    assert string_to_sign(notification().to_fields(), ITN_FIELD_ORDER) == (
        "123456|5555|AB-1|10.99|PLN|21|20260818120000|SUCCESS|INIT|CLIENT-HASH"
    )


def test_the_notification_signs_over_the_nested_parts():
    assert notification().sign(KEY) == digest_of(
        "123456|5555|AB-1|10.99|PLN|21|20260818120000|SUCCESS|"
        f"INIT|CLIENT-HASH|{KEY}"
    )


def test_a_recurring_notification_parses_verifies_and_confirms():
    document = RECURRING_ITN.format(digest=notification().sign(KEY))
    encoded = base64.b64encode(document.encode("utf-8")).decode("ascii")
    parsed = NotificationList.from_base64(encoded)

    assert parsed[0].recurring_data == RecurringData(
        recurring_action="INIT", client_hash="CLIENT-HASH"
    )
    assert parsed[0].verify(KEY) is True
    assert parsed.confirm(KEY).confirmations[0].confirmation == CONFIRMED


def test_the_nested_element_is_written_back_as_elements():
    original = listing(notification().signed(KEY))
    document = original.to_xml()

    assert (
        "<recurringData><recurringAction>INIT</recurringAction>"
        "<clientHash>CLIENT-HASH</clientHash></recurringData>"
    ) in document
    assert NotificationList.from_xml(document) == original


def test_a_client_hash_left_out_shortens_the_digest():
    message = notification(recurring_data=RecurringData(recurring_action="INIT"))
    assert message.sign(KEY) == digest_of(
        f"123456|5555|AB-1|10.99|PLN|21|20260818120000|SUCCESS|INIT|{KEY}"
    )
    assert "clientHash" not in message.to_xml()


def test_a_plain_notification_is_unchanged():
    message = notification(recurring_data=None)
    assert message.sign(KEY) == digest_of(
        f"123456|5555|AB-1|10.99|PLN|21|20260818120000|SUCCESS|{KEY}"
    )
    assert "recurringData" not in message.to_xml()
    reply = listing(message.signed(KEY)).confirm(KEY)
    assert reply.confirmations[0].confirmation == CONFIRMED


def test_the_action_the_payment_reports_is_required():
    payload = itn_holding(
        "<recurringData><clientHash>CLIENT-HASH</clientHash></recurringData>"
    )
    with pytest.raises(MissingElementError) as excinfo:
        NotificationList.from_xml(payload)
    assert excinfo.value.names == ("recurringAction",)


def test_an_unknown_part_of_the_recurring_data_is_reported():
    payload = itn_holding(
        "<recurringData><recurringAction>INIT</recurringAction>"
        "<clientHashh>x</clientHashh></recurringData>"
    )
    with pytest.raises(UnknownElementError) as excinfo:
        NotificationList.from_xml(payload)
    assert excinfo.value.name == "clientHashh"


@pytest.mark.parametrize(
    "recurring",
    ["<recurringData>INIT</recurringData>", "<recurringData/>"],
    ids=["text instead of elements", "nothing at all"],
)
def test_recurring_data_that_holds_no_parts_is_refused(recurring):
    with pytest.raises(XMLParseError):
        NotificationList.from_xml(itn_holding(recurring))


def test_the_recurring_data_is_immutable():
    with pytest.raises(dataclasses.FrozenInstanceError):
        notification().recurring_data.recurring_action = "CANCEL"
