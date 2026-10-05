# Revisão técnica do Sindri

Data: 05/10/2026. Projeto analisado: `C:\Users\user\Downloads\projeto`.

## Parecer

A separação entre geometria, interface e execução em segundo plano é uma boa base. O suporte a DXF original, materiais separados, projetos, checklist e testes demonstra que o programa já cobre um fluxo de trabalho considerável. A prioridade agora deve ser a confiabilidade dos desenhos e do estado salvo, antes de ampliar funcionalidades ou otimizar o algoritmo genético.

Foram reproduzidos problemas que podem alterar escala, geometria, cores e quantidades de corte, além de perder ou reutilizar indevidamente dados de projetos. Os testes existentes exercitam muitos caminhos, mas não cobrem essas invariantes. A promessa de ausência de sobreposição e de reabertura exatamente igual, presente no README, é mais forte que o comportamento atual.

Esta revisão não modifica o código de produção. Os arquivos adicionados em `docs` contêm o relatório, diagnósticos e resultados. Foram instalados `pytest`, `pytest-cov` e suas dependências no ambiente virtual existente.

## Escopo e verificação

Análise da importação, reconstrução de peças, transformações, NFP, posicionamento, otimização, colisão, validação, exportação, projetos, atualização, intranet, limpeza, interface, scripts de instalação e empacotamento. Os testes existentes também foram inspecionados. A imagem de interface já existente em `docs/tela_encaixe.png` foi consultada como referência histórica; ela ainda mostra o nome DXF Nest.

Ambiente utilizado: Windows, Python 3.11.9, PySide6 6.11.2, ezdxf 1.4.4, Shapely 2.1.2, NumPy 2.4.6 e pyclipper 1.4.0.

| Verificação | Resultado |
| --- | --- |
| Suíte original, 95 testes | 93 aprovados; 2 falhas; 58,16 s |
| Cobertura de linhas de `app/core` | 89%; 296 de 2.797 instruções não cobertas |
| Repetição dos dois testes com preferências isoladas | Interface aprovada; leitura de projeto inválido continua falhando |
| Diagnósticos adicionais com dados sintéticos | Comportamentos registrados em `revisao-reproducoes.json` |

O README ainda menciona 68 testes e aproximadamente 92% de cobertura. A medição atual é de linhas do núcleo: não representa cobertura da interface, de ramos condicionais ou uma prova de correção geométrica. Os processos filhos do otimizador aparecem parcialmente sem cobertura, portanto essa medição também não descreve completamente o trabalho paralelo.

Não foram executados login em conta real da FIAP, atualização contra o GitHub, geração/instalação do executável, abertura no RDWorks nem corte físico. A identificação de problemas de ordenação no DXF não confirma como cada configuração do RDWorks executará os caminhos. Esta revisão também não é uma auditoria completa de vulnerabilidades das dependências.

## Problemas de alta prioridade — P1

### 1. Corrigir a unidade de um arquivo altera todos os arquivos do lote

Referência: [files.py:208](C:/Users/user/Downloads/projeto/app/ui/mainwindow/files.py:208).

`_ask_fix_units` escolhe a unidade sugerida para o primeiro arquivo suspeito, grava uma única opção global e reimporta todos os arquivos com ela. Um lote pode conter arquivos corretamente declarados em polegadas e outro com unidade errada.

**Reprodução:** um retângulo válido de uma polegada, que deveria medir 25,4 mm, passou a medir 1 mm quando outro arquivo do lote acionou a correção para milímetros.

**Correção:** guardar a unidade efetiva por arquivo; aplicar a sugestão somente ao arquivo suspeito e persistir essa associação no projeto. Exibir nome do arquivo, unidade e dimensões resultantes antes de usar a escala corrigida.

### 2. Contornos com autointerseção perdem parte da geometria no cálculo

Referências: [geometry.py:263](C:/Users/user/Downloads/projeto/app/core/geometry.py:263) e [validate.py:18](C:/Users/user/Downloads/projeto/app/core/validate.py:18).

