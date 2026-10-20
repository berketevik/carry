## <img src="assets/carry-icon.png" width="96" alt="Carry simgesi">

🇬🇧 [English](README.md) · 🤖 Carry'yi kuran ya da anlatan asistanlar: önce [AGENTS.md](AGENTS.md) dosyasını okusun.

**Carry, Claude Code'u ve Codex'i bilgi bankanıza bağlar. Cevap vermeden önce orada ararlar ve her cevabın hangi nottan geldiğini görürsünüz.**

Bilgi bankası, Markdown (`.md`) notlarından oluşan bir klasördür: sizin notlarınız (örneğin bir Obsidian vault'u), ekibinizin GitHub'daki notları ya da ikisi. Carry onu bilgisayarınızda dizinler ve asistanınıza onda arama yapacağı tek bir araç verir: `carry_recall`. Eylülde bir müşterinizin user ID'lerinin hatalı olduğunu bulup not alıyorsunuz. Kasımda onları düzeltmeye oturup asistanınıza soruyorsunuz: bilgi bankasında arar, ne bulduğunuzu söyler ve yazdığınız notun bağlantısını verir. Notlarınız olduğu yerde kalır; Carry onları okur, siz onaylamadan hiçbir şey yazılmaz.

**Sürüm 0.16.0 · macOS 14+ · pilot**

## Başlayın

Claude Code'a ya da Codex'e bu sayfanın linkini verip "Carry'yi kur" deyin. Gerekenleri kurar ve bitince ne yapacağınızı söyler.

Ya da kendiniz kurun (git, [uv](https://docs.astral.sh/uv/) ve `xcode-select --install` gerekir):

```sh
uv tool install "git+https://github.com/berketevik/carry"
carry setup
```

Kurulum iki şey sorar: hangi bilgi bankasının bağlanacağını (kendi notlarınız, ekibinizinki ya da ikisi) ve her biri için hazırdakinin mi bağlanacağını yoksa sıfırdan mı kurulacağını. Sonra Claude Code'u (ya da Codex'i) kurulumun söylediği klasörde açıp sorun: "Notlarımda ne var?" Henüz notunuz yok mu? Kurulum, asistanınızın okuyup yazabileceği yeni bir not klasörü de oluşturabilir.

**Ekip liderleri:** ekip arkadaşlarınıza tek bir komut verin: `carry setup --team sahip/depo` (ekibinizin Markdown notlarını tutan GitHub deposu). Yalnızca ekibin notlarını isteyen kurulumda onu seçer; başka hiçbir şey eklenmez.

## Nasıl çalışır?

![Carry nasıl çalışır: Carry bilgi bankanızı bilgisayarınızda dizinler; asistanınız carry_recall ile kelimeyle ve anlamla arar, cevaplayan parçaları seçer ve cevabın her kısmının hangi nottan geldiğini gösterir; isteğe bağlı olarak sohbetler ve toplantılar onaylayacağınız taslaklara dönüşür](assets/how-it-works.tr.svg)

1. **Carry bilgi bankanıza bağlanır.** Notları okur, kısa parçalara böler ve bilgisayarınızda bir arama dizini tutar: kelimelere ve anlamına göre. Dizin, yaptığınız değişiklikleri kendiliğinden izler. Anlam modeli (yaklaşık 330 MB) Carry'nin içinde, Mac'inizin GPU'sunda çalışır; bilgisayarınızda [Ollama](https://ollama.com) varsa Carry onu kullanır.
2. **Asistanınız cevap vermeden önce orada arar.** Claude Code ve Codex, Carry'nin arama aracını (`carry_recall`) alır ve soru işinizle, kararlarınızla ya da projelerinizle ilgili olduğunda onu kullanır. Carry eşleşen parçaları kaynaklarıyla döndürür; asistanınız yalnızca soruyu gerçekten cevaplayanları tutar. “Geçen hafta” ya da “eylülde” gibi ifadeler aramayı o döneme daraltır.
3. **Bilgi bankasından geleni görürsünüz.** Her arama sohbette bir Carry çağrısı olarak görünür ve Carry asistandan her bilginin dayandığı notu göstermesini ister; böylece neyin bilgi bankanızdan geldiğini, neyi asistanın eklediğini ayırt edersiniz. Ekibinizin bilgi bankasından gelen parçalar ayrıca işaretlenir. Hiçbir şey eşleşmezse asistana tahmin yürütmek yerine bunu söylemesi bildirilir. Claude Code'da her aramayı ve asistanın kaydettiği her notu önce Claude Code'un kendi onay penceresinde siz onaylarsınız (bir cihazda kapatmak için `carry confirm off`).

Notlar, Google'ın [Open Knowledge Format](https://github.com/GoogleCloudPlatform/open-knowledge-format) standardında (OKF v0.2) düz Markdown dosyalarıdır: frontmatter notun ne olduğunu (`type`), sizin kontrol edip etmediğinizi (`status`, `verified`), neye dayandığını (`sources`) ve ne zaman eskiyeceğini (`stale_after`) söyler. Carry bunları her parçanın yanında gösterir; uygulamada bir taslağı onayladığınızda kontrolünüz nota yazılır.

**İsteğe bağlı ekler.**

- **Yeni bir not klasörü.** Henüz notunuz yok mu? Carry, asistanınız için hazırlanmış bir not klasörü oluşturabilir.
- **Her sohbet bir özetle açılır ve onayladığınız bir oturum özetiyle biter.** Açılış özeti son kararları ve yarım kalan işleri listeler. Sohbet bitince Carry kararları ve açık işleri not klasörünüzdeki `+/` gelen kutusuna taslak olarak yazar (`carry session end`; asistanın sohbet sonu hook'u çalıştırır). Uzun bir sohbet, Claude Code ya da Codex bağlamı sıkıştırmadan önce de özetlenir (`carry session checkpoint`); sohbet sonu yalnız sonrasını özetler. Önceden sahip olduğunuz bir not klasörü için bu hook'ları bu Mac'te `carry session hooks --install` açar. Carry uygulaması her özeti sohbetin adıyla listeler, her maddeyi bir soru olarak sorar (şöyle konuşmuştunuz, bu yüzden şunu kaydediyorum, doğru mu?); siz kabul eder, düzeltir, reddeder ya da pas geçersiniz. Kabul edilenler o günün log'una yazılır, pas geçilenler oraya "şüpheli" işaretiyle yazılır, reddedilenler hiçbir yere yazılmaz; siz onaylamadan hiçbir şey kaydedilmez.
- **Toplantılar da nota dönüşür.** Uygulamadaki Toplantılar sayfasından ya da bir görüşme mikrofonu açtığında menü çubuğundan Carry toplantıyı kaydeder ve konuşulanı o anda yazıya döker: mikrofonunuz ve macOS 14.2 ve sonrasında görüşmenin sesi, Mac'inizde yazıya dökülür. Durdur'a basınca transkript `sources/carry/meet/` altına kaydedilir. Apple Silicon bir Mac'te ardından Whisper (large-v3-turbo, bir kez yaklaşık 875 MB indirilir) her iki tarafın sesini Mac'inizin GPU'sunda yeniden ve daha doğru yazıya döker; mikrofon bir görüşmede açıkken bekler, bitince ses silinir. Sonra bağlı asistanınız toplantıyı bir kez özetler; kararlar, açık işler ve bilgiler söylendikleri satırla birlikte oturum özetleri gibi İncele'ye gelir. Bağlı asistan yoksa transkript olduğu gibi kalır. Başlıksız başlatılan bir toplantıya özetinden konusuyla ad verilir; kayıtta yazdığınız ya da sonradan değiştirdiğiniz başlık olduğu gibi kalır.
- **Ekiple, ana sayfa kendi işlerinizi gösterir.** Bir ekibin bilgi bankası bağlıyken uygulama bir kez şirket mailinizle giriş yapmanızı ister; mail bu Mac'te kalır, parola ve sunucu yoktur. Ana sayfa sonra açık Trello kartlarınızı (yalnız okuma), ekibin kurallarına göre sizi bekleyen ekip notlarını ve İncele'de kabul ettiğiniz açık işleri, bitti diyene kadar listeler. Trello ekibin anahtarıyla tek tıkla bağlanır.

## Verileriniz

Carry notlarınızı hiçbir yere yüklemez ve kendine ait bir çevrimiçi hizmet kullanmaz. Asistanınızın okuduğu parçalar yalnızca o asistana gider (kendi hesabınızla Claude Code ya da Codex); oturum özetleri de onun üzerinden çıkarılır. Toplantılar Mac'inizde yazıya dökülür: canlı olarak Apple'ın tanıyıcısıyla (Dikte'nin açık olması gerekir), sonra Whisper ile. Ses yalnız Whisper yazıya dökene kadar Carry'nin çalışma klasöründe kalır, Mac'inizden çıkmaz, ardından silinir. Özet için bağlı asistanınıza yalnızca transkript gider; bağlı asistan yoksa toplantıdan hiçbir şey Mac'inizden çıkmaz.

<details>
<summary><b>Sonra gerekirse</b></summary>

| Ne için | Komut |
|---|---|
| Ekibinizin bilgi bankasını sonradan eklemek | `carry setup --team sahip/depo` |
| Eski bir kurulumun dosyalarını not klasörüne taşımak (aşağıda; `--dry-run` her adımı gösterir) | `carry state move --vault <not klasörü>` |
| İsteğe bağlı, standardın parçası değil: büyük bir ekip deposunun vektörlerini depoyla göndermek, böylece ekip arkadaşları yeniden hesaplamaz (klonda çalıştırıp commit ve push edin) | `carry github pack --id team --out <klon>/.carry/index.db` |
| Sohbet sonu hook'unun kaçırdığı olursa diye her akşam 21:30'da biten sohbetleri de özetlemek | `carry session schedule --install` |
| Anlamına göre aramayı açıp kapatmak | `carry search --semantic on` (ya da `off`) |
| Onayladığınız taslak ve önerilerdeki adı değiştirmek (`verified: human:<ad>`; kurulum giriş adınızı kullanır) | `carry owner --set <ad>` |
| Claude Code'un her aramadan ve kaydettiği her nottan önce sormasını kapatmak (ya da yeniden açmak) | `carry confirm off` (ya da `on`) |
| Trello'yu komut satırından kendi API anahtarınızla bağlamak, kartlarınızı listelemek ya da bağlantıyı kesmek (uygulama ekibin anahtarıyla tek tıkla bağlar) | `carry trello connect` (ya da `cards`, `disconnect`) |
| Carry toplantılara konusuyla ad vermeden önce kaydedilmiş bir toplantıya ad vermek; asistanınıza yalnız özeti gider | `carry meet retitle --path <toplantı notu>` |
| Carry'yi güncellemek | `carry update`, ardından `carry app install` |
| Her şeyin çalıştığını kontrol etmek | `carry status --probe` |

Not klasörünün içinde her komut Carry'nin dosyalarını kendisi bulur; başka bir yerden `carry --workspace <not klasörü>/.carry/state …` ya da `export CARRY_WORKSPACE=…` ile gösterin. Diğerleri için `carry <komut> --help`.

</details>

<details>
<summary><b>Carry dosyalarını nerede tutar</b></summary>

Carry'nin bir not klasörü için tuttuğu her şey o klasörün `.carry/` klasöründedir; her cihazda ve ekibin her üyesinde aynıdır:

| | Ne | Git'te |
|---|---|---|
| `.carry/settings.json` | her cihazın aynı aramak zorunda olduğu şeyler: özetlerin dili, aramanın dışında kalan klasörler, sıralama ayarları, embedding modeli | commit edilir |
| `.carry/vault.json` | şablon damgası (Carry'nin yazdığı dosyalar) | commit edilir |
| `.carry/.gitignore` | Carry yazar: yalnız yukarıdakiler commit edilir | commit edilir |
| `.carry/state/` | bu cihazın: arama dizini, kilitler, durum, özet durumu, GitHub kopyaları, toplantı dosyaları, sahip kimliğiniz | asla |
| `.carry/local.json` | bu cihazın durumuna giden işaretçi | asla |

Bir cihazda yapılan değişiklik (örneğin `carry search --semantic off`) `settings.json`'a yazılır; klasör eşitlenince diğer cihazlar da onunla arar. Sağlayıcı (Ollama ya da Carry'nin kendi modeli), adresi ve bütün yollar cihaza özel kalır. Standartta vektör paketi yok: her cihaz dizinini notlardan kendisi kurar, bir kerelik birkaç dakika.

Not klasörü iCloud Drive'da, bir bulut depolama klasöründe (OneDrive, Dropbox, Google Drive) ya da `~/Dropbox` içindeyse `state/` dışarıda, `~/CarryState` içinde kalır: sürekli yazılan bir veritabanı dosya eşitlemesine dayanmaz. `settings.json` yine klasöre yazılır.

Eski bir kurulum durumunu `~/CarryState` içinde tutar. `carry state move --vault <not klasörü>` önce `.carry/.gitignore`'u yazar (git klasöründe cihaz dosyalarının gerçekten yok sayıldığını kontrol eder), hiçbir dizin kurulumu çalışmıyorken durumu `.carry/state`'e taşır, eski yolda bir sembolik bağ bırakır (o yolu gösteren her hook, MCP ayarı ve zamanlanmış iş çalışmaya devam eder), `settings.json`'ı yazar (başka bir cihaz önce taşındıysa onu benimser) ve Carry'nin kendi bağlantılarını yeni yere yöneltir. `--shared-only` yalnız `.gitignore` ile `settings.json`'ı yazar; cihazlar taşınmadan önce commit etmek için.

</details>

<details>
<summary><b>Ekip liderleri için: ekibin ayarları</b></summary>

Ekibin deposunda `.carry/team.json` bulunabilir. Her üyenin Carry'si onu okur; bilinmeyen bir anahtarı ya da hatalı bir değeri `carry status` bildirir, hiçbir şeyi bozmaz.

| Anahtar | Ne söyler |
|---|---|
| `retrieval` | her üyenin kullanması gereken arama ayarları; kurulum uygular, sonradan `carry team --apply` |
| `min_carry_version` | depoda aynı şekilde arayan en eski Carry sürümü |
| `email_domains` | uygulamadaki girişin kabul ettiği mail alan adları, örneğin `["ornek.com.tr"]` (yoksa her mail) |
| `trello_api_key` | ekibin Trello API anahtarı; üyeler Trello'yu tek tıkla bağlar. Anahtar gizli değildir; her üyenin token'ı gizlidir ve kendi Anahtar Zinciri'nde kalır |
| `work` | ana sayfada hangi ekip notlarının bir üyeyi beklediği: `review_statuses`, üyenin sahibi olduğu notlardan onu bekleyenlerin durumları; `inbox` ve `handed`, bir notu üyeye devreden klasör ve başlık kalıpları (`{name}` üyenin adıdır). Yoksa hiçbir ekip notu kimseyi beklemez |

```json
{
  "email_domains": ["ornek.com.tr"],
  "trello_api_key": "<ekibin anahtarı>",
  "work": {"inbox": "90_Inbox", "handed": ["{name} için"], "review_statuses": ["to-review"]}
}
```

Trello anahtarı varsa, anahtarın Trello Power-Up yönetim sayfasında (trello.com/power-ups/admin) izin verilen adreslere bir kez `http://localhost:47821` ekleyin. Trello üyenin tarayıcısını izniyle bu adrese geri gönderir; ekli değilse tek tıkla bağlantı çalışmaz.

</details>

<details>
<summary><b>Asistanlar için (MCP)</b></summary>

Claude Code veya Codex, Carry'nin yerel sunucusunu (`carry.mcp_server`) başlatır. Üç araç var: `carry_recall` (kaynaklı arama), `carry_catalog` (dosya listesi) ve `carry_status` (dizin durumu). O asistana düzeltme izni verirseniz `carry_propose` da gelir (düzeltme taslağı; yalnız siz kabul edebilirsiniz). Cevaplayan parçaları `carry-recall` yardımcı ajanı seçer (Claude Code'da Claude Sonnet). Bulunan metin talimat değil, veridir. Ekibin klasöründe sunucu yalnızca ekibin bilgi bankasında arar (`--scope team`); ekip notu yazan bir sohbet sizin notlarınızı okumaz. Eski bir Carry ile bağlanmış ekip klasörü, orada `carry connect` ya da kurulum bir sonraki çalıştığında geçer.

</details>

<details>
<summary><b>Geliştiriciler için</b></summary>

```sh
uv venv --python 3.13 .venv
uv pip install --python .venv/bin/python -e .
.venv/bin/python -m unittest discover -s tests -q
```

Python 3.11+ ister. Anlamına göre arama `llama-cpp-python` (llama.cpp; kurulumda kaynaktan derlenir, bu yüzden Xcode Command Line Tools gerekir; Windows'a kurulmaz, orada Ollama ya da kelime araması kullanılır) ve NumPy kullanır. Ek paket: `.[yaml]` (PyYAML). Asıl veri notlardır; dizin her zaman yeniden üretilebilir.

</details>

<details>
<summary><b>Sınırlar</b></summary>

- **Pilot:** uygulama Mac'inizde derlenir ve ad-hoc imzalanır; noter onaylı sürüm yoktur.
- Claude Code ve Codex ile çalışır. Yalnız `.md` dosyalarını okur.
- Arama ve oturum özetleri eksik ya da yanlış olabilir; taslakları kaynaklarıyla kontrol edin.
- `carry session` 0.14'ten önce `carry harvest` idi (`carry session start` da `carry context`); eski adlar çalışmaya devam eder, önceden yazılan özetler `— harvest —` dosya adlarını korur.
- Toplantı transkripti yalnızca sizi karşı taraftan ayırır, her konuşmacıyı ayrı ayrı göstermez. Hoparlörle görüşürken mikrofonunuz görüşmenin sesini de duyar; Carry bu yankıları ayıklar ama en temiz transkript kulaklıkla çıkar.
- Apple'ın cihazdaki modeli bir toplantıyı yalnızca siz seçerseniz özetler (`carry meet summarize --path … --extractor apple:on-device`); bağlı asistan yoksa transkript olduğu gibi kalır. Claude ya da Codex'ten daha az madde yakalar ve maddeleri notlarınızla karşılaştırmaz; her madde yine alıntısıyla gelir, İncele'de kontrol edin.
- Gizli bilgi maskeleme kusursuz değildir.

</details>

<sub>**Berke Tevik** tarafından geliştirildi. Ekip bilgi tabanı kuralları: **İsmail Aykut**.</sub>
