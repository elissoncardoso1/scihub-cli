#!/usr/bin/env python3
"""MCP server for scihub-cli.

Exposes the batch-download pipeline as MCP tools so MCP hosts (Claude Desktop,
Hermes, ...) can call it without wiring shell commands:

- extrair_dois_csv: extract DOIs from a Scopus-style CSV export
- baixar_batch: run the CLI batch downloader (Sci-Hub only by default)
- fallback_navegador: real-browser fallback for DOIs the CLI cannot find
- verificar: map downloaded PDFs back to DOIs (PyMuPDF, DOI on first 2 pages)

Install: pip install "scihub-cli[mcp]"  (fallback tool additionally needs
the [browser] extra). Run: scihub-mcp
"""

from __future__ import annotations

import csv
import json
import os
import subprocess
import sys
import tempfile

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("scihub")


def _run(args: list[str], timeout: int) -> dict:
    try:
        r = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return {"exit_code": -1, "output": f"TIMEOUT after {timeout}s", "stderr_tail": ""}
    return {
        "exit_code": r.returncode,
        "output": (r.stdout or "")[-3000:],
        "stderr_tail": (r.stderr or "")[-1500:],
    }


def _mcp_extra_missing() -> dict:
    return {
        "exit_code": -1,
        "output": "PyMuPDF not installed. Install with: pip install 'scihub-cli[mcp]'",
    }


@mcp.tool()
def extrair_dois_csv(csv_path: str, doi_col: str = "DOI") -> str:
    """Extract DOIs from a Scopus-style CSV export (handles quoted cells with long abstracts)."""
    with open(csv_path, newline="", encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
    dois, sem_doi = [], []
    for n, r in enumerate(rows, 1):
        d = (r.get(doi_col) or "").replace("https://doi.org/", "").strip()
        (dois if d else sem_doi).append(d if d else f"line {n}: EID={r.get('EID', '?')}")
    uniq = list(dict.fromkeys(dois))
    return json.dumps(
        {"total_rows": len(rows), "dois": uniq, "sem_doi": sem_doi},
        ensure_ascii=False,
    )


@mcp.tool()
def baixar_batch(
    dois: list[str], outdir: str, only_scihub: bool = True, timeout_s: int = 1200
) -> str:
    """Run the scihub-cli batch downloader. only_scihub=False enables the full OA chain (slow, 60-90s per DOI)."""
    os.makedirs(outdir, exist_ok=True)
    doifile = os.path.join(tempfile.mkdtemp(), "dois.txt")
    with open(doifile, "w") as f:
        f.write("\n".join(dois) + "\n")
    args = (
        [
            sys.executable,
            "-m",
            "scihub_cli.scihub_dl_refactored",
            "-o",
            outdir,
        ]
        + (["--only-scihub"] if only_scihub else [])
        + [doifile]
    )
    res = _run(args, timeout_s)
    res["outdir"] = outdir
    report = os.path.join(outdir, "download-report.json")
    if os.path.exists(report):
        res["report"] = report
    return json.dumps(res, ensure_ascii=False)


@mcp.tool()
def fallback_navegador(dois: list[str], outdir: str, timeout_s: int = 1800) -> str:
    """Real-browser fallback (system Chrome, persistent profile) for DOIs the CLI could not find.

    Requires the [browser] extra: pip install 'scihub-cli[mcp,browser]'.
    """
    try:
        import playwright.sync_api  # noqa: F401
    except ImportError:
        return json.dumps(
            {
                "exit_code": -1,
                "output": "playwright not installed. Install with: "
                "pip install 'scihub-cli[mcp,browser]' (uses the system Chrome).",
            },
            ensure_ascii=False,
        )
    doifile = os.path.join(tempfile.mkdtemp(), "dois.txt")
    with open(doifile, "w") as f:
        f.write("\n".join(dois) + "\n")
    res = _run(
        [sys.executable, "-m", "scihub_cli.fallback_navegador", doifile, "-o", outdir],
        timeout_s,
    )
    res["relatorio"] = os.path.join(outdir, "relatorio_fallback.json")
    return json.dumps(res, ensure_ascii=False)


@mcp.tool()
def verificar(outdir: str, dois: list[str] = None) -> str:
    """Map PDFs in outdir back to DOIs (DOI printed on the first 2 pages). Returns obtained/invalid/missing."""
    try:
        import pymupdf
    except ImportError:
        return json.dumps(_mcp_extra_missing(), ensure_ascii=False)
    import re

    def norm(s: str) -> str:
        s = re.sub(r"[^a-z0-9 ]", " ", s.lower())
        return re.sub(r"\s+", " ", s).strip()

    files = [f for f in os.listdir(outdir) if f.lower().endswith(".pdf")]
    if not dois:
        dois = [f.rsplit(".", 1)[0].replace("_", "/", 1) for f in files]
    mapeados: dict = {}
    ausentes = list(dois)
    for fn in files:
        stem = fn[:-4]
        esperado = stem.replace("_", "/")  # the CLI's sanitized-DOI filename form
        doi_alvo = next(
            (d for d in dois if esperado.startswith(d) or d in stem.replace("_", "/")),
            None,
        )
        try:
            doc = pymupdf.open(os.path.join(outdir, fn))
            txt = norm(" ".join(doc[i].get_text() for i in range(min(2, len(doc)))))
            if doi_alvo:
                ok = norm(doi_alvo) in txt  # norm strips ./_ on both sides
                mapeados[doi_alvo] = {"arquivo": fn, "paginas": len(doc), "valido": ok}
                if ok and doi_alvo in ausentes:
                    ausentes.remove(doi_alvo)
        except Exception as e:
            mapeados[doi_alvo or fn] = {"erro": str(e)[:120]}
    return json.dumps(
        {
            "obtidos": {d: v.get("arquivo") for d, v in mapeados.items() if v.get("valido")},
            "invalidos": {
                d: v
                for d, v in mapeados.items()
                if isinstance(v, dict) and not v.get("valido", True) and "erro" not in v
            },
            "erros": {d: v for d, v in mapeados.items() if "erro" in v},
            "ausentes": ausentes,
        },
        ensure_ascii=False,
    )


def main() -> None:
    """Run the MCP server. Transport via --transport or MCP_TRANSPORT (stdio|sse|streamable-http)."""
    import argparse

    ap = argparse.ArgumentParser(description="scihub-cli MCP server")
    ap.add_argument(
        "--transport",
        default=os.environ.get("MCP_TRANSPORT", "stdio"),
        choices=["stdio", "sse", "streamable-http"],
        help="stdio (local hosts) or streamable-http (remote hosts: ChatGPT, Claude.ai)",
    )
    mcp.run(transport=ap.parse_args().transport)


if __name__ == "__main__":
    main()
