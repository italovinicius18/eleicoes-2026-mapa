"""Gera os mapas interativos e os painéis da análise geográfica (Brasil + 27 UFs).

Saídas em docs/ (publicado na Cloudflare com `make publicar`):
  index.html, mapa.html            - visão nacional (Presidente por município) e tabela das UFs
  <UF>/index.html, <UF>/mapa.html  - painel e mapa de cada UF (municípios ou RAs no DF + locais)
O mapa usa scripts/modelo_mapa.html (Leaflet): uma geometria só, recolorida pelo seletor de disputa.
"""
import html
import shutil
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots
from shapely.geometry import mapping, shape

RAIZ = Path(__file__).resolve().parent.parent
RESUMO = RAIZ / "dados" / "resumo"
GEO = RAIZ / "dados" / "geo"
SAIDA = RAIZ / "docs"
MODELO = (Path(__file__).parent / "modelo_mapa.html").read_text(encoding="utf-8")
UFS = ("AC AL AM AP BA CE DF ES GO MA MG MS MT PA PB PE PI PR RJ RN RO RR RS SC SE SP TO").split()
NOME_UF = dict(AC="Acre", AL="Alagoas", AM="Amazonas", AP="Amapá", BA="Bahia", CE="Ceará",
               DF="Distrito Federal", ES="Espírito Santo", GO="Goiás", MA="Maranhão",
               MG="Minas Gerais", MS="Mato Grosso do Sul", MT="Mato Grosso", PA="Pará",
               PB="Paraíba", PE="Pernambuco", PI="Piauí", PR="Paraná", RJ="Rio de Janeiro",
               RN="Rio Grande do Norte", RO="Rondônia", RR="Roraima", RS="Rio Grande do Sul",
               SC="Santa Catarina", SE="Sergipe", SP="São Paulo", TO="Tocantins")

# Paletas. Presidente: azul = Flavio (22), vermelho = Lula (13).
# Governador: verde-água = eleito/1º colocado, laranja = 2º colocado (sem conotação partidária).
ESCALAS = {  # mesma régua do modelo_mapa.html
    "pres": {"a": ["#cde2fb", "#86b6ef", "#2a78d6", "#1c5cab", "#0d366b"],
             "b": ["#7a1c1c", "#b8302f", "#e34948", "#f19e9a", "#fbd0cd"]},
    "gov": {"a": ["#d3f2e6", "#8edcbf", "#1baf7a", "#13805a", "#0b5139"],
            "b": ["#7a2c0c", "#b8461c", "#eb6834", "#f4a37f", "#fbd7c6"]},
}
VIOLETAS = ["#ece9fb", "#d5cff6", "#b8aeee", "#9a8be4", "#7c6bdc", "#6250d6", "#4a3aa7", "#2f2470"]
COR = {"pres": ("#2a78d6", "#e34948"), "gov": ("#1baf7a", "#eb6834")}
EXTRAS = ["#eda100", "#e87ba4", "#6250d6", "#008300", "#8a5a00", "#5598e7"]
OUTROS = "#b5b4ae"
TINTA, TINTA2, FUNDO = "#1d1f22", "#5d626a", "#f4f5f2"
ROTULO = {"P1": "Presidente, 1º turno", "P2": "Presidente, 2º turno",
          "G1": "Governador, 1º turno", "G2": "Governador, 2º turno"}
CARGO_COD = {"Presidente": "P", "Governador": "G"}
LAYOUT = dict(template="plotly_white", font=dict(family="Archivo, system-ui, sans-serif", color=TINTA, size=13),
              paper_bgcolor=FUNDO, plot_bgcolor=FUNDO, margin=dict(l=10, r=10, t=70, b=50),
              hoverlabel=dict(bgcolor="white", font_size=13))


def fmt(x, casas=1):
    if x is None or (isinstance(x, float) and np.isnan(x)):
        return "–"
    return f"{x:,.{casas}f}".replace(",", "X").replace(".", ",").replace("X", ".")


def titulo(n):
    """'SÃO JOSÉ DOS CAMPOS' -> 'São José dos Campos'; mantém siglas curtas do DF."""
    if n in ("SIA", "SCIA"):
        return n
    minusc = {"e", "de", "do", "da", "dos", "das"}
    return " ".join(w.lower() if i and w.lower() in minusc else w
                    for i, w in enumerate(n.title().replace(" Ii", " II").split()))


# --- dados --------------------------------------------------------------------
CAND = pd.read_csv(RESUMO / "candidatos.csv")
DISP = pd.read_csv(RESUMO / "disputas.csv")
MUN = pd.read_csv(GEO / "municipios_tse.csv")


def polos_numeros(uf, cargo):
    if uf == "BR":
        return 22, 13
    row = DISP[(DISP.uf == uf) & (DISP.cargo == cargo)].iloc[0]
    return int(row.a), int(row.b)


def nome_cand(uf, cargo, numero):
    c = CAND[(CAND.uf == ("BR" if cargo == "Presidente" else uf)) & (CAND.cargo == cargo)
             & (CAND.numero == numero)]
    if not len(c):
        return str(numero)
    nome = c.nome.iloc[0]
    nome = nome.upper() if len(nome) <= 3 else titulo(nome)  # siglas como "JHC"
    return f"{nome} ({c.partido.iloc[0]})"


def curto(nome):
    return nome.split(" (")[0]


def codigo(cargo, turno):
    return f"{CARGO_COD[cargo]}{turno}"


def cargo_de(d):
    return "Presidente" if d[0] == "P" else "Governador"


def esc_de(cargo):
    return "pres" if cargo == "Presidente" else "gov"


def vetor(r):
    bn = 100 * r.brancos_nulos / r.comparecimento if r.comparecimento else None
    vals = [r.pct_a, r.pct_b, r.margem, int(r.vencedor), r.pct_vencedor, int(r.comparecimento),
            getattr(r, "abstencao", None), bn]
    return [None if v is None or (isinstance(v, float) and np.isnan(v))
            else (round(float(v), 1) if isinstance(v, float) else v) for v in vals]


def valores(df, chave):
    out = {}
    for r in df.itertuples():
        out.setdefault(str(getattr(r, chave)), {})[codigo(r.cargo, r.turno)] = vetor(r)
    return out


