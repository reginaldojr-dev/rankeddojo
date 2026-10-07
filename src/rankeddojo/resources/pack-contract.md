# Contrato de Pack — RankedDojo

Fonte única do contrato suportado por esta versão do app.

Packs são declarativos: eles descrevem atividades, arquivos, validação e metadados.
Eles não podem declarar comandos de shell, scripts arbitrários, plugins ou graders próprios.

Packs distribuídos não devem conter soluções completas dos exercícios. O pack contém
subjects e dados de validação para a implementação do aluno. Soluções corretas podem
existir apenas em testes internos do RankedDojo ou fixtures de desenvolvimento que
não sejam distribuídos como conteúdo de pack.

## Segurança

Harnesses e arquivos de suporte executáveis são compilados/executados durante a
correção, com as permissões do usuário local. A importação nunca executa código.

O app valida ids, paths relativos, ZIPs, symlinks/junctions e arquivos declarados antes
de copiar o pack. Não há sandbox completa; importe apenas conteúdo confiável.

## Schema

Use `schema_version: 3`.

O contrato v3 separa:

- `programming_language`: linguagem/runtime da atividade (`c`, `cpp`, `python`, `java`);
- `content_language`: idioma humano do conteúdo (`pt-BR`, `en`, etc.).

O idioma do conteúdo cobre subject, título, descrição, instruções, dicas e textos
pedagógicos. Ele não altera runtime, validação, compilação ou execução. UTF-8 é o padrão.

## Estrutura

```text
my-pack/
├── pack.json
└── level0/
    └── activity_id/
        ├── exercise.json
        └── subject.md
```

O schema usa o layout de level/activity acima. Semanticamente, `subject.md` é o
enunciado apresentado ao aluno; fixtures declarativas, valores esperados e dados
de validação suportados ficam no `exercise.json` ou em arquivos de suporte declarados.
Não existe diretório `solutions/` na estrutura recomendada para distribuição.

## pack.json

### Progressive project workspaces

Schema v3 packs may opt into a shared project workspace:

```json
"workspace": {"scope": "pack"}
```

The default is `{"scope": "exercise"}`. Exercise scope preserves the
traditional isolated layout. Pack scope uses one `training/<pack_id>/project`
directory (and one isolated exam-session project when used by an exam). Files
already present are never overwritten silently; a starter is copied only when
the target does not exist.

Python multi-file projects may declare `validation.strategy = "python_project"`
and a list of trusted declarative `validation.checks`. Supported checks are
`file_exists`, `module_imports`, `callable_exists`, `class_exists`,
`call_function`, and `raises`. The pack supplies data only. It cannot provide a
grader script, plugin, arbitrary shell command, `eval`, or `exec` payload.

```json
{
  "schema_version": 3,
  "id": "c-basics",
  "name": "C Basics",
  "version": "1.0.0",
  "languages": ["c"],
  "content_language": "pt-BR",
  "topics": ["c", "basics"],
  "description": "Pack autoral de fundamentos.",
  "exam": { "duration_minutes": 60 },
  "levels": [
    { "id": "level0", "path": "level0" }
  ]
}
```

`languages` é metadado/índice. A linguagem efetiva pertence à atividade.
Sem `exam.duration_minutes`, a prova usa 4 horas.

## exercise.json

```json
{
  "schema_version": 3,
  "id": "argc_counter",
  "type": "exercise",
  "name": "Argc Counter",
  "subject": "subject.md",
  "programming_language": "c",
  "content_language": "pt-BR",
  "topics": ["c", "basics"],
  "submission": { "filename": "argc_counter.c" },
  "usage": {
    "allowed": { "functions": ["write"] },
    "forbidden": { "functions": ["printf", "puts"] },
    "constraints": [
      "Não escreva mensagens extras.",
      "A saída deve terminar com newline."
    ]
  },
  "validation": {
    "strategy": "program_output",
    "tests": {
      "generator": "fixed_cases",
      "expectation": "literal",
      "cases": [
        { "args": ["a", "b"], "stdin": "", "expected_stdout": "2\n" }
      ]
    },
    "limits": { "timeout_seconds": 3 }
  }
}
```

### submission

- `filename`: arquivo principal esperado na workspace do aluno;
- `extra_files`: arquivos extras que o aluno também deve implementar, para exercícios
  multi-file.

### validation

Campos comuns:

- `strategy`: estratégia de execução reconhecida pelo app;
- `harness`: harness fornecido pelo pack quando a linguagem/strategy exige;
- `entry`: função/classe principal quando a linguagem/strategy exige;
- `args_format`: formato dos argumentos para function_call com harness do app;
- `reference`: solução de referência, somente quando a expectation/validator exigir;
- `tests`: casos e expectation;
- `limits.timeout_seconds`: timeout por caso;
- `support_files`: arquivos copiados para a workspace.

Estratégias atuais:

- `program_output`: compila/executa programa completo;
- `function_call`: valida função usando harness do pack ou do app.

O app pode evoluir para novas strategies/validators via registries. Packs devem usar
somente capabilities suportadas pela versão instalada.

