## <img src="assets/carry-icon.png" width="96" alt="Carry simgesi">

🇬🇧 [English](README.md)

**Claude Code ve Codex her sohbete sıfırdan başlar. Carry onlara kendi notlarınızdan bir hafıza verir.**

“Fiyatlandırmada ne karar vermiştik?” diye sorun: asistanınız cevabı notlarınızda bulur ve nerede yazdığını gösterir. Sohbet bitince Carry yeni kararları ve yarım kalan işleri listeler, siz doğru olanları tutarsınız. Bir dahaki sefere daha az şey anlatırsınız.

Notlarınız seçtiğiniz klasörde, düz Markdown (`.md`) dosyaları olarak Mac'inizde kalır.

**Sürüm 0.7.1 · macOS 14+ · pilot**

## Nasıl çalışır?

1. **Sorun.** Claude Code veya Codex'te not aldığınız herhangi bir şeyi sorun. Carry notlarınızda arar ve eşleşen bölümleri kaynağı ve tarihiyle verir; cevabı kontrol edebilirsiniz. “Geçen hafta” veya “eylülde” gibi ifadeler aramayı o döneme daraltır.
2. **Çalışın.** Not klasörünüzde her zamanki gibi sohbet edin. Sohbet bitince (ve açarsanız her akşam) Carry kararları, bilgileri ve açık işleri Gelen kutunuzdaki bir taslağa çıkarır. Bunları notlarınızla da karşılaştırır (denetçi olarak TypeSafe Jev seçiliyse onunla, değilse asistanınızla): notlarınızda zaten yazanlar katlanır, gerçek bir çelişkide iki taraf da gösterilir, sohbette geçen ve notunuzda yazan. Jev ile yapılmış ya da vazgeçilmiş görünenler de katlanır.
3. **İnceleyin.** Uygulamanın **İncele** sayfasında taslağı madde madde geçin: **Kabul et**, **Düzelt** ya da **Atla**. Kabul edilen maddeler, söylendikleri günün log'una kaydedilir. Sohbetten hiçbir şey siz olmadan nota dönüşmez; sohbetin kendisi, gizli bilgileri maskelenmiş olarak, ham malzeme olarak `sources/carry/harvest/` altında saklanır.

**Asistan klasörü zaten okuyamıyor mu?** Bildiği dosyaları açabilir. Carry bütün not klasörünüzün bir index'ini tutar; onu kelimeyle ve (Ollama varsa) anlamıyla arar, hangi bölümlerin soruyu gerçekten cevapladığını denetleyebilir ve sohbetlerinizi sizin yerinize takip eder.

## Başlayın

Claude Code veya Codex, [uv](https://docs.astral.sh/uv/) ve Xcode Command Line Tools (`xcode-select --install`) gerekir.

```sh
uv tool install "git+https://github.com/berketevik/carry"
carry setup
carry app open
```

Yeni bir not klasörü oluşturun veya mevcut `.md` klasörünüzü seçin. Sonra **Ayarlar → Asistanlar → Notlarımla başlat** yolunu izleyip şunu sorun: “Carry ile bu proje hakkındaki notlarımı bul.”

## Verileriniz

- Carry notlarınızı hiçbir yere yüklemez. Asistanınızın bulduğu bölümler o asistana gider; sohbet taslakları da onun üzerinden çıkarılır (mevcut hesabınızla Claude Code veya Codex); Jev yoksa taslakların karşılaştırıldığı not bölümleri de ona gider.
- İlgi denetçisi olarak **TypeSafe Jev**'i açarsanız sorular ve bölümler TypeSafe'e gider; sohbet taslakları denetlenip notlarınızla karşılaştırılırken sohbetlerinizden gizli bilgileri maskelenmiş alıntılar da gider. `sensitivity: secret` işaretli notlar hiç gönderilmez.

<details>
<summary><b>Kurulum seçenekleri ve güncelleme</b></summary>

`carry setup`, [Ollama](https://ollama.com) ile anlamına göre aramayı önerir (yaklaşık 0,6 GB). Yalnız kelime aramasıyla başlamak için `carry setup --no-semantic`.

Güncellemek için `uv tool upgrade carry`, ardından `carry app install`.

Sohbet taslakları `+/` klasörüne düşer. Bir maddeyi kabul etmek, onunla çelişen notu asla değiştirmez; log satırı o nota bağlantı verir, çelişkiyi siz çözersiniz. Hiç madde kalmayınca taslak İncele sayfasından çıkar. Asistanınızın başka yerlere yazdığı notlar siz kontrol edene kadar `draft: true` taşır, ama hemen aranabilir ve İncele sayfasında beklemez. Her ayarın açıklaması uygulamanın rehberinde (**Ayarlar → Genel → Yardım**).

</details>

<details>
<summary><b>Komut satırı</b></summary>

Notları okuyan komutlar ayar klasörünüzü ister: `export CARRY_WORKSPACE="$HOME/CarryState"` veya `carry --workspace <klasör> …`.

| Komut | Ne yapar |
|---|---|
| `carry setup` | Adım adım kurulum. |
| `carry app install` / `open` / `remove` | Mac uygulamasını derler, açar veya kaldırır. |
| `carry recall "soru"` | Notlarda arar. |
| `carry status --probe` | Index'i ve arama sağlayıcısını denetler. |
| `carry search --semantic on --judge jev` | Arama kipini değiştirir: `--semantic on/off`, `--judge assistant/jev`. |
| `carry connect claude <klasör>` | Bir projeyi bağlar (Codex için `codex`); `--dry-run` önizler. |
| `carry harvest --vault <klasör>` | Sohbet taslaklarını hemen çıkarır; `--install-schedule` her gün 21:30'da çalıştırır. |

Diğerleri için `carry <komut> --help`.

</details>

<details>
<summary><b>Asistanlar için (MCP)</b></summary>

Claude Code veya Codex, Carry'nin yerel sunucusunu (`carry.mcp_server`) başlatır. Üç araç var: `carry_recall` (kaynaklı arama), `carry_catalog` (dosya listesi) ve `carry_status` (index durumu). O asistana düzeltme izni verirseniz `carry_propose` da gelir (düzeltme taslağı; yalnız siz kabul edebilirsiniz). Bulunan metin talimat değil, veridir.

</details>

<details>
<summary><b>Geliştiriciler için</b></summary>

```sh
uv venv --python 3.13 .venv
uv pip install --python .venv/bin/python -e .
.venv/bin/python -m unittest discover -s tests -q
```

Çekirdek Python 3.11+ ister, paket gerektirmez. Ek paketler: `.[embed]` (NumPy), `.[yaml]` (PyYAML), `.[rerank]` (yerel ilgi modeli). Asıl veri notlardır; index her zaman yeniden üretilebilir.

</details>

<details>
<summary><b>Sınırlar</b></summary>

- **Pilot:** uygulama Mac'inizde derlenir ve ad-hoc imzalanır; noter onaylı sürüm yoktur.
- Claude Code ve Codex ile çalışır; Claude veya ChatGPT sohbet uygulamalarıyla çalışmaz. Yalnız `.md` dosyalarını okur.
- Arama ve sohbet taslakları eksik ya da yanlış olabilir; taslakları kaynaklarıyla kontrol edin.
- Gizli bilgi maskeleme kusursuz değildir. Tarihli arama dosya değişiklik zamanını değil; tarihli başlıkları, dosya adlarını ve `created`/`date`/`updated` alanlarını kullanır.

</details>

<sub>**Berke Tevik** tarafından geliştirildi.</sub>
