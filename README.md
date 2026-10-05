# Sindri — encaixe automático de peças para corte a laser

Programa para o laboratório maker que recebe um ou mais arquivos **DXF**, o tamanho da placa e alguns parâmetros, e
**organiza as peças da forma mais compacta possível** (nesting), gerando DXF pronto para o **RDWorks**.

Mesma abordagem do SVGnest/Deepnest (No-Fit Polygon + algoritmo genético), mas com entrada e saída em DXF, arcos e
círculos preservados, camadas/cores mantidas e interface em português.

## Como usar (3 cliques)
1. **Arraste o DXF** para a janela (ou `Ctrl+O`). As peças aparecem agrupadas na lista da esquerda, com a quantidade.
2. Escolha a **placa** no topo e clique em **▶ Encaixar** (`Espaço`). A melhor solução aparece e vai melhorando ao vivo.
   Clique em **⏹ Parar** quando estiver bom.
3. Clique em **⬇ Exportar para RDWorks** (`Ctrl+E`). Sai um DXF por placa (`projeto_placa01.dxf`, …) e, se quiser, um
   relatório PDF/PNG. Com **Abrir no RDWorks depois de exportar** (padrão), o RDWorks já abre com o arquivo
   (o de todas as placas, se gerado, senão a placa 1). Na primeira vez, se ele não for encontrado, o Sindri pede
   para você mostrar o `RDWorksV8.exe`.

Depois do encaixe você pode **arrastar** peças (ficam vermelhas se colidirem), **girar** (`R`), **travar** (`L`) e
encaixar de novo só o restante, mover entre placas (arraste até a outra placa ou botão direito), remover (`Del`) e
desfazer/refazer (`Ctrl+Z` / `Ctrl+Y`). O botão **Limpar** (acima da lista de peças, ou `Ctrl+Shift+Del`)
remove todas as peças e o encaixe para começar do zero. `F` enquadra tudo, roda do mouse = zoom, botão do meio (ou `Alt`+arrastar) =
mover a vista. `F1` mostra todos os atalhos.

## Instalação (Windows)
1. Instale o **Python 3.11** (python.org) marcando **"Add python.exe to PATH"**.
2. Dê dois cliques em **`executar.bat`**. Na primeira vez ele prepara tudo sozinho (alguns minutos), cria o atalho
   **Sindri** na área de trabalho e no menu Iniciar e abre o programa.
3. Dali em diante, abra pelo **atalho Sindri** (sem janela preta).

Para atualizar, extraia a versão nova **por cima da mesma pasta** (mantendo a pasta `.venv`): assim nada precisa ser
baixado de novo. Se o Windows bloquear uma biblioteca ("Controle de Aplicativo"), o `executar.bat`
tenta de novo algumas vezes e, se continuar bloqueado, usa o **Anaconda** já instalado no computador (o numpy dele costuma ser liberado), instalando o resto na pasta `libs`.

### Gerar um .exe (opcional)
`build_exe.bat` gera `dist\Sindri.exe`. Obs.: com o Controle Inteligente de Aplicativos ligado, um .exe sem
assinatura digital também pode ser bloqueado.

### Linha de comando (sem interface)
```bash
python -m app.cli peças.dxf --placa 600x400 --margem 5 --espaco 2 --tempo 30 --saida resultado/
```
Opções: `--rotacoes 4|8|…`, `--espelhar`, `--sem-part-in-part`, `--versao R12|R2000`, `--contorno-placa`.

## Baixar direto da intranet FIAP
Botão **Intranet FIAP** (ou `Ctrl+I`): abre a página de Solicitações Maker num navegador dentro do programa.
1. Na primeira vez, faça login normalmente — a sessão fica salva (o programa nunca vê sua senha).
2. Clique numa solicitação da fila (filtro por tipo e busca por nome/RM/nº): ela é só **visualizada** —
   aluno, RM, projeto, arquivos, materiais e quantidades. Nada é baixado.
