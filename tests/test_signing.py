from __future__ import annotations

import pytest

from bluemedia.signing import (
    ITN_FIELD_ORDER,
    TRANSACTION_FIELD_ORDER,
    DuplicateFieldError,
    UnknownFieldError,
    ordered_values,
    string_to_sign,
)

TRANSACTION_CASES = [
    (
        "minimal request",
        {"ServiceID": "123456", "OrderID": "5555", "Amount": "10.99"},
        "123456|5555|10.99",
    ),
    (
        "document order differs from signing order",
        {
            "Amount": "10.99",
            "OrderID": "5555",
            "Currency": "PLN",
            "ServiceID": "123456",
            "GatewayID": "21",
        },
        "123456|5555|10.99|21|PLN",
    ),
    (
        "description precedes gateway even though both are optional",
        {
            "GatewayID": "21",
            "Description": "Order 5555",
            "ServiceID": "123456",
        },
        "123456|Order 5555|21",
    ),
    (
        "customer block keeps its declared sequence",
        {
            "CustomerIP": "10.0.0.1",
            "CustomerEmail": "buyer@example.com",
            "TaxCountry": "PL",
            "CustomerNRB": "PL61109010140000071219812874",
            "ServiceID": "123456",
        },
        "123456|buyer@example.com|PL61109010140000071219812874|PL|10.0.0.1",
    ),
    (
        "BLIK fields sit between screen type and return URL",
        {
            "ReturnURL": "https://shop.example.com/return",
            "BlikAMKey": "AM-KEY",
            "ScreenType": "FULL",
            "BlikUIDKey": "UID-KEY",
            "BlikUIDLabel": "phone",
            "ServiceID": "123456",
        },
        "123456|FULL|UID-KEY|phone|AM-KEY|https://shop.example.com/return",
    ),
    (
        "recurring fields come after the regulation block",
        {
            "RecurringAction": "INIT",
            "DefaultRegulationAcceptanceState": "true",
            "RecurringAcceptanceState": "ACCEPTED",
            "DefaultRegulationAcceptanceID": "REG-1",
            "ServiceID": "123456",
        },
        "123456|true|REG-1|ACCEPTED|INIT",
    ),
    (
        "empty and missing values drop out",
        {
            "ServiceID": "123456",
            "OrderID": "5555",
            "Description": "",
            "Amount": "10.99",
            "Currency": None,
            "Title": "",
        },
        "123456|5555|10.99",
    ),
    (
        "field names match regardless of case",
        {"serviceid": "123456", "ORDERID": "5555", "aMouNt": "10.99"},
        "123456|5555|10.99",
    ),
    (
        "surrounding whitespace in a name is ignored",
        {" ServiceID ": "123456", "OrderID\n": "5555"},
        "123456|5555",
    ),
    (
        "the hash field never signs itself",
        {"ServiceID": "123456", "OrderID": "5555", "Hash": "deadbeef"},
        "123456|5555",
    ),
    (
        "a repeated field expands in place",
        {
            "ServiceID": "123456",
            "OrderID": "5555",
            "BlikAliasKey": ["alias-1", "alias-2"],
            "Currency": "PLN",
        },
        "123456|5555|PLN|alias-1|alias-2",
    ),
    (
        "nested structures expand depth first",
        {
            "ServiceID": "123456",
            "ReceiverAddress": {"street": "Main 1", "city": ["Sopot", "PL"]},
        },
        "123456|Main 1|Sopot|PL",
    ),
    (
        "numbers are rendered as text",
        {"ServiceID": 123456, "OrderID": 5555, "Amount": 10.99},
        "123456|5555|10.99",
    ),
    (
        "values are taken verbatim, including inner whitespace",
        {"ServiceID": "123456", "Title": "  payment for  order  "},
        "123456|  payment for  order  ",
    ),
    (
        "non-ASCII values survive untouched",
        {"ServiceID": "123456", "Description": "Zamówienie za 10 zł"},
        "123456|Zamówienie za 10 zł",
    ),
    (
        "no signable field yields an empty string",
        {"Hash": "deadbeef", "Amount": ""},
        "",
    ),
]

