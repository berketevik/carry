# Örnek ekip bilgi bankası

`ornek-ekip/`, Carry'ye bağlanmaya hazır bir ekip deposudur: kurgusal bir ajansın veri ve performans ekibi, iki müşterisi, süreçleri, kararları ve gelen kutusu. Bir ekip liderine "deponuz şöyle görünebilir" demek, ya da ekip özelliklerini (TEAM KNOWLEDGE BASE işareti, güven seviyeleri, ana sayfada bekleyen işler) denemek için.

Bütün adlar, tarihler ve kararlar uydurmadır; Örnek Ajans, Kuzey Kahve ve Kuzey Mobilya gerçek değildir. Örnek 20 Ekim 2026'ya göre yazıldı: bayatlık (`stale_after`) bu tarihe göre ayarlı. 31 Mart 2027'den sonra Kuzey Kahve notu da bayat görünür ve aşağıdaki ilk cevaba "bayat" uyarısı eklenir.

```
ornek-ekip/
├── .carry/team.json          ekibin ortak Carry ayarları
├── 00_Index/Home.md          giriş sayfası
├── 10_Müşteriler/            Kuzey Kahve, ölçüm planı (taslak), Kuzey Mobilya
├── 20_Süreçler/              rapor akışı, kampanya kontrol listesi, reklam hesabı erişimleri
├── 30_Kararlar/              biri geçersiz (deprecated), biri onun yerine geçen
├── 90_Inbox/                 yeni gelenler; üçü birine bırakılmış, biri sahipsiz
└── 99_Admin/                 ekip listesi, bilgi bankası kuralları
```

## Bir hafta, bu depoyla

