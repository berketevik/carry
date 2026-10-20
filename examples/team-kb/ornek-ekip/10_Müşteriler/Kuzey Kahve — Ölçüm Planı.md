---
type: plan
description: Kuzey Kahve'de birincil dönüşüm online sipariş (purchase), ikincisi yol tarifi tıklaması; atıf pencereleri ve UTM kuralı (taslak).
status: draft
owner: Ayşe Demir
generated: {"by": "claude-code", "at": "2026-10-13"}
---
# Kuzey Kahve — Ölçüm Planı

## Dönüşümler

- Birincil: online sipariş (`purchase`, GA4). Değeri sipariş tutarıdır.
- İkincil: "Yol tarifi al" tıklaması (`get_directions`), şube ziyaretinin yerine.
- Sayılmayan: sayfa görüntüleme ve sepete ekleme; yalnızca huni analizi için tutulur.

## Atıf

- Meta: 7 gün tıklama, 1 gün görüntüleme.
- Google Ads: veriye dayalı atıf, 30 günlük pencere.

## UTM kuralı

`utm_source` platformdur (meta, google), `utm_medium` cpc, `utm_campaign` `kk_<sezon>_<yıl>` (ör. `kk_kis_2026`).

## Açık soru

- Murat Öz'den, ödeme sayfasındaki çift `purchase` olayının düzeltildiği teyidi bekleniyor.