Após `make_valid`, `ring_to_polygon` mantém apenas o maior polígono. As primitivas originais continuam completas e são exportadas. O molde do encaixe e a validação fina deixam de representar tudo que será cortado.

**Reprodução:** um contorno em forma de laço, com dois triângulos, ficou com apenas 400 mm² no molde. Cerca de 96,53 mm do caminho original ficaram fora desse molde. Uma segunda peça na região omitida passou pela validação sem avisos.

**Correção:** recusar o contorno inválido com identificação visual, ou construir um envelope conservador que contenha todos os componentes. A validação deve verificar que todas as primitivas exportáveis estão contidas no envelope utilizado.

### 3. O agrupamento de peças parecidas modifica dimensões de fabricação

Referências: [part_builder.py:480](C:/Users/user/Downloads/projeto/app/core/part_builder.py:480) e [part_builder.py:494](C:/Users/user/Downloads/projeto/app/core/part_builder.py:494).

O agrupamento admite diferenças de área/perímetro e uma distância de Hausdorff de pelo menos 0,2 mm, normalmente 0,3 mm. Depois usa a geometria da primeira peça como modelo de todas as cópias.

**Reprodução:** retângulos de 100 × 100 mm e 100,2 × 100 mm viraram um único modelo com quantidade 2. A diferença de 0,2 mm desapareceu na exportação.

**Correção:** distinguir equivalência geométrica de aproximação para cálculo. Preservar as primitivas de cada original, ou exigir equivalência dentro de uma tolerância de fabricação explícita e muito mais restrita. Uma opção para desativar o agrupamento também ajuda a conferir arquivos.

### 4. Comparações de cores ignoram RGB e podem transformar gravação em furo aproveitável

Referências: [part_builder.py:352](C:/Users/user/Downloads/projeto/app/core/part_builder.py:352) e [part_builder.py:460](C:/Users/user/Downloads/projeto/app/core/part_builder.py:460).

A importação preserva `Prim.rgb`, mas a identificação de furos e de peças iguais compara apenas `color`, que é o índice ACI. Primitivas com o mesmo ACI e RGB diferentes passam por iguais, embora a exportação converta seus RGB para cores diferentes.

**Reprodução:** duas peças, uma vermelha e outra azul por RGB, foram agrupadas. Um contorno interno azul dentro de um externo vermelho também foi aceito como furo aproveitável.

**Correção:** usar uma representação consistente da cor/operação efetiva em todas as etapas. Comparar também a distribuição das operações na geometria, não apenas o conjunto de cores presentes. Permitir definir corte e gravação explicitamente é uma evolução útil.

### 5. Abrir um novo DXF mantém o destino do projeto anterior

Referências: [files.py:116](C:/Users/user/Downloads/projeto/app/ui/mainwindow/files.py:116) e [projects.py:18](C:/Users/user/Downloads/projeto/app/ui/mainwindow/projects.py:18).

`load_files(add=False)` substitui o trabalho, mas não redefine `project_path`. `save_project` reutiliza esse caminho sem abrir a escolha de destino.

**Reprodução:** abrir `a.sindri` e depois `outro-trabalho.dxf` mantém `a.sindri` como destino. Um próximo Ctrl+S sobrescreve o projeto anterior com o novo trabalho.

**Correção:** limpar `project_path` depois de uma substituição bem-sucedida dos arquivos. Em caso de falha de importação, restaurar todo o estado anterior, inclusive opções de importação e destino de salvamento.

### 6. Projetos não detectam alterações de geometria com a mesma área

Referências: [project.py:128](C:/Users/user/Downloads/projeto/app/core/project.py:128) e [part_builder.py:597](C:/Users/user/Downloads/projeto/app/core/part_builder.py:597).

O projeto guarda caminhos e propriedades, reimporta os DXF e associa peças por IDs sequenciais. A conferência adicional usa apenas área. Arquivos modificados podem receber posições, quantidades e checklist do desenho anterior.

