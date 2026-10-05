
from app.core.rdworks import files_to_open, find_rdworks


def test_escolhe_arquivo_para_abrir():
    files = ["a/t_placa01.dxf", "a/t_placa02.dxf", "a/t_relatorio.pdf"]
    assert files_to_open(files) == ["a/t_placa01.dxf"]
    files += ["a/t_MDF3mm_todas_placas.dxf", "a/t_todas_placas.dxf", "a/t_MDF6mm_todas_placas.dxf"]
    assert files_to_open(files) == ["a/t_todas_placas.dxf"]
    assert files_to_open(["a/t.pdf"]) == []


def test_acha_rdworks_salvo_ou_extra(tmp_path):
    exe = tmp_path / "RDWorksV8.exe"
    exe.write_bytes(b"MZ")
    assert find_rdworks(str(exe)) == str(exe)
    assert find_rdworks(None, extra=[str(exe)]) == str(exe)
    assert find_rdworks(str(tmp_path / "nao_existe.exe"), extra=[str(exe)]) == str(exe)