### usage

`usage` descreve restrições de estudo e implementação de forma estruturada, sem criar
uma DSL de validação. O subject pode renderizar essas informações e ferramentas futuras
como o PackPromptBuilder podem consultá-las diretamente.

```json
{
  "allowed": {
    "functions": ["write"],
    "libraries": [],
    "imports": [],
    "headers": ["unistd.h"],
    "apis": [],
    "flags": []
  },
  "forbidden": {
    "functions": ["printf"],
    "libraries": [],
    "imports": [],
    "headers": [],
    "apis": [],
    "flags": []
  },
  "constraints": ["Não use conversões prontas."],
  "style": ["Prefira funções pequenas."],
  "behavior": ["Não escreva mensagens extras."],
  "notes": ["Essas restrições são pedagógicas nesta versão."]
}
```

Todos os campos são opcionais. Nesta V1, o loader valida o formato e o importer
continua validando arquivos declarados e capabilities. Restrições como funções,
imports, headers, estilo e comportamento são declarativas/pedagógicas, a menos que
um validator futuro declare suporte explícito para verificá-las.

### reference

```json
{
  "source": "internal/reference/main.cpp",
  "harness": "harness/main.cpp",
  "extra_files": ["internal/reference/helper.cpp"]
}
```

`reference` não é obrigatória no nível genérico da activity. A compatibilidade com
`reference_output` permanece para fixtures internas, packs legados e casos de
desenvolvimento que realmente precisem dela; não é o caminho recomendado para
packs distribuídos novos. `literal` não exige uma implementação de referência.

`extra_files` permite referências multi-file em C++, Java e linguagens futuras.

### tests

Geradores atuais:

- `fixed_cases`;
- `random_arguments`;
- `random_int_array`;
- `random_integer`;
- `random_string`.

Expectations atuais:

- `reference_output`;
- `literal`;
- `echo_arguments`;
- `sum_integers`.

Para packs novos, prefira `literal` quando bons casos determinísticos forem suficientes.
Use `reference_output` somente em conteúdo interno/legado quando a saída esperada
depender tecnicamente de uma referência real. Não inclua uma solução completa no
pack distribuído para satisfazer essa opção.
Expectations embutidas são mantidas para regressão e exemplos simples.

#### tests.contract

`tests.contract` é opcional. Quando ausente, os generators mantêm o comportamento
legado. Quando presente, ele declara o formato dos inputs que o generator deve
produzir, sem código, scripts, shell ou expressões executáveis.

Exemplo para uma atividade estilo `do_op`:

```json
{
  "generator": "random_arguments",
  "expectation": "reference_output",
  "contract": {
    "args": [
      { "kind": "integer", "min": -100, "max": 100 },
      { "kind": "choice", "values": ["+", "-", "*", "/", "%"] },
      { "kind": "integer", "min": 1, "max": 100 }
    ]
  }
}
```

Kinds suportados:

- `string`: gera uma string simples;
- `integer`: gera representação decimal válida entre `min` e `max`;
- `choice`: escolhe um valor de `values`;
- `integer_sequence`: gera uma sequência de inteiros.

Exemplo de sequência com tamanho no primeiro argumento:

```json
{
  "kind": "integer_sequence",
  "min": -50,
  "max": 50,
  "min_items": 1,
  "max_items": 8,
  "include_length_arg": true
}
```

Com `include_length_arg: true`, os argumentos gerados seguem o formato:

```text
<quantidade> <valor1> <valor2> ...
```

`contract.args: []` declara atividade sem argumentos; nesse caso os casos gerados
usam `args = []`.

Se um generator não suportar o contrato declarado, o app reporta conteúdo inválido
no preflight/import/validação em vez de cair silenciosamente no comportamento legado.

## Linguagens

| programming_language | Runtime/toolchain | Observação |
| --- | --- | --- |
| `c` | compilador C compatível | `program_output` e `function_call` com harness do pack |
| `cpp` | compilador C++17 compatível | suporta arquivos `.cpp/.hpp` e exercícios multi-file |
| `python` | Python 3.9+ do sistema | `function_call` usa harness do app com `entry` |
| `java` | JDK (`javac` + `java`) | `program_output` usa `entry` como classe principal |

Ausência de runtime/toolchain bloqueia correção e preflight de prova, mas não impede
visualizar o subject.

## Subject

Subjects são UTF-8 e podem estar em `pt-BR`, `en` ou outro locale futuro:

```text
Assignment name  : argc_counter
Expected files   : argc_counter.c
--------------------------------------------------------------------------------

Escreva um programa em C que imprime a quantidade de argumentos recebidos.
```

O app não infere runtime a partir do idioma humano do subject. O subject deve explicar
objetivo, arquivos esperados, entrada/saída, regras, restrições permitidas/proibidas
e exemplos quando isso ajudar o estudo.

## Importação

1. Crie uma pasta ou ZIP com `pack.json`.
2. Importe em Configurações > Packs.
3. O app valida schema, arquivos declarados, paths seguros e capabilities.
4. Se houver código executável, o app lista os arquivos antes de copiar o pack.
