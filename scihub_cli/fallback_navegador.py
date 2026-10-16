#!/usr/bin/env python3
"""Último fallback ao Sci-Hub: navegador real quando o scihub_cli não acha o PDF.

Por que existe: algumas páginas só entregam o link do arquivo a um engine real
— o JS do desafio (Cloudflare / ALTCHA) roda, e o cookie que ele emite é o que
destrava o PDF. curl_cffi e cloudscraper falsificam headers/TLS e às vezes
executam o JS, mas não carregam a sessão adiante.

Rode com o Chrome do sistema instalado:

    pip install "scihub-cli[mcp,browser]"
    scihub-fallback <lista.txt> -o <dir>

<lista.txt>: um DOI por linha (aceita URL e linhas com #).

Por que channel="chrome": usa o Chrome instalado, não o chromium do playwright
— nenhum binário a baixar, brand/build reais, e `navigator.webdriver` já vem
False. O perfil persistente em ~/.scihub-cli/browser-profile guarda os cookies
entre execuções: desafio resolvido uma vez não é reencontrado.

Por que playwright num extra separado ([browser]): quem só usa o CLI não
paga a dependência; o tool do MCP recusa com instrução quando falta.

ponytail: um contexto sequencial, sem paralelismo — o gargalo é o desafio, não
a banda, e dois processos no mesmo user-data-dir corrompem o perfil.
"""

from __future__ import annotations

import argparse
import json
import random
import re
import sys
import time
from pathlib import Path
from urllib.parse import urlparse

from scihub_cli.config.mirrors import MirrorConfig
from scihub_cli.core.altcha_solver import is_altcha_challenge_html
from scihub_cli.core.challenge_detection import is_challenge_html, is_placeholder_pdf
from scihub_cli.core.pdf_link_extractor import extract_pdf_candidates

PERFIL = Path.home() / ".scihub-cli" / "browser-profile"
MIN_BYTES = 20_000
MAX_CANDIDATOS = 4
# Caminho de storage (hash/uuid), típico dos mirrors: o arquivo é chaveado pelo
# DOI na página, então o caminho em si já é a prova de que veio do registro certo.
_CAMINHO_OPACO = re.compile(r"^/(?:storage|downloads?)/|/[0-9a-f]{32,}", re.IGNORECASE)
_HOSTS_CONFIAVEIS = {urlparse(m).netloc for m in MirrorConfig.get_all_mirrors()} | {"sci-net.xyz"}


def _texto_do_pdf(caminho: Path, paginas: int = 2) -> str:
    import pymupdf  # lazy: só custa quando já temos bytes de PDF na mão

    with pymupdf.open(caminho) as doc:
        return "".join(p.get_text() for p in doc[:paginas])


def vincula(doi: str, url: str, pdf: Path | None = None, texto: str | None = None) -> bool:
    """O arquivo é mesmo o artigo pedido?

    URL com o DOI é uma *alegação*, não evidência: o mirror constrói
    `https://sci.bban.top/pdf/<doi>.pdf` para registros que não tem e responde
    com qualquer coisa. Sem este guard um PDF de cardiologia entrou no lugar de
    um artigo de Learning and Motivation.

    Evidência é (a) o DOI impresso nas duas primeiras páginas, ou (b) caminho de
    storage opaco num host de mirror/Sci-Net — ali o registro é chaveado pelo
    DOI, então um acerto não pode ser outro artigo.
    """
    if texto is None:
        if pdf is None:
            return False
        texto = _texto_do_pdf(pdf)
    if doi.lower() in texto.lower():
        return True
    partes = urlparse(url)
    return partes.netloc in _HOSTS_CONFIAVEIS and bool(_CAMINHO_OPACO.search(partes.path))


