"""Ponto de entrada do Sonora.

    python main.py                       -> abre a interface gráfica
    python main.py "<link>" [...]        -> baixa direto pelo terminal
    python main.py -h                    -> todas as opções
"""
import sys

from sonora.cli import main

if __name__ == "__main__":
    sys.exit(main())
