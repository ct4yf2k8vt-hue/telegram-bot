def build_victory_report(now_ts=None):
    """Gunun Zaferi -> 'GUNUN KAZANANI' formati. (metin, kazanan_dict) dondurur."""
    now_ts = now_ts or time.time()
    cutoff = now_ts - 86400
    with state_lock:
        recent = [
            s for s in signals_log
            if s["created_at"] >= cutoff
            and s.get("engine") in ("PUMP", "PUMP15M")
            and s.get("peak_pct", 0.0) >= 3.0
        ]
    best_by_sym = {}
    for s in recent:
        prev = best_by_sym.get(s["symbol"])
        if not prev or s["peak_pct"] > prev["peak_pct"]:
            best_by_sym[s["symbol"]] = s
    ranked = sorted(best_by_sym.values(), key=lambda x: x["peak_pct"], reverse=True)

    if not ranked:
        return (
            "🏆 <b>GÜNÜN KAZANANI (21:00 TR)</b>\n\n"
            "Son 24 saatte +%3 ve üzeri pik yapan Pump/Kırılım sinyali henüz oluşmadı.",
            None,
        )

    top = ranked[0]
    base = top["base"]
    entry = top["entry"]
    peak = top["peak_price"]
    peak_pct = top["peak_pct"]
    dur = fmt_peak_dur(top["created_at"], top.get("peak_at", top["created_at"]), ascii_only=False)

    lines = [
        f"🏆 <b>GÜNÜN KAZANANI: {html.escape(base)}</b>",
        "",
        "🔥 <b>24 saat içinde en çok yükselen coin!</b>",
        "",
        f"📉 Dip Fiyat: <b>{fmt_price(entry)}</b>",
        f"🚀 Zirve Fiyat: <b>{fmt_price(peak)}</b>",
        f"📈 Maksimum Yükseliş: <b>+{peak_pct:.1f}%</b>",
        "",
        f"💡 <b>Değerlendirme:</b> {html.escape(base)} gün içerisinde muazzam bir hacim ve fiyat patlaması yaşadı. "
        f"Pump Dedektör verilerimize göre günün en kârlı hareketi bu coinde gerçekleşti. "
        f"(Pik süresi: {dur})",
    ]

    if len(ranked) > 1:
        lines.append("")
        lines.append("🥈 <b>Diğer Öne Çıkanlar:</b>")
        for i, s in enumerate(ranked[1:5], 2):
            lines.append(f"{i}. <b>#{html.escape(s['base'])}</b> +{s['peak_pct']:.1f}%")

    return "\n".join(lines), top
