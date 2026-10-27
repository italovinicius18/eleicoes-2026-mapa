"""Monta as bases analíticas por UF (Presidente e Governador, 1º e 2º turnos 2026).

Entradas:
  dados_brutos/1turno/<UF>.csv                  - baixar_1turno.py
  dados_brutos/2turno/<UF>.csv                  - baixar_bu_2turno.py (UF sem arquivo = 2º turno pendente)
  dados_brutos/locais/eleitorado_local_votacao_2026_<UF>.csv, dados/geo/*  - baixar_base.py
  dados_brutos/candidatos/consulta_cand_2026_BRASIL.csv  - baixar_base.py (github.com/leofn/tse-candidatos-2026)

Saídas (dados/resumo/):
  candidatos.csv, disputas.csv      - nomes e os dois polos de cada disputa (UF x cargo)
  <UF>_unidade.csv, <UF>_local.csv  - indicadores por município (RA no DF) e por local de votação
  uf.csv, municipios_br.csv, total.csv - recortes nacionais
"""
import json
import sys
from pathlib import Path

import pandas as pd
from shapely.geometry import Point, shape

RAIZ = Path(__file__).resolve().parent.parent
BRUTO = RAIZ / "dados_brutos"
DADOS = RAIZ / "dados"
RESUMO = DADOS / "resumo"
LER = dict(sep=";", encoding="latin-1")
UFS = ("AC AL AM AP BA CE DF ES GO MA MG MS MT PA PB PE PI PR RJ RN RO RR RS SC SE SP TO").split()
BRANCO, NULO = 95, 96
POLOS_PRESIDENTE = (22, 13)  # Flávio Bolsonaro x Lula (2º turno nacional)
CHAVES = ["turno", "cargo"]


def candidatos():
    c = pd.read_csv(BRUTO / "candidatos" / "consulta_cand_2026_BRASIL.csv", **LER)
    c = c[(c.NR_TURNO == 1) & c.DS_CARGO.str.upper().isin(["PRESIDENTE", "GOVERNADOR"])
          & ~c.DS_SIT_TOT_TURNO.eq("#NULO")]
    c = pd.DataFrame({"uf": c.SG_UF.where(c.DS_CARGO.str.upper() == "GOVERNADOR", "BR"),
                      "cargo": c.DS_CARGO.str.title(), "numero": c.NR_CANDIDATO,
                      "nome": c.NM_URNA_CANDIDATO.str.title(), "partido": c.SG_PARTIDO,
                      "situacao": c.DS_SIT_TOT_TURNO})
    return c.drop_duplicates(["uf", "cargo", "numero"])


def votos(uf):
    cols = ["turno", "cargo", "municipio", "zona", "secao", "local", "numero", "votos"]
    partes = [pd.read_csv(BRUTO / "1turno" / f"{uf}.csv").assign(turno=1)[cols]]
    if (arq2 := BRUTO / "2turno" / f"{uf}.csv").exists():
        partes.append(pd.read_csv(arq2).assign(turno=2)[cols])
    return pd.concat(partes, ignore_index=True)


def locais(uf):
    """(seções, locais): seção -> local em cada turno, e cadastro único dos locais com coordenadas."""
    l = pd.read_csv(BRUTO / "locais" / f"eleitorado_local_votacao_2026_{uf}.csv", decimal=",", **LER)
    l = l.rename(columns={"NR_TURNO": "turno", "CD_MUNICIPIO": "municipio", "NR_ZONA": "zona",
                          "NR_SECAO": "secao", "NR_LOCAL_VOTACAO": "local",
                          "QT_ELEITOR_SECAO": "aptos"})
    secoes = l[["turno", "municipio", "zona", "secao", "local", "aptos"]]
    loc = (l.sort_values("turno").groupby(["municipio", "zona", "local"])
            .agg(nome_local=("NM_LOCAL_VOTACAO", "first"), bairro=("NM_BAIRRO", "first"),
                 lat=("NR_LATITUDE", "first"), lon=("NR_LONGITUDE", "first"))
            .reset_index())
    return secoes, loc


