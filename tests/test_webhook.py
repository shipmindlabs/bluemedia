"""The webhook side of a client: reading a pushed ITN and answering it."""

from __future__ import annotations

import base64
import dataclasses
from typing import Any

import pytest

from bluemedia import (
    CONFIRMED,
    NOTCONFIRMED,
    Base64DecodeError,
    Client,
    Confirmation,
    ConfirmationList,
    ForeignNotificationError,
    GatewayError,
    Notification,
    NotificationList,
    UnexpectedRootError,
)

KEY = "9dfc5eb3d1b2f4e0"
SERVICE_ID = "123456"


def notification(*, key: str = KEY, **overrides: Any) -> Notification:
    message = Notification(
        service_id=SERVICE_ID,
        order_id="5555",
        remote_id="AB-1",
        amount="10.99",
        currency="PLN",
        payment_date="20260818120000",
        payment_status="SUCCESS",
    )
    if overrides:
        message = dataclasses.replace(message, **overrides)
    return message.signed(key)


def encoded(document: str) -> str:
    return base64.b64encode(document.encode("utf-8")).decode("ascii")


def payload(*notifications: Notification, service_id: str = SERVICE_ID) -> str:
    listing = NotificationList(
        service_id=service_id, notifications=notifications
    )
    return encoded(listing.to_xml())


def client(**options: Any) -> Client:
    return Client(SERVICE_ID, KEY, **options)


def test_a_notification_for_this_service_is_read_without_a_transport():
    parsed = client().notifications(payload(notification()))
    assert len(parsed) == 1
    assert parsed[0].order_id == "5555"
    assert parsed[0].verify(KEY) is True


def test_the_line_breaks_a_form_field_carries_drop_out():
    raw = payload(notification())
    wrapped = "\n".join([raw[:20], raw[20:], ""])
    assert client().notifications(wrapped) == client().notifications(raw)


def test_the_reply_is_signed_with_the_key_the_client_holds():
    reply = client().confirm(payload(notification()))

    assert reply.service_id == SERVICE_ID
    assert reply.confirmations == (
        Confirmation(order_id="5555", confirmation=CONFIRMED),
    )
    assert reply.hash == ConfirmationList(
        service_id=SERVICE_ID, confirmations=reply.confirmations
    ).sign(KEY)


def test_the_callback_only_sees_notifications_that_verify():
    seen: list[str] = []
    forged = notification(order_id="6666", key="another-shared-key")
    reply = client().confirm(
        payload(notification(), forged),
        accept=lambda n: seen.append(n.order_id) is None,
    )

    assert seen == ["5555"]
    assert [entry.confirmation for entry in reply.confirmations] == [
        CONFIRMED,
        NOTCONFIRMED,
    ]


def test_a_payment_the_shop_will_not_take_is_answered_notconfirmed():
    reply = client().confirm(payload(notification()), accept=lambda n: False)
    assert reply.confirmations[0].confirmation == NOTCONFIRMED


def test_notifications_verify_even_when_answers_are_not_verified():
    gateway = client(verify_responses=False)
    reply = gateway.confirm(payload(notification(key="another-shared-key")))
    assert reply.confirmations[0].confirmation == NOTCONFIRMED


def test_a_notification_for_another_service_is_refused():
    foreign = payload(notification(service_id="654321"), service_id="654321")
    with pytest.raises(ForeignNotificationError) as excinfo:
        client().notifications(foreign)

    assert isinstance(excinfo.value, GatewayError)
    assert excinfo.value.expected == SERVICE_ID
    assert excinfo.value.found == "654321"
    assert len(excinfo.value.notifications) == 1
    assert "654321" in str(excinfo.value)


def test_a_foreign_notification_reaches_no_callback():
    seen: list[Notification] = []
    foreign = payload(notification(service_id="654321"), service_id="654321")
    with pytest.raises(ForeignNotificationError):
        client().confirm(foreign, accept=lambda n: seen.append(n) is None)
    assert seen == []


def test_a_payload_that_is_not_base64_is_refused():
    with pytest.raises(Base64DecodeError):
        client().notifications("not base64 at all!")


def test_a_document_that_is_not_a_notification_is_refused():
    document = "<transaction><orderID>5555</orderID></transaction>"
    with pytest.raises(UnexpectedRootError):
        client().notifications(encoded(document))
