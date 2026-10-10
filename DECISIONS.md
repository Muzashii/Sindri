# Decisões de implementação

Registro das escolhas feitas onde a especificação deixava espaço, conforme pedido no documento.

## Importação
1. **Explosão de blocos própria** (`INSERT.virtual_entities()` recursivo) em vez de `recursive_decompose`, para resolver
   a camada `0` e a cor `BYBLOCK` pelo bloco pai. `MINSERT` é expandido em todas as cópias; `ATTRIB` vira texto.
2. **Extrusão invertida (0,0,-1)** — comum em arquivos do CorelDraw/Inkscape — é normalizada com `ezdxf.upright`.
   Entidades com extrusão realmente inclinada (3D) são convertidas em polilinha fina.
3. **Cor**: BYLAYER/BYBLOCK são resolvidos na importação e gravados explicitamente na exportação (o RDWorks separa camadas
   pela cor da entidade). Cor RGB (true color) é convertida para a **ACI mais próxima**, pois R12/R2000 não guardam RGB.
4. **Duplicadas**: removidas quando a geometria coincide com 0,01 mm (incluindo linhas invertidas). Sobreposição
   **parcial** de segmentos colineares não é tratada na v1.
5. **Encadeamento**: as pontas são agrupadas por grade espacial com a tolerância de junção; o caminho começa por nós de
   grau ímpar (pontas de cadeias abertas) e estende para os dois lados.
6. **Peças**: cada contorno fechado que não está dentro de outro é uma peça; *tudo* dentro dele (qualquer profundidade)
   pertence à peça. Isso inclui um contorno fechado que estava dentro de um furo no arquivo original.
7. **Furos aproveitáveis (part-in-part)**: só os furos diretos, **sem nada dentro**, com a **mesma cor do contorno
   externo** (cor diferente = provável gravação) e que não tocam outra geometria interna (texto/linhas de gravação).
8. **Contornos abertos e marcas soltas**: são agrupados quando se tocam (1 mm) e viram uma peça cujo "molde" para o
   encaixe é o envoltório convexo — nunca se sobrepõem a outras peças. Ficam marcados em vermelho com aviso.
9. **Peças idênticas**: comparação por área, perímetro, nº de furos, camadas/cores e textos; depois distância de
   Hausdorff do desenho completo após alinhar centroides e testar rotações candidatas (arestas mais longas e eixo
   principal). Peças desenhadas de formas diferentes (ex.: arco solto × polilinha com bulge) são reconhecidas como iguais;
   o desenho da primeira ocorrência é usado como modelo. Espelhadas **não** são agrupadas.
10. **Texto**: a caixa delimitadora é estimada (≈0,7 × altura por caractere), pois as fontes do RDWorks podem diferir.

11a. **Unidade errada no cabeçalho**: se, na unidade declarada, o desenho ficar maior que 6 m ou menor que 2 mm,
    o programa procura a unidade que deixa o desenho com tamanho plausível (prioridade mm, polegadas, cm, m), aplica
    sozinho e mostra um aviso amarelo. Pode ser trocada em Parâmetros › Arquivo DXF › Unidade (salva no projeto).
11b. **Textos ignorados por padrão na interface/linha de comando**: nos arquivos do laboratório os textos são nomes
    de peça/anotações e não devem ser gravados. Desmarque "Ignorar textos" (ou use `--com-textos`) para gravá-los.
11c. **Filtro de camadas**: a lista de camadas mostra todas as do arquivo; camadas desmarcadas (cotas, nomes…) não
    entram no encaixe nem na exportação.

## Geometria e garantia de espaçamento
11. Cada peça é discretizada (tolerância de curva), simplificada (Douglas-Peucker) e recebe offset arredondado de
    `espaço/2 + tol. de curva + tol. de simplificação + tol. dos arcos do offset`. Assim, **se os polígonos com folga
    não se sobrepõem, as peças reais ficam a ≥ espaço de distância**, mesmo com os erros de discretização. Custo: cerca de
    0,2 mm de folga extra por lado com os valores padrão.
