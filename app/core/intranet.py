"""Integração com a intranet FIAP (Solicitação Maker) — partes sem Qt.

A página é aberta num navegador embutido (sessão do próprio usuário). Daqui saem:
* os scripts JavaScript que leem a lista de solicitações e o detalhe de uma solicitação;
* a organização dos arquivos baixados por material e as quantidades da tabela.

Estrutura da página (observada em 10/2026):
  lista:   <a class="js-visualisa-solicitacao" data-codigo="8759" onclick="abreSolicitacao(8759)">
  detalhe: <h4 id="myModalLabel">Solicitação n° 8759</h4>
           <tbody id="tabela-corpo-arquivos"><tr><td><a download="Cod8759_..." href=".../X.dxf">nome.dxf</a></td>
           <th>MDF 3mm</th><td>2</td></tr>...
"""
from __future__ import annotations

import os
import re
import unicodedata
from dataclasses import dataclass, field
from typing import Optional

INTRANET_URL = "https://intranet.fiap.com.br/net/SolicitacaoMaker"


def should_go_to_requests(url: str, has_password_field: bool, target: str = INTRANET_URL) -> bool:
    """Depois do login a intranet abre a página inicial: se já estamos logados na intranet
    (não é tela de login) e fora de Solicitações Maker, devemos ir para lá."""
    from urllib.parse import urlparse
    u, t = urlparse(url or ""), urlparse(target)
    if has_password_field or not u.netloc:
        return False
    if u.netloc.lower() != t.netloc.lower():
        return False                      # login da Microsoft / SSO: deixa o usuário terminar
    low = (u.path + "?" + u.query).lower()
    if any(k in low for k in ("login", "logon", "signin", "auth", "sso", "account")):
        return False
    return not u.path.lower().rstrip("/").startswith(t.path.lower().rstrip("/"))

# Lê todas as solicitações de todas as abas (Aguardando, Em execução...). As tabelas usam
# paginação (DataTables), que tira do HTML as linhas das outras páginas: quando a biblioteca
# existe, as linhas vêm direto dela; senão, do que está na página.
JS_LIST = r"""
(function(){
  var out = [];
  function tabLabel(pane){
    if(!pane || !pane.id) return '';
    var a = document.querySelector('a[href="#'+pane.id+'"], [data-target="#'+pane.id+'"]');
    return a ? a.textContent.replace(/\s+/g,' ').trim() : '';
  }
  function add(tr, pane){
    var a = tr.querySelector('a.js-visualisa-solicitacao'); if(!a) return;
    var c = Array.prototype.map.call(tr.querySelectorAll('td'), function(td){ return td.textContent.trim(); });
    out.push({codigo: a.getAttribute('data-codigo') || c[0], rm: c[1]||'', nome: c[2]||'', tipo: c[3]||'',
              data: c[4]||'', status: c[5]||'', responsavel: c[6]||'',
              aba: pane ? pane.id : '', abaNome: tabLabel(pane)});
  }
  try {
    if (window.jQuery && jQuery.fn && jQuery.fn.dataTable) {
      jQuery.fn.dataTable.tables().forEach(function(t){
        var pane = t.closest('.tab-pane');
        jQuery(t).DataTable().rows().nodes().toArray().forEach(function(tr){ add(tr, pane); });
      });
    }
  } catch(e) {}
  document.querySelectorAll('a.js-visualisa-solicitacao').forEach(function(a){
    var tr = a.closest('tr'); if(tr) add(tr, a.closest('.tab-pane'));
  });
  return JSON.stringify({url: location.href, temFuncao: typeof abreSolicitacao === 'function',
                         temSenha: !!document.querySelector('input[type=password]'),
                         solicitacoes: out});
})()
"""

