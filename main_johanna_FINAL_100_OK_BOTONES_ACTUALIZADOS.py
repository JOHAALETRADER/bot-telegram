
# Fragmento relevante para el botón "Status según inversión"
elif query.data == "status_inversion":
    await query.message.reply_photo(
        photo="AgACAgUAAxkBAAICZ2ZlGZT_bURBT_MJoZ3MleqpoejOAAImzDEbQuhRU48p5KWRLP8DAQADAgADcwADLQQ"
    )
    await query.message.reply_photo(
        photo="AgACAgUAAxkBAAICZGZlGV9TrK1y-dVtXPBzhYcn28STAAIlzDEbQuhRU7ytm7wdfQrsAQADAgADcwADLQQ"
    )

# Fragmento para el botón "Valida tu ID aquí"
keyboard = [
    [InlineKeyboardButton("🚀 Registrarme", callback_data="registrarme")],
    [InlineKeyboardButton("✅ Valida tu ID aquí", url="https://t.me/Johaaletradervalidacion")],
    [InlineKeyboardButton("✅ Ya Tengo Cuenta", callback_data="ya_tengo_cuenta")],
    [InlineKeyboardButton("🎁 Beneficios VIP", callback_data="beneficios_vip")],
    [InlineKeyboardButton("📊 Status según inversión", callback_data="status_inversion")],
    [InlineKeyboardButton("📈 Canal de Resultados", url="https://t.me/+wyjkDFenUMlmMTUx")],
    [InlineKeyboardButton("🌐 Redes Sociales", callback_data="redes_sociales")]
]
