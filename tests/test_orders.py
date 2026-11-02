"""Every field order agrees with the model it signs.

A name in a ``FIELD_ORDER`` that no model declares never reaches a digest, and
a model field the order does not mention raises only once something sets it --
so neither shows up while a document is built from its required fields alone.
Each document is built here with every field set, which is when the order and
the model have to agree.
"""

from __future__ import annotations

import dataclasses

import pytest

from bluemedia import (
    CANCEL_FIELD_ORDER,
    CANCEL_RESPONSE_FIELD_ORDER,
    ITN_FIELD_ORDER,
    REFUND_FIELD_ORDER,
    REFUND_RESPONSE_FIELD_ORDER,
    TRANSACTION_FIELD_ORDER,
    TRANSACTION_INIT_FIELD_ORDER,
    TRANSACTION_REFUSAL_FIELD_ORDER,
    TRANSACTION_STATUS_FIELD_ORDER,
    CancelRequest,
    CancelResponse,
    Notification,
    RefundRequest,
    RefundResponse,
    StatusRequest,
    TransactionInit,
    TransactionRefusal,
    TransactionStart,
)

KEY = "9dfc5eb3d1b2f4e0"

DOCUMENTS = [
    (TransactionStart, TRANSACTION_FIELD_ORDER),
    (TransactionInit, TRANSACTION_INIT_FIELD_ORDER),
    (TransactionRefusal, TRANSACTION_REFUSAL_FIELD_ORDER),
    (Notification, ITN_FIELD_ORDER),
    (RefundRequest, REFUND_FIELD_ORDER),
    (RefundResponse, REFUND_RESPONSE_FIELD_ORDER),
    (CancelRequest, CANCEL_FIELD_ORDER),
    (CancelResponse, CANCEL_RESPONSE_FIELD_ORDER),
    (StatusRequest, TRANSACTION_STATUS_FIELD_ORDER),
]


def spec(field: dataclasses.Field) -> str:
    return field.metadata["spec"]


def filled(model: type):
    """Build a document with every one of its fields set."""
    values = {}
    for field in dataclasses.fields(model):
        nested = field.metadata.get("model")
        values[field.name] = (
            filled(nested) if nested is not None else f"{spec(field)}-value"
        )
    return model(**values)


def declared_values(document) -> list[str]:
    """The values the document carries, nested parts expanded, hash aside."""
    parts: list[str] = []
    for field in dataclasses.fields(document):
        if spec(field).lower() == "hash":
            continue
        value = getattr(document, field.name)
        if field.metadata.get("model") is not None:
            parts.extend(declared_values(value))
        else:
            parts.append(value)
    return parts


@pytest.mark.parametrize(
    ("model", "order"),
    DOCUMENTS,
    ids=[model.__name__ for model, _ in DOCUMENTS],
)
def test_the_order_agrees_with_the_model_it_signs(model, order):
    assert model.FIELD_ORDER is order
    assert len({name.lower() for name in order}) == len(order)
    assert "hash" not in {name.lower() for name in order}

    declared = [spec(f) for f in dataclasses.fields(model) if spec(f).lower() != "hash"]
    assert [name.lower() for name in declared] == [name.lower() for name in order]

    document = filled(model)
    assert document.values() == declared_values(document)

    signed = document.signed(KEY)
    assert signed.verify(KEY) is True
    assert model.from_xml(signed.to_xml()) == signed
