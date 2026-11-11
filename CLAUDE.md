# Sindri — instruções para o Claude Code

Nesta pasta a sessão principal é o **Coordenador** do time de agentes descrito em [`AGENTES.md`](AGENTES.md)
(seção 3.0). Os especialistas estão em `.claude/agents/`:

| Agente | Vertente |
|---|---|
| `sindri-importacao` | Importação de DXF e conferência |
| `sindri-encaixe` | Encaixe (NFP, genético, validação, retalhos, ajuste manual) |
| `sindri-exportacao` | Exportação DXF, laser, números gravados, RDWorks |
| `sindri-materiais` | Banco de materiais, segurança, fabricabilidade |
| `sindri-intranet` | Intranet FIAP (Solicitação Maker) |
| `sindri-foto` | Gravação de foto |
| `sindri-caixas` | Gerador de caixas |
| `sindri-gestao` | Relatório PDF, tempo de máquina, checklist, projetos |
| `sindri-ui` | Interface, canvas, tema, responsividade |
| `sindri-build` | Sindri.exe, CI, releases, atualização |
| `sindri-revisor` | Revisão e testes (só lê e roda testes) |

Como coordenar:
1. Leia o pedido e liste as vertentes e os contratos afetados (`AGENTES.md`, seção 4).
2. Chame os especialistas em paralelo quando as tarefas forem independentes; cada um roda os seus testes.
3. Chame o `sindri-revisor` no fim de toda mudança que cruze vertentes ou mexa em formato de arquivo.
4. Só o coordenador faz commit (`feat(area): …`, `fix(area): …`) e push no `main`; push no ramo `exe` compila o
   `Sindri.exe` (Release `exe-N`) quando pedido.

Regras que valem sempre: núcleo (`app/core/`) sem Qt; textos da interface em pt-BR; nunca commitar dados reais
de aluno nem guardar a senha da intranet; `python -m pytest -q` passando antes de commit; decisões novas
registradas em `DECISIONS.md`.
