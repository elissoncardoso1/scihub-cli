"""MCP server + browser-fallback guard tests."""

import pytest


class TestFallbackGuard:
    """The `vincula` guard must reject a wrong PDF served under a claimed-DOI URL."""

    DOI = "10.1016/j.lmot.2020.101694"
    WRONG_TEXT = "FIT Clinical Decision Making A646 JACC March 17, 2015 Volume 65"

    def test_url_with_doi_is_not_evidence(self):
        from scihub_cli.fallback_navegador import vincula

        assert not vincula(
            self.DOI, f"https://sci.bban.top/pdf/{self.DOI}.pdf", texto=self.WRONG_TEXT
        )

    def test_doi_in_pdf_text_is_evidence(self):
        from scihub_cli.fallback_navegador import vincula

        assert vincula(
            self.DOI,
            f"https://sci.bban.top/pdf/{self.DOI}.pdf",
            texto=f"Learning and Motivation 71 (2020) {self.DOI}",
        )

    def test_opaque_storage_path_on_trusted_host_is_evidence(self):
        from scihub_cli.fallback_navegador import vincula

        assert vincula(
            self.DOI, "https://sci-net.xyz/storage/9744890/" + "a" * 64 + "/Title.pdf", texto=""
        )
        assert vincula(self.DOI, "https://sci-hub.ru/storage/2024/1/abc/paper.pdf", texto="")

    def test_publisher_and_unknown_hosts_rejected(self):
        from scihub_cli.fallback_navegador import vincula

        assert not vincula(
            self.DOI, f"https://onlinelibrary.wiley.com/doi/pdf/{self.DOI}", texto=self.WRONG_TEXT
        )
        assert not vincula(self.DOI, "https://exemplo.invalid/x.pdf", texto=self.WRONG_TEXT)


class TestMcpServer:
    def test_tools_registered(self):
        pytest.importorskip("mcp")
        import asyncio

        from scihub_cli.mcp_server import mcp

        tools = asyncio.run(mcp.list_tools())
        names = {t.name for t in tools}
        assert names == {"extrair_dois_csv", "baixar_batch", "fallback_navegador", "verificar"}