def ra_do_df(loc):
    ras = [(f["properties"]["ra_nome"], shape(f["geometry"]))
           for f in json.load(open(DADOS / "geo" / "ra.geojson"))["features"]]

    def ra_de(lat, lon):
        p = Point(lon, lat)
        dentro = [n for n, g in ras if g.contains(p)]
        return dentro[0] if dentro else min(ras, key=lambda r: r[1].distance(p))[0]

    return [ra_de(a, o) for a, o in zip(loc.lat, loc.lon)]


def definir_polos(uf, v, cand):
    """Os dois polos de cada cargo: finalistas do 2º turno ou, sem 2º turno, os dois mais votados."""
    polos = {"Presidente": POLOS_PRESIDENTE}
    if "Governador" in set(v.cargo):
        g = cand[(cand.uf == uf) & (cand.cargo == "Governador")]
        fin = g[g.situacao == "2º TURNO"].numero.tolist()
        g2 = v[(v.cargo == "Governador") & (v.turno == 2)]
        if len(fin) == 2 and len(g2):
            tot = g2.groupby("numero").votos.sum()
            polos["Governador"] = tuple(tot.loc[fin].sort_values(ascending=False).index)
        else:
            g1 = v[(v.cargo == "Governador") & (v.turno == 1) & ~v.numero.isin([BRANCO, NULO])]
            polos["Governador"] = tuple(g1.groupby("numero").votos.sum().nlargest(2).index)
    return polos


def resumir(v, chaves, polos):
    """Indicadores por chave para cada turno x cargo. Polo a = 1º colocado/eleito; b = 2º."""
    linhas = []
    for (turno, cargo), g in v.groupby(CHAVES):
        a, b = polos[cargo]
        t = g.pivot_table(index=chaves, columns="numero", values="votos", aggfunc="sum", fill_value=0)
        for n in (a, b):
            if n not in t:
                t[n] = 0
        cand_cols = [c for c in t.columns if c not in (BRANCO, NULO)]
        validos = t[cand_cols].sum(axis=1)
        comp = t.sum(axis=1)
        r = pd.DataFrame({"turno": turno, "cargo": cargo, "comparecimento": comp, "validos": validos,
                          "brancos_nulos": comp - validos})
        div = validos.where(validos > 0)
        r["pct_a"] = 100 * t[a] / div
        r["pct_b"] = 100 * t[b] / div
        r["margem"] = r.pct_a - r.pct_b
        r["vencedor"] = t[cand_cols].idxmax(axis=1)
        r["pct_vencedor"] = 100 * t[cand_cols].max(axis=1) / div
        for n in cand_cols:
            r[f"pct_{n}"] = 100 * t[n] / div
        linhas.append(r.reset_index())
    return pd.concat(linhas, ignore_index=True)