**Reprodução:** salvar uma peça de 100 × 50 mm, alterar o DXF para 125 × 40 mm e reabrir o projeto mantém o encaixe antigo sem avisos, pois ambas têm área 5.000 mm².

**Correção:** armazenar hash dos arquivos e identificadores geométricos estáveis. Na divergência, exigir reconciliação antes de reaplicar posições/checklist. Para reabertura fiel e portabilidade, salvar uma cópia das fontes ou das primitivas dentro do projeto.

### 7. Salvar fontes de outra unidade do Windows falha, inclusive no autosave

Referências: [project.py:53](C:/Users/user/Downloads/projeto/app/core/project.py:53), [projects.py:33](C:/Users/user/Downloads/projeto/app/ui/mainwindow/projects.py:33) e [projects.py:60](C:/Users/user/Downloads/projeto/app/ui/mainwindow/projects.py:60).

`os.path.relpath` não representa caminhos entre unidades diferentes. O método de salvamento manual trata apenas `OSError`; esse caso gera `ValueError`. No salvamento automático, a exceção é ignorada.

**Reprodução:** salvar em C: um projeto que referencia `Z:/arquivo.dxf` falhou com `path is on mount 'Z:', start on mount 'C:'`, antes de ler o arquivo. Um DXF num pendrive ou unidade de rede mapeada pode, portanto, impedir o backup automático em Documentos.

**Correção:** usar caminho absoluto como alternativa quando não houver caminho relativo, e aplicar a mesma estratégia a arquivos, materiais, multiplicadores e tags. Informar falhas de autosave de forma persistente.

### 8. Reduzir kits mantém cópias excedentes no encaixe e na exportação

Referência: [editing.py:253](C:/Users/user/Downloads/projeto/app/ui/mainwindow/editing.py:253).

`multiply_kits` muda `Part.quantity`, mas não elimina posicionamentos cujo número de instância exceda a nova quantidade. A exportação só avisa quando faltam peças; não quando existem cópias a mais.

**Reprodução:** depois de posicionar três cópias, selecionar um kit deixou quantidade 1 e três posicionamentos. O exportador continua escrevendo os três.

**Correção:** centralizar alterações de quantidade, reconciliar instâncias e posições, compactar placas, atualizar checklist e recalcular peças sem lugar. Validar a correspondência entre quantidades e posicionamentos antes de exportar.

### 9. Atualização interrompida deixa uma instalação parcialmente substituída

Referências: [updater.py:119](C:/Users/user/Downloads/projeto/app/core/updater.py:119) e [updates.py:87](C:/Users/user/Downloads/projeto/app/ui/mainwindow/updates.py:87).

Os arquivos são substituídos individualmente. Não há rollback do conjunto caso uma gravação falhe. A interface afirma “Nada foi alterado”, embora arquivos anteriores já possam ter sido trocados. O backup anterior é removido antes da nova tentativa.

**Reprodução:** ao simular uma falha na substituição de `app/main.py`, `sindri.py` já estava com conteúdo novo e `app/main.py` ainda estava antigo.

**Correção:** baixar e validar numa área temporária, preparar backup versionado e aplicar uma transação com rollback. Impedir atualizações concorrentes e encerramento durante a aplicação. Só registrar a versão depois de concluir; apresentar o estado real se a restauração falhar.

## Correções importantes — P2

### 10. Validação final deixa passar sobreposições com espaçamento até 0,02 mm

Referência: [validate.py:60](C:/Users/user/Downloads/projeto/app/core/validate.py:60).

A condição usa somente `distance < spacing - tol`. Quando `spacing <= tol`, a distância zero de duas peças sobrepostas não é menor que o limite.

**Reprodução:** dois quadrados exatamente sobrepostos, com interseção de 100 mm² e espaçamento zero, retornaram uma lista vazia de problemas.