1. **Ekip lideri (Deniz)** depoyu GitHub'a koyar ve ekibe tek bir komut verir: `carry setup --team ornek-ajans/bilgi-bankasi`. Kurulum `.carry/team.json`'daki arama ayarlarını herkese aynı uygular; uygulamaya yalnızca `@ornek.com.tr` mailiyle girilir.
2. **Pazartesi sabahı Can**, ekip klasöründe Claude'a sorar: "Kuzey Kahve raporu ne zaman gidiyor, nasıl?" Cevap: Pazartesi 10:00, Looker Studio linki ve üç maddelik yorum. Asistan bunun ekibin bilgi bankasından geldiğini söyler ve `Kuzey Kahve.md` ile Haziran kararını kaynak gösterir. Perşembe 14:00 (Kuzey Mobilya'nın günü) ya da PDF (Mart'taki eski karar) demez.
3. **Ayşe** uygulamayı açar; ana sayfasında taslak duran kendi notu "Kuzey Kahve — Ölçüm Planı" bekler. Gözden geçirip `status: stable` ve `verified` yazınca not oradan düşer ve asistan artık "taslak" uyarısı yapmaz.
4. **Can**'ın ana sayfasında Deniz'in bıraktığı "Can için Kuzey Kahve kış kampanyası brifi", **Ercan**'ınkinde Can'ın bıraktığı "Ercan için banner boyutları" vardır. Ercan'a bırakılan not Can'a gitmez: ad tam kelime olarak eşleşir. İzne çıkacak Ayşe de Kuzey Mobilya'nın Ekim raporunu "Devir: Deniz" başlıklı bir notla Deniz'e bırakmıştır.
5. Biri "Kuzey Kahve Meta hesabına kim erişebiliyor?" diye sorar. Asistan tabloyu verir ama listenin 30 Eylül'de bayatladığını, Can'ın gözden geçirmesi gerektiğini de söyler.
6. "Kış kampanyasının bütçesi ne kadar?" sorusuna asistan rakam uyduramaz: depoda hiçbir tutar yok, brif bütçenin henüz onaylanmadığını söyler.
7. Ayşe bir sohbette teklif formunda çift kayıt bulur ve ekip klasöründeki asistandan bunu kurala uygun bir gelen kutusu notu olarak yazmasını ister (`YYYY-MM-DD — Konu.md`, `Kaynak:` satırı), sonra commit'ler. Başlıkta kimsenin adı olmadığından not kimseyi beklemez; ekip Pazartesi toplantısında kalıcı yerine taşır. `carry share` aynı biçimi kendisi yazar, ama ekip deposunun GitHub kaynağı olarak eklenmesini (`carry github add`) ve paylaşıma açılmasını ister; `setup --team`'in kurduğu yerel kopyayla çalışmaz.

## Denemek (GitHub'sız, birkaç saniye)

Model indirmesin diye kelime tabanlı arama (`hashing`) ile:

```sh
cp -R examples/team-kb/ornek-ekip /tmp/ornek-ekip
git -C /tmp/ornek-ekip init -q && git -C /tmp/ornek-ekip add -A && git -C /tmp/ornek-ekip commit -qm "Örnek ekip"
carry --workspace /tmp/ornek-ws init --embedding hashing
carry --workspace /tmp/ornek-ws source add --id ekip --root /tmp/ornek-ekip --scope team
carry --workspace /tmp/ornek-ws team --apply      # top_k = 8, ekibin ayarı
carry --workspace /tmp/ornek-ws index
carry --workspace /tmp/ornek-ws status            # okf: 15 dosya, conformant, invalid_status 0
carry --workspace /tmp/ornek-ws recall "Kuzey Kahve rapor Pazartesi"
```

Asistanla denemek için `carry --workspace /tmp/ornek-ws connect claude /tmp/ornek-ekip --language Turkish` ve Claude'u `/tmp/ornek-ekip`'te açın; ekip kaynağı olduğu için Carry aracı yalnızca ekibin notlarında arar (`--scope team`). `hashing` yalnızca deneme içindir; gerçek kurulum anlamına göre de arar ve sıralaması daha iyidir.

Gerçek bir ekipte: klasörün içeriğini bir GitHub deposuna koyun, üyeler `carry setup --team <sahip>/<depo>` çalıştırır.

## Sorular ve beklenen cevaplar

| Soru | Beklenen cevap | Gösterdiği |
|---|---|---|
| Kuzey Kahve haftalık raporu ne zaman ve nasıl gidiyor? | Pazartesi 10:00, Looker Studio linki ve üç maddelik yorum | insan onaylı (`human-reviewed`) not; Kuzey Mobilya tuzağı (Perşembe 14:00, PDF) |
| Kuzey Kahve raporları PDF olarak mı gidiyor? | Hayır; 15 Haziran 2026'dan beri panel linki | `deprecated` Mart kararı aramada çıkmaz, yalnızca `--history` ile |
| Kuzey Kahve'de dönüşüm olarak neyi sayıyoruz? | Online sipariş (`purchase`), ikincisi yol tarifi; **taslak** | `status: draft`, `trust: unverified` |
| Kuzey Kahve Meta hesabına kim erişebiliyor? | Yönetici Can, düzenleyici Ayşe, yalnız okuma Deniz; liste **bayat** | `stale_after: 2026-09-30` geçti, `stale: true` |
| Kış kampanyasının bütçesi ne kadar? | Depoda yok; brif bütçenin henüz onaylanmadığını söylüyor | depoda hiçbir tutar yok, asistan uydurmamalı |
| Kuzey Kahve'de kime ulaşmalıyım? | Selin Aras (onay, rapor), Murat Öz (site, ölçüm) | sıradan bir müşteri notu |

Bütün parçalar asistana `TEAM KNOWLEDGE BASE · …` diye işaretli gelir; asistan cevapta bunu söyler.

## Ana sayfada kimi ne bekler

`team.json`'daki `work` kuralları: `90_Inbox/` içinde başlığı "`<ad>` için" ya da "Devir: `<ad>`" olan notlar o kişiye bırakılmıştır; kişinin sahibi olduğu `draft` notlar onun gözden geçirmesini bekler.

| Üye | Bekleyen | Neden |
|---|---|---|
| Deniz Kaya | 2026-10-19 — Devir: Deniz — Kuzey Mobilya Ekim lead raporu | gelen kutusunda, başlıkta "Devir: Deniz"; sahibi olduğu notlar `stable` ya da `deprecated` olduğundan başka bir şey beklemez |
| Ayşe Demir | Kuzey Kahve — Ölçüm Planı | sahibi, `status: draft` |
| Can Yıldız | 2026-10-14 — Can için Kuzey Kahve kış kampanyası brifi | gelen kutusunda, başlıkta "Can için" |
| Ercan Şahin | 2026-10-16 — Ercan için banner boyutları | gelen kutusunda, başlıkta "Ercan için"; Can'a gitmez |

"2026-10-15 — Kuzey Mobilya teklif formu çift kayıt" kimseyi beklemez. `carry share`'in yazdığı biçimdedir (`Kaynak:` satırı, yalnızca onaylanan madde); frontmatter'ı burada elle eklendi.

## Ekip lideri için

- `review_statuses` için OKF'nin değerlerini kullanın (`draft`). `to-review` gibi bir değer ana sayfada çalışır ama `carry status` onu `invalid_status` sayar ve arama o notu `stable` okur; asistan notun gözden geçirilmediğini söylemez.
- Her `handed` kalıbında `{name}` olmalı; yoksa Carry kuralı uyarıyla yok sayar. Ad tam kelime olarak eşleşir; büyük-küçük harf, Türkçe harf farkı ve noktalama gözetilmez. Bu yüzden dosya adında `Devir Deniz` yazmak yeter; iki nokta Windows'ta dosya adında kullanılamaz.
- `description` aramada ilk görünen satırdır; notun cevabını içersin.
- Eski kararı silmeyin: `status: deprecated` ve `superseded_by` yazın. Arama onu tarihçe sayar.
- Zamanla eskiyen bilgiye (erişim listesi, takvim) `stale_after` yazın.
- Girişte önerilen adlar notların `owner:` alanlarından gelir. Ercan hiçbir notun sahibi olmadığı için listede yoktur; "Listede yok"u seçip adını kendisi yazar.
- Trello kullanıyorsanız `team.json`'a `trello_api_key` ekleyin (32 karakterlik anahtar); ayrıntı ana README'de.