ITN_CASES = [
    (
        "notification order is not the transaction order",
        {
            "amount": "10.99",
            "orderID": "5555",
            "serviceID": "123456",
            "currency": "PLN",
            "remoteID": "REMOTE-1",
        },
        "123456|5555|REMOTE-1|10.99|PLN",
    ),
    (
        "payment status details follow the status",
        {
            "paymentStatusDetails": "AUTHORIZED",
            "paymentStatus": "PENDING",
            "paymentDate": "20260818120000",
            "serviceID": "123456",
        },
        "123456|20260818120000|PENDING|AUTHORIZED",
    ),
    (
        "recurring data closes the notification",
        {
            "recurringData": ["INIT", "CLIENT-HASH"],
            "startAmount": "1.00",
            "serviceID": "123456",
            "orderID": "5555",
        },
        "123456|5555|1.00|INIT|CLIENT-HASH",
    ),
    (
        "camelCase names are matched case-insensitively too",
        {"ServiceID": "123456", "OrderID": "5555", "AddressIP": "10.0.0.1"},
        "123456|5555|10.0.0.1",
    ),
]


@pytest.mark.parametrize(
    ("fields", "expected"),
    [pytest.param(f, e, id=name) for name, f, e in TRANSACTION_CASES],
)
def test_transaction_string_to_sign(fields, expected):
    assert string_to_sign(fields, TRANSACTION_FIELD_ORDER) == expected


@pytest.mark.parametrize(
    ("fields", "expected"),
    [pytest.param(f, e, id=name) for name, f, e in ITN_CASES],
)
def test_itn_string_to_sign(fields, expected):
    assert string_to_sign(fields, ITN_FIELD_ORDER) == expected


def test_ordered_values_returns_the_parts_unjoined():
    fields = {"OrderID": "5555", "ServiceID": "123456", "Amount": "10.99"}
    assert ordered_values(fields, TRANSACTION_FIELD_ORDER) == [
        "123456",
        "5555",
        "10.99",
    ]


@pytest.mark.parametrize("separator", ["|", "", "&", "::"])
def test_separator_only_joins(separator):
    fields = {"ServiceID": "123456", "OrderID": "5555"}
    assert string_to_sign(
        fields, TRANSACTION_FIELD_ORDER, separator=separator
    ) == separator.join(["123456", "5555"])


def test_a_value_containing_the_separator_is_not_escaped():
    fields = {"ServiceID": "123456", "Title": "a|b"}
    assert string_to_sign(fields, TRANSACTION_FIELD_ORDER) == "123456|a|b"


def test_field_orders_are_declared_without_duplicates():
    for order in (TRANSACTION_FIELD_ORDER, ITN_FIELD_ORDER):
        names = [name.lower() for name in order]
        assert len(names) == len(set(names))


def test_unknown_field_is_reported_not_skipped():
    fields = {"ServiceID": "123456", "OrderId2": "x", "Amout": "10.99"}
    with pytest.raises(UnknownFieldError) as excinfo:
        string_to_sign(fields, TRANSACTION_FIELD_ORDER)
    assert excinfo.value.names == ("amout", "orderid2")


def test_transaction_field_rejected_in_a_notification():
    with pytest.raises(UnknownFieldError):
        string_to_sign({"ScreenType": "FULL"}, ITN_FIELD_ORDER)


def test_case_variants_of_one_field_are_ambiguous():
    with pytest.raises(DuplicateFieldError):
        string_to_sign(
            {"ServiceID": "123456", "serviceID": "654321"},
            TRANSACTION_FIELD_ORDER,
        )


def test_bytes_are_rejected_rather_than_guessed():
    with pytest.raises(TypeError):
        string_to_sign({"ServiceID": b"123456"}, TRANSACTION_FIELD_ORDER)