12. Furos para part-in-part são encolhidos em `espaço/2 + tol. de curva + tol. dos arcos`.
13. A margem é aplicada ao desenho real: a região permitida para o polígono com folga é a placa menos a margem, mais
    `espaço/2`.
14. Coordenadas inteiras do Clipper em micrômetros (1 unidade = 0,001 mm).

11d. **Detalhe do contorno no encaixe (desempenho)**: no modo *Equilibrado* (padrão) o contorno usado no cálculo
    passa por um fechamento morfológico de 10 mm (cresce e encolhe com quinas vivas), que preenche dentes/rasgos de
    borda com abertura até ~20 mm — típico de caixas com "finger joint". Só acrescenta área, então nunca gera
    sobreposição. Numa peça com dentes isso reduz ~290 vértices para ~13 e o NFP de 4 s para menos de 1 ms.
    *Preciso* desliga o fechamento; *Rápido* usa 20 mm. O offset do contorno externo passou a ser em quina viva
    (miter), que contém o arredondado e não multiplica vértices.
11e. Os processos de cálculo recebem o cache de NFP já calculado pela primeira solução.

11f. **Intranet FIAP**: navegador embutido (QtWebEngine) com perfil persistente — o usuário faz o login e a
    sessão fica salva; o programa não manipula senha. Os dados são lidos do DOM da página
    (`a.js-visualisa-solicitacao`, `abreSolicitacao(n)`, `#myModalLabel`, `#tabela-corpo-arquivos`) e os arquivos
    são baixados pela própria página (mesma sessão). Se o layout da intranet mudar, só `app/core/intranet.py`
    (scripts JS) precisa ser ajustado. A quantidade da tabela multiplica todas as peças daquele arquivo.

## NFP e posicionamento
15. NFP = soma de Minkowski de A com −B (`pyclipper.MinkowskiSum`) **unida a A−b₀ e a a₀−B**, cobrindo os casos
    "B inteira dentro de A" e "A inteira dentro de B" (sem isso apareciam posições falsamente válidas).
16. O NFP é encolhido 0,002 mm para permitir contato exato (encostar) sem gerar regiões degeneradas, e "furos" de NFP
    menores que 0,25 mm² são descartados (artefatos numéricos que permitiam sobreposição).
17. Critério de posição: os dois da especificação viraram um **gene** do indivíduo — `bbox` (menor caixa delimitadora,
    desempate x, y) e `left` (mais à esquerda, depois mais abaixo). O algoritmo genético escolhe o melhor.
18. Peças em furos que estão dentro de outro furo funcionam: o anfitrião e seus "ancestrais" não bloqueiam a região.
19. Otimizações do decodificador: uma placa que recusou uma variante (peça+rotação) nunca mais a aceitará (a placa só
    enche); rejeição rápida por área livre; translação de NFP com NumPy; filtro por caixa delimitadora.
20. Rotação "livre" = passos de 15° (24 posições), para o cache de NFP continuar eficiente.

## Algoritmo genético
21. Indivíduo = genes `(instância, rotação, espelhado)` + critério. População inicial: área decrescente com a rotação de
    menor caixa (deitada), a mesma com critério `left`, ordenação pela maior dimensão e mutações.
22. Fitness: `100·peças_sem_lugar + 2·(placas−1) + área_bbox_última/área_placa + 0,1·largura_última/largura_placa +
    0,05·Σ área_bbox_outras/área_placa`. Peças sem lugar vêm antes do nº de placas (só ocorrem quando o número máximo de
    placas é atingido ou "abrir nova placa" está desligado).
23. Paralelismo com `ProcessPoolExecutor` (contexto *spawn*), limitado por padrão ao menor entre 8,
    tamanho da população e núcleos disponíveis menos 1. Cada processo tem seu cache de NFP.
    A primeira solução é calculada antes de abrir os processos. O decodificador verifica cancelamento
    entre peças e operações; uma chamada nativa em andamento precisa terminar antes de parar.