def disputas_de(df):
    return sorted({codigo(c, t) for c, t in zip(df.cargo, df.turno)},
                  key=lambda d: ("PG".index(d[0]), d[1]))


def polos_de(uf, df):
    polos, nomes = {}, {}
    cols = [c for c in df.columns if c.startswith("pct_") and c[4:].isdigit()]
    for d in disputas_de(df):
        cargo = cargo_de(d)
        polos[d] = [nome_cand(uf, cargo, n) for n in polos_numeros(uf, cargo)]
        sub = df[(df.cargo == cargo) & (df.turno == int(d[1]))]
        nomes[d] = {int(c[4:]): nome_cand(uf, cargo, int(c[4:])) for c in cols if sub[c].notna().any()}
    return polos, nomes


def cores_vencedor(uf, cargo, df):
    """Cor fixa por candidato: polos nas cores da disputa, demais por ordem de votos no 1º turno."""
    a, b = polos_numeros(uf, cargo)
    cores = {a: COR[esc_de(cargo)][0], b: COR[esc_de(cargo)][1]}
    sub = df[(df.cargo == cargo) & (df.turno == 1)]
    cols = [c for c in sub.columns if c.startswith("pct_") and c[4:].isdigit()]
    peso = sub[cols].fillna(0).mul(sub.validos, axis=0).sum().sort_values(ascending=False)
    extras = iter(EXTRAS)
    for c in peso.index:
        n = int(c[4:])
        if n not in cores and peso[c] > 0:
            cores[n] = next(extras, OUTROS)
    return cores


def camadas(uf, un, outros=()):
    disp = disputas_de(un)
    out = [{"id": d, "rotulo": ROTULO[d], "tipo": "margem", "d": d, "esc": esc_de(cargo_de(d))}
           for d in disp]
    for c in "PG":
        if f"{c}1" in disp and f"{c}2" in disp:
            out.append({"id": f"V{c}", "rotulo": f"Virada entre turnos, {cargo_de(c)}", "tipo": "virada",
                        "d1": f"{c}1", "d2": f"{c}2", "esc": esc_de(cargo_de(c))})
    for c in "PG":
        if f"{c}1" in disp:
            cargo = cargo_de(c)
            cores = cores_vencedor(uf, cargo, un)
            sub = pd.concat([un, *outros])
            ganhou = set(sub[(sub.cargo == cargo) & (sub.turno == 1)].vencedor.astype(int))
            out.append({"id": f"W{c}", "rotulo": f"Mais votado no 1º turno, {cargo}", "tipo": "vencedor",
                        "d": f"{c}1", "cores": {str(k): v for k, v in cores.items()},
                        "legenda": {str(n): nome_cand(uf, cargo, n) for n in cores if n in ganhou}})
    if "abstencao" in un and un.abstencao.notna().any():
        t = int(un.turno.max())
        out.append({"id": "AB", "rotulo": f"Abstenção no {t}º turno", "tipo": "abstencao", "d": f"P{t}"})
    return out


def geo_simplificado(caminho, id_prop, nome_de, tolerancia):
    g = json.loads(Path(caminho).read_text(encoding="utf-8"))
    feats = []
    for f in g["features"]:
        geom = shape(f["geometry"]).simplify(tolerancia, preserve_topology=True)
        gid = str(f["properties"][id_prop])
        geo = json.loads(json.dumps(mapping(geom)), parse_float=lambda x: round(float(x), 4))
        feats.append({"type": "Feature", "geometry": geo, "properties": {"id": gid, "nome": nome_de(gid)}})
    return {"type": "FeatureCollection", "features": feats}


def escrever_mapa(destino, titulo_pag, dados):
    texto = json.dumps(dados, ensure_ascii=False, separators=(",", ":"))
    destino.write_text(MODELO.replace("__TITULO__", html.escape(titulo_pag)).replace("__DADOS__", texto),
                       encoding="utf-8")


def mapa_uf(uf, un, lc):
    nomes_mun = MUN[MUN.uf == uf].dropna(subset=["ibge"])
    nomes_mun = nomes_mun.nome.set_axis(nomes_mun.ibge.astype(int).astype(str))
    if uf == "DF":
        geo = geo_simplificado(GEO / "ra.geojson", "ra_nome", titulo, 0.0005)
    else:
        geo = geo_simplificado(GEO / f"mun_{uf}.geojson", "codarea",
                               lambda c: titulo(nomes_mun.get(c, c)), 0.002)
    lc = lc.assign(id=lc.municipio.astype(str) + "-" + lc.zona.astype(str) + "-" + lc.local.astype(str))
    ok = lc.lat.between(-35, 6) & lc.lon.between(-75, -28)
    pontos = [[round(r.lat, 5), round(r.lon, 5), r.id, titulo(str(r.nome_local)),
               f"{titulo(str(r.nome_unidade))}, zona {r.zona}"]
              for r in lc[ok].drop_duplicates("id").itertuples()]
    polos, nomes = polos_de(uf, un)
    abst = un[un.turno == un.turno.max()].abstencao.dropna()
    dados = {
        "geo": geo, "pontos": pontos,
        "nome_unidade": "Regiões Administrativas" if uf == "DF" else "Municípios",
        "valores": {"unidade": valores(un, "unidade"), "local": valores(lc, "id")},
        "camadas": camadas(uf, un, [lc]), "inicial": "P2" if "P2" in disputas_de(un) else "P1",
        "polos": polos, "nomes": nomes, "raio": max(1.0, float(np.sqrt(lc.comparecimento.max())) / 14),
        "escalas": {**ESCALAS, "abst": {"cores": VIOLETAS, "min": float(abst.quantile(.05)),
                                        "max": float(abst.quantile(.95))}},
    }
    escrever_mapa(SAIDA / uf / "mapa.html", f"{NOME_UF[uf]}: eleições 2026", dados)


