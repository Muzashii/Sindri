# Direção de design — Sindri

## Conceito
Preciso, técnico, sóbrio e confiável. A área de desenho é o centro do trabalho;
a interface auxilia a conferência de peças, materiais e etapas de fabricação.
Preservar a marca, os formatos, as regras de encaixe e os fluxos de exportação.

## Diagnóstico e plano
A base possui temas e ícones consistentes. Priorizar contraste, densidade da lista,
identificação no canvas, acesso contextual e redução de rolagem nos formulários.
Implementar tokens, depois componentes e por último a organização das telas.

## Fundação
Paletas clara/escura em app/ui/theme.py. Neutros 50–950 e tokens semânticos.
Segoe UI, com corpo 9 pt, auxiliares 9 pt, títulos 10/13/15 pt.
Escala de espaçamento 4/8/12/16/24/32; raios 4/8/12; controles nativos Qt.
Sem animação decorativa: feedback imediato preserva desempenho e reduz movimento.
Ícones vetoriais existentes, sem dependências de rede nem novos recursos externos.

## Telas
- Encaixe: lista compacta, botões explícitos para recolher os painéis, resumo da seleção,
  aproveitamento total identificado e rótulos discretos que acompanham o zoom.
- Foto: grupos recolhíveis, comparação original existente mantida; detalhes de ajustes
  recolhidos inicialmente. Exportação continua dependente de resultado válido.
- Caixa: passos recolhíveis e resumo/ações fixos existentes preservados.
- Intranet e diálogos: recebem a fundação visual comum; preservar integração e fluxo.

## Componentes e estados
Botões primário/secundário/sucesso/perigo; campos normal/hover/foco/desabilitado;
seções abertas/fechadas; peças selecionadas/com aviso/feitas; banners info/aviso/sucesso.
Foco com token próprio e espaço de borda reservado nos botões. Colisão recebe
contorno tracejado além do preenchimento. Seleção tem resumo textual acessível.

## Uso dos tokens
`theme.tokens()` fornece cores e valores do stylesheet; `theme.metric(nome)` fornece
medidas; `theme.qcolor(nome)` fornece QColor, incluindo alfa no formato RGBA.
Não confundir cores originais de camadas DXF com cores de feedback da interface.
`SectionToggle` mantém conteúdo e valores ao recolher uma seção; nunca destrói campos.

## Validação executada
Capturas com dados sintéticos, preferências isoladas e temas claro/escuro.
Revisadas larguras 1024/1100/1440 e escala 125% em Qt offscreen.
Suíte completa: 225 aprovados, 6 ignorados (combinações redundantes de divisórias).
Testes de interface/UX/design: 20 aprovados; regressões novas de design: 6 aprovadas.
Build sdist/wheel aprovado; instalação isolada e montagem da janela verificadas.
`git diff --check` usado para verificar whitespace. Não há lint configurado no projeto.
A integração com intranet autenticada, laser e RDWorks físico exige validação no laboratório.

## Resumo implementado
- Temas: contraste de texto secundário, campos, sucesso e avisos corrigido; tokens de
  medidas e neutros; foco com espaço reservado nos botões principais.
- Encaixe: barra em duas linhas, controles de painéis sincronizados com menus e
  preferências; lista de peças compacta; resumo textual da seleção; aproveitamento
  total explícito e detalhamento por placa na dica; estado em linha própria.
- Canvas: rótulos discretos e sensíveis ao zoom/seleção, fundos adequados ao tema,
  contorno tracejado para colisões e margem para etiquetas ao enquadrar.
- Foto: seções recolhíveis, ajustes secundários fechados inicialmente e formulários
  empilhados no modo compacto.
- Caixa: passos recolhíveis com suporte a teclado; parâmetros de arestas fechados
  inicialmente; resumo e ações fixos preservados.
- Parâmetros: seções recolhíveis e campos que acompanham a largura disponível.
- Intranet/diálogos: fundação visual compartilhada, sem alteração da integração.

## Arquivos alterados e adicionados
- app/ui/theme.py — paletas, medidas e estilos.
- app/ui/components.py — novo SectionToggle.
- app/ui/settings_panel.py, photo_panel.py, box_panel.py — seções/formulários.
- app/ui/parts_panel.py — densidade e checklist.
- app/ui/canvas.py — rótulos, estados e enquadramento.
- app/ui/mainwindow/ui_build.py — navegação e organização.
- app/ui/mainwindow/editing.py — resumo acessível da seleção.
- app/ui/mainwindow/status.py — detalhamento de aproveitamento.
- tests/test_design.py — regressões de teclado, dados, largura e contraste.
- README.md, ASSETS_CREDITS.md e docs/design/*.png — documentação e capturas.

## Pontos para validação no laboratório
A auditoria de contraste cobre pares de tokens e controles, não certifica conformidade
WCAG completa. Validar com NVDA, preferências reais de escala/tamanho de texto,
lotes extensos e operadores. Confirmar a integração com intranet autenticada, RDWorks
e corte físico. As capturas usam fixtures e nomes fictícios; não representam métricas
de eficiência do algoritmo. O aproveitamento dos exemplos é apenas ilustrativo.