# A tabela de cada aba mostra 10 por página; as outras páginas só vêm quando se clica no número
# (<a data-codigostatus="1" data-pagina="2" data-qtdlinhas="20">). Este script percorre as páginas
# clicando nelas, como a pessoa faria, e guarda as linhas em window.__sindri. Abas enormes
# (Finalizado, Cancelados) ficam só nas primeiras páginas.
JS_CRAWL = r"""
(function(maxRows, bigPages){
  if (window.__sindri && window.__sindri.running) return 'running';
  var st = window.__sindri = {running: true, done: false, rows: [], progress: '', error: ''};
  function tabLabel(pane){
    if(!pane || !pane.id) return '';
    var a = document.querySelector('a[href="#'+pane.id+'"], [data-target="#'+pane.id+'"]');
    return a ? a.textContent.replace(/\s+/g,' ').trim() : '';
  }
  function rowsOf(pane){
    var out = [];
    pane.querySelectorAll('a.js-visualisa-solicitacao').forEach(function(a){
      var tr = a.closest('tr'); if(!tr) return;
      var c = Array.prototype.map.call(tr.querySelectorAll('td'), function(td){ return td.textContent.trim(); });
      out.push({codigo: a.getAttribute('data-codigo') || c[0], rm: c[1]||'', nome: c[2]||'', tipo: c[3]||'',
                data: c[4]||'', status: c[5]||'', responsavel: c[6]||'', aba: pane.id, abaNome: tabLabel(pane)});
    });
    return out;
  }
  function codes(pane){ return rowsOf(pane).map(function(r){ return r.codigo; }).join(','); }
  function curPage(pane){ var i = pane.querySelector('input[id=paginaAtual]'); return i ? parseInt(i.value, 10) || 1 : 1; }
  function anchors(code){ return Array.prototype.slice.call(document.querySelectorAll('a[data-codigostatus="'+code+'"]')); }
  function sleep(ms){ return new Promise(function(r){ setTimeout(r, ms); }); }
  async function goTo(code, paneId, n){
    var pane = document.getElementById(paneId);
    if (pane && curPage(pane) === n) return pane;
    var as = anchors(code);
    var a = as.filter(function(x){ return x.getAttribute('data-pagina') === String(n) && /^\d+$/.test(x.textContent.trim()); })[0]
         || as.filter(function(x){ return x.getAttribute('data-pagina') === String(n); })[0];
    if (!a) return null;
    var before = pane ? codes(pane) : '';
    a.click();
    for (var t = 0; t < 80; t++) {            // até ~12 s por página
      await sleep(150);
      pane = document.getElementById(paneId);
      if (pane && (curPage(pane) === n || (codes(pane) !== before && codes(pane) !== ''))) {
        await sleep(100);
        return document.getElementById(paneId);
      }
    }
    return null;
  }
  (async function(){
    try {
      var seen = [], jobs = [];
      document.querySelectorAll('a[data-codigostatus]').forEach(function(a){
        var code = a.getAttribute('data-codigostatus');
        if (seen.indexOf(code) >= 0) return;
        var pane = a.closest('.tab-pane'); if (!pane || !pane.id) return;
        seen.push(code);
        var total = parseInt(a.getAttribute('data-qtdlinhas'), 10) || 0;
        var per = Math.max(10, rowsOf(pane).length);
        var pages = Math.max(1, Math.ceil(total / per));
        if (total > maxRows) pages = Math.min(pages, bigPages);
        jobs.push({code: code, pane: pane.id, pages: pages, label: tabLabel(pane)});
      });
      for (var j = 0; j < jobs.length; j++) {
        var job = jobs[j];
        if (job.pages <= 1) continue;
        for (var n = 1; n <= job.pages; n++) {
          st.progress = (job.label || 'aba') + ' ' + n + '/' + job.pages;
          var pane = await goTo(job.code, job.pane, n);
          if (!pane) break;
          st.rows = st.rows.concat(rowsOf(pane));
        }
        await goTo(job.code, job.pane, 1);   // deixa o site como estava
      }
    } catch (e) { st.error = String(e); }
    st.running = false; st.done = true; st.progress = '';
  })();
  return 'started';
})(%d, %d)
"""

JS_CRAWL_STATE = r"""
(function(){ var s = window.__sindri; if (!s) return null;
  return JSON.stringify({done: s.done, progress: s.progress, error: s.error, rows: s.rows}); })()
"""

JS_CRAWL_RESET = "(function(){ if (window.__sindri && !window.__sindri.running) window.__sindri = null; })()"

CRAWL_MAX_ROWS = 300      # abas com até isso são lidas inteiras
CRAWL_BIG_PAGES = 5       # abas maiores (Finalizado, Cancelados): só as 5 primeiras páginas

ALL_STATUS = "Todos os status"


def row_status(r: dict) -> str:
    """Status da solicitação = nome da aba onde ela está (AGUARDANDO → Aguardando)."""
    s = (r.get("abaNome") or r.get("status") or "").strip()
    s = re.sub(r"\s+", " ", s)
    return s[:1].upper() + s[1:].lower() if s else "Sem status"


def merge_rows(rows: list[dict]) -> list[dict]:
    """Remove repetidas (mesma solicitação lida da tabela e da página) e preenche 'situacao'."""
    seen, out = set(), []
    for r in rows:
        key = str(r.get("codigo", "")).strip()
        if not key or key in seen:
            continue
        seen.add(key)
        r = dict(r, codigo=key)
        r["situacao"] = row_status(r)
        out.append(r)
    return out


def status_options(rows: list[dict]) -> list[str]:
    """Status na ordem das abas do site; 'Resultado pesquisa' vai para o fim."""
    opts = []
    for r in rows:
        s = r.get("situacao") or row_status(r)
        if s not in opts:
            opts.append(s)
    opts.sort(key=lambda s: s.lower().startswith("resultado"))
    return opts


JS_OPEN = "(function(){ if (typeof abreSolicitacao !== 'function') return 'sem-funcao'; abreSolicitacao(%d); return 'ok'; })()"