def mapa_brasil(br, completo):
    m = MUN.dropna(subset=["ibge"])
    nomes = (m.nome.map(titulo) + " (" + m.uf + ")").set_axis(m.ibge.astype(int).astype(str))
    geo = geo_simplificado(GEO / "br_municipios.geojson", "codarea", lambda c: nomes.get(c, c), 0.01)
    polos, nm = polos_de("BR", br)
    dados = {"geo": geo, "pontos": [], "nome_unidade": "Municípios",
             "valores": {"unidade": valores(br, "ibge"), "local": {}},
             "camadas": camadas("BR", br), "inicial": "P2" if completo else "P1", "borda": 0.25,
             "polos": polos, "nomes": nm, "raio": 1,
             "escalas": {**ESCALAS, "abst": {"cores": VIOLETAS, "min": 0, "max": 1}}}
    escrever_mapa(SAIDA / "mapa.html", "Brasil: Presidente 2026", dados)


# --- gráficos -----------------------------------------------------------------
def maiores(un, n=30):
    return un.groupby("unidade").aptos.max().sort_values(ascending=False).index[:n]


def fig_margem(uf, un, rotulo_un):
    cargos = [c for c in ("Presidente", "Governador") if (un.cargo == c).any()]
    ult = {c: int(un[un.cargo == c].turno.max()) for c in cargos}
    sel = maiores(un, 40 if uf == "DF" else 30)
    fig = make_subplots(rows=1, cols=len(cargos), subplot_titles=[ROTULO[codigo(c, ult[c])] for c in cargos],
                        horizontal_spacing=0.22)
    for col, cargo in enumerate(cargos, 1):
        d = un[(un.cargo == cargo) & (un.turno == ult[cargo]) & un.unidade.isin(sel)].sort_values("margem")
        a, b = (nome_cand(uf, cargo, n) for n in polos_numeros(uf, cargo))
        ca, cb = COR[esc_de(cargo)]
        fig.add_bar(y=d.nome.map(titulo), x=d.margem, orientation="h", showlegend=False,
                    marker=dict(color=[ca if v > 0 else cb for v in d.margem], line_width=0),
                    customdata=d[["pct_a", "pct_b", "comparecimento"]],
                    hovertemplate=f"<b>%{{y}}</b><br>{a}: %{{customdata[0]:.1f}}%<br>"
                                  f"{b}: %{{customdata[1]:.1f}}%<br>Margem: %{{x:+.1f}} p.p."
                                  f"<br>Comparecimento: %{{customdata[2]:,.0f}}<extra></extra>",
                    row=1, col=col)
        fig.update_xaxes(title_text=f"← {curto(b)}   margem (p.p.)   {curto(a)} →", zeroline=True,
                         zerolinecolor=TINTA2, row=1, col=col)
    fig.update_layout(**LAYOUT, height=max(500, 24 * len(sel) + 160), bargap=0.25,
                      title=f"Margem entre os dois principais candidatos, por {rotulo_un}")
    return fig


def fig_virada(uf, un, rotulo_un):
    cargos = [c for c in ("Presidente", "Governador") if un[un.cargo == c].turno.nunique() == 2]
    if not cargos:
        return None
    sel = maiores(un, 40 if uf == "DF" else 30)
    fig = make_subplots(rows=1, cols=len(cargos), subplot_titles=cargos, horizontal_spacing=0.22)
    for col, cargo in enumerate(cargos, 1):
        d = un[(un.cargo == cargo) & un.unidade.isin(sel)]
        p = d.pivot(index="nome", columns="turno", values="margem").sort_values(2)
        rot = p.index.map(titulo)
        for y, x1, x2 in zip(rot, p[1], p[2]):
            fig.add_scatter(x=[x1, x2], y=[y, y], mode="lines", line=dict(color="#c3c2b7", width=2),
                            hoverinfo="skip", showlegend=False, row=1, col=col)
        for t, cor_f, cor_l in ((1, FUNDO, TINTA2), (2, TINTA2, FUNDO)):
            fig.add_scatter(x=p[t], y=rot, mode="markers", name=f"{t}º turno",
                            marker=dict(size=9, color=cor_f, line=dict(color=cor_l, width=2)),
                            showlegend=(col == 1), legendgroup=str(t),
                            hovertemplate=f"<b>%{{y}}</b><br>{t}º turno: %{{x:+.1f}} p.p.<extra></extra>",
                            row=1, col=col)
        a, b = (curto(nome_cand(uf, cargo, n)) for n in polos_numeros(uf, cargo))
        fig.update_xaxes(title_text=f"← {b}   margem   {a} →", zeroline=True, zerolinecolor=TINTA2,
                         row=1, col=col)
    fig.update_layout(**{**LAYOUT, "margin": dict(l=10, r=10, t=100, b=50)},
                      height=max(500, 24 * len(sel) + 180),
                      title=f"Como a margem andou do 1º para o 2º turno, por {rotulo_un}",
                      legend=dict(orientation="h", y=1.08, x=0, xanchor="left"))
    return fig


def fig_cruzamento(uf, lc):
    """% de Flavio x % do governador eleito, por local, no turno mais recente com os dois cargos."""
    t = max(t for t in (1, 2) if {"Presidente", "Governador"} <= set(lc[lc.turno == t].cargo))
    chave = ["municipio", "zona", "local"]
    p = lc[(lc.cargo == "Presidente") & (lc.turno == t)].set_index(chave)
    g = lc[(lc.cargo == "Governador") & (lc.turno == t)].set_index(chave)
    d = p[["pct_a", "comparecimento", "nome_local", "nome_unidade"]].join(
        g[["pct_a"]].rename(columns={"pct_a": "pct_gov"})).dropna(subset=["pct_a", "pct_gov"])
    gov = curto(nome_cand(uf, "Governador", polos_numeros(uf, "Governador")[0]))
    corr = d.pct_a.corr(d.pct_gov)
    fig = go.Figure()
    fig.add_scatter(x=[0, 100], y=[0, 100], mode="lines", hoverinfo="skip", showlegend=False,
                    line=dict(color="#c3c2b7", dash="dot", width=1.5))
    fig.add_scatter(x=d.pct_a, y=d.pct_gov, mode="markers", showlegend=False,
                    marker=dict(size=np.sqrt(d.comparecimento) / 6 + 3, color=COR["gov"][0], opacity=0.5,
                                line=dict(color=FUNDO, width=0.5)),
                    customdata=np.stack([d.nome_local.map(lambda s: titulo(str(s))),
                                         d.nome_unidade.map(lambda s: titulo(str(s))), d.comparecimento], 1),
                    hovertemplate="<b>%{customdata[0]}</b><br>%{customdata[1]}<br>Flavio: %{x:.1f}%<br>"
                                  f"{gov}: %{{y:.1f}}%<br>Comparecimento: %{{customdata[2]:,.0f}}<extra></extra>")
    fig.update_layout(**LAYOUT, height=560,
                      title=f"Flavio (Presidente) × {gov} (Governador) no {t}º turno, por local de votação"
                            f" (correlação {fmt(corr, 2)})",
                      xaxis=dict(title="% Flavio Bolsonaro (válidos)", range=[0, 100]),
                      yaxis=dict(title=f"% {gov} (válidos)", range=[0, 100]))
    return fig, corr, t, gov


