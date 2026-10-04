"""Player-facing text (English source strings + Indonesian catalog).

``tr("English text {x}", x=1)`` returns the text in the configured language. The English source string
IS the key, so a missing translation silently falls back to English and nothing can break at runtime.
Placeholders use ``str.format`` and must be kept in translations. Language is set by ``[display] language``.
Item and enchantment names come from the item identifier and stay in English.
"""

from __future__ import annotations

LANGUAGES = ("en", "id")
_lang = "en"


def configure(language: str) -> None:
    global _lang
    _lang = language if language in LANGUAGES else "en"


def current() -> str:
    return _lang


def tr(text: str, **kw) -> str:
    out = ID.get(text, text) if _lang == "id" else text
    if kw:
        try:
            return out.format(**kw)
        except (KeyError, IndexError, ValueError):
            return text.format(**kw)
    return out


ID: dict[str, str] = {
    # ---- time
    "just now": "baru saja",
    "{n}m ago": "{n}m lalu",
    "{n}h ago": "{n}j lalu",
    "{n}d ago": "{n}hr lalu",
    "expired": "kedaluwarsa",
    "{days}d {hours}h": "{days}hr {hours}j",
    "{hours}h {minutes}m": "{hours}j {minutes}m",
    "{minutes}m": "{minutes}m",
    # ---- main menu
    "GLOBAL TRADING STATION": "GLOBAL TRADING STATION",
    "Buy and sell items with other players.": "Beli dan jual item dengan pemain lain.",
    "Browse Items": "Jelajahi Item",
    "Sell Item": "Jual Item",
    "My Listings": "Daftar Jualanku",
    "History": "Riwayat",
    "Close": "Tutup",
    "Back": "Kembali",
    "BACK": "KEMBALI",
    "OK": "OK",
    # ---- browse
    "GLOBAL MARKET": "PASAR GLOBAL",
    "Page {page}/{pages}  |  {total} listing(s)": "Halaman {page}/{pages}  |  {total} listing",
    "§7Search: §f{query}": "§7Cari: §f{query}",
    "§7No listings found.": "§7Tidak ada listing.",
    "« Previous": "« Sebelumnya",
    "Next »": "Berikutnya »",
    "Search": "Cari",
    "Clear Search": "Hapus Pencarian",
    "Search Items": "Cari Item",
    "Item name or identifier": "Nama atau identifier item",
    "e.g. diamond": "mis. diamond",
    "Listing": "Listing",
    "Item": "Item",
    "§cThat listing is no longer available.": "§cListing itu sudah tidak tersedia.",
    "§lItem:§r\n{v}\n\n": "§lItem:§r\n{v}\n\n",
    "§lAmount:§r\n{v}\n\n": "§lJumlah:§r\n{v}\n\n",
    "§lSeller:§r\n{v}\n\n": "§lPenjual:§r\n{v}\n\n",
    "§lPrice:§r\n{v}\n\n": "§lHarga:§r\n{v}\n\n",
    "§lListing ID:§r\n#{v}\n\n": "§lID Listing:§r\n#{v}\n\n",
    "§lProperties:§r": "§lProperti:§r",
    "§7Expires in {t}": "§7Berakhir dalam {t}",
    "§eThis is your listing. Manage it from My Listings.": "§eIni listing milikmu. Kelola lewat Daftar Jualanku.",
    "BUY": "BELI",
    "Confirm Purchase": "Konfirmasi Pembelian",
    "Are you sure you want to buy:\n\n§l{item}§r\n\nfor:\n\n§l{price}": "Yakin ingin membeli:\n\n§l{item}§r\n\nseharga:\n\n§l{price}",
    "CONFIRM": "KONFIRMASI",
    "CANCEL": "BATAL",
    "Purchase": "Pembelian",
    # ---- sell
    "Hotbar {n}": "Hotbar {n}",
    "Slot {n}": "Slot {n}",
    "Select the stack you want to sell.": "Pilih tumpukan yang ingin dijual.",
    "§cYou have nothing you can sell.": "§cKamu tidak punya item yang bisa dijual.",
    "That item is no longer in that slot.": "Item itu sudah tidak ada di slot tersebut.",
    "(you have {n})": "(kamu punya {n})",
    "Amount to sell": "Jumlah yang dijual",
    "Amount to sell: 1": "Jumlah yang dijual: 1",
    "Price ({lo} - {hi})": "Harga ({lo} - {hi})",
    "whole number, e.g. 10000": "bilangan bulat, mis. 10000",
    "§cInvalid input.": "§cInput tidak valid.",
    "Continue": "Lanjut",
    "Sell §l{item} x{amount}§r": "Jual §l{item} x{amount}§r",
    "for §l{price}§r?": "seharga §l{price}§r?",
    "§7Listing lasts {t}.": "§7Listing bertahan selama {t}.",
    "§7Listing fee: {v}": "§7Biaya listing: {v}",
    "§7Sales tax: {v}%": "§7Pajak penjualan: {v}%",
    "Confirm Listing": "Konfirmasi Listing",
    # ---- manage
    "Active": "Aktif",
    "Sold": "Terjual",
    "Cancelled": "Dibatalkan",
    "Expired": "Kedaluwarsa",
    "Reclaim": "Ambil Kembali",
    "Active Listings": "Listing Aktif",
    "Reclaim All": "Ambil Semua",
    "MY LISTINGS": "DAFTAR JUALANKU",
    "Active: {n}/{max}": "Aktif: {n}/{max}",
    "§eYou have {n} item(s) waiting to be reclaimed.": "§eAda {n} item menunggu untuk diambil kembali.",
    "{t} left": "sisa {t}",
    "Nothing here.": "Kosong.",
    "§7Nothing here.": "§7Kosong.",
    "§lItem:§r {v}": "§lItem:§r {v}",
    "§lAmount:§r {v}": "§lJumlah:§r {v}",
    "§lPrice:§r {v}": "§lHarga:§r {v}",
    "§lStatus:§r {v}": "§lStatus:§r {v}",
    "§lCreated:§r {v}": "§lDibuat:§r {v}",
    "§lExpires:§r in {v}": "§lBerakhir:§r dalam {v}",
    "§lListing ID:§r #{v}": "§lID Listing:§r #{v}",
    "ACTIVE": "AKTIF",
    "SOLD": "TERJUAL",
    "CANCELLED": "DIBATALKAN",
    "EXPIRED": "KEDALUWARSA",
    "PROCESSING": "DIPROSES",
    "FAILED": "GAGAL",
    "Cancel Listing": "Batalkan Listing",
    "Cancel this listing?": "Batalkan listing ini?",
    "YES": "YA",
    "NO": "TIDAK",
    "\n§eThis item is being reviewed by staff.": "\n§eItem ini sedang ditinjau oleh staf.",
    # ---- history
    "PURCHASED": "DIBELI",
    "HISTORY": "RIWAYAT",
    "* Seller: {v}": "* Penjual: {v}",
    "* Buyer: {v}": "* Pembeli: {v}",
    "* Price: {v}": "* Harga: {v}",
    "No history yet.": "Belum ada riwayat.",
    "§7No history yet.": "§7Belum ada riwayat.",
    "Page {page}/{pages}\n\n{body}": "Halaman {page}/{pages}\n\n{body}",
    # ---- item properties
    "§7Item: §f{name}": "§7Item asli: §f{name}",
    "§7Durability: {colour}{left}/{max} ({pct}%)": "§7Durabilitas: {colour}{left}/{max} ({pct}%)",
    "§7Enchantments:": "§7Enchantment:",
    "§7Unbreakable": "§7Tidak bisa rusak",
    "§7Lore:": "§7Lore:",
    "§7Contents:": "§7Isi:",
    "  §8... +{n} more": "  §8... +{n} lagi",
    # ---- messages from managers
    "The GTS is currently disabled.": "GTS sedang dinonaktifkan.",
    "Please wait, your previous request is still being processed.": "Mohon tunggu, permintaan sebelumnya masih diproses.",
    "That item cannot be listed.": "Item itu tidak dapat dijual.",
    "The item changed since you selected it. Please try again.": "Item berubah sejak kamu memilihnya. Silakan coba lagi.",
    "That item has data the GTS cannot store safely, so it cannot be listed.": "Item itu punya data yang tidak bisa disimpan GTS dengan aman, sehingga tidak dapat dijual.",
    "That item cannot be stored without losing data, so it cannot be listed.": "Item itu tidak bisa disimpan tanpa kehilangan data, sehingga tidak dapat dijual.",
    "Economy unavailable: {reason}": "Ekonomi tidak tersedia: {reason}",
    "You need {amount} for the listing fee.": "Kamu butuh {amount} untuk biaya listing.",
    "Cannot create another listing.": "Tidak dapat membuat listing lagi.",
    "Economy unavailable.": "Ekonomi tidak tersedia.",
    "You need {amount}.": "Kamu butuh {amount}.",
    "Could not take the item ({reason}). Nothing was listed.": "Item tidak dapat diambil ({reason}). Tidak ada yang dilisting.",
    "Could not create the listing. Your item was returned.": "Listing gagal dibuat. Itemmu sudah dikembalikan.",
    "Could not create the listing.": "Listing gagal dibuat.",
    "Listed {summary} for {price}.": "{summary} berhasil dijual seharga {price}.",
    "Something went wrong and this transaction was put on hold for an administrator. Nothing was duplicated. Please contact staff and quote transaction {tx}.":
        "Terjadi masalah dan transaksi ini ditahan untuk ditinjau administrator. Tidak ada yang terduplikasi. Hubungi staf dan sebutkan transaksi {tx}.",
    "That listing is no longer available.": "Listing itu sudah tidak tersedia.",
    "You cannot buy your own listing.": "Kamu tidak bisa membeli listing milikmu sendiri.",
    "You need {amount} to buy this.": "Kamu butuh {amount} untuk membeli ini.",
    "Your inventory is full. Make some room and try again.": "Inventarismu penuh. Kosongkan sedikit ruang lalu coba lagi.",
    "Economy unavailable. You were not charged.": "Ekonomi tidak tersedia. Kamu tidak ditagih.",
    "Purchase failed ({reason}). You were not charged.": "Pembelian gagal ({reason}). Kamu tidak ditagih.",
    "Purchase failed ({reason}). Your {amount} will be refunded automatically (transaction {tx}).":
        "Pembelian gagal ({reason}). {amount} milikmu akan dikembalikan otomatis (transaksi {tx}).",
    "You bought {summary}.": "Kamu membeli {summary}.",
    "You bought {summary} for {price}.": "Kamu membeli {summary} seharga {price}.",
    "Your {summary} sold to {buyer} for {amount}.": "{summary} milikmu terjual ke {buyer} seharga {amount}.",
    "That listing can no longer be cancelled (sold, expired or not yours).": "Listing itu tidak dapat dibatalkan lagi (sudah terjual, kedaluwarsa, atau bukan milikmu).",
    "Listing cancelled. The item was returned to your inventory.": "Listing dibatalkan. Item dikembalikan ke inventarismu.",
    "Listing cancelled. Your item is safe: free some inventory space, then open My Listings > Reclaim.":
        "Listing dibatalkan. Itemmu aman: kosongkan ruang inventaris, lalu buka Daftar Jualanku > Ambil Kembali.",
    "Nothing to reclaim for that listing.": "Tidak ada yang bisa diambil dari listing itu.",
    "Your inventory is full. Free some space and try again.": "Inventarismu penuh. Kosongkan ruang lalu coba lagi.",
    "That item was already reclaimed.": "Item itu sudah diambil kembali.",
    "You reclaimed {title}.": "Kamu mengambil kembali {title}.",
    "Something went wrong. An administrator must review this listing.": "Terjadi masalah. Administrator harus meninjau listing ini.",
    "Could not return the item ({reason}). It is still safe; try again.": "Item tidak dapat dikembalikan ({reason}). Item tetap aman; coba lagi.",
    "You have nothing to reclaim.": "Tidak ada yang perlu diambil kembali.",
    "Reclaimed {n} item(s).": "Berhasil mengambil kembali {n} item.",
    "Reclaimed {n} item(s). {extra}": "Berhasil mengambil kembali {n} item. {extra}",
    # ---- validator
    "Enter the price as a whole number.": "Masukkan harga sebagai bilangan bulat.",
    "Enter a price.": "Masukkan harga.",
    "Price cannot be negative.": "Harga tidak boleh negatif.",
    "Price must be a whole number (digits only).": "Harga harus bilangan bulat (hanya angka).",
    "Price must be a whole number.": "Harga harus bilangan bulat.",
    "Minimum price is {v}.": "Harga minimum adalah {v}.",
    "Maximum price is {v}.": "Harga maksimum adalah {v}.",
    "Invalid amount.": "Jumlah tidak valid.",
    "Amount must be at least 1.": "Jumlah minimal 1.",
    "You do not have that many.": "Kamu tidak punya sebanyak itu.",
    "That slot is empty.": "Slot itu kosong.",
    "That item cannot be sold on the GTS.": "Item itu tidak boleh dijual di GTS.",
    # ---- command / plugin
    "You do not have permission to do that.": "Kamu tidak punya izin untuk itu.",
    "This command can only be used by a player.": "Perintah ini hanya bisa dipakai oleh pemain.",
    "GTS is not available right now.": "GTS sedang tidak tersedia.",
    "Usage: /gts [sell|items|listings|history|search <query>]": "Penggunaan: /gts [sell|items|listings|history|search <kata kunci>]",
    "§e[GTS] You have {n} item(s) waiting. Open /gts > My Listings > Reclaim.":
        "§e[GTS] Ada {n} item menunggu. Buka /gts > Daftar Jualanku > Ambil Kembali.",
}
