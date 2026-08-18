# bluemedia

Python client for the Blue Media payment gateway: XML protocol, SHA-256 signing,
BLIK, ITN and recurring notifications.

## Status

Early. The package can build the string a message is signed over; hashing, XML
and transport are not implemented yet.

## Installation

```bash
pip install bluemedia
```

## The string to sign

Blue Media does not hash a serialised document. It hashes the values of a fixed
set of fields joined by `|`, taken in the order the specification declares --
which is neither document order nor alphabetical order. That order is data here,
not control flow: `TRANSACTION_FIELD_ORDER` for outgoing requests and
`ITN_FIELD_ORDER` for incoming notifications.

```python
from bluemedia import TRANSACTION_FIELD_ORDER, string_to_sign

string_to_sign(
    {
        "Amount": "10.99",
        "OrderID": "5555",
        "Currency": "PLN",
        "ServiceID": "123456",
        "GatewayID": "21",
    },
    TRANSACTION_FIELD_ORDER,
)
# '123456|5555|10.99|21|PLN'
```

Field names match regardless of case, empty and missing values drop out, a field
holding several values expands in place, and the `hash` field is skipped because
it carries the result rather than an input. A field the declared order does not
mention raises `UnknownFieldError` instead of quietly disappearing from the
string.

Use `ordered_values()` when the parts are more useful than the joined string.

## Development

```bash
pip install -e ".[dev]"
pytest
```

## License

MIT, see [LICENSE](LICENSE).

Maintained by [Shipmind Labs](https://shipmindlabs.com).