def fig_primeiro_turno(uf, un, cargo, rotulo_un, top=5):
    d = un[(un.cargo == cargo) & (un.turno == 1) & un.unidade.isin(maiores(un, 40 if uf == "DF" else 30))]
    cols = [c for c in d.columns if c.startswith("pct_") and c[4:].isdigit()]
    principais = d[cols].fillna(0).mul(d.validos, axis=0).sum().sort_values(ascending=False).index[:top]
    cores = cores_vencedor(uf, cargo, un)
    d = d.sort_values(f"pct_{polos_numeros(uf, cargo)[0]}")
    rot = d.nome.map(titulo)
    fig = go.Figure()
    for c in principais:
        n = int(c[4:])
        nome = nome_cand(uf, cargo, n)
        fig.add_bar(y=rot, x=d[c].fillna(0), orientation="h", name=nome,
                    marker=dict(color=cores.get(n, OUTROS), line=dict(color=FUNDO, width=1)),
                    hovertemplate=f"<b>%{{y}}</b><br>{nome}: %{{x:.1f}}%<extra></extra>")
    fig.add_bar(y=rot, x=100 - d[list(principais)].fillna(0).sum(axis=1), orientation="h", name="Outros",
                marker=dict(color="#dcdbd6", line=dict(color=FUNDO, width=1)),
                hovertemplate="<b>%{y}</b><br>Outros: %{x:.1f}%<extra></extra>")
    fig.update_layout(**{**LAYOUT, "margin": dict(l=10, r=10, t=110, b=50)}, barmode="stack",
                      height=max(500, 24 * len(d) + 200),
                      title=f"{cargo}, 1º turno: votos válidos por {rotulo_un}",
                      xaxis=dict(title="% dos votos válidos", range=[0, 100]),
                      legend=dict(orientation="h", y=1.1, x=0))
    return fig


# --- páginas ------------------------------------------------------------------
# Identidade: o "chassi" é cinza como a urna eletrônica; cor só para votos.
# A navegação entre UFs é um cartograma de teclas (posição aproximada no mapa).
FONTE_URL = "https://fonts.googleapis.com/css2?family=Archivo:wdth,wght@62..125,300..900&display=swap"
GRADE = {  # UF -> (linha, coluna) do cartograma de teclas
    "RR": (1, 2), "AP": (1, 4),
    "AM": (2, 2), "PA": (2, 3), "MA": (2, 4), "CE": (2, 5), "RN": (2, 6),
    "AC": (3, 1), "RO": (3, 2), "TO": (3, 3), "PI": (3, 4), "PE": (3, 5), "PB": (3, 6),
    "MT": (4, 2), "GO": (4, 3), "BA": (4, 4), "SE": (4, 5), "AL": (4, 6),
    "MS": (5, 2), "DF": (5, 3), "MG": (5, 4), "ES": (5, 5),
    "PR": (6, 2), "SP": (6, 3), "RJ": (6, 4),
    "SC": (7, 2),
    "RS": (8, 2),
}
CORTES = [-40, -25, -15, -7, -2, 2, 7, 15, 25, 40]
NEUTRO = "#e4e5e1"

