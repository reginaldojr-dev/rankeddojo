# Current Project State

## Git

- Branch base: `main`.
- Branch de trabalho atual: `feat/declarative-test-contracts-and-editor-reuse`.
- HEAD base confirmado para iniciar Fase D/E: `474d852192344a459ca12cf57854e39dcff6f96`.
- Última etapa concluída: Fase D/E pós-V1 em validação local.
- Working tree esperado após conclusão/commit: limpo.
- Roadmap oficial mais recente encontrado: `_local/agents_outputs/roadmap-fechamento-v1-final-v8.md`.

## Tests

- Suíte completa validada após Fase D/E: `338 passed, 6 skipped, 43 subtests passed`.
- Focados Fase D/E: contratos/loaders/generators/preflight/grader/runtime/editor/Qt/workspace verdes.
- Focados S6: `137 passed, 4 skipped, 40 subtests passed`.
- Build real: `python build.py --force` OK.
- Build cache/run: `python build.py --run` pulou rebuild e abriu o executável.
- Smoke do executável validado na S6 antes do rename público; novo build deve gerar `_local/dist/RankedDojo.exe`.
- Smoke Qt responsivo: 760x520, 1024x720, 1440x900 e 1920x1080.
- Skips conhecidos: compilador C/C++ ausente em testes dependentes de toolchain; symlink no Windows exige privilégio.
- Warnings pytest conhecidos: nenhum na suíte final da S6.

## Architecture

- Domain: modelos puros para activity/exercise, validation, progress, grading, identifiers e workspace; sem Qt/SQLite/filesystem concreto.
- Application: `MVPTrainerCoordinator` ainda centraliza fluxos, mas delega projeções de histórico para `HistoryService`, resolve behavior/scope por `SessionPolicyRegistry` e expõe capabilities para UI.
- Ports: contratos para compiler, editor, grader, pack, progress, runtime e workspace.
- Adapters: Qt UI, SQLite, filesystem/workspace, pack loading/import, runtimes/toolchains, editor e graders concretos.
- UI: PySide6; consome application/services; subjects Markdown centralizados em `SubjectMarkdownView`; Home usa `StudyIntent`/`PackPromptBuilder`; Histórico consome projeções da application.
- Persistence: SQLite local via adapters; schema v3 guarda identidade neutra (`activity_id`, `activity_kind`) e `policy` mantendo colunas legadas.

## Core Contracts

- ActivityDefinition: fronteira neutra exposta por ExerciseDefinition; inclui `programming_language`, `content_language`, `usage` e `validation_plan`.
- TestContract: contrato declarativo opcional de inputs em `tests.contract`; generators respeitam args posicionais, string, integer, choice, sequência de inteiros e no-args.
- ValidationPlan: contém steps declarativos derivados do contrato de pack.
- ValidationStep: representa strategy/execution/testes necessários para validação atual.
- RuntimeRegistry: autoridade para runtimes registrados, status, disponibilidade, descriptors e lookup por linguagem.
- SessionPolicy: `TrainingPolicy` e `ExamPolicy` registradas em `SessionPolicyRegistry`; policy futura pode ser registrada pelo coordinator.
- History: `HistoryService` é leitura/projeção e oferece query/timeline por pack, activity, session, policy e status.
- Workspace: `WorkspaceScope` no port; adapter resolve roots físicos para training/exams/projects.
- StudyIntent/PackPromptBuilder: modelo de intenção de estudo e prompt vendor-neutral para criação externa de packs.

## Pack Contract

- Schema atual: `schema_version: 3`.
- `programming_language`: linguagem/runtime efetiva da activity.
- `content_language`: idioma humano do conteúdo; independente do runtime.
- `usage`: restrições estruturadas declarativas/pedagógicas (`allowed`, `forbidden`, `constraints`, `style`, `behavior`, `notes`).
- `tests.contract`: extensão aditiva v3 para declarar formato dos inputs gerados; ausência mantém comportamento legado.
- `reference`/`solution`: opcionais; exigidas apenas quando expectation/validator declara necessidade, como `reference_output`.
- Runtimes suportados no contrato/app: C, C++, Python e Java.
- Packs públicos versionados: `c-basics`, `cpp-basics`, `python-basics`, `java-basics`, `sample_rank`.
- Packs locais privados/estudo em `_local/packs/` continuam ignorados, incluindo `rank02-practice` e `rank02-original` a `rank06-original`.
- Packs v3 podem declarar `workspace.scope: pack` para um projeto compartilhado; o padrão legado continua `exercise`.
- A strategy `python_project` usa somente checks confiáveis declarativos do core (`file_exists`, `module_imports`, `callable_exists`, `class_exists`, `call_function`, `raises`).

## Persistence

