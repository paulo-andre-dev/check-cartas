from monitor_cartas.core.modality import MODALITY_IMOVEL, MODALITY_VEICULO, normalize_modality


def test_normalize_common_variants():
    assert normalize_modality("imoveis") == MODALITY_IMOVEL
    assert normalize_modality("Imóvel") == MODALITY_IMOVEL
    assert normalize_modality("IMÓVEL") == MODALITY_IMOVEL
    assert normalize_modality("Imóveis") == MODALITY_IMOVEL
    assert normalize_modality("Imovel") == MODALITY_IMOVEL

    assert normalize_modality("veiculo") == MODALITY_VEICULO
    assert normalize_modality("Veículo") == MODALITY_VEICULO
    assert normalize_modality("VEÍCULO") == MODALITY_VEICULO
    assert normalize_modality("Veículos") == MODALITY_VEICULO


def test_contemplei_moveis_quirk_means_veiculo():
    assert normalize_modality("moveis") == MODALITY_VEICULO


def test_unknown_or_missing():
    assert normalize_modality(None) is None
    assert normalize_modality("") is None
    assert normalize_modality("servico") is None
