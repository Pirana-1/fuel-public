# Yakıt Operasyon Sistemi

Sabit tank, mobil tanker ve saha araçları arasındaki motorin hareketlerini PostgreSQL üzerinde tutan Django uygulaması.

## Temel akış

```text
Tedarikçi → Sabit tank → Mobil tanker → Araç/makine
                    └────────────────→ Araç/makine
```

- Tedarikçi girişi toplam gelen motorine eklenir.
- İç transfer kaynak stoğu azaltır, hedef stoğu artırır; tüketim değildir.
- Sabit tank veya mobil tanker üzerinden araca verilen yakıt tüketimdir.
- Stok, hareket defterinden hesaplanır.

## İptal ve stok düzeltme

- Yakıt hareketleri silinmez. `change_fuelmovement` yetkisi olan kullanıcı, gerekçe yazarak hareketi iptal eder.
- Hatalı bir hareket **Düzelt** işlemiyle değiştirildiğinde eski kayıt denetim izi olarak kapanır ve yeni hareket eski kayda bağlanır. İki adım tek transaction içinde uygulanır.
- İptal edilen hareket stok ve rapor hesaplarından çıkar; kullanıcı, zaman ve gerekçe bilgisiyle hareket defterinde kalır.
- İptal sonucunda bir tank veya tanker stoğu eksiye düşecekse işlem reddedilir.
- Fiziksel sayım farkları yetkili **Stok düzeltmesi** ekranından artırma ya da azaltma olarak girilir; gelen yakıt veya araç tüketimi sayılmaz.
- Giriş ve transferlerde kapasite aşımı, çıkışlarda negatif stok ve araç dolumlarında kronolojik sayaç tutarsızlığı engellenir.

## Yönetim ve raporlar

- Bölge, tedarikçi, tank/tanker ve araç tanımları düzenlenebilir; hareket geçmişi ve stok güvenliği korunarak aktif/pasif yapılabilir.
- Uzun araç, tank ve tedarikçi listelerinde arama; liste ekranlarında durum filtresi ve sayfalama bulunur.
- Rapor ekranı dönem toplamlarının yanında bölge, stok noktası ve araç bazlı özetler üretir.
- Excel çıktısı `Özet`, `Hareketler`, `Araç Özeti`, `Bölge Özeti` ve `Stok Noktaları` sayfalarını içerir.
- Süper yöneticiler uygulama içinden kullanıcı oluşturabilir, rol atayabilir, hesabı pasife alabilir ve parola yenileyebilir.

## Yerel kurulum

Gereksinim: Docker Desktop ve Docker Compose.

1. `.env.example` dosyasını `.env` adıyla kopyalayın ve yerel parolaları değiştirin.
2. Uygulamayı oluşturup başlatın:

```powershell
docker compose up --build -d
```

3. Kullanıcı gruplarını oluşturun:

```powershell
docker compose exec web python manage.py setup_roles
```

4. İlk yöneticiyi oluşturun:

```powershell
docker compose exec web python manage.py createsuperuser
```

Uygulama: `http://127.0.0.1:8000`

Sağlık kontrolü: `http://127.0.0.1:8000/health/`

## Testler

```powershell
docker compose run --rm web python manage.py test --noinput
```

`--noinput`, yarıda kalan bir çalıştırmadan kalan `test_akaryakit` veritabanını soru sormadan siler.

## Sürekli tümleştirme

`.github/workflows/ci.yml` her push ve pull request'te Python 3.13 ve PostgreSQL 17 servisiyle şu adımları çalıştırır:

- `python manage.py makemigrations --check --dry-run` (model ve migration tutarlılığı)
- `python manage.py check`
- `python manage.py test`

## Yedekleme ve geri yükleme

PostgreSQL yedeği `backups/` klasörüne sıkıştırılmış dump olarak alınır:

```powershell
.\scripts\backup.ps1
```

Geri yükleme mevcut veritabanı nesnelerini değiştiren yetkili bir işlemdir. Yalnızca doğrulanmış bir yedekle ve açık onay parametresiyle çalışır:

```powershell
.\scripts\restore.ps1 -BackupFile .\backups\akaryakit-YYYYMMDD-HHMMSS.dump -ConfirmRestore
```

Geri yükleme öncesinde ayrıca güncel bir yedek alınması önerilir. `backups/` içeriği sürüm kontrolüne alınmaz.

## Sık kullanılan komutlar

```powershell
docker compose logs -f web
docker compose exec web python manage.py migrate
docker compose exec web python manage.py collectstatic --noinput
docker compose down
```

`docker compose down` veritabanı volume'ünü silmez. Verilerin silinmesi istenmiyorsa `-v` seçeneği kullanılmamalıdır.

## Güvenlik notları

- Gerçek parola ve anahtarlar `.env` içinde tutulur; `.env` sürüm kontrolüne alınmaz.
- `DEBUG=0` iken `SECRET_KEY` zorunludur; eksikse uygulama başlamaz (fail-fast).
- Üretimde HTTPS sertleştirme otomatik açılır; ters proxy sonlandırması varsa `.env`'e `SECURE_SSL_REDIRECT=0` yazın.
- Harici akaryakıt platformu entegrasyonu (API erişimi, tarayıcı otomasyonu veya dış sistem veritabanı erişimi) MVP kapsamı dışındadır.
- Excel dosyaları yalnızca araç/makine ana verisi içe aktarmada okunur girdidir; depoda tutulmaz ve uygulama tarafından değiştirilmez.

## Lisans

Bu proje **GNU Affero General Public License v3.0 veya sonrası** (AGPL-3.0-or-later) koşullarıyla dağıtılır. Tam metin: [LICENSE](LICENSE).

Copyright (C) 2026 Pirana-1

Bu program özgür yazılımdır: GNU Affero Genel Kamu Lisansı koşulları altında yeniden dağıtabilir ve/veya değiştirebilirsiniz. Program, satılabilirlik veya belirli bir amaca uygunluk garantisi dahi verilmeksizin "olduğu gibi" dağıtılır; ayrıntı için lisans metnine bakın.

AGPL'in 13. maddesi gereği, bu yazılımı değiştirip bir ağ hizmeti olarak sunan taraflar, kullanıcılara değiştirilmiş sürümün kaynak kodunu sunmak zorundadır.
