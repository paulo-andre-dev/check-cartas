from monitor_cartas.core.dedupe import fingerprint, primary_key
from tests.conftest import make_cota


def test_primary_key():
    cota = make_cota(source_site="contemplei", source_id="324351")
    assert primary_key(cota) == ("contemplei", "324351")


def test_fingerprint_deterministic_when_group_and_quota_known():
    a = make_cota(administrator="Caixa Consórcios", group="5805", quota="12")
    b = make_cota(source_site="outro_site", source_id="999", administrator="Caixa Consórcios", group="5805", quota="12")
    assert fingerprint(a) == fingerprint(b)
    assert fingerprint(a).startswith("det:")


def test_fingerprint_probabilistic_fallback_without_quota():
    cota = make_cota(quota=None)
    fp = fingerprint(cota)
    assert fp is not None
    assert fp.startswith("prob:")


def test_fingerprint_none_without_administrator_or_credit():
    cota = make_cota(administrator=None, nominal_credit=None, quota=None)
    assert fingerprint(cota) is None
