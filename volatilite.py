
def build_victory_report(now_ts=None):
    cutoff = (now_ts or time.time()) - 86400

    with state_lock:
        recent = [
            s.copy() for s in signals_log
            if safe_float(s.get("created_at")) >= cutoff
        ]

    best = {}

    for s in recent:
        symbol = s["symbol"]

        if (
            symbol not in best
            or safe_float(s.get("peak_pct"))
            > safe_float(best[symbol].get("peak_pct"))
        ):
            best[symbol] = s

    ranked = sorted(
        best.values(),
        key=lambda x: safe_float(x.get("peak_pct")),
        reverse=True
    )

    if not ranked:
        return (
            "🏆 <b>GÜNÜN KAZANANI</b>\n\n"
            "Son 24 saatte pump sinyali bulunmuyor."
        )

    winner = ranked[0]

    symbol = winner["symbol"]
    dip = safe_float(winner.get("entry"))
    peak = safe_float(winner.get("peak_price"))
    pct = safe_float(winner.get("peak_pct"))

    message = (
        f"🏆 <b>GÜNÜN KAZANANI: {symbol}</b>\n\n"
        f"🔥 24 saat içinde en çok yükselen coin!\n\n"
        f"📉 Dip Fiyat: <b>{fmt_price(dip)}</b>\n"
        f"🚀 Zirve Fiyat: <b>{fmt_price(peak)}</b>\n"
        f"📈 Maksimum Yükseliş: <b>+{pct:.1f}%</b>\n\n"
        f"💡 <b>Değerlendirme:</b> {symbol} gün içerisinde "
        f"muazzam bir hacim ve fiyat patlaması yaşadı. "
        f"Pump Dedektör verilerimize göre günün en güçlü "
        f"hareketi bu coinde gerçekleşti."
    )

    if len(ranked) > 1:
        message += "\n\n📊 <b>Diğer güçlü pump hareketleri:</b>\n"

        for i, s in enumerate(ranked[1:6], 2):
            message += (
                f"\n{i}. #{s['symbol']} "
                f"— +{safe_float(s.get('peak_pct')):.2f}%"
            )

    return message
