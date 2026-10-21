"""Gera estatísticas e o gráfico-resumo dos 3 ensaios (Chiron x G500).

Uso (a partir da raiz do repositório):
    python analysis/gerar_resumo.py
Saída: images/resultados/resumo_cd_picos.png e tabela no terminal.
"""
from pathlib import Path
import pandas as pd
import matplotlib.pyplot as plt

RAIZ = Path(__file__).resolve().parent.parent
df = pd.read_csv(RAIZ / "analysis" / "resumo_testes.csv")

resumo = df.groupby("objeto").agg(
    cd_medio=("cd_medio", "mean"),
    cd_desvio=("cd_medio", "std"),
    pico_N=("pico_forca_N", "mean"),
    pico_desvio=("pico_forca_N", "std"),
    vel_pico=("velocidade_no_pico_kmh", "mean"),
)
resumo["cd_cv_%"] = 100 * resumo["cd_desvio"] / resumo["cd_medio"]
print(resumo.round(4).to_string())

chiron = resumo.loc["Bugatti Chiron"]
g500 = resumo.loc["Mercedes-Benz G500"]
print(f"\nRazão Cd  (G500/Chiron): {g500.cd_medio / chiron.cd_medio:.2f}")
print(f"Razão pico de força    : {g500.pico_N / chiron.pico_N:.2f}")

cores = {"Bugatti Chiron": "blue", "Mercedes-Benz G500": "red"}
fig, ax = plt.subplots(1, 2, figsize=(10, 4.2))
largura = 0.35
for i, (obj, g) in enumerate(df.groupby("objeto")):
    x = [t + (i - 0.5) * largura for t in range(len(g))]
    ax[0].bar(x, g["cd_medio"], largura, color=cores[obj], label=obj)
    ax[1].bar(x, g["pico_forca_N"], largura, color=cores[obj], label=obj)
for a, titulo, ylab in zip(ax, ["Cd médio por ensaio", "Pico de força por ensaio"],
                           ["Cd (adimensional)", "Força (N)"]):
    a.set_xticks(range(3))
    a.set_xticklabels([f"Teste {t}" for t in sorted(df.teste.unique())])
    a.set_title(titulo)
    a.set_ylabel(ylab)
    a.grid(axis="y", alpha=0.4)
ax[0].legend()
fig.suptitle("Wind Tunnel 2.0 - Bugatti Chiron x Mercedes-Benz G500")
fig.tight_layout()
saida = RAIZ / "images" / "resultados" / "resumo_cd_picos.png"
fig.savefig(saida, dpi=150)
print(f"\nGráfico salvo em {saida}")
