"""Validação preditiva (iteração 3): baselines, replay histórico, bootstrap, shadow mode, drift.

Nada aqui altera decisões em produção; tudo é medição. Os módulos são importados de forma
preguiçosa pelos jobs para não carregar pandas/duckdb no arranque do scheduler.
"""