def processar_uf(uf, cand, municipios):
    v = votos(uf)
    secoes, loc = locais(uf)
    polos = definir_polos(uf, v, cand)
    # o local de cada seção vem do cadastro do turno (o arquivo de votos às vezes traz o local original)
    v = v.merge(secoes[["turno", "municipio", "zona", "secao", "local"]],
                on=["turno", "municipio", "zona", "secao"], how="left", suffixes=("_voto", ""))
    sem_local = int(v.local.isna().sum())
    v["local"] = v.local.fillna(v.local_voto).astype(int)
    v = v.drop(columns="local_voto")

    if uf == "DF":
        loc["unidade"] = ra_do_df(loc)
        loc["nome_unidade"] = loc.unidade
    else:
        mun = municipios[municipios.uf == uf].set_index("municipio")
        loc["unidade"] = loc.municipio.map(mun.ibge).astype("Int64").astype(str)
        loc["nome_unidade"] = loc.municipio.map(mun.nome)
    chave_local = ["municipio", "zona", "local"]
    v = v.merge(loc[chave_local + ["unidade"]], on=chave_local, how="left")
    secoes = secoes.merge(loc[chave_local + ["unidade"]], on=chave_local, how="left")
    if uf != "DF":  # local fora do cadastro: o município da própria seção define a unidade
        mun = municipios[municipios.uf == uf].set_index("municipio")
        for d in (v, secoes):
            faltam = d.unidade.isna()
            d.loc[faltam, "unidade"] = d.loc[faltam, "municipio"].map(mun.ibge).astype("Int64").astype(str)

    un = resumir(v, ["unidade"], polos)
    un["nome"] = un.unidade.map(loc.groupby("unidade").nome_unidade.first())
    aptos_un = secoes.groupby(["turno", "unidade"]).aptos.sum().rename("aptos")
    un = un.merge(aptos_un, left_on=["turno", "unidade"], right_index=True, how="left")
    un["abstencao"] = 100 * (1 - un.comparecimento / un.aptos)

    lc = resumir(v, chave_local, polos)
    aptos_lc = secoes.groupby(["turno"] + chave_local).aptos.sum().rename("aptos")
    lc = lc.merge(aptos_lc, left_on=["turno"] + chave_local, right_index=True, how="left")
    lc = lc.merge(loc, on=chave_local, how="left")
    lc["abstencao"] = 100 * (1 - lc.comparecimento / lc.aptos)

    tot = resumir(v.assign(uf=uf), ["uf"], polos)
    tot = tot.merge(secoes.groupby("turno").aptos.sum(), left_on="turno", right_index=True)
    tot["abstencao"] = 100 * (1 - tot.comparecimento / tot.aptos)

    un.to_csv(RESUMO / f"{uf}_unidade.csv", index=False)
    lc.to_csv(RESUMO / f"{uf}_local.csv", index=False)
    disputa = [{"uf": uf, "cargo": c, "a": a, "b": b,
                "segundo_turno": bool(len(v[(v.cargo == c) & (v.turno == 2)]))}
               for c, (a, b) in polos.items()]
    print(f"[{uf}] seções sem local: {sem_local}; turnos: {sorted(v.turno.unique())}; polos: {polos}",
          flush=True)
    # votos por município (IBGE) para o mapa nacional
    v["ibge"] = v.municipio.map(municipios[municipios.uf == uf].set_index("municipio").ibge)
    return tot, disputa, v


def main():
    RESUMO.mkdir(parents=True, exist_ok=True)
    cand = candidatos()
    municipios = pd.read_csv(DADOS / "geo" / "municipios_tse.csv")
    ufs = sys.argv[1:] or UFS
    totais, disputas, pres = [], [], []
    for uf in ufs:
        tot, disp, v = processar_uf(uf, cand, municipios)
        totais.append(tot)
        disputas += disp
        pres.append(v[v.cargo == "Presidente"].assign(uf=uf))

    # Exterior (ZZ): só Presidente, entra no total nacional
    zz = pd.read_csv(BRUTO / "1turno" / "ZZ.csv").assign(turno=1)
    if (BRUTO / "2turno" / "ZZ.csv").exists():
        zz = pd.concat([zz, pd.read_csv(BRUTO / "2turno" / "ZZ.csv").assign(turno=2)])
    pres.append(zz[zz.cargo == "Presidente"].assign(uf="ZZ"))
    pres = pd.concat(pres, ignore_index=True)

    polos_p = {"Presidente": POLOS_PRESIDENTE}
    uf_tab = pd.concat(totais, ignore_index=True)
    uf_tab.to_csv(RESUMO / "uf.csv", index=False)
    resumir(pres.assign(pais="BR"), ["pais"], polos_p).to_csv(RESUMO / "total.csv", index=False)
    br = resumir(pres[pres.uf != "ZZ"].dropna(subset=["ibge"]).astype({"ibge": int}), ["ibge"], polos_p)
    br.to_csv(RESUMO / "municipios_br.csv", index=False)
    pd.DataFrame(disputas).to_csv(RESUMO / "disputas.csv", index=False)
    cand.to_csv(RESUMO / "candidatos.csv", index=False)
    print("ok", flush=True)


if __name__ == "__main__":
    main()
