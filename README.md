# RankedDojo

RankedDojo é um app desktop local-first para prática de programação e simulação de
provas/exames. Ele gerencia packs de estudo, workspaces, verificação de
runtime/toolchain, traces de correção, progresso de treino e sessões/histórico de
exames em uma UI compacta em PySide6.

Este repositório contém o motor do app e packs de exemplo públicos. Ele não contém
packs de prova privados/originais nem material de estudo proprietário.

## Funcionalidades

- Modo Treino com seleção de atividade por nível ou aleatória.
- Modo Exame com prazo absoluto, retomada, fluxo PASS/FAIL e regra de conclusão 100%.
- Camada de runtime genérica para C, C++, Python e Java/JDK.
- Preflight de runtime: toolchains ausentes bloqueiam início de correção/exame com
  mensagens claras.
- Contrato de pack v3 com subjects em Markdown, `programming_language`,
  `content_language`, restrições de uso, planos de validação e referências opcionais
  apenas quando necessário.
- Progresso/histórico em SQLite com migrations e backups.
- Separação de workspace entre treino e exames.
- Fluxo da Home para `QUERO ESTUDAR ALGO NOVO`: cria um prompt neutro de fornecedor
  para gerar packs compatíveis, e depois importá-los.
- Trilha de aprendizado progressivo por linguagem (C, C++, Python, Java), combinando
  conteúdo oficial embutido e packs instalados pelo usuário.
- Instalação distribuível via `pip` no Linux e build PyInstaller preservado no Windows.

## Requisitos

- Python 3.12+
- PySide6
- Toolchains externas opcionais, dependendo do pack:
  - C: compilador compatível com GCC/Clang
  - C++: compilador com suporte a C++17
  - Python: Python do sistema 3.9+ (`py -3`, `python3` ou `python`)
  - Java: JDK (`javac` e `java`)

O app nunca instala runtimes automaticamente. Runtimes ausentes são reportados na UI.
Quando empacotado (frozen), exercícios de Python precisam de um Python do sistema, e
não do executável do app.

## Como executar (uso normal)

A forma pública recomendada de rodar o RankedDojo é pelo comando instalado ou pelo
executável:

```bash
rankeddojo
```

```text
RankedDojo.exe   # Windows, build gerado por PyInstaller
```

O comando `rankeddojo` é instalado junto com o pacote Python e é o ponto de entrada
público declarado em `pyproject.toml` (`[project.scripts]`).

## Linux

Instalação a partir do pacote publicado:

```bash
python -m pip install rankeddojo
rankeddojo
```

Desenvolvimento local:

```bash
git clone https://github.com/reginaldojr-dev/rankeddojo.git
cd rankeddojo
python -m pip install -e .
rankeddojo
```

Atalho no Linux:

```bash
make
```

Esse target instala o checkout atual com `python -m pip install -e .` e inicia
`rankeddojo`. No Ubuntu 22.04 amd64, ele também prepara localmente
`libxcb-cursor.so.0` quando a biblioteca não existe no sistema, sem usar `sudo`.
Para instalar a versão publicada, use separadamente:
`python -m pip install rankeddojo`.

O pacote instala as dependências Python e os recursos públicos necessários, incluindo
os packs de exemplo. Algumas distribuições Linux ainda podem exigir bibliotecas
nativas do Qt para o backend gráfico. Se o Qt informar `Could not load the Qt
platform plugin "xcb"`, o `make` tenta preparar a biblioteca oficial Ubuntu
Jammy no cache `.vendor/` quando estiver em Ubuntu 22.04 amd64. Em outras
distribuições ou arquiteturas, consulte a documentação do sistema para as
dependências nativas correspondentes; nenhum gestor de pacotes é executado pelo
RankedDojo.

O fallback local usa somente o pacote oficial Ubuntu Jammy `libxcb-cursor0`
amd64 `0.1.1-4ubuntu1`, baixado de `archive.ubuntu.com` e validado por SHA-256
antes da extração. O pacote upstream é MIT/X Consortium; somente
`libxcb-cursor.so.0` é extraída para `.vendor/linux/lib`, que é ignorada pelo Git.

## Desenvolvimento

Use o ambiente virtual local do repositório, na raiz do projeto:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e ".[build]"
```

Linux/macOS:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e ".[build]"
```

Depois de instalado em modo editável, o comando público `rankeddojo` já fica
disponível dentro do `.venv`.