def _autoteste() -> None:
    """O guard contra o mirror devolver outro artigo. Falha se ele regredir."""
    doi = "10.1016/j.lmot.2020.101694"
    bban = f"https://sci.bban.top/pdf/{doi}.pdf"
    outro = "FIT Clinical Decision Making A646 JACC March 17, 2015 Volume 65"

    assert not vincula(doi, bban, texto=outro), "URL com o DOI não é evidência"
    assert vincula(doi, bban, texto=f"Learning and Motivation 71 (2020) 101694 {doi}")
    assert vincula(doi, "https://sci-net.xyz/storage/9744890/" + "a" * 64 + "/Title.pdf", texto="")
    assert vincula(doi, "https://sci-hub.ru/storage/2024/1/abc/paper.pdf", texto="")
    assert not vincula(doi, f"https://onlinelibrary.wiley.com/doi/pdf/{doi}", texto=outro)
    assert not vincula(doi, "https://exemplo.invalid/x.pdf", texto=outro)
    print("autoteste ok")


def paginas(doi: str) -> list[str]:
    """Páginas a abrir, na ordem. Mirrors derivados do config, nunca hardcoded."""
    urls = [f"{m}/{doi}" for m in MirrorConfig.get_all_mirrors()]
    urls.append(f"https://sci-net.xyz/{doi}")
    urls.append(f"https://doi.org/{doi}")
    return urls


def _html(page, tentativas: int = 3) -> str:
    """`page.content()` corre com o redirecionamento do doi.org (302 → publisher).

    Sem isto o item inteiro morre com "Unable to retrieve content because the
    page is navigating": a exceção sobe de `aguardar_desafio` e o laço de páginas
    do DOI nem chega nas seguintes. Insistir é mais barato que perder o item.
    """
    for _ in range(tentativas):
        try:
            return page.content()
        except Exception:
            page.wait_for_timeout(800)
    return ""


def eh_desafio(html: str) -> bool:
    return is_challenge_html(html) or is_altcha_challenge_html(html)


def aguardar_desafio(page, limite: float) -> str:
    """Deixa o JS do desafio rodar até a página virar artigo (ou estourar)."""
    fim = time.time() + limite
    html = _html(page)
    while eh_desafio(html) and time.time() < fim:
        page.wait_for_timeout(1000)
        html = _html(page)
    return html


def baixar(ctx, origem: str, url: str, destino: Path, doi: str) -> int | None:
    """Baixa pela sessão do browser. Devolve bytes, ou None se não for o artigo."""
    try:
        r = ctx.request.get(url, headers={"Referer": origem}, timeout=120_000)
    except Exception as exc:  # rede / TLS / timeout
        print(f"      err {type(exc).__name__} em {url[:70]}")
        return None
    if r.status != 200:
        return None
    body = r.body()
    if body[:5] != b"%PDF-":
        return None
    tmp = destino.with_name(destino.name + ".part")
    tmp.write_bytes(body)
    if len(body) < MIN_BYTES or is_placeholder_pdf(str(tmp)) or not vincula(doi, url, tmp):
        tmp.unlink(missing_ok=True)
        return None
    tmp.replace(destino)
    return len(body)