**Correção:** verificar interseção com área positiva independentemente da distância; permitir contato de borda conforme a regra escolhida. Manter a tolerância apenas na comparação do espaçamento. A checagem manual é uma proteção adicional na interface, mas não substitui esse contrato do núcleo.

### 11. IDs sequenciais fazem quantidades se perderem ao adicionar arquivos

Referências: [files.py:168](C:/Users/user/Downloads/projeto/app/ui/mainwindow/files.py:168) e [part_builder.py:597](C:/Users/user/Downloads/projeto/app/core/part_builder.py:597).

A tentativa de preservar quantidades usa `(id, área)`. A ordenação por área muda os IDs quando chega uma peça maior. As identidades usadas por checklist e travas também precisam ser reconciliadas.

**Reprodução:** uma peça com quantidade 7 voltou a 1 depois de adicionar outro arquivo com uma peça maior.

**Correção:** usar identidade estável por fonte e geometria; preservar ajustes somente quando houver correspondência inequívoca. Não reaplicar marcações antigas a novos IDs numéricos.

### 12. Alterações de edição não são salvas automaticamente de modo consistente

Referências: [editing.py:130](C:/Users/user/Downloads/projeto/app/ui/mainwindow/editing.py:130), [editing.py:161](C:/Users/user/Downloads/projeto/app/ui/mainwindow/editing.py:161) e [projects.py:51](C:/Users/user/Downloads/projeto/app/ui/mainwindow/projects.py:51).

Arrastar uma peça agenda autosave. Girar, espelhar, remover, desfazer/refazer e alterar quantidades não o fazem diretamente. Travar/destravar posições também não marca `dirty`. Alterações podem depender de um timer anterior ainda pendente ou de outra ação posterior para serem gravadas.

**Reprodução:** com o timer anterior encerrado, girar uma peça deixou o timer de autosave inativo. Pela leitura do código, uma alteração apenas de trava após salvar também não dispara a confirmação de saída.

**Correção:** criar uma operação comum de mudança de estado que atualize histórico, `dirty`, validação e autosave. Incluir parâmetros, travas e checklist; registrar erros de gravação. Rever também a condição de saída que só pergunta sobre mudanças quando existem posicionamentos.

### 13. Checklist marca um tipo como concluído mesmo faltando cópias

Referências: [checklist.py:80](C:/Users/user/Downloads/projeto/app/ui/mainwindow/checklist.py:80) e [sheets.py:88](C:/Users/user/Downloads/projeto/app/core/sheets.py:88).

A conclusão considera apenas as placas dos posicionamentos existentes, sem comparar com a quantidade solicitada.

**Reprodução:** quantidade 3, somente uma cópia encaixada; marcar a placa dessa cópia como cortada colocou o tipo em `done_parts`.

**Correção:** contabilizar as instâncias solicitadas, posicionadas e cortadas separadamente. A conclusão automática deve exigir todas as cópias; manter a marcação manual como uma ação explícita distinta.

### 14. A ordem de exportação não respeita dependências entre peças dentro de furos

Referências: [dxf_export.py:29](C:/Users/user/Downloads/projeto/app/core/dxf_export.py:29) e [dxf_export.py:34](C:/Users/user/Downloads/projeto/app/core/dxf_export.py:34).

`inner_first` ordena apenas as primitivas de uma peça. Entre peças, a escolha é por proximidade dos centros. Uma peça inserida no furo de outra pode ser escrita depois dos contornos que liberam o material ao redor dela.

**Reprodução:** a sequência de entidades foi furo da hospedeira → externo da hospedeira → peça encaixada no furo.

**Correção:** construir relações de contenção entre os caminhos e aplicar uma ordenação que corte primeiro os elementos dependentes. Otimizar deslocamentos apenas entre operações que possam ser permutadas. Validar o resultado no RDWorks real, que pode reordenar o DXF conforme suas próprias opções.

### 15. CLI informa sucesso sem gerar corte completo