## Interface
24. As placas ficam lado a lado no canvas; arrastar uma peça para outra placa a move para lá. ◀ ▶ centralizam cada placa.
25. Aba "Arquivo original" mostra o desenho importado (contornos abertos em vermelho); aba "Encaixe" mostra o resultado.
26. Checagem de colisão manual usa os mesmos polígonos com folga do encaixe (vermelho = viola espaço ou margem).
27. `Del` remove a peça do encaixe **e diminui a quantidade** (senão ela voltaria no próximo encaixe).
28. Alterar a tolerância de curva reprocessa os arquivos automaticamente (a discretização depende dela).

## Exportação
29. A geometria original é escrita de novo (não copiada entre documentos) a partir das primitivas normalizadas, já
    transformadas. **R12** não tem LWPOLYLINE/ELLIPSE/SPLINE/MTEXT: polilinhas viram POLYLINE com bulge (arcos
    preservados), elipses e splines viram POLYLINE fina (0,01 mm) e MTEXT vira TEXT por linha. R12 também não tem campo
    de unidade — o RDWorks deve ser configurado para mm na importação.
30. Ordem de corte: contornos internos antes do externo. Peças contidas em outras precedem a hospedeira;
    entre peças sem dependência, usa-se o vizinho mais próximo quando a opção de otimização está ligada.
31. O relatório é PDF (resumo + uma página por placa), gerado com Qt. A interface prepara DXF e PDF
    em temporários antes de publicar os dois arquivos; não gera PNG por placa nesse fluxo.
32. Projetos v2 incorporam as primitivas e contornos, unidades por arquivo, avisos e hashes SHA-256 das fontes.
    Fontes alteradas ou ausentes geram aviso; a geometria salva é mantida. Projetos v1 ainda abrem,
    mas o encaixe precisa ser refeito porque não há identidade verificável dos desenhos antigos.
33. O agrupamento usa equivalência de 0,001 mm e compara cor efetiva RGB e camada. Auto-interseções
    significativas recebem um envoltório conservador, com aviso, sem descartar trechos do desenho.
34. A validação final confere sobreposição separadamente do espaçamento, além de instâncias,
    quantidades, placas e materiais. A interface bloqueia exportação inválida; a CLI retorna 3 nesse caso.
    Encaixe incompleto retorna 4 e só exporta com `--permitir-parcial`.
35. Caches de variantes/NFP/IFP e soluções avaliadas têm limite de entradas (4096); isso não equivale
    a um limite rígido de RAM. O benchmark em `tools/benchmark.py` registra uma base repetível.

## Gerador de caixas
36. Cada face é desenhada no tamanho externo; a faixa de largura = espessura de cada aresta compartilhada é
    dividida em um número ímpar de trechos (≈ largura do dente), alternando dono. Os cubos dos cantos pertencem
    à face de maior prioridade (base > tampa > frente/fundo > laterais), o que mantém as três faces de um canto
    coerentes sem casos especiais. Um teste voxeliza a caixa montada e exige ocupação exatamente 1 nas paredes.
37. Kerf: cada peça cresce kerf/2 para fora (junção em quina), estreitando rasgos e furos. As divisórias
    descontam 2·kerf + folga no comprimento para não forçar as paredes.
38. Divisórias: meia-madeira (A com rasgo de cima, B com rasgo de baixo) e um dente por vão entrando em furo
    na base. Com tampa solta, ficam uma espessura abaixo da borda (a guia da tampa ocupa esse espaço).
39. Com o nome gravado, peças idênticas recebem o mesmo texto ("Frente/Fundo", "Lateral") para continuarem
    agrupadas no encaixe.
40. Vista 3D em QPainter (sem OpenGL, para não pesar o .exe nem depender da placa de vídeo). A ordem de
    desenho é topológica pelo eixo que separa as caixas delimitadoras de cada peça (calculada uma vez por
    octante da câmera); as divisórias são divididas nos cruzamentos só para o desenho.
41. "Enviar para o encaixe" grava o DXF em `Documentos/Sindri/Caixas` e usa o mesmo caminho de importação dos
    DXFs comuns (multiplicador = nº de caixas, material = "MDF {espessura}mm" se não informado).