3. Clique em **Enviar tudo para a placa** (ou em um material só). Só então os DXF são baixados, para
   `Documentos\Sindri\Solicitações\<nº> - <aluno>\<material>\`. As quantidades da tabela da intranet já entram nas peças.
4. A tela principal mostra um cartão com **nº da solicitação, RM, aluno, projeto e professor**.
5. **Materiais nunca dividem placa**: cada material (ex.: MDF 3mm em azul, MDF 6mm em laranja) ganha suas
   próprias placas, com a cor no contorno e na etiqueta. Arrastar uma peça para a placa de outro material deixa
   ela vermelha. A exportação gera `8759_RM500123_MDF3mm_placa01.dxf`, `8759_RM500123_MDF6mm_placa01.dxf`…

## Dicas
- **Peças com tamanho absurdo (ex.: 141000 × 12500 mm)?** O arquivo declara a unidade errada. O programa corrige
  sozinho e avisa; se precisar, troque em *Parâmetros › Arquivo DXF › Unidade*.
- **Nomes das peças aparecendo como gravação?** Deixe marcado *Ignorar textos* (padrão) ou desmarque a camada dos
  nomes na lista de camadas.

## O que o programa faz
- **Importação**: LINE, ARC, CIRCLE, LWPOLYLINE/POLYLINE (com bulge), SPLINE, ELLIPSE, INSERT/MINSERT (blocos aninhados,
  com escala e rotação), TEXT/MTEXT/ATTRIB. Converte unidades (`$INSUNITS`) para mm; sem unidade → assume mm e avisa.
- **Reconstrução das peças**: encadeia linhas/arcos soltos (tolerância de 0,05 mm), fecha contornos quase fechados,
  remove linhas duplicadas, vincula furos/rasgos/gravações/textos à peça que os contém, agrupa peças idênticas
  (mesmo giradas) e destaca contornos abertos em vermelho.
- **Encaixe**: NFP com cache, posicionamento por região válida, algoritmo genético em paralelo (todos os núcleos),
  part-in-part (peças pequenas dentro de furos grandes), várias placas, rotações configuráveis, espelhamento opcional,
  trava de rotação por peça, quantidades editáveis (botão "× Kits").
- **Garantia**: nunca há sobreposição nem peça fora da margem — o espaçamento já inclui as tolerâncias de cálculo
  (ver `DECISIONS.md`) e há uma verificação final com geometria fina antes de exportar.
- **Exportação**: geometria **original** (arcos continuam arcos) apenas girada/movida; mm, origem em (0,0); camadas e
  cores preservadas; R2000 (padrão) ou R12; contorno da placa opcional na camada `PLACA` (cinza, desligado por padrão);
  furos antes do contorno externo; caminho mais curto entre peças; relatório PDF/PNG.
- **Projetos**: salva/abre `.sindri` (JSON) com arquivos, parâmetros, quantidades e o encaixe — reabre exatamente igual.

## Estrutura
```
app/
  main.py               inicialização da interface
  cli.py                linha de comando
  core/                 núcleo SEM Qt (pode ser trocado por libnest2d/Rust no futuro)
    dxf_import.py       leitura, unidades, blocos, cores
    part_builder.py     encadeamento, árvore de contenção, peças idênticas
    geometry.py         discretização, transformações, offset
    nfp.py              No-Fit Polygon / Inner-Fit Polygon com cache
    placement.py        decodificador (posicionamento)
    optimizer.py        algoritmo genético paralelo
    validate.py         verificação final com geometria fina
    collision.py        colisão para o ajuste manual
    dxf_export.py       escrita do DXF final
    project.py          salvar/abrir .sindri
    models.py           Part, Placement, NestResult, NestParams
  ui/                   PySide6: janela, canvas, painéis, diálogos, relatório, tema
  workers/nest_worker.py  thread que coordena o encaixe sem travar a tela
tests/                  pytest (+ fixtures/make_fixtures.py que gera os DXF de teste)
```

## Testes
```bash
pip install pytest pytest-cov
python -m pytest --cov=app/core
```
68 testes (importação, geometria, NFP, posicionamento sem sobreposição, otimizador paralelo, exportação R12/R2000 com
reimportação, projeto, CLI e um teste de ponta a ponta da interface em modo sem tela). Cobertura do `core/`: ~92%.

## Pendente de validação no laboratório
- Abrir os DXF exportados no **RDWorks** (R2000 e R12) e cortar uma placa de teste.
- Comparar o aproveitamento com o encaixe manual de um arquivo real.
- Gerar o `.exe` no Windows com `build_exe.bat`.

Referências: SVGnest e Deepnest (Jack Qiao, MIT), ezdxf, Shapely, pyclipper; Burke et al. (2007) sobre NFP.