CSS = f"""
:root{{--papel:{FUNDO};--tinta:{TINTA};--tinta2:{TINTA2};--linha:#d9dcd6;--tecla:#2a2d31;
  --a:{COR['pres'][0]};--b:{COR['pres'][1]}}}
*{{box-sizing:border-box}}
body{{margin:0;background:var(--papel);color:var(--tinta);
  font:400 16px/1.55 Archivo,system-ui,sans-serif;font-variant-numeric:tabular-nums}}
a{{color:inherit;text-decoration-color:var(--linha);text-underline-offset:3px}}
a:hover{{text-decoration-color:currentColor}}
:focus-visible{{outline:3px solid var(--tinta);outline-offset:2px}}
main{{max-width:1240px;margin:0 auto;padding:28px 28px 64px}}
.topo{{display:flex;justify-content:space-between;align-items:flex-start;gap:24px;flex-wrap:wrap}}
.marca{{font-size:14px;color:var(--tinta2);text-decoration:none}}
h1{{font-stretch:62%;font-weight:800;font-size:clamp(56px,10vw,128px);line-height:.88;
  letter-spacing:-.01em;margin:18px 0 14px}}
h2{{font-stretch:85%;font-weight:700;font-size:26px;line-height:1.15;margin:56px 0 6px}}
.lead{{color:var(--tinta2);max-width:68ch;margin:0 0 22px}}
.pend{{color:var(--tinta2)}}
/* placar: barra dividida dos votos válidos */
.placares{{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,460px),1fr));gap:28px 48px;margin:28px 0 8px}}
.placar h3{{font-size:15px;font-weight:600;margin:0 0 8px}}
.nomes{{display:flex;justify-content:space-between;gap:16px;font-size:15px}}
.nomes span:last-child{{text-align:right}} .nomes small{{color:var(--tinta2);font-size:13px}}
.pcts{{display:flex;justify-content:space-between;font-stretch:70%;font-weight:750;font-size:44px;line-height:1}}
.barra{{position:relative;display:flex;gap:2px;height:18px;margin:6px 0 8px}}
.barra span{{display:block;height:100%}} .barra span:first-child{{border-radius:3px 0 0 3px}}
.barra span:last-child{{border-radius:0 3px 3px 0}} .barra .outros{{background:#c9ccc6}}
.barra::after{{content:"";position:absolute;left:50%;top:-5px;bottom:-5px;border-left:2px solid var(--tinta)}}
.barra.vazia{{background:repeating-linear-gradient(135deg,#e2e4df 0 6px,var(--papel) 6px 12px);border-radius:3px}}
.placar .nota{{font-size:13px;color:var(--tinta2);margin:0}}
/* teclado-cartograma */
.teclado{{display:grid;grid-template-columns:repeat(6,var(--k));grid-auto-rows:var(--k);gap:6px;--k:58px}}
.tecla{{display:flex;flex-direction:column;justify-content:center;align-items:center;text-decoration:none;
  border-radius:7px;font-weight:700;font-size:17px;line-height:1.1;background:var(--c1);color:var(--t1);
  box-shadow:inset 0 -4px 0 rgba(0,0,0,.22);transition:transform .08s,box-shadow .08s}}
.tecla small{{font-weight:500;font-size:11px}}
.tecla:hover{{transform:translateY(2px);box-shadow:inset 0 -2px 0 rgba(0,0,0,.22)}}
[data-turno="2"] .tecla{{background:var(--c2);color:var(--t2)}}
[data-turno="2"] .tecla .m1,[data-turno="1"] .tecla .m2{{display:none}}
.tecla.atual{{outline:3px solid var(--tinta);outline-offset:2px}}
.teclado.mini{{--k:30px;gap:3px}} .mini .tecla{{font-size:11px;border-radius:4px;box-shadow:inset 0 -2px 0 rgba(0,0,0,.22)}}
.mini .tecla small{{display:none}}
.chave{{display:inline-flex;border:1px solid var(--linha);border-radius:7px;padding:3px;gap:3px;margin:0 0 14px}}
.chave button{{font:inherit;font-size:14px;border:0;border-radius:5px;padding:5px 12px;background:none;
  color:var(--tinta2);cursor:pointer}}
.chave button[aria-pressed=true]{{background:var(--tecla);color:#fff}}
.escala{{display:flex;gap:2px;margin:12px 0 4px;max-width:{6 * 58 + 5 * 6}px}} .escala span{{flex:1;height:10px}}
.escala-rot{{display:flex;justify-content:space-between;font-size:12px;color:var(--tinta2);max-width:{6 * 58 + 5 * 6}px}}
.cabeca-uf{{display:grid;grid-template-columns:1fr auto;gap:32px;align-items:start}}
.cabeca-uf .lead{{margin-bottom:0}}
.painel-br{{display:grid;grid-template-columns:minmax(0,1fr) 380px;gap:36px;align-items:start}}
@media (max-width:900px){{.painel-br,.cabeca-uf{{grid-template-columns:1fr}} .teclado{{--k:min(48px,13vw)}} .teclado.mini{{--k:24px}}}}
/* conteúdo */
iframe{{display:block;width:100%;height:700px;border:1px solid var(--linha);border-radius:4px;background:#fff}}
ul.destaques{{max-width:72ch;padding-left:1.1em}} ul.destaques li{{margin-bottom:10px}}
.graf{{margin:28px 0;border-top:1px solid var(--linha);padding-top:8px}}
.rolagem{{overflow-x:auto}} table{{border-collapse:collapse;width:100%;min-width:640px;font-size:14px}}
th,td{{padding:6px 10px;border-bottom:1px solid var(--linha);text-align:right;vertical-align:top}}
th{{font-weight:600;font-size:13px;color:var(--tinta2)}}
th:first-child,td:first-child{{text-align:left}} thead th{{position:sticky;top:0;background:var(--papel)}}
tbody tr:hover{{background:#eaece7}}
details summary{{cursor:pointer;color:var(--tinta2);margin-bottom:10px}}
.fonte{{color:var(--tinta2);font-size:13px;max-width:90ch;margin-top:64px;border-top:1px solid var(--linha);padding-top:16px}}
@media (prefers-reduced-motion:reduce){{.tecla{{transition:none}} .tecla:hover{{transform:none}}}}
"""
FONTES = """<p class="fonte">Fontes: votação por seção do 1º turno (dados abertos do TSE); boletins de urna do
2º turno (resultados.tse.jus.br, arquivo-urna, pleito 3221); eleitorado por local de votação e de-para de
municípios (TSE); candidatos e situação (github.com/leofn/tse-candidatos-2026); malhas municipais (IBGE);
Regiões Administrativas do DF (IDE-DF/SEDUH). Margem é a diferença, em pontos percentuais (p.p.) dos votos
válidos, entre os dois principais candidatos de cada disputa: os finalistas do 2º turno ou, sem 2º turno, os
dois mais votados. Cada local de votação é atribuído ao município do cadastro do TSE; no DF, à RA que contém
a sua coordenada.</p>"""
TECLADO_JS = """<script>
document.querySelectorAll('.chave').forEach(ch => ch.addEventListener('click', e => {
  const b = e.target.closest('button'); if (!b) return;
  ch.querySelectorAll('button').forEach(x => x.setAttribute('aria-pressed', x === b));
  document.getElementById(ch.dataset.alvo).dataset.turno = b.dataset.turno;
}));
</script>"""


def pagina(titulo_pag, corpo):
    return f"""<!doctype html><html lang="pt-BR"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>{html.escape(titulo_pag)}</title>
<link rel="icon" href="/favicon.svg" type="image/svg+xml">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin><link rel="stylesheet" href="{FONTE_URL}">
<script src="https://cdn.plot.ly/plotly-2.35.2.min.js"></script><style>{CSS}</style></head>
<body><main>{corpo}{FONTES}</main>{TECLADO_JS}</body></html>"""


def cor_margem(m, esc="pres"):
    cores = ESCALAS[esc]["b"] + [NEUTRO] + ESCALAS[esc]["a"]
    return cores[sum(m > c for c in CORTES)]


def texto_sobre(cor):
    r, g, b = (int(cor[i:i + 2], 16) / 255 for i in (1, 3, 5))
    lum = 0.2126 * r ** 2.2 + 0.7152 * g ** 2.2 + 0.0722 * b ** 2.2
    return TINTA if lum > 0.3 else "#ffffff"


def rot_margem(m):
    casas = 1 if abs(m) < 9.95 else 0
    return f"{'F' if m > 0 else 'L'} +{abs(m):.{casas}f}".replace(".", ",")


