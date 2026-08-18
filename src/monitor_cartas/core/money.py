"""Utilitarios para valores monetarios em BRL, sempre em Decimal/centavos.

Nunca usar float para dinheiro: reajustes e percentuais acumulam erro de
arredondamento que pode mascarar uma inconsistencia real do anuncio.
"""
from decimal import Decimal, InvalidOperation
import re

CENTS = Decimal("100")

_BRL_PATTERN = re.compile(r"-?\d{1,3}(?:\.\d{3})+(?:,\d{1,2})?|-?\d+(?:,\d{1,2})?")


def cents_to_decimal(cents: int | None) -> Decimal | None:
    if cents is None:
        return None
    return Decimal(cents) / CENTS


def decimal_to_cents(value: Decimal | None) -> int | None:
    if value is None:
        return None
    return int((value * CENTS).to_integral_value())


def format_brl(value: Decimal | None) -> str:
    if value is None:
        return "não informado"
    quantized = value.quantize(Decimal("0.01"))
    negative = quantized < 0
    quantized = abs(quantized)
    integer_part, _, decimal_part = f"{quantized:.2f}".partition(".")
    grouped = f"{int(integer_part):,}".replace(",", ".")
    sign = "-" if negative else ""
    return f"{sign}R$ {grouped},{decimal_part}"


def parse_brl_to_decimal(text: str | None) -> Decimal | None:
    """Converte texto tipo 'R$ 1.234,56' extraido de HTML em Decimal.

    Usado pelos adapters que precisam raspar texto (sem API JSON). Retorna
    None quando o texto nao contem um valor monetario reconhecivel — nunca
    presume zero.
    """
    if not text:
        return None
    match = _BRL_PATTERN.search(text)
    if not match:
        return None
    raw = match.group(0)
    normalized = raw.replace(".", "").replace(",", ".")
    try:
        return Decimal(normalized)
    except InvalidOperation:
        return None


def format_percentage(value: Decimal | None, casas: int = 2) -> str:
    if value is None:
        return "não informado"
    return f"{(value * 100).quantize(Decimal('1.' + '0' * casas))}%"
