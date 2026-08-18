from decimal import Decimal

from monitor_cartas.core.money import cents_to_decimal, format_brl, format_percentage, parse_brl_to_decimal


def test_cents_to_decimal():
    assert cents_to_decimal(1050) == Decimal("10.50")
    assert cents_to_decimal(None) is None


def test_format_brl():
    assert format_brl(Decimal("265423.17")) == "R$ 265.423,17"
    assert format_brl(None) == "não informado"


def test_parse_brl_to_decimal():
    assert parse_brl_to_decimal("R$ 1.234,56") == Decimal("1234.56")
    assert parse_brl_to_decimal("sob consulta") is None
    assert parse_brl_to_decimal(None) is None


def test_format_percentage():
    assert format_percentage(Decimal("0.0953")) == "9.53%"
    assert format_percentage(None) == "não informado"