def teclado(uf_tab, id_, atual=None, mini=False, prefixo=""):
    pres = uf_tab[uf_tab.cargo == "Presidente"].set_index(["uf", "turno"]).margem
    teclas = []
    for uf, (lin, col) in GRADE.items():
        estilo, rot = [f"grid-area:{lin}/{col}"], []
        for t in (1, 2):
            if (uf, t) in pres.index:
                m = pres[(uf, t)]
                c = cor_margem(m)
                estilo += [f"--c{t}:{c}", f"--t{t}:{texto_sobre(c)}"]
                rot.append(f'<small class="m{t}">{rot_margem(m)}</small>')
                dica = f"{NOME_UF[uf]}: Flavio − Lula = {m:+.1f} p.p. no {t}º turno".replace(".", ",")
            else:
                estilo += [f"--c{t}:#d3d5d0", f"--t{t}:{TINTA2}"]
                rot.append(f'<small class="m{t}">…</small>')
        cls = "tecla atual" if uf == atual else "tecla"
        teclas.append(f'<a class="{cls}" href="{prefixo}{uf}/index.html" style="{";".join(estilo)}" '
                      f'title="{NOME_UF[uf]}" aria-label="{NOME_UF[uf]}">{uf}{"".join(rot)}</a>')
    tem_2t = (uf_tab.turno == 2).any()
    completo = uf_tab[(uf_tab.cargo == "Presidente") & (uf_tab.turno == 2)].uf.nunique() == len(UFS)
    turno = 2 if completo else 1
    chave = ""
    if tem_2t:
        chave = (f'<div class="chave" data-alvo="{id_}" role="group" aria-label="Turno">'
                 + "".join(f'<button type="button" data-turno="{t}" aria-pressed="{str(t == turno).lower()}">'
                           f"{t}º turno</button>" for t in (1, 2)) + "</div>")
    return chave, (f'<div class="teclado{" mini" if mini else ""}" id="{id_}" data-turno="{turno}">'
                   f'{"".join(teclas)}</div>')


def escala_html():
    cores = ESCALAS["pres"]["b"] + [NEUTRO] + ESCALAS["pres"]["a"]
    return (f'<div class="escala">{"".join(f"<span style=background:{c}></span>" for c in cores)}</div>'
            '<div class="escala-rot"><span>Lula +40 p.p.</span><span>empate</span>'
            '<span>Flavio +40 p.p.</span></div>')


def rotulo_cand(n):
    """'Lula (PT)' -> 'Lula <small>PT</small>'."""
    nome, _, partido = n.rpartition(" (")
    return f"{nome} <small>{partido.rstrip(')')}</small>" if nome else n


def placar(titulo_p, r, a, b, esc):
    cab = f'<h3>{titulo_p}</h3><div class="nomes"><span>{rotulo_cand(a)}</span><span>{rotulo_cand(b)}</span></div>'
    if r is None:
        return (f'<div class="placar">{cab}<div class="barra vazia"></div>'
                '<p class="nota">Aguardando os boletins de urna do TSE.</p></div>')
    ca, cb = COR[esc]
    outros = max(0.0, 100 - r.pct_a - r.pct_b)
    return f"""<div class="placar">{cab}
  <div class="barra" role="img" aria-label="{curto(a)} {fmt(r.pct_a)}%, {curto(b)} {fmt(r.pct_b)}%">
    <span style="width:{r.pct_a:.2f}%;background:{ca}"></span>{f'<span class="outros" style="width:{outros:.2f}%"></span>' if outros >= 0.05 else ''}<span style="width:{r.pct_b:.2f}%;background:{cb}"></span></div>
  <div class="pcts"><span>{fmt(r.pct_a)}%</span><span>{fmt(r.pct_b)}%</span></div>
  <p class="nota">{fmt(r.validos, 0)} votos válidos{f", outros candidatos {fmt(outros)}%" if outros >= 0.05 else ""}; brancos e nulos {fmt(100 * r.brancos_nulos / r.comparecimento)}% do comparecimento.</p></div>"""


def graficos_html(figs):
    cfg = {"displaylogo": False}
    return "".join(f'<section class="graf">{f.to_html(full_html=False, include_plotlyjs=False, config=cfg)}</section>'
                   for f in figs if f is not None)


def margem_total(d):
    """Margem a − b (p.p.) somando os votos de todas as unidades."""
    return float(((d.pct_a - d.pct_b) * d.validos).sum() / d.validos.sum())


def destaques_uf(uf, un, corr_info, rotulo_un):
    out = []
    minimo = 10_000 if uf == "DF" else 50_000
    grandes = un[un.aptos >= minimo]
    if grandes.unidade.nunique() < 5:
        grandes = un[un.unidade.isin(maiores(un, 10))]
    for cargo in ("Presidente", "Governador"):
        sub = un[un.cargo == cargo]
        t = int(sub.turno.max())
        d = sub[sub.turno == t]
        a, b = (curto(nome_cand(uf, cargo, n)) for n in polos_numeros(uf, cargo))
        ga = d[d.margem > 0]
        txt = (f"<li><b>{cargo}, {t}º turno:</b> {a} ficou à frente de {b} em {len(ga)} de {len(d)} "
               f"{rotulo_un}, que reúnem {fmt(100 * ga.aptos.sum() / d.aptos.sum(), 0)}% do eleitorado.")
        dg = grandes[(grandes.cargo == cargo) & (grandes.turno == t)]
        if len(dg):
            mx, mn = dg.loc[dg.margem.idxmax()], dg.loc[dg.margem.idxmin()]
            txt += f" Entre as maiores: vantagem máxima de {a} em {titulo(mx.nome)} ({fmt(mx.margem)} p.p.); "
            txt += (f"de {b} em {titulo(mn.nome)} ({fmt(-mn.margem)} p.p.)." if mn.margem < 0
                    else f"menor vantagem em {titulo(mn.nome)} ({fmt(mn.margem)} p.p.).")
        out.append(txt + "</li>")
        if sub.turno.nunique() == 2:
            p = sub.pivot(index="unidade", columns="turno", values="margem").dropna()
            ap = sub[sub.turno == 2].set_index("unidade").aptos
            dv = p[2] - p[1]
            pro_a = ap[dv[dv > 0].index].sum() / ap[dv.index].sum()
            n_a = int((dv > 0).sum())
            if n_a in (0, len(dv)):
                quem = a if n_a else b
                resumo = f"a favor de {quem} em todos os {len(dv)} {rotulo_un}"
            else:
                resumo = (f"a favor de {a} em {n_a} de {len(dv)} {rotulo_un} ({fmt(100 * pro_a, 0)}% do "
                          f"eleitorado) e a favor de {b} nos demais")
            out.append(f"<li><b>{cargo}, do 1º para o 2º turno:</b> a margem andou {resumo}; no total "
                       f"a margem foi de {fmt(sub[sub.turno == 1].pipe(margem_total))} p.p. para "
                       f"{fmt(sub[sub.turno == 2].pipe(margem_total))} p.p.</li>")
    corr, t, gov = corr_info
    out.append(f"<li><b>Voto casado:</b> por local de votação, no {t}º turno, a correlação entre o % de Flavio "
               f"e o % de {gov} foi de {fmt(corr, 2)}.</li>")
    return "".join(out)


