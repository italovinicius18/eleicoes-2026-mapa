"""Baixa os Boletins de Urna (bu.dat) do 2º turno 2026 e extrai os votos por seção.

Fonte: https://resultados.tse.jus.br/oficial/ele2026/arquivo-urna/3221/...
Uso:   python baixar_bu_2turno.py [UF ...]   (sem argumentos: todas as UFs + ZZ/exterior)
       python baixar_bu_2turno.py --cache UF  (só monta o CSV com os BUs já baixados, sem rede)
Saída: dados_brutos/2turno/<UF>.csv  (uf, municipio, zona, secao, local, cargo, aptos,
       comparecimento, numero, votos) — brancos = 95, nulos = 96.
O download é retomável: cada BU fica em dados_brutos/bu_2turno/<uf>/.
"""
import csv
import json
import sys
import threading
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

PLEITO = "3221"
BASE = f"https://resultados.tse.jus.br/oficial/ele2026/arquivo-urna/{PLEITO}"
RAIZ = Path(__file__).resolve().parent.parent
CACHE = RAIZ / "dados_brutos" / "bu_2turno"
SAIDA = RAIZ / "dados_brutos" / "2turno"
UFS = ("ac al am ap ba ce df es go ma mg ms mt pa pb pe pi pr rj rn ro rr rs sc se sp to zz").split()
CARGOS = {1: "Presidente", 3: "Governador"}
NUMERO_TIPO = {2: 95, 3: 96}  # tipo de voto branco/nulo -> número convencional do TSE
TRABALHADORES = 16


class Limitador:
    """Taxa adaptativa (AIMD): sobe devagar a cada sucesso, cai pela metade e pausa em 429."""

    def __init__(self, taxa=20.0, minima=2.0, maxima=80.0):
        self.taxa, self.minima, self.maxima = taxa, minima, maxima
        self.proximo = 0.0
        self.pausa = 30.0
        self.trava = threading.Lock()

    def aguardar(self):
        with self.trava:
            agora = time.monotonic()
            vez = max(agora, self.proximo)
            self.proximo = vez + 1 / self.taxa
        time.sleep(max(0.0, vez - time.monotonic()))

    def sucesso(self):
        with self.trava:
            self.taxa = min(self.maxima, self.taxa + 0.02)
            self.pausa = max(30.0, self.pausa * 0.99)

    def bloqueado(self):
        with self.trava:
            agora = time.monotonic()
            if self.proximo - agora > 5:  # outra thread já aplicou a pausa
                return
            self.taxa = max(self.minima, self.taxa / 2)
            self.proximo = agora + self.pausa
            print(f"  429: pausa {self.pausa:.0f}s, taxa -> {self.taxa:.1f} req/s", flush=True)
            self.pausa = min(600.0, self.pausa * 2)


LIMITE = Limitador(taxa=5.0)


def get(url, tentativas=30):
    for i in range(tentativas):
        LIMITE.aguardar()
        try:
            with urllib.request.urlopen(url, timeout=30) as r:
                dados = r.read()
            LIMITE.sucesso()
            return dados
        except urllib.error.HTTPError as e:
            if e.code == 404:
                raise
            if e.code == 429:
                LIMITE.bloqueado()
            elif i >= 5:
                raise
        except Exception:
            if i >= 5:
                raise
            time.sleep(2 ** i)
    raise RuntimeError(f"desisti: {url}")


# --- decodificador BER mínimo -------------------------------------------------
def ler_tlv(b, i):
    t = b[i]
    i += 1
    cls, cons, tag = t >> 6, (t >> 5) & 1, t & 31
    if tag == 31:
        tag = 0
        while True:
            x = b[i]
            i += 1
            tag = (tag << 7) | (x & 127)
            if not x & 128:
                break
    n = b[i]
    i += 1
    if n & 128:
        k = n & 127
        n = int.from_bytes(b[i:i + k], "big")
        i += k
    return cls, cons, tag, i, n


def arvore(b, ini=0, fim=None):
    """Lista de nós (cls, tag, filhos|bytes)."""
    fim = len(b) if fim is None else fim
    nos, i = [], ini
    while i < fim:
        cls, cons, tag, vi, n = ler_tlv(b, i)
        nos.append((cls, tag, arvore(b, vi, vi + n) if cons else b[vi:vi + n]))
        i = vi + n
    return nos


def inteiro(no):
    return int.from_bytes(no[2], "big", signed=True)


def filhos(no):
    return no[2] if isinstance(no[2], list) else []