Execução alternativa a partir do código-fonte (uso interno/dev, não é o comando
público do produto):

```bash
python -m rankeddojo.main
```

Esse caminho roda o mesmo pacote a partir do código-fonte, sem passar pelo
comando instalado; é útil em desenvolvimento, mas não deve ser usado como
instrução de uso para o usuário final.

Rodar os testes:

```bash
python -m pytest -q
```

## Packs

Packs podem ser importados de uma pasta ou ZIP em `Configuracoes > Packs`.
O contrato completo suportado é a fonte única da verdade:

- [`src/rankeddojo/resources/pack-contract.md`](src/rankeddojo/resources/pack-contract.md)

Packs de exemplo públicos ficam em [`examples/packs`](examples/packs):

- `c-basics`
- `cpp-basics`
- `python-basics`
- `java-basics`
- `sample_rank`
- `rank02-practice`, quando presente no checkout

Packs privados/originais ficam em `_local/packs/` e são ignorados pelo Git.

Nota de segurança: packs podem conter harnesses ou referências que são
compiladas/executadas durante a correção com as permissões do usuário atual. Importe
apenas packs em que você confia. O importador rejeita paths inseguros, ZIP traversal,
symlinks/junctions e estratégias não suportadas, mas não oferece um sandbox completo.

## Workspace e dados

No Windows, a configuração do app e o banco SQLite local ficam em:

```text
%APPDATA%\rankeddojo\
```

(Linux/macOS seguem o mecanismo padrão do sistema, usando a mesma pasta `rankeddojo`.)

Instalações antigas que ainda tinham dados em `%APPDATA%\exam-trainer\` são migradas
automaticamente e uma única vez no primeiro startup após a atualização: config, banco,
packs importados e temas são movidos para a nova pasta sem apagar nada. Se a pasta nova
já existir com dados (por exemplo, instalação feita direto na versão atual), a pasta
antiga não é tocada nem mesclada automaticamente.

Arquivos de treino e de exame ficam separados:

```text
workspace/
├── training/
│   └── <pack_id>/
│       └── <activity_id>/
└── exams/
    └── <session_id>/
        └── <activity_id>/
```

Workspaces de treino legados são migrados de forma defensiva quando é seguro fazê-lo.
Arquivos existentes do usuário nunca são apagados silenciosamente.

## Build Windows

Build a partir do `.venv` do projeto:

```bash
python build.py            # builda só se houver mudança relevante nos inputs
python build.py --force    # força o rebuild
python build.py --run      # builda se necessário e já abre o executável
```

Saída:

```text
_local/dist/RankedDojo.exe
_local/dist/RankedDojo.exe.sha256
```

No Linux/macOS o fluxo oficial é a instalação via `pip` acima. O build PyInstaller
continua disponível para Windows e gera `RankedDojo.exe`.

Comportamento do build:

- faz hash apenas dos inputs de release/build (`src/`, `examples/`, README, LICENSE,
  CHANGELOG, spec, pyproject e versões de ambiente);
- ignora `_local/`, packs privados, metadados do Git, caches, logs e arquivos
  temporários;
- builda em `_local/build/_staging`;
- mantém o executável anterior se o PyInstaller falhar;
- só substitui o executável após um build bem-sucedido;
- reporta bloqueios de execução do Windows App Control / Smart App Control sem
  traceback.

Assinatura de código confiável é um passo externo de release. Nenhum certificado fica
armazenado neste repositório.

## Checklist de release

1. Rodar a suíte de testes completa.
2. Buildar com `python build.py --force`.
3. Fazer smoke test do executável.
4. Assinar o executável quando houver certificado confiável disponível.
5. Verificar o checksum SHA-256.
6. Publicar uma release manualmente, se desejado.

Nenhuma release remota é criada pelo script de build.

## Documentação

- Snapshot operacional atual: [`docs/current-state.md`](docs/current-state.md)
- Log de sessão/status: [`docs/project-status.md`](docs/project-status.md)
- Decisões de arquitetura: [`docs/decisions`](docs/decisions)
- Notas de runtime: [`docs/runtimes.md`](docs/runtimes.md)
- Contrato de pack: [`src/rankeddojo/resources/pack-contract.md`](src/rankeddojo/resources/pack-contract.md)

## Licença

RankedDojo é distribuído sob a [Licença MIT](LICENSE).
