---
type: guide
description: Bu bilgi bankasına not yazma kuralları; klasörler, frontmatter, durumlar ve gelen kutusu.
status: stable
owner: Deniz Kaya
verified: {"by": "human:deniz.kaya", "at": "2026-09-01"}
---
# Bilgi Bankası Kuralları

## Ne buraya yazılır

Ekipte birden fazla kişinin bilmesi gereken ve bir aydan uzun yaşayacak bilgi: müşteri kapsamı, süreçler, kararlar, ölçüm kuralları.

Yazılmaz: parolalar ve kurtarma kodları, kişisel veriler (müşterinin müşterileri), sözleşme metinleri, günlük iş takibi (o Trello'da).

## Yeni bir şey nereye

Yeni her şey önce `90_Inbox/` klasörüne, `YYYY-MM-DD — Konu.md` adıyla ve bir `Kaynak:` satırıyla girer. Pazartesi toplantısında ekip onu kalıcı klasörüne taşır ya da siler.

Bir işi birine bırakıyorsan başlığa `<Ad> için` ya da `Devir: <Ad>` yaz (ör. "Can için kış kampanyası brifi"). Not o kişinin Carry ana sayfasında belirir; yalnızca adın tamamı eşleşir, "Can" yazan not Ercan'a gitmez. Noktalama sayılmaz: dosya adında `Devir Can` yazmak yeter (iki nokta Windows'ta dosya adında kullanılamaz), notun başlığında `Devir: Can` yazabilirsin.

## Frontmatter

```yaml
---
type: client | process | decision | reference | plan | guide | note
description: Tek cümle; aramada ilk görünen satır, notun cevabını içersin.
status: draft | stable | deprecated
owner: Ad Soyad
verified: {"by": "human:ad.soyad", "at": "YYYY-MM-DD"}
stale_after: YYYY-MM-DD
---
```

- `draft`: henüz gözden geçirilmedi. Sahibinin ana sayfasında bekler; asistan bu nottan cevap verirken taslak olduğunu söyler.
- `stable`: gözden geçirildi. `verified` gözden geçireni ve tarihi yazar.
- `deprecated`: eski karar. Silinmez, `superseded_by` yerine geçen notu gösterir; aramada varsayılan olarak çıkmaz.
- `stale_after`: zamanla eskiyen bilgide (erişim listesi, takvim) yazılır. Bu tarihten sonra Carry notu bayat diye işaretler; sahibi gözden geçirip tarihi ileri alır.

## Commit

Gelen kutusuna eklenen: `docs(inbox): <konu>`. Kalıcı yerine taşınan: `docs: <konu> → <klasör>`.
