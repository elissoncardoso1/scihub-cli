# Servidor MCP do Sci-Hub CLI

O `scihub-mcp` expõe toda a pipeline de download de artigos do Sci-Hub CLI como
um servidor [Model Context Protocol (MCP)](https://modelcontextprotocol.io). Com
ele, qualquer host MCP — Claude Desktop, Claude Code, Cursor, VS Code/Copilot,
Codex CLI, Gemini CLI e ChatGPT — consegue extrair DOIs, baixar lotes, acionar
o fallback de navegador e conferir os arquivos baixados, sem nunca tocar em
linha de comando.

O protocolo é agnóstico de modelo: a compatibilidade é com os **hosts**, não
com um modelo específico. O mesmo servidor atende a todos; o que muda entre
eles é apenas o transporte e o formato do arquivo de configuração.

---

## Instalação

### 1. Sem clonar nada (recomendado)

Qualquer host MCP resolve, instala as dependências e sobe o servidor sozinho a
partir do GitHub. O primeiro uso demora ~10s (baixa e compila); depois vem do
cache.

```bash
uvx --from "scihub-cli[mcp] @ git+https://github.com/elissoncardoso1/scihub-cli.git" scihub-mcp
```

> ⚠️ **Não** aponte para o PyPI: o `scihub-cli` publicado lá é o upstream, que
> ainda não tem os extras. Use o fork via `git+` (acima) ou clone + `pip`.

### 2. Com clone do repositório

```bash
git clone https://github.com/elissoncardoso1/scihub-cli.git
pip install "scihub-cli[mcp]"
scihub-mcp            # servidor MCP (stdio)
scihub-fallback LISTA.txt -o PASTA   # fallback de navegador fora do MCP
```

### Extras opcionais

| Extra | Inclui | Quando usar |
| --- | --- | --- |
| `[mcp]` | SDK do MCP (`mcp<2`) + PyMuPDF | obrigatório para o servidor e o `verificar` |
| `[browser]` | playwright | habilita o `fallback_navegador` (usa o Chrome do sistema) |

Instalação completa (servidor + fallback):

```bash
pip install "scihub-cli[mcp,browser]"
```

Se o `[browser]` não estiver instalado, o tool `fallback_navegador` responde
sozinho com a instrução de instalação, em vez de falhar silenciosamente.

---

## As 4 ferramentas

| Ferramenta | Propósito |
| --- | --- |
| `extrair_dois_csv` | Extrai DOIs de um CSV estilo export Scopus |
| `baixar_batch` | Roda o downloader em lote (Sci-Hub apenas, por padrão) |
| `fallback_navegador` | Re-tentativa por navegador real para DOIs que o CLI não achou |
| `verificar` | Mapeia os PDFs baixados de volta aos DOIs (checa o DOI nas 2 primeiras páginas) |

### `extrair_dois_csv(csv_path, doi_col="DOI")`

Lê um CSV (células com citação longa e abstracts multi-parágrafo são tratadas
corretamente via `DictReader`) e devolve:

```json
{ "total_rows": 10, "dois": ["10.1002/jaba.908", "..."], "sem_doi": ["linha 3: EID=..."] }
```

DOIs duplicados são removidos preservando a ordem; linhas sem DOI mas com EID
são reportadas, nunca descartadas em silêncio.

### `baixar_batch(dois, outdir, only_scihub=true, timeout_s=1200)`

Executa o downloader. Por padrão `only_scihub=true` restringe a cadeia de
fontes ao Sci-Hub (rápido). `only_scihub=false` ativa a cadeia completa de
acesso aberto (OpenAlex, Europe PMC, Unpaywall, arXiv…), que custa 60–90s por
DOI. Retorna o final do log e o caminho de `download-report.json`.

### `fallback_navegador(dois, outdir, timeout_s=1800)`

Para o que o CLI não obtém, abre mirror/Sci-Net/publisher no Chrome do sistema
(perfil persistente em `~/.scihub-cli/browser-profile`), resolve páginas
protegidas por desafio (ALTCHA/Cloudflare) e — antes de gravar qualquer arquivo
— **vincula o PDF ao DOI pedido** (guarda contra o mirror devolver outro
artigo). Requer `[browser]`.

### `verificar(outdir, dois=[])`

Abre cada PDF com PyMuPDF e confere se o DOI aparece nas duas primeiras páginas.
Devolve a contabilidade final:

```json
{ "obtidos": {"10.1002/jaba.908": "10.1002_jaba.908.pdf"},
  "invalidos": {}, "erros": {}, "ausentes": ["10.1080/..."] }
```

`dois` vazio = deriva a lista dos arquivos da pasta.

---

## Transportes

O mesmo núcleo é exposto em dois transportes:

### stdio (padrão)

O transporte de todo host local/desktop (Claude Desktop, Claude Code, Cursor,
VS Code, Codex, Gemini CLI…). O cliente inicia o servidor como subprocesso e
fala JSON-RPC pelo stdout.

### streamable-http

Para hosts que só aceitam servidor **remoto** (ChatGPT em developer mode e os
conectores do Claude.ai):

```bash
scihub-mcp --transport streamable-http            # 127.0.0.1:8000/mcp
scihub-mcp --transport streamable-http --host 0.0.0.0 --port 8123
# ou via variáveis: MCP_TRANSPORT, MCP_HOST, MCP_PORT
```

Qualquer exposição pública exige um proxy reverso TLS na frente. Para
deployment multiusuário real, a spec prevê OAuth 2.1 — fora do escopo de um
servidor local/uso próprio.

---

## Configuração por host

Mesmo servidor, formato de arquivo diferente. (Caminhos e chaves mudam com o
tempo — confira a documentação atual de cada host.)

```jsonc
// Claude Desktop: claude_desktop_config.json | Cursor: .cursor/mcp.json
// Gemini CLI: settings.json | Windsurf: mcp_config.json
{ "mcpServers": { "scihub": { "command": "uvx", "args": [
  "--from", "scihub-cli[mcp] @ git+https://github.com/elissoncardoso1/scihub-cli.git", "scihub-mcp" ] } } }
```

```jsonc
// VS Code / Copilot: .vscode/mcp.json  (a chave é "servers", não "mcpServers")
{ "servers": { "scihub": { "type": "stdio", "command": "uvx", "args": [
  "--from", "scihub-cli[mcp] @ git+https://github.com/elissoncardoso1/scihub-cli.git", "scihub-mcp" ] } } }
```

```toml
# Codex CLI: ~/.codex/config.toml
[mcp_servers.scihub]
command = "uvx"
args = ["--from", "scihub-cli[mcp] @ git+https://github.com/elissoncardoso1/scihub-cli.git", "scihub-mcp"]
```

```bash
# Claude Code
claude mcp add scihub -- uvx --from "scihub-cli[mcp] @ git+https://github.com/elissoncardoso1/scihub-cli.git" scihub-mcp
```

ChatGPT e Claude.ai aceitam apenas servidor **remoto**: rode
`scihub-mcp --transport streamable-http` atrás de um proxy TLS e informe ao
host a URL HTTPS do `/mcp`.

---

## Compatibilidade garantida

- **Schemas portáteis**: todo `inputSchema` é `type: object` com propriedades
  explícitas, sem `anyOf`/`oneOf`/`$ref`/`patternProperties` (o que quebra
  hosts que validam JSON Schema estritamente, como OpenAI e Gemini). Um teste
  de regressão (`test_schemas_are_portable`) falha se isso regredir.
- **Nomes válidos**: `[a-zA-Z0-9_-]{1,64}`, sem pontos ou espaços.
- **Disciplina de stdio**: JSON-RPC só no stdout; logs no stderr. As tools que
  chamam o pacote usam `sys.executable -m`, nunca `which uv` nem cwd presumido.
- **Sem segredos no código**: tudo que for sensível vem por variável de
  ambiente.

---

## Nota legal

O downloader é baseado no Sci-Hub e em fontes de acesso aberto. O uso é
direcionado à biblioteca de pesquisa do próprio usuário. Verifique os termos e
a legislação aplicável ao seu caso antes de usar em contexto institucional.

---

## Contato

Dúvidas, problemas ou sugestões: **[pontobhv@proton.me](mailto:pontobhv@proton.me)**