def tabela_uf(uf, un, rotulo_un):
    disp = disputas_de(un)
    polos = polos_de(uf, un)[0]
    cab = "".join(f"<th>{ROTULO[d]}<br>{curto(polos[d][0])} / {curto(polos[d][1])}</th>" for d in disp)
    linhas = []
    for nome, g in un.groupby("nome"):
        cel = []
        for d in disp:
            s = g[(g.cargo == cargo_de(d)) & (g.turno == int(d[1]))]
            cel.append(f"<td>{fmt(s.pct_a.iloc[0])} / {fmt(s.pct_b.iloc[0])}</td>" if len(s) else "<td>–</td>")
        abst = g[g.turno == g.turno.max()].abstencao.iloc[0]
        linhas.append((g.aptos.max(), f"<tr><td>{titulo(nome)}</td><td>{fmt(g.aptos.max(), 0)}</td>"
                                      f"{''.join(cel)}<td>{fmt(abst)}</td></tr>"))
    n = un.unidade.nunique()
    corpo = "".join(l for _, l in sorted(linhas, key=lambda x: -x[0]))
    return (f"<details{' open' if n <= 40 else ''}><summary>% dos votos válidos ({n} {rotulo_un}, do maior "
            f"para o menor eleitorado)</summary><div class=rolagem><table><thead><tr><th>{rotulo_un.capitalize()}</th>"
            f"<th>Aptos</th>{cab}<th>Abstenção (último turno)</th></tr></thead><tbody>{corpo}</tbody>"
            f"</table></div></details>")


def cabeca_uf(uf_tab, uf, lead):
    """Título do estado à esquerda; teclado-cartograma em miniatura (navegação) à direita."""
    _, mini = teclado(uf_tab, "mini", atual=uf, mini=True, prefixo="../")
    return (f'<header class="cabeca-uf"><div><a class="marca" href="../index.html">Eleições 2026: mapa dos '
            f'votos, voltar ao Brasil</a><h1>{NOME_UF[uf]}</h1><p class="lead">{lead}</p></div>'
            f'<nav aria-label="Outros estados">{mini}</nav></header>')


def painel_uf(uf, un, lc, tot, uf_tab):
    rotulo_un = "Regiões Administrativas" if uf == "DF" else "municípios"
    gov = CAND[(CAND.uf == uf) & (CAND.cargo == "Governador")]
    tem_2t_gov = (gov.situacao == "2º TURNO").sum() == 2
    placares = []
    for cargo in ("Presidente", "Governador"):
        a, b = (nome_cand(uf, cargo, n) for n in polos_numeros(uf, cargo))
        for t in (1, 2):
            if cargo == "Governador" and t == 2 and not tem_2t_gov:
                continue
            r = tot[(tot.cargo == cargo) & (tot.turno == t)]
            placares.append(placar(ROTULO[codigo(cargo, t)], r.iloc[0] if len(r) else None, a, b, esc_de(cargo)))
    f_cruz, corr, t_cruz, gov_nome = fig_cruzamento(uf, lc)
    figs = [fig_margem(uf, un, rotulo_un), fig_virada(uf, un, rotulo_un), f_cruz,
            fig_primeiro_turno(uf, un, "Presidente", rotulo_un),
            fig_primeiro_turno(uf, un, "Governador", rotulo_un)]
    eleito = gov[gov.situacao == "ELEITO"]
    if len(eleito):
        nota_gov = (f"{titulo(eleito.nome.iloc[0])} ({eleito.partido.iloc[0]}) foi eleito governador "
                    "no 1º turno.")
    else:
        nota_gov = "O governo foi decidido no 2º turno."
    pend = "" if (un.turno == 2).any() else (
        " Os boletins de urna do 2º turno deste estado ainda estão sendo baixados do TSE.")
    n_un = un.unidade.nunique()
    sel_txt = f"todas as {n_un} RAs" if uf == "DF" else (
        f"os 30 maiores dos {n_un} municípios" if n_un > 30 else f"todos os {n_un} municípios")
    lead = (f"Votos para Presidente e Governador em 4 e 25 de outubro de 2026, por {rotulo_un} e por "
            f"local de votação. {nota_gov}{pend}")
    corpo = f"""{cabeca_uf(uf_tab, uf, lead)}
<div class="placares">{''.join(placares)}</div>
<h2>O que os números mostram</h2><ul class="destaques">{destaques_uf(uf, un, (corr, t_cruz, gov_nome), rotulo_un)}</ul>
<h2>Mapa</h2><p class="lead">Escolha a disputa e o recorte no canto do mapa: {rotulo_un} ou locais de
votação. Passe o mouse sobre uma área ou ponto para ver os números.</p>
<iframe src="mapa.html" title="Mapa de {NOME_UF[uf]}"></iframe>
<h2>Gráficos</h2><p class="lead">Os gráficos por área mostram {sel_txt}, ordenados por eleitorado.</p>
{graficos_html(figs)}
<h2>Tabela</h2>{tabela_uf(uf, un, rotulo_un)}"""
    (SAIDA / uf / "index.html").write_text(pagina(f"{NOME_UF[uf]}: eleições 2026", corpo), encoding="utf-8")