# Lê o modal aberto (retorna null enquanto não carregou o número pedido)
JS_DETAIL = r"""
(function(codigo){
  var h = document.querySelector('#myModalLabel');
  if(!h) return null;
  var num = (h.textContent.match(/\d+/) || [''])[0];
  if(num !== String(codigo)) return null;
  var body = h.closest('.modal-content') || document;
  var info = {};
  body.querySelectorAll('.modal-body > b').forEach(function(b){
    var k = b.textContent.replace(':','').trim(); var t = '';
    var n = b.nextSibling;
    while(n && !(n.nodeName === 'BR' || n.nodeName === 'B' || n.nodeName === 'TABLE')){ t += n.textContent || ''; n = n.nextSibling; }
    info[k] = t.trim();
  });
  var rows = [];
  body.querySelectorAll('#tabela-corpo-arquivos tr').forEach(function(tr){
    var a = tr.querySelector('a[href]'); if(!a) return;
    var cells = Array.prototype.slice.call(tr.children);
    var qtd = cells.length ? cells[cells.length-1].textContent.trim() : '1';
    var mat = cells.length >= 3 ? cells[1].textContent.trim() : '';
    rows.push({nome: a.textContent.trim(), href: a.href, download: a.getAttribute('download') || '',
               material: mat, quantidade: qtd});
  });
  if(!rows.length && body.querySelector('#tabela-corpo-arquivos') === null) return null;
  return JSON.stringify({codigo: codigo, titulo: h.textContent.trim(), info: info, arquivos: rows});
})(%d)
"""


@dataclass
class RequestFile:
    name: str
    url: str
    material: str
    quantity: int
    local_path: Optional[str] = None

    @property
    def is_dxf(self) -> bool:
        return self.name.lower().endswith(".dxf") or self.url.lower().split("?")[0].endswith(".dxf")


@dataclass
class RequestDetail:
    code: int
    info: dict
    files: list[RequestFile] = field(default_factory=list)

    @property
    def rm(self) -> str:
        return self.info.get("RM", "")

    @property
    def dxf_files(self) -> list[RequestFile]:
        return [f for f in self.files if f.is_dxf]

    @property
    def student(self) -> str:
        return self.info.get("Nome", "")

    def materials(self) -> dict[str, list[RequestFile]]:
        out: dict[str, list[RequestFile]] = {}
        for f in self.files:
            if f.is_dxf:
                out.setdefault(f.material or "Sem material", []).append(f)
        return out


def parse_detail(data: dict) -> RequestDetail:
    files = []
    for r in data.get("arquivos", []):
        try:
            q = int(re.sub(r"[^\d]", "", str(r.get("quantidade", "1"))) or 1)
        except ValueError:
            q = 1
        files.append(RequestFile(name=r.get("nome") or os.path.basename(r.get("href", "")),
                                 url=r.get("href", ""), material=(r.get("material") or "").strip(),
                                 quantity=max(1, q)))
    return RequestDetail(int(data.get("codigo")), dict(data.get("info") or {}), files)


def safe_name(text: str, max_len: int = 80) -> str:
    """Nome seguro para pasta/arquivo no Windows (mantém acentos)."""
    text = unicodedata.normalize("NFC", str(text)).strip()
    text = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", text)
    text = re.sub(r"\s+", " ", text).strip(" .")
    return (text or "sem_nome")[:max_len]


def request_folder(base: str, detail: RequestDetail) -> str:
    name = f"{detail.code}"
    if detail.student:
        name += f" - {detail.student}"
    return os.path.join(base, safe_name(name))


def target_path(base: str, detail: RequestDetail, f: RequestFile) -> str:
    folder = os.path.join(request_folder(base, detail), safe_name(f.material or "Sem material", 40))
    return os.path.join(folder, safe_name(f.name, 120))


def default_base_folder() -> str:
    docs = os.path.join(os.path.expanduser("~"), "Documents")
    if not os.path.isdir(docs):
        docs = os.path.expanduser("~")
    return os.path.join(docs, "Sindri", "Solicitações")


def file_materials(files: list[RequestFile]) -> dict[str, str]:
    """Caminho local -> material (as peças de cada material vão para placas próprias)."""
    return {os.path.abspath(f.local_path): f.material for f in files if f.local_path}


def request_summary(detail: "RequestDetail", materials: list[str]) -> dict:
    """Dados exibidos no cartão da solicitação e salvos no projeto."""
    i = detail.info
    return {"code": detail.code, "rm": i.get("RM", ""), "nome": i.get("Nome", ""),
            "projeto": i.get("Projeto", ""), "professor": i.get("Professor", ""),
            "turma": i.get("Turma", ""), "materials": list(materials), "info": dict(i)}


def file_multipliers(files: list[RequestFile]) -> dict[str, int]:
    """Caminho local -> quantidade pedida na tabela (cópias de tudo que há no arquivo)."""
    return {os.path.abspath(f.local_path): f.quantity for f in files if f.local_path}