42. Tampas com dobradiça (baú, porta dupla): dobradiça integrada de MDF, mesmo princípio do ChestHinge do
    boxes.py (substituiu as abas externas com pino/parafuso). Disco p = 2t, nó R = 3t, lingueta
    t × √((0,9p)² − t²), folga g = max(0,3; 0,1t), folga do disco ≥ kerf + 0,15. Eixo na linha da tampa (Hb), no
    meio da espessura da parede da dobradiça; essa parede do corpo desce para Hb − ρ − g (ρ = meia diagonal
    da lingueta) e, perto do topo (acima de Hb − R − 1), a aresta com as paredes do nó fica lisa — senão os
    dentes ocupariam o lugar do nó/disco. A tampa é uma caixa rasa da mesma largura do corpo, com as paredes
    no mesmo plano. Testes: montagem sem sobreposição, disco no centro do nó, lingueta no centro do disco e
    abertura de 0 a 100° sem colisão.
43. Tampa deslizante: rasgo nas laterais do topo da frente (mais baixa) até UMA espessura antes do fundo; se
    fosse até o fundo, a faixa de cima ficaria presa só nos dentes.
44. Arestas com encaixe só em parte do comprimento: o vizinho pode ser (nome, a, b); fora do trecho a faixa é
    da própria placa e a ponta do trecho vai para a de maior prioridade. Junta lisa = um trecho só, todo da placa
    de maior prioridade.
45. Painel em passos numerados (MakerCase): os botões ilustrados são desenhados pela própria vista 3D a partir
    do gerador, então mostram a geometria real de cada tampa.
46. Modelos além da caixa (ideias do boxes.py, mas sem as dezenas de parâmetros dele): gaveta, eletrônica,
    bandeja e teste de kerf. Cada modelo mostra só os passos de que precisa; o resto vem com valor pronto.
47. Eletrônica: tampa lisa 2 mm maior que as paredes (o furo de 3,3 mm não encosta na borda), um parafuso M3
    no meio de cada parede (2 se a parede passa de 140 mm), rasgo em T na parede: canal de 3,1 mm até
    (parafuso − espessura + 1) e rasgo da porca 5,7 × 2,6 mm a 2 mm da ponta. A placa (Uno/Mega/RPi) fica
    centralizada e gira 90° se só couber assim; aparece em verde na prévia, mas não vai para o corte.
48. Gaveta: medidas = móvel por fora; folga dos lados e em cima; a gaveta encosta na boca e a frente de
    acabamento (W−1 × H−1) é colada nela, cobrindo a boca. O puxador vazado atravessa as duas frentes.
49. Bandeja: rampas a 45° coladas, encostadas no fundo e na parede da frente de cada compartimento (o canto
    toca as superfícies, sem sobrepor). A prévia da bandeja olha mais de cima (senão as rampas não aparecem).
50. Teste de kerf: rasgos de largura (espessura − k) para k = 0…0,30 mm, cortados SEM compensação; o rasgo
    em que a tira entra justa dá o kerf. Os valores são gravados (camada azul) e vão para o DXF como TEXT.
51. Largura do dente: chave de arrastar limitada a 2×–4× a espessura (abaixo disso os dentes quebram; acima,
    poucos dentes e encaixe frouxo). A faixa acompanha a espessura escolhida.
52. Divisórias dinâmicas: a grade (colunas × linhas) define as posições possíveis; cada divisória inteira pode ser
    tirada com um clique (cols_off / rows_off). Cruzamentos, dentes na base e rampas se ajustam às que ficam;
    trocar o nº de colunas/linhas mantém só as escolhas que ainda existem.