def painel_brasil(uf_tab, total):
    pres = uf_tab[uf_tab.cargo == "Presidente"]
    ufs_2t = sorted(set(pres[pres.turno == 2].uf))
    completo = len(ufs_2t) == len(UFS) and (total.turno == 2).any()
    a, b = nome_cand("BR", "Presidente", 22), nome_cand("BR", "Presidente", 13)
    placares = placar("Presidente, 1º turno (com o exterior)", total[total.turno == 1].iloc[0], a, b, "pres")
    placares += placar("Presidente, 2º turno (com o exterior)",
                       total[total.turno == 2].iloc[0] if completo else None, a, b, "pres")
    status = "" if completo else (
        f" O 2º turno já está disponível em {len(ufs_2t)} dos 27 estados; o total nacional aparece quando "
        "todos terminarem de baixar.")
    linhas = []
    for uf in UFS:
        p = pres[pres.uf == uf].set_index("turno")
        g = uf_tab[(uf_tab.uf == uf) & (uf_tab.cargo == "Governador")].set_index("turno")
        ga, gb = (nome_cand(uf, "Governador", n) for n in polos_numeros(uf, "Governador"))
        sit = CAND[(CAND.uf == uf) & (CAND.cargo == "Governador")]
        if (sit.situacao == "ELEITO").any():
            gtxt = f"{ga}, eleito no 1º turno com {fmt(g.loc[1].pct_a)}%"
        elif 2 in g.index:
            gtxt = f"{curto(ga)} {fmt(g.loc[2].pct_a)}% contra {curto(gb)} {fmt(g.loc[2].pct_b)}% no 2º turno"
        else:
            gtxt = f"{curto(ga)} contra {curto(gb)} no 2º turno <span class='pend'>(aguardando)</span>"
        p2 = (f"<td>{fmt(p.loc[2].pct_a)}</td><td>{fmt(p.loc[2].pct_b)}</td>" if 2 in p.index
              else "<td colspan=2 class='pend' style='text-align:center'>aguardando</td>")
        linhas.append(f"<tr><td><a href='{uf}/index.html'>{NOME_UF[uf]}</a></td>"
                      f"<td>{fmt(p.loc[1].pct_a)}</td><td>{fmt(p.loc[1].pct_b)}</td>{p2}"
                      f"<td style='text-align:left'>{gtxt}</td></tr>")
    p1 = pres[pres.turno == 1].sort_values("margem")
    fig = go.Figure()
    fig.add_bar(y=p1.uf.map(NOME_UF), x=p1.margem, orientation="h", name="1º turno",
                marker=dict(color=[COR["pres"][0] if v > 0 else COR["pres"][1] for v in p1.margem]),
                customdata=p1[["pct_a", "pct_b"]],
                hovertemplate="<b>%{y}</b><br>Flavio: %{customdata[0]:.1f}%<br>Lula: %{customdata[1]:.1f}%"
                              "<br>Margem: %{x:+.1f} p.p.<extra>1º turno</extra>")
    p2 = pres[pres.turno == 2].set_index("uf").reindex(p1.uf)
    if p2.margem.notna().any():
        fig.add_scatter(y=p1.uf.map(NOME_UF), x=p2.margem.values, mode="markers", name="2º turno",
                        marker=dict(size=10, color=TINTA, line=dict(color=FUNDO, width=2)),
                        hovertemplate="<b>%{y}</b><br>2º turno: %{x:+.1f} p.p.<extra></extra>")
    fig.update_layout(**LAYOUT, height=820,
                      title="Margem Flavio − Lula por estado: barras no 1º turno, pontos no 2º turno",
                      xaxis=dict(title="← Lula   margem (p.p.)   Flavio →", zeroline=True, zerolinecolor=TINTA2),
                      legend=dict(orientation="h", y=1.04, x=0))
    chave, tecl = teclado(uf_tab, "teclado-br")
    corpo = f"""<a class="marca" href="index.html">Eleições 2026: mapa dos votos</a>
<h1>Brasil</h1>
<p class="lead">Votos para Presidente em cada um dos 5.570 municípios, e um painel por estado com Presidente e
Governador nos dois turnos, por município e por local de votação.{status}</p>
<div class="placares">{placares}</div>
<h2>Onde cada candidato venceu</h2>
<div class="painel-br">
  <iframe src="mapa.html" title="Mapa do Brasil por município" style="height:720px"></iframe>
  <aside>
    <p class="lead" style="margin-bottom:12px">Cada tecla é um estado, na posição aproximada do mapa, com a cor
    da margem entre Flavio e Lula. Clique para abrir o painel do estado.</p>
    {chave}{tecl}{escala_html()}
  </aside>
</div>
<h2>Estados</h2><p class="lead">Percentuais dos votos válidos.</p>
<div class="rolagem"><table><thead><tr><th>Estado</th><th>Flavio 1º turno</th><th>Lula 1º turno</th><th>Flavio 2º turno</th>
<th>Lula 2º turno</th><th style="text-align:left">Governador</th></tr></thead><tbody>{''.join(linhas)}</tbody></table></div>
{graficos_html([fig])}"""
    (SAIDA / "index.html").write_text(pagina("Eleições 2026: mapa dos votos no Brasil", corpo), encoding="utf-8")


def main():
    ufs = sys.argv[1:] or UFS
    uf_tab = pd.read_csv(RESUMO / "uf.csv")
    for uf in ufs:
        (SAIDA / uf).mkdir(parents=True, exist_ok=True)
        un = pd.read_csv(RESUMO / f"{uf}_unidade.csv", dtype={"unidade": str})
        lc = pd.read_csv(RESUMO / f"{uf}_local.csv")
        mapa_uf(uf, un, lc)
        painel_uf(uf, un, lc, uf_tab[uf_tab.uf == uf], uf_tab)
        print(f"[{uf}] ok", flush=True)
    ufs_2t = set(uf_tab[(uf_tab.cargo == "Presidente") & (uf_tab.turno == 2)].uf)
    mapa_brasil(pd.read_csv(RESUMO / "municipios_br.csv"), completo=len(ufs_2t) == len(UFS))
    painel_brasil(uf_tab, pd.read_csv(RESUMO / "total.csv"))
    shutil.copy(Path(__file__).parent / "favicon.svg", SAIDA / "favicon.svg")
    print("ok ->", SAIDA)


if __name__ == "__main__":
    main()