def extrair(bu_bytes):
    envelope = arvore(bu_bytes)[0]
    conteudo = [n for n in filhos(envelope) if n[0] == 0 and n[1] == 4][-1][2]
    bu = filhos(arvore(conteudo)[0])
    ident = [n for n in bu if n[0] == 0 and n[1] == 16 and len(filhos(n)) == 3
             and filhos(n)[0][1] == 16][0]
    municipio, zona = (inteiro(x) for x in filhos(filhos(ident)[0]))
    local, secao = inteiro(filhos(ident)[1]), inteiro(filhos(ident)[2])

    linhas = []
    por_eleicao = [n for n in bu if n[0] == 0 and n[1] == 16 and filhos(n)
                   and all(f[1] == 16 and filhos(f) and filhos(f)[0][1] == 2 for f in filhos(n))][0]
    for el in filhos(por_eleicao):
        f = filhos(el)
        aptos = inteiro(f[1])
        resultados = [x for x in f if x[0] == 0 and x[1] == 16][0]
        for rv in filhos(resultados):
            comparecimento = inteiro(filhos(rv)[1])
            for tvc in filhos(filhos(rv)[2]):
                cod_cargo = inteiro(filhos(tvc)[0])
                if cod_cargo not in CARGOS:
                    continue
                for vv in filhos(filhos(tvc)[2]):
                    campos = {n[1]: n for n in filhos(vv) if n[0] == 2}
                    tipo = inteiro(campos[1])
                    numero = inteiro(filhos(campos[3])[1]) if 3 in campos else NUMERO_TIPO.get(tipo)
                    if numero is None:
                        continue
                    linhas.append((municipio, zona, secao, local, CARGOS[cod_cargo], aptos,
                                   comparecimento, numero, inteiro(campos[2])))
    return linhas


# --- download -----------------------------------------------------------------
def baixar_secao(uf, mun, zona, secao):
    destino = CACHE / uf / f"{mun}_{zona}_{secao}.dat"
    if destino.exists():
        return destino
    pasta = f"{BASE}/dados/{uf}/{mun}/{zona}/{secao}"
    try:
        aux = json.loads(get(f"{pasta}/p00{PLEITO}-{uf}-m{mun}-z{zona}-s{secao}-aux.json"))
    except urllib.error.HTTPError as e:
        if e.code == 404:  # seção sem boletim (agregada/sem votação)
            return None
        raise
    for h in aux["hashes"]:
        bu = [a["nm"] for a in h["arq"] if a["tp"] == "bu"]
        if bu:
            dados = get(f"{pasta}/{h['hash']}/{bu[0]}")
            tmp = destino.with_suffix(".tmp")
            tmp.write_bytes(dados)
            tmp.rename(destino)
            return destino
    return None


def gravar_csv(uf, arquivos):
    SAIDA.mkdir(parents=True, exist_ok=True)
    with open(SAIDA / f"{uf.upper()}.csv", "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["uf", "municipio", "zona", "secao", "local", "cargo", "aptos",
                    "comparecimento", "numero", "votos"])
        for p in sorted(arquivos):
            w.writerows((uf.upper(), *l) for l in extrair(p.read_bytes()))


def processar_uf(uf):
    (CACHE / uf).mkdir(parents=True, exist_ok=True)
    cfg = json.loads(get(f"{BASE}/config/{uf}/{uf}-p00{PLEITO}-cs.json"))
    secoes = [(m["cd"], z["cd"], s["ns"]) for a in cfg["abr"] for m in a["mu"]
              for z in m["zon"] for s in z["sec"]]
    print(f"[{uf}] {len(secoes)} seções", flush=True)

    arquivos, sem_bu, erros = [], 0, []
    with ThreadPoolExecutor(max_workers=TRABALHADORES) as ex:
        futuros = {ex.submit(baixar_secao, uf, *s): s for s in secoes}
        for k, fut in enumerate(as_completed(futuros), 1):
            try:
                p = fut.result()
                if p is None:
                    sem_bu += 1
                else:
                    arquivos.append(p)
            except Exception as e:
                erros.append((*futuros[fut], repr(e)))
            if k % 5000 == 0:
                print(f"[{uf}] {k}/{len(secoes)} ({LIMITE.taxa:.0f} req/s)", flush=True)

    if erros:  # UF incompleta: não grava o CSV para não publicar resultado parcial
        print(f"[{uf}] incompleta: {len(erros)} seções com erro", flush=True)
        return len(erros)
    gravar_csv(uf, arquivos)
    print(f"[{uf}] ok: {len(arquivos)} BUs, {sem_bu} sem boletim, {len(erros)} erros", flush=True)
    for e in erros[:10]:
        print(f"[{uf}]   erro {e}", file=sys.stderr, flush=True)
    return len(erros)


def main():
    if sys.argv[1:2] == ["--cache"]:
        for uf in (u.lower() for u in sys.argv[2:]):
            gravar_csv(uf, list((CACHE / uf).glob("*.dat")))
            print(f"[{uf}] csv gerado do cache")
        return
    ufs = [u.lower() for u in sys.argv[1:]] or UFS
    erros = sum(processar_uf(uf) for uf in ufs)
    print(f"fim; seções com erro: {erros} (rode de novo para retomar)", flush=True)


if __name__ == "__main__":
    main()
