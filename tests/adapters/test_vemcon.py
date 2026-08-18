from decimal import Decimal
from pathlib import Path

from monitor_cartas.adapters.vemcon import parse_detail, parse_sitemap

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"


def test_parse_sitemap_extracts_card_urls():
    xml = """<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
    <url><loc>https://vemcon.com.br/consorcio/imovel/teste</loc></url></urlset>"""
    assert parse_sitemap(xml) == ["https://vemcon.com.br/consorcio/imovel/teste"]


def test_parse_detail_reads_prerendered_table():
    item = parse_detail((FIXTURES / "vemcon_detail.html").read_text())
    assert item["code"] == "IMV-000301"
    assert item["Administradora"] == "Embracon"
    assert item["Valor do crédito"] == "R$ 250.000,00"
