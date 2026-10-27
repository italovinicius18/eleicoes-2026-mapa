"""Baixa a votação por seção do 1º turno 2026 (TSE dados abertos) para Presidente e Governador.

Fonte: https://cdn.tse.jus.br/estatistica/sead/odsele/votacao_secao/
  votacao_secao_2026_<UF>.zip  -> Governador
  votacao_secao_2026_BR.zip    -> Presidente (todas as UFs + ZZ/exterior)
Saída: dados_brutos/1turno/<UF>.csv (uf, municipio, zona, secao, local, cargo, numero, votos)
Os zips são lidos em streaming (sem extrair o CSV) e apagados depois.
"""
import sys
import urllib.request
import zipfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pandas as pd

BASE = "https://cdn.tse.jus.br/estatistica/sead/odsele/votacao_secao"
RAIZ = Path(__file__).resolve().parent.parent
TMP = RAIZ / "dados_brutos" / "secao_1turno"
SAIDA = RAIZ / "dados_brutos" / "1turno"
UFS = ("AC AL AM AP BA CE DF ES GO MA MG MS MT PA PB PE PI PR RJ RN RO RR RS SC SE SP TO").split()
COLS = {"SG_UF": "uf", "CD_MUNICIPIO": "municipio", "NR_ZONA": "zona", "NR_SECAO": "secao",
        "NR_LOCAL_VOTACAO": "local", "DS_CARGO": "cargo", "NR_VOTAVEL": "numero", "QT_VOTOS": "votos"}


def baixar(nome):
    destino = TMP / nome
    if not destino.exists():
        tmp = destino.with_suffix(".part")
        urllib.request.urlretrieve(f"{BASE}/{nome}", tmp)
        tmp.rename(destino)
    return destino


def ler(zip_path, cargo):
    with zipfile.ZipFile(zip_path) as z:
        csv = [n for n in z.namelist() if n.endswith(".csv")][0]
        with z.open(csv) as fh:
            for parte in pd.read_csv(fh, sep=";", encoding="latin-1", usecols=list(COLS),
                                     chunksize=2_000_000):
                yield parte[parte.DS_CARGO == cargo].rename(columns=COLS)


def governador(uf):
    destino = SAIDA / f"gov_{uf}.csv"
    if destino.exists() or (SAIDA / f"{uf}.csv").exists():
        return
    z = baixar(f"votacao_secao_2026_{uf}.zip")
    pd.concat(ler(z, "Governador")).to_csv(destino, index=False)
    z.unlink()
    print(f"[{uf}] governador ok", flush=True)


def presidente():
    if (SAIDA / "pres_SP.csv").exists() or (SAIDA / "ZZ.csv").exists():
        return
    z = baixar("votacao_secao_2026_BR.zip")
    d = pd.concat(ler(z, "Presidente"))
    for uf, g in d.groupby("uf"):
        g.to_csv(SAIDA / f"pres_{uf}.csv", index=False)
    z.unlink()
    print("presidente ok", flush=True)


def main():
    TMP.mkdir(parents=True, exist_ok=True)
    SAIDA.mkdir(parents=True, exist_ok=True)
    ufs = sys.argv[1:] or UFS
    with ThreadPoolExecutor(max_workers=4) as ex:
        futuros = [ex.submit(presidente)] + [ex.submit(governador, uf) for uf in ufs]
        for f in futuros:
            f.result()
    for uf in ufs + ["ZZ"]:
        arquivos = [p for p in (SAIDA / f"pres_{uf}.csv", SAIDA / f"gov_{uf}.csv") if p.exists()]
        if arquivos:
            pd.concat(pd.read_csv(p) for p in arquivos).to_csv(SAIDA / f"{uf}.csv", index=False)
            for p in arquivos:
                p.unlink()
    print("fim", flush=True)


if __name__ == "__main__":
    main()
