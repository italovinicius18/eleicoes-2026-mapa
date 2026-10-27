"""Baixa as bases de apoio: malhas, de-para de municípios, locais de votação e candidatos.

Saídas versionadas (dados/geo/):
  mun_<UF>.geojson      - municípios de cada UF (IBGE, qualidade intermediária)
  br_municipios.geojson - municípios do Brasil (IBGE, qualidade mínima) para o mapa nacional
  br_ufs.geojson        - UFs do Brasil (IBGE, qualidade mínima)
  ra.geojson            - Regiões Administrativas do DF (IDE-DF/SEDUH)
  municipios_tse.csv    - código TSE -> código IBGE e nome (config da divulgação do TSE)
Saídas não versionadas (dados_brutos/):
  locais/eleitorado_local_votacao_2026_<UF>.csv - locais de votação e eleitorado (TSE)
  candidatos/consulta_cand_2026_BRASIL.csv      - candidatos (github.com/leofn/tse-candidatos-2026)
"""
import csv
import gzip
import json
import urllib.request
import zipfile
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
GEO = RAIZ / "dados" / "geo"
BRUTO = RAIZ / "dados_brutos"
UFS = ("AC AL AM AP BA CE DF ES GO MA MG MS MT PA PB PE PI PR RJ RN RO RR RS SC SE SP TO").split()
IBGE = "https://servicodados.ibge.gov.br/api/v3/malhas"
FORMATO = "formato=application/vnd.geo+json"
CANDIDATOS = ("https://raw.githubusercontent.com/leofn/tse-candidatos-2026/main/dados/"
              "consulta_cand_2026_BRASIL.csv")


def baixar(url, destino):
    if not destino.exists():
        destino.parent.mkdir(parents=True, exist_ok=True)
        tmp = destino.with_suffix(destino.suffix + ".part")
        urllib.request.urlretrieve(url, tmp)
        dados = tmp.read_bytes()
        if dados[:2] == b"\x1f\x8b":  # a API do IBGE responde gzip
            tmp.write_bytes(gzip.decompress(dados))
        tmp.rename(destino)
    return destino


def main():
    for uf in UFS:
        baixar(f"{IBGE}/estados/{uf}?{FORMATO}&qualidade=intermediaria&intrarregiao=municipio",
               GEO / f"mun_{uf}.geojson")
    baixar(f"{IBGE}/paises/BR?{FORMATO}&qualidade=minima&intrarregiao=municipio",
           GEO / "br_municipios.geojson")
    baixar(f"{IBGE}/paises/BR?{FORMATO}&qualidade=minima&intrarregiao=UF", GEO / "br_ufs.geojson")
    baixar("https://www.geoservicos.ide.df.gov.br/arcgis/rest/services/Publico/LIMITES/MapServer/1/"
           "query?where=1%3D1&outFields=*&outSR=4326&f=geojson&geometryPrecision=5",
           GEO / "ra.geojson")

    cm = json.loads(baixar("https://resultados.tse.jus.br/oficial/ele2026/6257/config/"
                           "mun-e006257-cm.json", BRUTO / "geo" / "mun-e006257-cm.json").read_text())
    with open(GEO / "municipios_tse.csv", "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["uf", "municipio", "ibge", "nome", "capital"])
        for a in cm["abr"]:
            for m in a["mu"]:
                w.writerow([a["cd"].upper(), int(m["cd"]), m.get("cdi", ""), m["nm"], m.get("c") == "s"])

    locais = BRUTO / "locais"
    if not (locais / "eleitorado_local_votacao_2026_SP.csv").exists():
        z = baixar("https://cdn.tse.jus.br/estatistica/sead/odsele/eleitorado_locais_votacao/"
                   "eleitorado_local_votacao_2026.zip", locais / "locais.zip")
        with zipfile.ZipFile(z) as zf:
            for n in zf.namelist():
                if n.endswith(".csv") and "BRASIL" not in n:
                    zf.extract(n, locais)
        z.unlink()

    baixar(CANDIDATOS, BRUTO / "candidatos" / "consulta_cand_2026_BRASIL.csv")
    print("ok")


if __name__ == "__main__":
    main()