## Pendências conhecidas / a validar no laboratório
- Testar R12 × R2000 no RDWorks real e manter como padrão o que importar melhor (hoje: R2000).
- Comparar o aproveitamento com o encaixe manual de referência (critério de aceite 4) usando arquivos reais.
- Gerar e testar o `.exe` no Windows (`build_exe.bat`).
53. Dobradiça do baú/porta dupla igual à do MakerCase ("Laser Hinge Box"), no lugar da anterior (nó na caixa +
    disco girando colado na tampa): o NÓ com furo é da lateral da TAMPA, que desce em diagonal até ele; o pivô
    fica no meio da espessura da parede do lado da dobradiça, 1,15 × o raio do nó abaixo da linha da tampa; o
    DISCO (Ø do pivô, automático 4 × espessura) tem furo retangular para a LINGUETA da parede da caixa e não
    gira. A parede da caixa do lado da dobradiça termina logo acima da lingueta; a lateral da caixa ganha o
    recorte da diagonal e do nó com folga (e sem ponta fina perto da borda). Simulação: 0–100° sem colisão.

## Relatório de bancada (out/2026) — da exportação à máquina
54. **Operação por primitiva** (`core/operations.py`): cada primitiva vira `corte_externo`, `corte_interno`,
    `vinco`, `gravacao_vetorial` ou `gravacao_raster`. O contorno externo é sempre corte; as outras primitivas
    seguem a operação escolhida para a cor delas (por material). Padrão: as cores que aparecem no contorno
    externo são corte, as outras são gravação vetorial (arquivo todo preto continua todo corte). Corrige o
    "uma cor por material", que mandava a gravação azul para a cor de corte (um círculo de gravação virava furo).
55. No modo por material só o corte vai para a cor do material; vinco e gravação ganham, cada um, uma cor
    própria por material, nunca a de corte de nenhum material nem a dos números (preferências: vinco azul,
    gravação verde, raster amarelo). Esgotadas as 5 cores exatas, usa ACIs que caem cada uma numa camada
    diferente da paleta do RDWorks. Dentro da peça a ordem é gravação → vinco → furos → contorno.
56. Arquivo com mais de uma cor num material: aviso na importação ("azul → gravação vetorial") e diálogo de
    confirmação na 1ª exportação. As escolhas e as cores já conferidas vão no projeto (`extra`).
57. **Material proibido** (`core/material_safety.py`): PVC, vinil, policarbonato (Lexan/Makrolon), ABS, fibra de
    vidro/FR4/G10 e couro sintético bloqueiam a exportação com o motivo; acrílico sem "cast"/"extrudado" gera
    atenção. O filtro é pelo nome (texto livre da intranet, sem acento/maiúscula); o banco de materiais pode
    marcar outros como proibidos.
58. **Um DXF de corte por placa** (`export_cut_files`): `nome_placaNN_material.dxf`, origem (0,0) no canto da
    chapa e **sem** contorno da placa (não existe camada cinza para esquecer de desligar). O arquivo com todas as
    placas lado a lado continua saindo como *conferência* (com contorno/nº das placas, se marcado). `Ctrl+E` abre
    no RDWorks só a 1ª placa ainda não cortada; o botão ▶ de cada placa no checklist abre a dela. Cada arquivo
    exportado guarda uma impressão digital (peças, posições, cores, números): se a placa mudou, o ▶ exporta de novo.
59. **Origem/home**: a posição das peças nunca muda; a opção "começar o corte pelo canto superior direito" só
    move o ponto de partida do caminho do vizinho mais próximo (perto do home comum da Ruida).
60. **Aviso camada a camada**: cor, operação, modo, velocidade/potência (mín–máx e passadas quando houver) e
    saída, na ordem de trabalho (números e gravação antes do corte). Linhas que caem na mesma camada do RDWorks
    são juntadas.
61. **Potência mín./máx.**: gravamos (velocidade, mínima, máxima). Sem mínima informada, ela é 65% da máxima no
    modo corte (a Ruida usa a mínima nos cantos e acelerações; mín. = máx. queima o canto em MDF e derrete em
    acrílico) e igual à máxima no scan. É ponto de partida, a validar na grade de teste de cada material.
62. **Máquina de 1 tubo (padrão do laboratório)**: o Sindri só escreve velocidade + tubo 1 (3 doubles do
    registro); o tubo 2 fica como estava. A opção "Máquina com 2 tubos" volta a gravar os dois.
