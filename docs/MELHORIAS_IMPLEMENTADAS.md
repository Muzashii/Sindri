# Correções e validação — 6 de outubro de 2026

Este documento acompanha a implementação da revisão técnica. O relatório original em
`REVISAO_TECNICA.md` é um registro do estado anterior. A cópia de segurança anterior às alterações
está em `antes-melhorias-2026-10-05.zip`.

## Problemas corrigidos

| Item da revisão | Comportamento implementado |
|---|---|
| 1. Unidades no lote | Correções por arquivo; um DXF suspeito não altera a escala dos demais. Unidades persistidas no projeto. |
| 2. Contornos inválidos | Reparo só é aceito se conservar os trechos originais; caso contrário, envoltório conservador e aviso. Resíduos numéricos não preenchem concavidades indevidamente. |
| 3. Agrupamento impreciso | Tolerância de equivalência de 0,001 mm, independente da resolução usada para encaixar. |
| 4. Cores/operações | Comparação de RGB efetivo e camada, incluindo a geometria de cada operação. Furos com outra cor não são aproveitados. |
| 5. Destino do projeto | Novo DXF limpa o destino anterior; falha na importação preserva peças e opções anteriores. |
| 6. Fonte alterada | Projeto v2 incorpora desenhos e hashes. Alterar ou remover o DXF gera aviso, mantendo o desenho salvo. Projetos v1 exigem recalcular o encaixe. |
| 7. Discos diferentes | Caminhos relativos têm alternativa absoluta quando as unidades de disco diferem. |
| 8. Redução de kits | Quantidades, cópias posicionadas, pendências e placas são reconciliadas. |
| 9. Atualização parcial | Preparação completa antes da troca, backups por tentativa, reversão em falha de gravação, bloqueio de concorrência e fechamento. |
| 10. Validação | Detecta sobreposição mesmo com espaçamento zero; confere instâncias, duplicatas, placas, números finitos e materiais. |
| 11. Identidade de peças | Adicionar arquivos preserva IDs, quantidades personalizadas e travas de rotação pelo conteúdo do desenho. |
| 12. Salvamento automático | Edições relevantes, travas, quantidades, parâmetros e checklist agendam salvamento. Funciona antes de encaixar, informa falhas e inclui travas/checklist no desfazer. |
| 13. Checklist | Cópias ainda não encaixadas entram no total; cortar apenas as cópias posicionadas não conclui o tipo inteiro. |
| 14. Ordem de corte | Peças dentro de furos precedem a peça hospedeira, inclusive quando a ordenação por proximidade está desligada. |
| 15. CLI | Valida parâmetros; bloqueia exportação inválida; retorna erro específico para resultado incompleto e exige opção explícita para exportação parcial. |
| 16. Pacote | Descoberta de todos os subpacotes `app`, inclusive `app.ui.mainwindow`; wheel instalado e janela aberta fora da árvore fonte. |
| 17. Proteção do atualizador | Nomes protegidos sem distinção entre maiúsculas e minúsculas, lista de arquivos/pastas de aplicação permitidos e verificação de destinos. |
| 18. Testes/JSON | Preferências e arquivos de teste isolados; JSON malformado, codificação inválida e versão incompatível geram `ProjectError`. |

## Melhorias adicionais

- DXF e PDF preparados em pasta temporária; falha no PDF não publica um DXF incompleto como resultado final.
- Download para temporário, substituição somente depois do sucesso, cancelamento real e liberação de referências.
- Nomes reservados do Windows tratados; pasta Documentos consultada pela API do Windows.
- Logs da intranet deixam de registrar URLs, caminhos finais, respostas e detalhes brutos de requisições.
- Número padrão de processos limitado; caches e soluções avaliadas limitados em entradas; cancelamento cooperativo no decodificador e nos processos de cálculo.
- Versões mínimas unificadas e verificadas pelo lançador; configuração de CI para Windows e Linux.
- Menu “Arquivos e avisos da importação”, indicação de alterações não salvas e horário de recuperação; contagens detalhadas no indicador de peças.
- Documentação de projeto portátil, exportação, camada PLACA e retornos da CLI revisada.

## Evidências

Validação final local: **125 testes passaram em 69,89 s**, processo encerrado com **código 0**,
e **89% de cobertura do núcleo**. A suíte mantém a aplicação Qt viva e destrói páginas antes do
perfil para evitar erro de encerramento após os testes. Os 34 testes de regressão/atualização
também foram executados separadamente e passaram.

- `melhorias-testes.xml`: resultado da suíte completa.
- `melhorias-cobertura.json`: cobertura do núcleo.
- `tests/test_reliability.py`: regressões de geometria, arquivos, UI, exportação, cancelamento e atualização.
- `tools/check_wheel.py`: instalação temporária e abertura da janela fora da pasta fonte.
- `dist/sindri-1.0.0-py3-none-any.whl`: pacote final gerado (dentro desta pasta `docs`). O teste de
  instalação também verifica que um wheel não usa o atualizador por ZIP sobre `site-packages`.
- `melhorias-benchmark.json`: caso de 35 peças, semente 17, população 4, duas gerações, sem processos adicionais.

No benchmark local, a importação levou aproximadamente 1,34 s; a primeira solução, 0,22 s;
o encaixe completo, 1,28 s. Foram usadas duas placas, nenhuma peça ficou pendente e a validação
não encontrou problemas. A medição de memória cobre alocações Python, não toda a memória das
bibliotecas nativas. Esse caso serve como base repetível, sem demonstrar desempenho de lotes grandes.

## Limites e evolução posterior

Os testes usam DXF locais e uma intranet simulada. Ainda é necessário validar R12/R2000 e a ordem
efetiva de corte no RDWorks/máquina, além do executável PyInstaller. A configuração de CI foi
adicionada, mas não foi executada em um servidor remoto nesta sessão.

As recomendações de evolução que não estão concluídas são: importação/exportação inteiramente em
segundo plano com progresso e cancelamento; orçamento de memória em bytes e avaliação de lotes
reais grandes; separar os serviços da janela da intranet e oferecer limpeza completa de sessão;
adoção gradual de lint/tipos; placas com dimensões próprias por material e retalhos. A arquitetura
continua baseada em mixins, com reconciliação compartilhada de estado, sem uma reescrita completa.

A reversão do atualizador trata exceções durante as trocas de arquivos. Uma interrupção forçada do
processo ou queda de energia pode exigir recuperação manual usando o backup e `transaction.json`.
