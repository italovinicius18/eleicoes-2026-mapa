PY ?= .venv/bin/python

.PHONY: all ambiente dados resumo site servir

## Pipeline completo: baixa tudo e gera o site (o 2º turno leva horas: ~517 mil boletins de urna)
all: dados resumo site

ambiente:
	uv venv .venv && uv pip install --python $(PY) -r requirements.txt

## Downloads (vão para dados_brutos/, fora do git); todos podem ser retomados
dados:
	$(PY) -I scripts/baixar_base.py
	$(PY) -I scripts/baixar_1turno.py
	$(PY) -I scripts/baixar_bu_2turno.py

## dados_brutos/ -> dados/resumo/
resumo:
	$(PY) -I scripts/preparar_dados.py

## dados/resumo/ + dados/geo/ -> docs/ (não precisa de rede)
site:
	$(PY) -I scripts/gerar_mapas.py

servir:
	cd docs && python3 -m http.server 8000
