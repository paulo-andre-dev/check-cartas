"""Normaliza o texto de modalidade que cada site publica (em português,
com ou sem acento, no singular ou plural) para "imovel" ou "veiculo".

A Contemplei tem uma particularidade real: o segmento de veículos na API
dela se chama literalmente "moveis" (bens móveis), não "veiculos" — por
isso o caso especial abaixo, confirmado inspecionando os dados reais, não
suposto.
"""
import unicodedata

MODALITY_IMOVEL = "imovel"
MODALITY_VEICULO = "veiculo"


def _strip_accents(text: str) -> str:
    normalized = unicodedata.normalize("NFD", text)
    return "".join(c for c in normalized if unicodedata.category(c) != "Mn")


def normalize_modality(raw: str | None) -> str | None:
    if not raw:
        return None

    text = _strip_accents(raw).strip().lower()

    if text == "moveis":  # segmento de veículos da Contemplei
        return MODALITY_VEICULO
    if "imov" in text:
        return MODALITY_IMOVEL
    if "veic" in text:
        return MODALITY_VEICULO
    return None