Referência: [cli.py:66](C:/Users/user/Downloads/projeto/app/cli.py:66).

O retorno considera erros geométricos, mas não `res.unplaced`. Além disso, o programa escreve arquivos mesmo depois de detectar problemas na validação, antes de retornar código 3.

**Reprodução:** uma peça maior que uma placa de 10 × 10 mm produziu código de saída 0 e nenhum DXF.

**Correção:** definir retornos distintos para sucesso, resultado incompleto e resultado inválido. Bloquear arquivos inválidos por padrão; exportação parcial deve ser uma opção explícita. Validar números finitos, dimensões positivas e margem compatível antes de iniciar.

### 16. Empacotamento não inclui o subpacote da janela principal

Referência: [pyproject.toml:27](C:/Users/user/Downloads/projeto/pyproject.toml:27).

A lista explícita de pacotes omite `app.ui.mainwindow`, embora `app.ui.main_window` o importe. A execução diretamente da pasta mascara esse problema.

**Verificação:** a enumeração de módulos do `setuptools.build_py` com a configuração atual retornou zero módulos desse subpacote. Não foi construído ou instalado um wheel nesta revisão.

**Correção:** configurar descoberta de `app*`, declarar o backend de build e verificar um pacote instalado fora da pasta-fonte. A documentação do [setuptools sobre descoberta de pacotes](https://setuptools.pypa.io/en/stable/userguide/package_discovery.html) descreve a configuração correspondente.

### 17. Proteção de diretórios da atualização diferencia maiúsculas no Windows

Referência: [updater.py:128](C:/Users/user/Downloads/projeto/app/core/updater.py:128).

O primeiro componente do ZIP é comparado literalmente com `PROTECTED`. Em um sistema de arquivos que não distingue maiúsculas, `.VENV` acessa a mesma pasta que `.venv`.

**Reprodução:** um ZIP sintético contendo `.VENV/probe.txt` substituiu um arquivo dentro da pasta protegida `.venv`.

**Correção:** normalizar componentes com a semântica do sistema de arquivos, resolver e conferir destinos, e adotar uma lista explícita de arquivos de aplicação atualizáveis. O teste de travessia com `..` e barra invertida foi rejeitado pelo ZIP/leitor atual; não foi confirmado escape da raiz. Este achado é sobre a proteção prometida das pastas locais, não uma demonstração de invasão remota.

### 18. Testes não isolam preferências no Windows e dependem da codificação local

Referências: [test_ui.py:31](C:/Users/user/Downloads/projeto/tests/test_ui.py:31), [dialogs.py:20](C:/Users/user/Downloads/projeto/app/ui/dialogs.py:20), [test_export_project.py:100](C:/Users/user/Downloads/projeto/tests/test_export_project.py:100) e [project.py:77](C:/Users/user/Downloads/projeto/app/core/project.py:77).

`QSettings.setPath(NativeFormat, ...)` não altera o destino no Windows. Os testes usam a chave real `LabMaker/DXFNest`, compartilham estado e podem alterar preferências do usuário. Isso é documentado pelo [Qt em QSettings.setPath](https://doc.qt.io/qt-6/qsettings.html#setPath).

**Evidência:** o teste de interface usou a pasta de exportação deixada pelo teste de limpeza e criou `t_2_todas_placas.dxf` ali. Com uma função de preferências injetada para um INI temporário, o mesmo teste passou. A execução original pode ter alterado preferências; não havia uma cópia prévia para restaurá-las com segurança.

O teste de arquivo inválido escreve caracteres acentuados sem `encoding`. Neste Windows, isso gerou bytes não UTF-8. O carregador espera UTF-8 e não transforma `UnicodeDecodeError` em `ProjectError`. A janela tem um tratamento genérico adicional, mas o contrato público do carregador e seu teste falham.

**Correção:** usar configuração e diretórios temporários em todos os testes, também para navegador, autosave e exportação; substituir integrações externas. Especificar UTF-8 nas fixtures e testar separadamente bytes inválidos, JSON malformado e esquema inválido. Não apenas enfraquecer as asserções para a suíte ficar verde.

## Melhorias de arquitetura e operação

### Estado e persistência

- Consolidar alterações de projeto em um serviço de estado. Os mixins reduziram o tamanho do arquivo principal, mas continuam compartilhando muitos atributos mutáveis sem um ponto único de validação.
- Definir invariantes: IDs estáveis, instâncias únicas, quantidades reconciliadas, placas com um material, checklist coerente e geometria validada. Checá-las na carga, após edição e antes da exportação.
- Validar formato e versão do JSON e criar migrações. Atualmente a versão é gravada, mas não é usada para recusar formatos incompatíveis.
- Oferecer projeto portátil. A limpeza já avisa que remover os DXF invalida projetos antigos; guardar fontes junto do projeto elimina essa dependência para trabalhos arquivados.
- Preparar DXF e PDF em temporários e concluir a exportação como conjunto, evitando resultados parciais com aparência de exportação concluída.

### Desempenho e responsividade

- Medir primeiro com lotes reais: tempo de importação, primeira solução, melhora final, RAM e número de NFP. Guardar tamanho/complexidade do caso e semente para repetir comparações.
- Limitar processos conforme carga e memória. O padrão atual usa `cpu_count()-1`; cada processo recebe seus próprios caches. Um computador com muitos núcleos e um lote pequeno não precisa de tantos processos.
- Dar orçamento de memória aos caches de NFP e às soluções já avaliadas. Os dicionários em `NFPCache` e `_seen` não têm limite; avaliar crescimento em sessões longas antes de escolher uma política de descarte.
- Tornar cancelamento cooperativo dentro da preparação e do decodificador. A primeira avaliação antecede a checagem de parada e `stop_nest(wait=True)` pode bloquear a thread da interface por até 30 segundos.
- Mover importação/reconstrução e exportações demoradas para tarefas em segundo plano com progresso e cancelamento. Hoje essas etapas rodam diretamente a partir da interface.

Esses pontos de desempenho vêm da análise do código; não foram realizados benchmarks grandes nem medição de consumo máximo de memória.

### Intranet e arquivos

- Baixar para arquivos temporários e substituir os existentes somente depois de confirmar o sucesso. Em `intranet.py:806`, a cópia anterior é apagada antes de começar o novo download.
- Cancelar efetivamente downloads ativos ao encerrar ou atingir timeout. Limpar `_pending` apenas perde o acompanhamento; `_downloads` também deve liberar objetos concluídos.
- Separar apresentação, leitura da página e fila de downloads. A janela da intranet concentra mais de mil linhas, com vários timers e estados concorrentes.
- Tratar nomes reservados do Windows e colisões de nomes após normalização. Usar a pasta Documentos obtida por API do sistema, inclusive quando redirecionada para OneDrive/rede.
- Oferecer saída da sessão e limpeza do perfil do navegador para computadores compartilhados. Reduzir dados pessoais e corpos de requisição nos logs de diagnóstico.

### Instalação, testes e documentação

- Unificar as versões mínimas de `pyproject.toml` e `requirements.txt`, hoje divergentes para NumPy, Shapely e PySide6. Manter um conjunto de versões testadas para distribuir o aplicativo.
- Fazer a atualização verificar requisitos de versão: o `executar.bat` pula a instalação se os imports funcionarem, mesmo quando a atualização passa a exigir uma versão mais nova de uma biblioteca existente.
- Adicionar CI para Windows e verificações do núcleo em outro sistema. Separar testes rápidos, geometria, interface, multiprocessing e pacote instalado.
- Incluir os casos reproduzidos nesta revisão como regressões que expressem o comportamento correto. Para geometria, complementar exemplos com testes gerados de rotações, espelhamentos, furos e contornos degenerados.
- Fortalecer testes de exportação: a asserção atual do teste de ordem de corte verifica apenas que há um círculo, sem provar que ele precede o contorno correspondente.
- Introduzir lint e checagem de tipos de forma gradual, priorizando fronteiras entre modelos, UI e integração. Substituir `except Exception: pass` relevantes por registros úteis e mensagens recuperáveis.
- Atualizar README e DECISIONS para o comportamento medido: quantidade de testes, cobertura, geração de PNG, correção de unidades da CLI e estado da camada PLACA. O código desenha a camada, mas não a desliga automaticamente como uma passagem do README afirma.
- Esta pasta não contém `.git`. Antes de implementar as correções, manter histórico versionado e uma versão recuperável do estado atual.

### Interface e fluxo de trabalho

Preservar a interface em português, os atalhos, a identificação por solicitação e a indicação por material. As melhorias mais úteis estão na clareza do estado do trabalho:

- Lista persistente de avisos, acessível depois de fechar um banner.
- Resumo por arquivo de unidade, escala aplicada, dimensões e operações reconhecidas.
- Contagem separada de peças solicitadas, encaixadas, cortadas e pendentes.
- Indicação visível de “alterações não salvas” e horário do último autosave confirmado.
- Resumo pré-exportação com material, dimensões de placa, quantidades e validação; impedir aparência de sucesso quando houver problema.
- Placas com dimensões próprias por material e aproveitamento de retalhos, depois de estabilizar as invariantes atuais. Hoje o tamanho da placa é global.

## Ordem sugerida de implementação

1. **Preservar os dados:** corrigir destino de projeto, caminhos entre unidades, identidade das peças e sincronização das quantidades; isolar os testes.
2. **Garantir fidelidade do corte:** unidades por arquivo, contornos inválidos, agrupamento, cores/operações e validação final independente da interface.
3. **Confiabilidade do fluxo:** checklist, autosave, exportação completa, retorno da CLI, ordem de caminhos e atualização com rollback.
4. **Distribuição e manutenção:** pacote instalado, versões testadas, CI, logs e documentação.
5. **Desempenho e evolução:** medir lotes reais, ajustar processos/caches, reduzir bloqueios e então adicionar novas funções.

## Como reproduzir

Na raiz do projeto, os comandos usados foram:

```powershell
.\.venv\Scripts\python.exe -m pytest --cov=app/core --cov-report=term-missing --cov-report=json:docs/revisao-cobertura.json --junitxml=docs/revisao-testes.xml
.\.venv\Scripts\python.exe -u -X faulthandler docs\revisao_probes.py
.\.venv\Scripts\python.exe -m pytest -p docs.revisao_pytest_plugin tests/test_ui.py::test_fluxo_completo tests/test_export_project.py::test_projeto_invalido --junitxml=docs/revisao-testes-isolados.xml
```

A primeira linha registra a execução original e seu problema de isolamento. Para futuras execuções, corrigir a fixture da suíte ou usar o plugin temporário de isolamento também na suíte completa. O script de diagnóstico cria seus próprios DXF, ZIPs e preferências em temporários dentro de `docs`, sem acessar a intranet nem instalar atualizações reais. Seus resultados descrevem os defeitos atuais; não substituem testes de regressão que devam passar após as correções.

Arquivos da revisão:

- [Diagnósticos reproduzíveis](C:/Users/user/Downloads/projeto/docs/revisao_probes.py)
- [Resultados dos diagnósticos](C:/Users/user/Downloads/projeto/docs/revisao-reproducoes.json)
- [Resultado da suíte original](C:/Users/user/Downloads/projeto/docs/revisao-testes.xml)
- [Resultado dos dois testes isolados](C:/Users/user/Downloads/projeto/docs/revisao-testes-isolados.xml)
- [Medição de cobertura](C:/Users/user/Downloads/projeto/docs/revisao-cobertura.json)
- [Plugin temporário de isolamento](C:/Users/user/Downloads/projeto/docs/revisao_pytest_plugin.py)
