# Localizador de Contatos Empresariais

Aplicação desktop em Python que lê uma planilha de clientes, pesquisa os
contatos de cada empresa em fontes públicas da internet e preenche a planilha
de volta — colorindo cada linha conforme o resultado e registrando a origem
exata de cada dado.

**Princípio central:** o programa nunca inventa, infere ou completa uma
informação. Todo telefone, e-mail ou site gravado foi extraído de uma página
pública real e carrega a URL de origem no log. Havendo qualquer dúvida sobre a
identidade da empresa, o campo fica em branco e a linha vai para revisão manual.

---

## Índice

- [Instalação do zero (passo a passo)](#instalação)
- [Uso](#uso)
- [Como a planilha é preenchida](#como-a-planilha-é-preenchida)
- [Fontes e níveis de confiança](#fontes-e-níveis-de-confiança)
- [Como a precisão é garantida](#como-a-precisão-é-garantida)
- [Arquivos gerados](#arquivos-gerados)
- [Estrutura do projeto](#estrutura-do-projeto)
- [Configuração](#configuração)
- [Testes](#testes)
- [Limitações conhecidas](#limitações-conhecidas)
- [Solução de problemas](#solução-de-problemas)

---

## Instalação

Guia completo do zero, para quem acabou de baixar o projeto. Se você já tem
Python e Git, pule direto para o [passo 3](#3-baixar-o-projeto).

### 1. Instalar o Python

Baixe em **[python.org/downloads](https://www.python.org/downloads/)** — versão
**3.10 ou superior**.

> No instalador do Windows, marque **"Add python.exe to PATH"** na primeira
> tela, antes de clicar em Install. Sem isso o comando `python` não funciona no
> terminal e você terá que reinstalar.

Confira abrindo um terminal novo (PowerShell no Windows, Terminal no
macOS/Linux):

```bash
python --version
```

Deve aparecer algo como `Python 3.12.2`. Se disser que o comando não foi
encontrado, feche e reabra o terminal; se persistir, reinstale marcando a opção
do PATH.

### 2. Instalar o Git

Baixe em **[git-scm.com/downloads](https://git-scm.com/downloads)** e aceite as
opções padrão. Confira:

```bash
git --version
```

> Alternativa sem Git: na página do projeto no GitHub, clique em **Code →
> Download ZIP**, extraia a pasta e siga a partir do passo 4.

### 3. Baixar o projeto

```bash
git clone https://github.com/AntonioCampeaoLobo/LocalizadorContatos.git
```

Entre na pasta criada:

```bash
cd LocalizadorContatos
```

**Todos os comandos seguintes são executados de dentro dessa pasta.**

### 4. Criar o ambiente virtual

O ambiente virtual isola as bibliotecas do projeto das do resto do sistema —
evita conflitos de versão e erros de permissão.

```bash
python -m venv .venv
```

Isso cria a pasta `.venv`. Ela é local, não vai para o Git e não deve ser
copiada para outro computador.

### 5. Ativar o ambiente virtual

No **Windows (PowerShell)**:

```bash
.venv\Scripts\Activate.ps1
```

No **Windows (Prompt de Comando)**:

```bash
.venv\Scripts\activate.bat
```

No **macOS ou Linux**:

```bash
source .venv/bin/activate
```

Deu certo quando aparece `(.venv)` no início da linha do terminal.

> **Erro comum no Windows:** se aparecer *"a execução de scripts foi desabilitada
> neste sistema"*, o PowerShell está bloqueando scripts. Rode o comando abaixo e
> tente ativar de novo — ele libera apenas para o seu usuário, sem alterar
> configurações do sistema:
>
> ```bash
> Set-ExecutionPolicy -Scope CurrentUser -ExecutionPolicy RemoteSigned
> ```

### 6. Instalar as bibliotecas

```bash
pip install -r requirements.txt
```

Leva de 1 a 3 minutos. Se aparecerem erros de permissão, quase sempre significa
que o ambiente virtual não está ativo — volte ao passo 5 e confirme o `(.venv)`.

### 7. Instalar o navegador do Playwright

```bash
playwright install chromium
```

Baixa cerca de 150 MB. É o navegador usado para consultar o Google Maps.

> Este passo é **opcional**. Sem ele o programa funciona normalmente, apenas sem
> a fonte Google Business Profile — as demais (site oficial, Receita Federal,
> buscadores) continuam ativas.

### 8. Conferir se está tudo certo

```bash
python main.py --checar
```

A resposta esperada é `Todas as dependências estão instaladas.` Se algum item
aparecer como `[FALTA]`, repita o passo 6.

Opcionalmente, rode a bateria de testes — ela não acessa a internet e leva menos
de um segundo:

```bash
python -m unittest discover -s tests
```

### 9. Executar

Interface gráfica:

```bash
python main.py
```

No Windows, o atalho `executar.bat` faz o mesmo e dispensa ativar o ambiente
virtual — ele detecta a pasta `.venv` sozinho:

```bash
.\executar.bat
```

### 10. Primeira execução

Antes de processar uma carteira inteira, rode um teste com poucas empresas para
conferir a qualidade do resultado:

```bash
python main.py --cli --planilha "CAMINHO\DA\SUA\PLANILHA.xlsx" --limite 10
```

Abra `saida\Empresas_Preenchidas.xlsx` e `saida\log.txt` e confira se os
contatos encontrados batem com as empresas certas. Estando bom, rode sem o
`--limite`.

> A planilha original **nunca é alterada** — tudo é gravado em
> `saida\Empresas_Preenchidas.xlsx`.

### Nas próximas vezes

Os passos 1 a 7 são feitos uma única vez. Depois, para usar o programa, basta:

```bash
cd LocalizadorContatos
```

E então:

```bash
.\executar.bat
```

### Quanto tempo leva o processamento

Medições reais em uma carteira de 409 empresas: cerca de **30 a 45 segundos por
empresa**, variando conforme quantas fontes estão respondendo.

| Configuração | 100 empresas | 400 empresas |
|---|---|---|
| `--threads 5` (padrão) | ~40 min | 2h30 a 3h30 |
| `--threads 3` | ~1h | 4h a 4h30 |
| `--threads 2 --delay-min 5 --delay-max 12` | ~2h | 7h a 9h |

A configuração conservadora é mais lenta, porém reduz muito o risco de bloqueio
por IP nos buscadores.

Pode interromper a qualquer momento: a planilha é salva após **cada** empresa e,
ao reabrir, as já preenchidas são ignoradas — o processamento continua de onde
parou. Dá para rodar durante a noite sem supervisão.

> Mantenha o `Empresas_Preenchidas.xlsx` **fechado** no Excel enquanto o
> programa roda, senão a gravação falha.

---

## Uso

### Interface gráfica

```bash
python main.py
```

1. **Selecionar planilha** — escolha o `.xlsx`. O rodapé mostra quantas
   empresas existem e quantas serão pesquisadas.
2. Ajuste, se quiser, o número de empresas simultâneas, o intervalo entre
   requisições e quais fontes usar.
3. **Iniciar pesquisa**. Acompanhe pela barra de progresso, pelos indicadores e
   pelo log em tempo real.
4. **Pausar / Continuar / Cancelar** funcionam a qualquer momento. Nada é
   perdido: a planilha é salva após **cada** empresa.

### Linha de comando

Útil para execuções longas, servidores e agendamento.

```bash
python main.py --cli --planilha "C:\caminho\carteira.xlsx"
```

Opções principais:

| Opção | Efeito |
|---|---|
| `--planilha ARQUIVO` | planilha de entrada (obrigatório com `--cli`) |
| `--saida PASTA` | pasta dos arquivos gerados (padrão: `./saida`) |
| `--threads N` | empresas simultâneas (padrão: 5) |
| `--limite N` | processa apenas as N primeiras pendentes — ideal para testar |
| `--delay-min` / `--delay-max` | intervalo aleatório entre requisições, em segundos |
| `--sem-maps` | não consulta o Google Maps (dispensa o Playwright) |
| `--sem-busca` / `--sem-cnpj` / `--sem-site` | desliga a fonte correspondente |
| `--verboso` | log detalhado no terminal |
| `--checar` | apenas verifica dependências e sai |

Teste rápido antes de rodar a carteira inteira:

```bash
python main.py --cli --planilha carteira.xlsx --limite 10 --threads 3
```

`Ctrl+C` cancela com segurança — a planilha permanece salva.

---

## Como a planilha é preenchida

A planilha de origem **nunca é modificada**. Ela é copiada para
`Empresas_Preenchidas.xlsx` na pasta de saída, e todas as gravações acontecem
sobre a cópia, preservando 100% da formatação original (larguras, fontes,
bordas, formatos).

### Colunas

O programa localiza as colunas pelo texto do cabeçalho, sem depender da posição:

| Coluna | Uso |
|---|---|
| **Razão Social** | informação principal da pesquisa |
| **Cidade** | desambigua homônimos; entra em todas as consultas |
| **Região** | apenas leitura |
| **Contato** | onde telefones e e-mails são gravados |
| **Confiança** | criada automaticamente se não existir |
| **Observação** | criada automaticamente se não existir |

### Regras de gravação

- Conteúdo existente **nunca** é apagado. Novos contatos são anexados abaixo do
  que já estava na célula.
- Empresas cuja coluna Contato **já contém um telefone válido** são ignoradas.
  Uma célula com apenas e-mail continua pendente (falta o telefone).
- Múltiplos telefones e e-mails ficam na mesma célula, um por linha:
  ```
  (19) 3555-0100
  (19) 99555-0100
  contato@empresa.com.br
  ```
- A planilha é salva **após cada empresa**, de forma atômica (grava em arquivo
  temporário e só então substitui o definitivo). Uma queda no meio do processo
  não corrompe o que já foi encontrado.

### Cores das linhas

| Cor | Situação |
|---|---|
| 🟩 Verde claro | telefone encontrado |
| 🟨 Amarelo | apenas e-mail encontrado |
| 🟥 Vermelho | nenhum contato confiável encontrado |
| 🟧 Laranja | dúvida sobre a empresa — revisão manual necessária |

---

## Fontes e níveis de confiança

As fontes são consultadas nesta ordem, e a cascata para assim que um telefone
de confiança **Alta** é confirmado:

| # | Fonte | Confiança |
|---|---|---|
| 1 | Site oficial da empresa | **Alta** |
| 2 | Google Business Profile (Maps) | **Alta** |
| 3 | Receita Federal / dados públicos de CNPJ | **Alta** |
| 4 | LinkedIn da empresa | Média |
| 5 | Diretórios empresariais confiáveis | Média |
| 6 | Catálogos comerciais e outras fontes | Baixa |

**Somente confiança Alta ou Média é gravada automaticamente.** Quando o melhor
dado disponível é de confiança Baixa, o contato **não** é preenchido; a coluna
Observação recebe `Necessita conferência manual.` e a linha fica laranja.

A confiança da fonte pode ser **rebaixada** quando faltam confirmações:

- cidade da planilha não localizada na página → um nível abaixo;
- DDD do telefone incompatível com a UF da empresa → um nível abaixo (ou direto
  para Baixa, se a identidade não foi confirmada por CNPJ);
- DDD herdado do número vizinho → um nível abaixo;
- cadastro do CNPJ baixado/inapto → Alta vira Média.

---

## Como a precisão é garantida

Esta seção descreve os mecanismos que impedem um contato errado de entrar na
planilha. Vários deles nasceram de falsos positivos observados em testes reais.

### 1. Nenhum dado sem procedência (garantia estrutural)

Não é uma convenção: é impossível registrar um contato sem origem. A classe
`Evidencia` rejeita no construtor qualquer instância sem URL, e `DadoContato`
exige uma `Evidencia`. Não há caminho de código que grave um telefone
"solto".

### 2. Pesquisa inteligente por CNPJ

Antes da pesquisa principal, o sistema busca o CNPJ da empresa, valida o dígito
verificador e o consulta em APIs públicas que espelham a Receita Federal
(BrasilAPI, MinhaReceita, CNPJ.ws). O CNPJ só é adotado quando a consulta
oficial confirma **razão social e município**. Confirmado, ele vira a prova de
identidade mais forte disponível — e o cadastro ainda fornece telefone e e-mail
oficiais.

Se dois CNPJs distintos forem confirmados com razões sociais diferentes, o caso
é tratado como ambíguo e nada é preenchido.

### 3. Consultas limpas (o que mais afeta a taxa de acerto)

Razões sociais brasileiras são cheias de iniciais e formas societárias, e
buscadores tratam iniciais isoladas como termos independentes. Antes de
pesquisar, o nome é limpo:

| Razão social na planilha | Consulta enviada |
|---|---|
| `A. B. FERRAZONI COMERCIAL` | `FERRAZONI COMERCIAL` |
| `A.R. BENTANO MATERIAIS EIRELI` | `BENTANO MATERIAIS` |
| `B2 TRANSPORTES E LOGISTICA LTDA ME` | `B2 TRANSPORTES LOGISTICA` |

> **Caso real:** buscar `"A. B. FERRAZONI COMERCIAL" SUMARE CNPJ` devolvia a
> tabela do Brasileirão **Série B** e o verbete da letra "B" na Wikipédia —
> nenhum resultado sobre a empresa. A razão social original nunca é usada como
> consulta; ela permanece apenas como referência para validar a correspondência.

Cada empresa gera várias consultas, alternando busca livre e frase exata, e a
etapa de CNPJ percorre as variantes até obter candidatos.

### 4. Comparação conservadora de nomes

O comparador remove sufixos societários (LTDA, ME, EPP, EIRELI, S/A) e separa
os **tokens distintivos** — as palavras que realmente identificam a empresa —
das palavras genéricas do ramo (`transportes`, `comercio`, `materiais`,
`executiva`, `nacional`, `express`…).

Consequências práticas:

- `VELTRINI BUFFET` **não** casa com `VELTRINI TRANSPORTES`;
- `SILVA COMERCIO DE MATERIAIS` **não** casa com `PEREIRA COMERCIO DE MATERIAIS`;
- `PORTAIS SERRA AZUL EMPREENDIMENTOS IMOB` (truncado na planilha) **casa**
  com `Portais Serra Azul Empreendimentos Imobiliários Ltda`.

### 5. Validação de identidade do site oficial

Um site só é aceito como oficial se:

- o **CNPJ** da empresa aparecer na página (prova definitiva); **ou**
- pelo menos **dois** tokens distintivos forem confirmados; **ou**
- havendo um único token distintivo, ele estiver no **próprio domínio** e o
  domínio refletir o nome **inteiro** da empresa.

Sem a cidade confirmada na página, a confiança cai para Média.

A exigência de o domínio cobrir o nome inteiro é o que separa duas homônimas.
A cobertura conta **todas** as palavras da razão social, inclusive as genéricas
— é justamente a palavra genérica que diferencia as duas:

| Razão social | Domínio | Cobertura | Decisão |
|---|---|---|---|
| `VELTRINI BUFFET` | `veltrini.app.br` | 50% | rejeita |
| `VELTRINI TRANSPORTES` | `veltrini.app.br` | 50% | rejeita |
| `A.R. BENTANO MATERIAIS` | `bentanomateriais.com.br` | 100% | aceita |

> **Caso real:** duas empresas distintas da mesma cidade, uma de buffet e outra
> de transportes, compartilhavam o token `veltrini` presente no domínio e
> receberam **contatos idênticos** do mesmo site — que era de outra empresa, em
> outra região. Foi o erro mais grave encontrado em produção.

> **Caso real:** `J & J EXECUTIVA TRANSPORTES LTDA - ME`, de Cosmópolis/SP,
> casava com `executiva-exemplo.com.br` — uma empresa do Paraná — porque
> "executiva" era tratada como token distintivo. Hoje a palavra está na lista de
> genéricas, a razão social fica sem token identificador e o site é rejeitado.
> A empresa passou a ser resolvida corretamente pela Receita Federal, com os
> telefones (19) 3555-1010 e (19) 3555-1011.

### 6. E-mails de terceiros são descartados

Em um site oficial, só são aceitos e-mails do **próprio domínio** ou de
provedores gratuitos (Gmail, Hotmail, UOL…). Qualquer outro domínio pertence a
um terceiro citado na página.

> **Caso real:** `imprensa@prefeitura-exemplo.sp.gov.br` — o e-mail da prefeitura —
> foi capturado do site de uma transportadora e quase entrou na planilha.

### 7. Validação rigorosa de telefones

Um número só é aceito se tiver DDD válido da Anatel, 10 dígitos (fixo iniciado
em 2–5) ou 11 dígitos (celular iniciado em 9). São rejeitados: CNPJ, CEP,
datas, sequências repetidas e números sem DDD isolados.

Formatos reconhecidos, incluindo os legados das carteiras antigas:

```
(19) 3555-0100      19 99555-0100       +55 11 4004-5500
019 35550100        (019) 3555-0200     1935550100
0800 555 0100       5519995550100
```

Números sem DDD herdam o do número imediatamente anterior — convenção
tipográfica brasileira (`(19) 3555-0100 / 99555-0100`) — mas somente se
estiverem a menos de 40 caracteres de distância e sem texto corrido entre eles.
Números com DDD herdado têm a confiança rebaixada.

**Coerência de DDD com a localidade.** A checagem é feita no nível da **cidade**,
não da UF. Comparar por estado é grosseiro demais: um telefone `(11)` da capital
passa como "SP" para uma empresa do interior, embora a região dela seja `19`.
Um DDD incoerente derruba a confiança e, sem o CNPJ confirmando a identidade,
leva o telefone direto para Baixa — ou seja, ele não é gravado.

O mapa `DDD_POR_CIDADE` em `config.py` cobre as cidades do interior paulista;
cidades fora dele caem na checagem por UF, mais frouxa porém segura. Para outras
regiões, basta acrescentar as cidades ao mapa.

### 8. WhatsApp só por link declarado

Apenas links `wa.me` e `api.whatsapp.com` viram WhatsApp. Um telefone comum
escrito ao lado da palavra "WhatsApp" não é promovido.

### 9. Desambiguação de homônimos

Quando o Google Maps retorna vários estabelecimentos com nome compatível em
cidades diferentes, o resultado é marcado como ambíguo, **nada é gravado**, a
linha fica laranja e a Observação recebe `Revisão Manual Necessária` com a lista
dos candidatos. A ambiguidade só é dispensada quando o CNPJ já confirmou a
identidade.

---

## Arquivos gerados

Todos na pasta de saída (padrão: `./saida`):

| Arquivo | Conteúdo |
|---|---|
| `Empresas_Preenchidas.xlsx` | a planilha preenchida e colorida |
| `log.txt` | um bloco legível por empresa, no formato exigido |
| `log_tecnico.txt` | log técnico detalhado, para diagnóstico |
| `Relatorio_Sem_Contato.xlsx` | empresas sem contato, com o motivo |
| `Relatorio_Sem_Contato.csv` | o mesmo relatório, para importar em CRM |
| `config_usuario.json` | preferências salvas da interface |

Exemplo de bloco do `log.txt`:

```
==============================================================================
Empresa:
J & J EXECUTIVA TRANSPORTES LTDA - ME

Cidade:
COSMOPOLIS

CNPJ:
11.222.333/0001-81

Telefone encontrado:
(19) 3555-1010
(19) 3555-1011

Site:
(nenhum)

Fonte utilizada:
Receita Federal

URL utilizada:
https://brasilapi.com.br/api/cnpj/v1/11222333000181

Confiança:
Média

Tempo da pesquisa:
1min 56s

Status:
Encontrado

Origem de cada dado:
  - (19) 3555-1010  [Média]  Receita Federal  <https://brasilapi.com.br/api/cnpj/v1/11222333000181>
==============================================================================
```

Se a execução for interrompida e reiniciada, o `Empresas_Preenchidas.xlsx`
existente é reaproveitado: as empresas já preenchidas passam a ter telefone e
são automaticamente ignoradas.

---

## Estrutura do projeto

```
LocalizadorContatos/
├── main.py            Ponto de entrada (GUI e CLI), checagem de dependências
├── interface.py       Interface CustomTkinter
├── pesquisa.py        Orquestração: cascata de fontes, motor multithread, log.txt
├── excel.py           Leitura/escrita da planilha, cores, relatório
├── google.py          Motores de busca e descoberta do site oficial
├── maps.py            Google Business Profile via Playwright
├── site.py            Rastreamento e validação do site oficial
├── cnpj.py            Descoberta e validação de CNPJ (Receita Federal)
├── modelos.py         Modelos de domínio e a garantia de procedência
├── utils.py           Extração, validação, HTTP, rate limit, logging
├── config.py          Toda a configuração e os limiares
├── requirements.txt
├── README.md
├── tests/
│   └── test_extracao.py    79 testes das rotinas críticas
└── saida/                  arquivos gerados
```

`modelos.py` e `cnpj.py` são acréscimos à estrutura mínima solicitada: o
primeiro concentra os modelos de domínio (evitando dependência circular entre
as camadas) e o segundo isola a pesquisa por CNPJ, que serve a várias etapas.

### Nota sobre `site.py` e `google.py`

Esses dois nomes de arquivo colidem com módulos conhecidos do Python: `site` é
da **biblioteca padrão** (carregado na inicialização do interpretador) e
`google` é o *namespace package* usado por `protobuf` e `google-cloud-*`. Um
`import site` comum devolveria o módulo da stdlib, não o do projeto — falha que
ocorreu em teste e foi corrigida.

Os nomes foram mantidos conforme especificado. `pesquisa.py` os carrega pelo
caminho absoluto via `_importar_local()`, registrando-os sob os aliases
`localizador_site` e `localizador_google`. Isso resolve a colisão
definitivamente sem quebrar bibliotecas de terceiros.

---

## Configuração

Tudo que é ajustável está em `config.py`, agrupado por tema. Os parâmetros mais
relevantes:

| Constante | Padrão | Efeito |
|---|---|---|
| `MAX_WORKERS` | 5 | empresas pesquisadas em paralelo |
| `DELAY_MIN` / `DELAY_MAX` | 1.2 / 3.5 s | intervalo aleatório entre requisições |
| `MAX_PAGINAS_SITE` | 6 | páginas internas visitadas por site |
| `SIMILARIDADE_MINIMA_ACEITE` | 0.82 | limiar para aceitar um nome |
| `SIMILARIDADE_MINIMA_DUVIDA` | 0.62 | abaixo disso o candidato é descartado |
| `EXIGIR_CNPJ_PARA_TOKEN_UNICO` | `False` | modo paranoico: nomes com um só token distintivo exigem CNPJ |
| `MIN_TOKENS_CONFIRMACAO_SITE` | 2 | tokens necessários para aceitar um site |
| `COBERTURA_MINIMA_DOMINIO` | 0.8 | fração do nome que o domínio deve conter |
| `PALAVRAS_GENERICAS` | — | palavras que não identificam a empresa |
| `DDD_POR_CIDADE` | — | DDD esperado por cidade; ausentes caem na checagem por UF |
| `HERDAR_DDD` | `True` | herança de DDD entre números vizinhos |
| `INCLUIR_FAX_DO_CADASTRO` | `False` | inclui o fax da Receita como contato |
| `PARAR_NA_PRIMEIRA_ALTA` | `True` | encerra a cascata ao confirmar telefone de confiança Alta |
| `DISJUNTOR_FALHAS` / `DISJUNTOR_PAUSA` | 3 / 600 s | falhas para desligar uma fonte e por quanto tempo |
| `TIMEOUT_CONEXAO` / `TIMEOUT_HTTP` | 5 s / 15 s | limites de conexão e de leitura |

**Para máxima precisão** (menos contatos, praticamente zero erro):

```python
EXIGIR_CNPJ_PARA_TOKEN_UNICO = True
CONFIANCAS_PREENCHIVEIS = {"Alta"}
HERDAR_DDD = False
```

**Para máxima cobertura** (mais contatos, exige conferência):

```python
CONFIANCAS_PREENCHIVEIS = {"Alta", "Média", "Baixa"}
MIN_TOKENS_CONFIRMACAO_SITE = 1
```

### Desempenho e bloqueios

- Intervalo **aleatório** entre requisições (não fixo), para descaracterizar o
  padrão robótico.
- **Rotação de User-Agent** a cada requisição, via `fake-useragent` com lista
  fixa de reserva.
- Recuo automático diante de HTTP 429/503.
- **Detecção de captcha**: ao encontrar um desafio anti-robô, o motor pausa,
  avisa o usuário e permite retomar. Sem intervenção, retoma sozinho após 90 s.
- **Timeout de conexão (5 s) separado do de leitura (15 s)**: um host
  inalcançável trava no aperto de mão TCP, e esperar o timeout de leitura
  inteiro seria desperdício.
- **Disjuntor por fonte** (`utils.Disjuntor`): após 3 falhas consecutivas, a
  fonte é desligada por 10 minutos, com estado meio-aberto para recuperação.

O disjuntor existe por causa da pior falha de rede na prática: a fonte bloqueada
por IP não devolve erro HTTP, apenas recusa a conexão.

> **Caso real:** com o DuckDuckGo bloqueado, cada consulta gastava o timeout
> inteiro antes de cair para o buscador seguinte. Com cerca de 5 consultas por
> empresa, 10 empresas passaram de **6 minutos** para **mais de 13 minutos sem
> concluir nenhuma**. Com o disjuntor, a fonte morta é descartada logo nas
> primeiras tentativas e o processamento segue pelas demais.

Estimativa: **30 a 45 segundos por empresa** com 5 threads. Uma carteira de 400
empresas leva de 2h30 a 3h30 — bastante variável conforme a disponibilidade das
fontes.

---

## Testes

```bash
python -m unittest discover -s tests -v
```

79 testes cobrindo extração e validação de telefones (incluindo os formatos
legados `019 35550100` e `1935550100`), e-mails, WhatsApp, CNPJ, comparação de
nomes, classificação de domínios, validação de identidade de site, a garantia de
procedência dos dados e a detecção de captcha.

Vários testes são regressões de falsos positivos reais encontrados rodando
contra a carteira de Campinas.

---

## Limitações conhecidas

- **Raspagem de Google Search e Google Maps viola os Termos de Serviço do
  Google** e é bloqueada com frequência. A aplicação implementa essas fontes
  conforme solicitado, com detecção de captcha e pausa automática, mas prioriza
  fontes estáveis e legítimas (site oficial, Receita Federal, DuckDuckGo). Nos
  testes de campo o Google Search retornou zero resultados em todas as
  tentativas. Para uso intensivo, considere as APIs oficiais (Google Places API,
  Custom Search API) — a arquitetura de fontes já está pronta para receber um
  novo provedor: basta uma subclasse de `MotorBusca` em `google.py`.

- **Buscadores bloqueiam por IP sob uso sustentado.** Em teste de campo, após
  algumas centenas de requisições o DuckDuckGo passou a recusar conexão TCP
  (timeout, não HTTP 429), e o Bing continuou respondendo. O bloqueio é
  temporário e o sistema degrada automaticamente para o próximo motor, mas para
  carteiras grandes vale aumentar `--delay-min`/`--delay-max` e reduzir
  `--threads`. Os parsers dos buscadores têm testes com fixtures offline
  justamente para que um bloqueio não seja confundido com erro de parsing.

- **Microempresas frequentemente não estão indexadas.** Buscadores gerais não
  trazem bem empresas sem site próprio — parte relevante de qualquer carteira.
  Nesses casos a única fonte útil é o cadastro de CNPJ, e ele depende de o
  buscador encontrar a empresa em um diretório. Espere uma taxa de sucesso
  modesta em carteiras dominadas por micro e pequenas empresas.
- O `openpyxl` preserva formatação de células, larguras e formatos condicionais,
  mas **não** preserva gráficos, imagens e tabelas dinâmicas. Planilhas com
  esses elementos devem ser tratadas com cuidado — a original nunca é alterada.
- A taxa de sucesso depende muito do ramo. Empresas pequenas em nome de pessoa
  física (`PAULO MENDES`, `ROBERTO CAETANO`) frequentemente não têm
  presença online e ficarão em branco — o comportamento correto.
- Com o Maps ativo, cada thread abre seu próprio Chromium. Cinco threads
  consomem memória considerável; reduza o número se a máquina for modesta.

---

## Solução de problemas

**"Não foi possível salvar Empresas_Preenchidas.xlsx"**
O arquivo está aberto no Excel. Feche-o — o processamento continua e o
salvamento é retomado na próxima empresa.

**"Playwright não instalado" / Maps sempre ignorado**
Rode `playwright install chromium`. Sem isso, a aplicação funciona normalmente
com as demais fontes.

**Muitos captchas**
Aumente `--delay-min` e `--delay-max`, reduza `--threads`, ou rode com
`--sem-busca` (mantendo CNPJ e site oficial, que raramente bloqueiam).

**Poucos contatos encontrados**
Confira o `Relatorio_Sem_Contato.xlsx`: a coluna Motivo diz exatamente por que
cada empresa foi descartada. Se o motivo recorrente for identidade não
confirmada, ajuste `MIN_TOKENS_CONFIRMACAO_SITE` — cientes de que isso aumenta o
risco de contato errado.

**Interface não abre**
`pip install customtkinter`. Alternativamente, use o modo `--cli`.