- SQLite é a autoridade local da V1.
- Schema atual: v3.
- Migration v3 adiciona identidade neutra e policy preservando colunas/dados antigos.
- Path principal no Windows: `%APPDATA%\rankeddojo\` (renomeado de `%APPDATA%\exam-trainer\` na migração de namespace pós-Fase 2; dados antigos são migrados automaticamente uma única vez).
- Packs públicos em `examples/packs`; packs locais privados em `_local/packs`.
- DB readonly investigado na S4: causa provável é ACL/sandbox, não schema corrompido.

## Workspace

- Layout atual de treino: `training/<pack_id>/<activity_id>`.
- Layout atual de provas: `exams/<session_id>/<activity_id>`.
- Existe migração defensiva de workspace legado de treino quando seguro.
- Nenhum workspace antigo deve ser apagado silenciosamente.
- `projects/<pack>/<project>` é reservado no adapter como scope futuro, sem feature funcional.
- Um workspace progressivo usa `training/<pack_id>/project` e preserva arquivos existentes; subjects ficam em `.rankeddojo/subjects/`.

## UI

- Produto público: `RankedDojo`.
- Home inclui `QUERO ESTUDAR ALGO NOVO`, geração/cópia de prompt e importação de pack.
- Training, Exam, Histórico, Configurações e ajuda de pack existem em Qt.
- Histórico oferece Overview, Learning por linguagem, Pack History e visões separadas de Training/Exam Sessions; inspector é contextual e só aparece após seleção.
- Configurações > Packs mostra resumo de contrato/capabilities e abre a documentação completa de packs.
- Subjects `subject.md` continuam Markdown e são renderizados com suporte nativo do Qt.
- UI não deve acessar SQL nem decidir regras de session/policy.
- A IDE abre a pasta da activity atual. Após o usuário abrir a IDE em um contexto, trocas de exercise reutilizam a mesma janela quando o adapter suporta isso; VS Code usa `--reuse-window`.

## Build

- Versão V1: `1.0.0`.
- Instalação Linux oficial: `python -m pip install rankeddojo` seguida de `rankeddojo`.
- Desenvolvimento instalado: `python -m pip install -e .` seguido de `rankeddojo`.
- Desenvolvimento local via `make`: instala o checkout, prepara `libxcb-cursor.so.0` localmente no Ubuntu 22.04 amd64 quando necessário e inicia o app.
- `rankeddojo` e `python -m rankeddojo.main` também executam o bootstrap Linux antes de importar PySide6; checkouts usam `.vendor/linux` e instalações pip usam `${XDG_CACHE_HOME:-~/.cache}/rankeddojo/linux`.
- Dependências de runtime vêm de `pyproject.toml`; PyInstaller permanece somente no extra `build` para o fluxo Windows.
- Build helper Windows: `build.py`.
- Spec: `RankedDojo.spec`.
- Executável Windows: `_local/dist/RankedDojo.exe`.
- Checksum: `_local/dist/RankedDojo.exe.sha256`.
- SHA-256 validado na S6: `fc4f5a9509be544db639d5e370b8242e7cb128ff9baa00f1679220be00e8a739`.
- Build usa staging em `_local/build/_staging`, substitui o exe só após sucesso e mantém o exe anterior em falha.
- Signing/trusted certificate é pendência externa; nenhum certificado fica no repo.

## Known Issues

- `MVPTrainerCoordinator` ainda é grande e concentra fluxos de treino/prova.
- Colunas legadas `exercise_id`/`mode` permanecem por compatibilidade.
- Restrições de `usage` ainda não são verificadas automaticamente.
- `tests.contract` valida formato de inputs gerados, mas não prova correção semântica da solução.
- C/C++/Python/Java dependem de toolchains externos instalados/configurados.
- Release público Windows ainda precisa assinatura confiável para reduzir bloqueios de App Control.

## Important Invariants

- Exam default: 4 horas.
- Pack pode sobrescrever duração da prova.
- Deadline de prova é absoluto; fechar o app não pausa o tempo.
- Prova exige 100%.
- Em prova: PASS avança; FAIL mantém a mesma activity; timeout salva resultado parcial.
- Training e Exam têm progresso separado.
- Progresso é namespaced por pack.
- Runtime/toolchain ausente bloqueia correção/preflight, mas não impede ver subject/workspace quando aplicável.
- `programming_language` != `content_language`.
- StudyIntent mantém `programming_language` separado de `content_language`.
- Reference só é obrigatória quando validator/expectation exige.
- Pack não pode declarar comandos shell arbitrários.
- Test contracts são dados puros; sem scripts, comandos, shell, `eval` ou `exec`.
- Domain não depende de Qt/SQLite/filesystem concreto.
- Application não deve importar adapter concreto.
- UI não deve conter regra de negócio nem SQL.
- Material privado/original não deve ser versionado nem empacotado no release público.
