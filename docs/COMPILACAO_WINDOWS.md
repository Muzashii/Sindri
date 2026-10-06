# Executável Windows — 6 de outubro de 2026

## Situação atual: inicialização aprovada após alteração pelo usuário

Após o usuário informar que desativou a proteção, o teste de inicialização de
`dist/Sindri.exe` passou novamente: processo ativo, preferências gravadas e ausência
de log de erro, com ambiente limpo e diretórios isolados. O agente não alterou as
configurações de segurança. Isso confirma a inicialização nesse ambiente, mas não
valida o fluxo completo nem a execução com Smart App Control ativo.

## Histórico: bloqueio pelo Controle de Aplicativos

O teste inicial abaixo passou uma vez, mas novas aberturas falharam, inclusive no
caminho `dist/corrigido/Sindri.exe`. Naquele estado, o executável não estava validado para uso.
Os eventos CodeIntegrity 3077 e 3033 de 06/10/2026 às 09:22 e 09:25 confirmam que
`numpy/fft/_pocketfft_internal.cp311-win_amd64.pyd` não atende à política de assinatura
`{0283ac0f-fff1-49ae-ada1-8a933130cad6}`. É necessário que o responsável pela política
avalie uma autorização específica ou uma distribuição assinada aprovada. A proteção
do Windows não foi alterada. Trocar versões não resolveu o bloqueio de forma confiável.

## Compilação e verificações anteriores

- Arquivo corrigido: `dist/corrigido/Sindri.exe` na raiz do projeto.
- Tamanho: 239.184.644 bytes (aproximadamente 228 MiB).
- SHA-256: `CDAC4AF996D3F3B35FE4B03AFB09E78DC3BF2D3203575CA8252E3A343AB0FD9F`.
- Build: Python 3.11.9, PyInstaller 6.22.3, PySide6 6.11.2; executável único, sem console.
- 125 testes aprovados no ambiente `.venv-build`; relatório `docs/build-compat-testes.xml`.
- Teste de inicialização offscreen aprovado com PATH limpo, sem PYTHONPATH/PYTHONHOME,
  sem redirecionamento de console e com preferências e diretórios isolados. O processo abriu,
  gravou suas preferências e permaneceu ativo sem gerar log de erro. O teste foi encerrado depois da verificação.

O build inicial incluiu DLLs incompatíveis de outras ferramentas presentes no PATH. O build final
usa `app/build_windows.py`, que isola os caminhos e inclui explicitamente os runtimes distribuídos
com o Qt. O `build_exe.bat` já chama esse procedimento.

A versão anterior com NumPy 2.4.6 falhou na abertura normal: o Controle de Aplicativos do
Windows bloqueou sua extensão nativa. A versão corrigida usa NumPy 1.26.4 e Pyclipper
1.3.0.post6, com dependências fixadas em `requirements-build.txt`. Nenhuma proteção do
Windows foi alterada. O teste antigo com ambiente herdado não detectou essa falha.

Após o fechamento da versão antiga, `dist/Sindri.exe` também foi substituído pelo
arquivo corrigido (mesmo SHA-256 acima). A cópia anterior foi preservada como
`dist/Sindri-anterior.exe.bak`. Ambos os caminhos agora contêm a versão corrigida.

Para recompilar em `dist/Sindri.exe`, feche o aplicativo e execute `build_exe.bat`.
Para gerar em outra pasta com o ambiente de build já instalado:

```powershell
.\.venv-build\Scripts\python.exe -m app.build_windows --distpath dist/corrigido
```

Para repetir apenas o teste de inicialização do arquivo corrigido:

```powershell
.\tools\check_exe.ps1 -Executable dist/corrigido/Sindri.exe
```

O teste não substitui a validação do fluxo completo no executável, da intranet real ou do corte no RDWorks.