63. **Leitura de volta**: depois de gravar o `config`, o Sindri relê o arquivo, reencontra as tabelas e confere
    cada camada pedida. Se algo não bater, o `config` volta a ser o de antes (a cópia `config.antes_sindri`
    continua existindo) e o aviso diz o motivo. O aviso final mostra "Aplicado no RDWorks (RDWorks x.y, config
    8.0.01): preto = 20 mm/s 13–20%…"; a versão do executável (Windows) e a do cabeçalho do `config` ajudam a
    saber em que versão a tabela foi reconhecida. O ajudante como administrador grava o resultado ao lado do
    pedido (`.ok`/`.erro`) e a janela acompanha por ~30 s.
64. **Modo, passadas, intervalo do scan, bidirecional e sopro** ficam nos parâmetros de cada camada (⋯ no painel
    do laser) e aparecem na conferência camada a camada e no relatório, mas **não** são gravados no `config`:
    não sabemos ainda em que bytes o RDWorks guarda esses campos. `tools/config_diff.py` compara dois `config`
    (antes/depois de mudar um campo no RDWorks) e diz a tabela, a camada e o byte do registro que mudou.
65. **Banco de materiais** (`core/material_db.py`): `materiais.json` versionado (`format`/`version`), em
    `Documentos/Sindri` ou numa pasta compartilhada escolhida no diálogo (todos os PCs passam a usar o mesmo).
    Por material: espessura, chapa padrão, margem, espaçamento, kerf, peça mínima, veio, tipo de acrílico,
    data do teste e quem validou, proibido + motivo e os parâmetros de cada camada (corte, vinco, gravação
    vetorial, raster). Substitui os valores que ficavam só no QSettings do PC (migrados na criação do banco).
    Nomes são comparados sem caixa/acento/espaço ("mdf 3 mm" = "MDF 3mm").
66. **Gravação concorrente**: cada gravação relê o arquivo e junta os materiais que outro PC criou nesse meio
    tempo (para o mesmo material, vale o último a salvar); troca atômica. Arquivo estragado ou de versão mais
    nova nunca é sobrescrito: o Sindri usa os pontos de partida só na memória e avisa. No painel do laser, a
    digitação muda o material na memória na hora e grava no arquivo 0,7 s depois (e ao fechar).
67. **Chapa por material**: 0/"do painel" em chapa, margem e espaçamento = usar o painel, para o banco nunca
    mudar o encaixe de surpresa; o painel mostra quais materiais usam valores do banco. O encaixe guarda isso em
    `NestParams.material_sheets`; cada placa nasce do tamanho do material dela e cada peça recebe a folga do
    espaçamento do SEU material (peças de materiais diferentes nunca dividem placa, então cada par de vizinhas
    tem o mesmo espaçamento). Validação, colisão, canvas, DXF e PDF usam o formato de cada placa
    (`core/sheetspec.py`). Material com veio: só 0° e 180° (encaixe e tecla R).
68. Os pontos de partida do banco (MDF 3/6 mm, acrílico cast 3 mm, compensado 3 mm) **não trazem velocidade nem
    potência**: cada laboratório preenche depois da grade de teste. Teste com mais de 90 dias (ou sem teste)
    aparece com ⚠ no painel do laser.
69. **Fabricabilidade** (`core/manufacturability.py`, só avisos): parede mais fina que 1,5 × espessura (abertura
    morfológica com quinas vivas — canto vivo não conta como fino; os trechos são pintados de laranja), furo
    menor que 2 × kerf, peça com as duas medidas menores que a peça mínima do material (sugere micro-pontes),
    peça maior que a área útil, rasgo retangular com largura = espessura nominal (sem compensar o kerf) e
    texto que não virou curva. Sem cadastro: espessura 3 mm, kerf 0,15 mm, peça mínima 10 mm.
70. **Selo por solicitação** (OK / atenção / bloqueado): material (bloqueia) + avisos de fabricabilidade e
    arquivos que não são DXF (atenção). Aparece no cartão do lote e é guardado em `selos.json`, para a lista da
    intranet mostrar o selo de solicitações já abertas antes; no detalhe da solicitação o selo do material e dos
    arquivos aparece na hora, antes de juntar no lote.