def coletar(page, ctx, doi: str, outdir: Path, limite_desafio: float) -> dict:
    destino = outdir / (doi.replace("/", "_") + ".pdf")
    if destino.exists() and destino.stat().st_size >= MIN_BYTES:
        return {
            "doi": doi,
            "status": "ja_tinha",
            "arquivo": str(destino),
            "bytes": destino.stat().st_size,
        }

    tentativas: list[str] = []
    for pagina in paginas(doi):
        try:
            page.goto(pagina, timeout=45_000, wait_until="domcontentloaded")
        except Exception as exc:
            print(f"  {pagina} -> goto falhou ({type(exc).__name__})")
            continue

        html = aguardar_desafio(page, limite_desafio)
        if eh_desafio(html):
            print(f"  {pagina} -> desafio não resolveu")
            continue

        candidatos = [
            u
            for u in extract_pdf_candidates(html, page.url, min_score=1)
            # A página "não está na base" vaza pelo parser como o próprio DOI.
            if not (u == pagina or u.startswith(pagina + "?"))
        ]
        if not candidatos:
            print(f"  {page.url} -> sem link de PDF")
            continue

        for url in candidatos[:MAX_CANDIDATOS]:
            time.sleep(random.uniform(0.8, 2.2))  # jitter: o pedido é o único clique
            print(f"  tentando {url[:90]}")
            n = baixar(ctx, page.url, url, destino, doi)
            if n:
                print(f"  OK {n} bytes -> {destino}")
                return {
                    "doi": doi,
                    "status": "baixado",
                    "arquivo": str(destino),
                    "bytes": n,
                    "url": url,
                    "pagina": page.url,
                }
            tentativas.append(url)

    return {"doi": doi, "status": "falhou", "arquivo": None, "bytes": 0, "tentativas": tentativas}


def ler_lista(caminho: str) -> list[str]:
    alvo = Path(caminho)
    if alvo.is_file():
        linhas = alvo.read_text(encoding="utf-8").splitlines()
    else:  # aceita DOIs soltos na linha de comando
        linhas = [caminho, *sys.argv[2:]]
    return [t for t in (linha.split("#")[0].strip() for linha in linhas) if t]


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("lista", nargs="?", help="arquivo com um DOI (ou URL) por linha")
    ap.add_argument("-o", "--out", help="pasta de saída")
    ap.add_argument(
        "--visivel",
        action="store_true",
        help="mostra a janela do Chrome (mais resistente a detecção)",
    )
    ap.add_argument(
        "--desafio", type=float, default=30, help="segundos de espera por desafio (default 30)"
    )
    ap.add_argument("--relatorio", help="caminho do relatório JSON")
    ap.add_argument("--autoteste", action="store_true", help="só roda o self-check do guard e sai")
    args = ap.parse_args()

    if args.autoteste:
        _autoteste()
        return 0

    from playwright.sync_api import sync_playwright

    if not args.out:
        ap.error("-o/--out é obrigatório (exceto com --autoteste)")

    itens = ler_lista(args.lista)
    if not itens:
        print("lista vazia", file=sys.stderr)
        return 2

    outdir = Path(args.out).expanduser().resolve()
    outdir.mkdir(parents=True, exist_ok=True)
    PERFIL.mkdir(parents=True, exist_ok=True)

    resultados = []
    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(
            str(PERFIL),
            channel="chrome",
            headless=not args.visivel,
            locale="pt-BR",
            timezone_id="America/Sao_Paulo",
            viewport={"width": 1280, "height": 900},
            args=["--disable-blink-features=AutomationControlled", "--no-first-run"],
        )
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        try:
            for i, doi in enumerate(itens, 1):
                print(f"[{i}/{len(itens)}] {doi}")
                try:
                    resultados.append(coletar(page, ctx, doi, outdir, args.desafio))
                except Exception as exc:  # um item quebrado não derruba o lote
                    print(f"  ERRO {type(exc).__name__}: {exc}")
                    resultados.append(
                        {"doi": doi, "status": "erro", "erro": f"{type(exc).__name__}: {exc}"}
                    )
        finally:
            ctx.close()

    ok = sum(1 for r in resultados if r["status"] in {"baixado", "ja_tinha"})
    falhas = [r["doi"] for r in resultados if r["status"] not in {"baixado", "ja_tinha"}]
    print(f"\n{ok}/{len(resultados)} obtidos")
    if falhas:
        print("falharam: " + ", ".join(falhas))
        print("dica: repita os que falharam com --visivel")
    rel = Path(args.relatorio) if args.relatorio else outdir / "relatorio_fallback.json"
    rel.write_text(json.dumps(resultados, ensure_ascii=False, indent=1), encoding="utf-8")
    print("relatorio:", rel)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
