"""Cover image for the README (docs/img/cover.png and cover.pt-BR.png). Run: python docs/make_cover.py"""
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch
from pathlib import Path

OUT = Path(__file__).parent / "img"
INK, MUTED, LINE, BG = "#111111", "#555555", "#d9dde3", "#fdfdfc"
BLUE, ORANGE, AQUA = "#2a78d6", "#eb6834", "#1baf7a"
for fam in ("Inter", "DejaVu Sans"):
    try:
        matplotlib.font_manager.findfont(fam, fallback_to_default=False); plt.rcParams["font.family"] = fam; break
    except ValueError:
        pass

T = {
    "": dict(tag="Batch APIs at half price,\nwithout paying twice", cost_t="same product, Gemini, measured", sync="one call at a time", batch="batch API", half="half the price: ×0.5 held across 196 calls",
             head="batches/summary_20261020_141503_271845.json",
             rows=[("product 101", "a3f9…", "written", AQUA), ("product 102", "7c1e…", "written", AQUA),
                   ("product 103", "e08b…", "error: empty", ORANGE), ("product 104", "5d22…", "pending", MUTED)],
             sig="signature 9b41…  ·  job batches/…  ·  SUCCEEDED",
             checks_t="what it prevents", checks=["answer on the wrong record", "manifest overwritten", "half a collection written twice",
                                                  "the same record paid twice", "empty answer stored as text", "SDK object in a batch config"],
             foot="−39% on a real round  ·  Postgres queue with SKIP LOCKED"),
    ".pt-BR": dict(tag="APIs de lote pela metade,\nsem pagar duas vezes", cost_t="mesmo produto, Gemini, medido", sync="uma chamada por vez", batch="API de lote", half="metade do preço: ×0,5 em 196 chamadas",
                   head="batches/resumo_20261020_141503_271845.json",
                   rows=[("produto 101", "a3f9…", "gravado", AQUA), ("produto 102", "7c1e…", "gravado", AQUA),
                         ("produto 103", "e08b…", "erro: vazia", ORANGE), ("produto 104", "5d22…", "pendente", MUTED)],
                   sig="assinatura 9b41…  ·  job batches/…  ·  SUCCEEDED",
                   checks_t="o que ela evita", checks=["resposta no registro errado", "manifesto sobrescrito", "meia coleta gravada duas vezes",
                                                       "o mesmo registro pago duas vezes", "resposta vazia virando texto", "objeto do SDK na config do lote"],
                   foot="−39% numa rodada real  ·  fila no Postgres com SKIP LOCKED"),
}

OUT.mkdir(parents=True, exist_ok=True)
for suf, t in T.items():
    fig = plt.figure(figsize=(11, 5.6), dpi=110); fig.patch.set_facecolor(BG)
    ax = fig.add_axes([0, 0, 1, 1]); ax.set_xlim(0, 110); ax.set_ylim(0, 56); ax.axis("off")
    ax.add_patch(FancyBboxPatch((5, 41), 9, 9, boxstyle="round,pad=0,rounding_size=2", fc=BLUE, ec="none"))
    ax.text(9.5, 45.5, "½", color="white", fontsize=22, weight="bold", ha="center", va="center")
    ax.text(16.5, 45.5, "llm-batch-kit", fontsize=20, weight="bold", color=INK, va="center")
    ax.text(5, 33.5, t["tag"], fontsize=15, color=INK, va="center", linespacing=1.25)
    # cost of the same product, measured: one by one vs batch
    ax.text(5, 24.6, t["cost_t"], fontsize=8.8, color=MUTED, va="center")
    for k, (lab, usd, col) in enumerate(((t["sync"], 0.1463, ORANGE), (t["batch"], 0.0747, AQUA))):
        y = 19.6 - k * 6.4
        w = 21 * usd / 0.1463
        ax.add_patch(FancyBboxPatch((5, y - 1.7), w, 3.4, boxstyle="round,pad=0,rounding_size=0.6", fc=col, ec="none"))
        ax.text(5, y + 3.0, lab, fontsize=8.6, color=INK, va="center")
        ax.text(5 + w + 1, y, f"US$ {usd:.4f}".replace(".", "," if suf else "."), fontsize=10, weight="bold",
                color=INK, va="center")
    ax.text(5, 7.2, t["half"], fontsize=8.6, color=AQUA, weight="bold", va="center")
    px, py, pw, ph = 41, 9, 39, 40
    ax.add_patch(FancyBboxPatch((px, py), pw, ph, boxstyle="round,pad=0,rounding_size=1.2", fc="white", ec=LINE, lw=1.2))
    ax.text(px + 2, py + ph - 3.5, t["head"], fontsize=8.2, weight="bold", color=INK, family="monospace")
    ax.text(px + 2, py + ph - 8.5, "item", fontsize=8.4, color=MUTED); ax.text(px + 15, py + ph - 8.5, "prompt_sha", fontsize=8.4, color=MUTED)
    for i, (a, b, c, col) in enumerate(t["rows"]):
        y = py + ph - 14 - i * 5.4
        ax.text(px + 2, y, a, fontsize=9.2, color=INK)
        ax.text(px + 15, y, b, fontsize=9.2, color=MUTED, family="monospace")
        ax.add_patch(FancyBboxPatch((px + 24.5, y - 1.5), 12, 3.2, boxstyle="round,pad=0,rounding_size=0.8", fc=col, ec="none", alpha=0.95))
        ax.text(px + 30.5, y + 0.05, c, fontsize=7.8, color="white", weight="bold", ha="center", va="center")
    ax.plot([px + 2, px + pw - 2], [py + 6.5, py + 6.5], color=LINE, lw=1, ls=(0, (2, 2)))
    ax.text(px + 2, py + 3.2, t["sig"], fontsize=7.4, color=MUTED, family="monospace")
    tx = 82
    ax.text(tx, 46, t["checks_t"], fontsize=11, weight="bold", color=INK)
    for j, s in enumerate(t["checks"]):
        ax.text(tx, 40.5 - j * 5, "✓  " + s, fontsize=8.6, color=INK)
    ax.text(5, 3.2, t["foot"], fontsize=8.4, color=MUTED)
    ax.text(105, 3.2, "github.com/CaiqueDourado", fontsize=8.4, color=MUTED, ha="right")
    fig.savefig(OUT / f"cover{suf}.png", facecolor=BG); plt.close(fig)
print("ok")
