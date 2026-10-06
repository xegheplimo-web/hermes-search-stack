# Playbook: Dữ liệu kinh doanh / cửa hàng / doanh nghiệp / địa danh Việt Nam

Cập nhật: 2026-10-06 (v2 — R4-C xong; thêm trạng thái tài khoản Goong).
Mục đích: cách lấy + **cập nhật** dữ liệu địa điểm kinh doanh, doanh nghiệp, địa danh VN cho search stack.
Trạng thái toolkit: `vn_geo/` (Round 4 — **đã xong**: admin_units, overpass_poi, places, enterprises; 102/102 tests).

**Định hướng (Sếp chốt 06/10): CHỈ CẦN VIỆT NAM.** Nguồn ưu tiên 100% VN: **Goong · Vietmap · Map4D · CKAN tỉnh · provinces.open-api.vn · masothue · Foody · Vpostcode**. Google Maps Platform (API trả phí) = loại (chặn vùng VN). Google Maps consumer qua browser (maps.google.com) **vẫn giữ** — đó là công cụ xem dữ liệu VN đang dùng, không phải GMP. OSM chỉ là nguồn nền bổ trợ.

## 1. Bức tranh nguồn dữ liệu (đã kiểm chứng qua nghiên cứu 2026-10)

### 1a. Địa điểm / POI / quán ăn (live lookup)

| Nguồn | Dữ liệu | Truy cập | Chi phí | Ghi chú |
|---|---|---|---|---|
| **Google Maps** (browser CDP — đang dùng) | Đầy đủ nhất cho VN: tên, địa chỉ, ⭐rating + số review, giờ, SĐT, website, plus code | `browser_exec` (đã có quy trình `local-place-scan`) | Free | ToS xám — dùng cá nhân, không redistribute |
| **Google Places API (New)** | ❌ **KHÔNG khả dụng cho tài khoản VN** — verify 06/10/2026: console chặn enable với lỗi "Maps Platform unavailable in this region — cannot be billed to accounts located in prohibited territories"; trang ToS hiện hành của Google liệt kê **Việt Nam** trong Prohibited Territories (cùng TQ, Cuba, Iran, Triều Tiên, Crimea, Donetsk/Luhansk) | — | — | **Đừng tính vào pipeline.** Không phải lỗi cấu hình — Google chặn theo vùng cho billing VN. Dữ liệu Google chỉ còn đường consumer Maps scraping (browser CDP — đang dùng). Giải pháp thay thế: **Goong / Vietmap / Map4D** |
| **Goong** (iMap VN) | Search/autocomplete/place detail/reverse — dữ liệu VN | API key (đăng ký free) | **Free 30k req/tháng** (1000/ngày) + $100 bonus một lần; SingleSearch+PlaceDetail ~$7/1k sau đó | Claim: phủ vùng ven/nông thôn tốt hơn Google Maps |
| **Vietmap** | 9.36M POI · 18.1M địa chỉ · cập nhật **hàng tuần** · 34 tỉnh đến cấp thôn/ấp · địa chỉ cũ+mới | API key (đăng ký free) | **Free 60k transactions** (2 tháng, không cần thẻ); sau đó ~50đ/trans (~$2/1k) | Dữ liệu VN tự xây từ 2006 — mạnh nhất về địa chỉ/đường |
| **OSM / Overpass** | POI nền (yếu cho quán nhỏ VN), đường + ranh giới tốt | Free API (mirror) / Geofabrik extract | Free (ODbL — ghi nguồn) | Nguồn bulk offline; **rỗng ở nông thôn là bình thường** (verify 06/10: bbox Yên Dũng `food` → **0 kết quả**, mirror khỏe — xác nhận cần Google/Goong cho quán nhỏ) |
| **Foody.vn** | Quán ăn + review (rating /10, review count, SĐT, GPS) | Scrape (gray) — có sẵn scraper Apify/mã nguồn mở | Free | Verify 06/10: listing pages **server-rendered, 200, không cần login**, ~20 `result-item`/trang, không có robots.txt (trang HTML "not found"); **không có tọa độ trong listing** → chi tiết mới là vùng xám. Dùng: enrichment F&B, listing-only, ≥2s/trang |

### 1b. Doanh nghiệp (tên, MST, địa chỉ, ngành, người đại diện)

| Nguồn | Truy cập | Ghi chú |
|---|---|---|
| **masothue.com** | Scrape ✅ — robots.txt: `Allow: /`, chỉ cấm `/Ajax/*` | ~2M DN (mã số, tên, địa chỉ, người đại diện, tình trạng). `/Search/?q=` redirect bot → dùng **trang index** `/tra-cuu-ma-so-thue-theo-tinh/` (table server-rendered + JSON-LD, parse bằng stdlib). Không thấy rate-limit. Gray nhưng robots cho phép listing |
| **data.gov.vn** | ❌ DNS không resolve từ máy này (verify 06/10, cả nslookup) — **thử lại từ mạng khác** | Bù lại: **CKAN tỉnh** (cùng phần mềm): `data.haiphong.gov.vn` — danh sách DN thành lập mới/giải thể **hàng tháng** (XLSX, DataStore API); `data.tayninh.gov.vn` — danh sách DN **có cột lat/lng** |
| **CKAN tỉnh** ⭐ | ✅ **VERIFIED WORKING 06/10**: `datastore_search` JSON API pull trực tiếp được — Hải Phòng **1.429 DN** (mã số, tên, địa chỉ, vốn, SĐT, người đại diện) + Tây Ninh **198 DN** (kèm tọa độ) | Free, open data — **nguồn doanh nghiệp tốt nhất cho bulk + refresh hàng tháng**; delta theo tháng → khớp versioning/diff. Quirk: cột "Latitude" của Tây Ninh chứa giá trị longitude |
| **dichvuthongtin.dkkd.gov.vn** | Tra cứu công khai **free, không cần account**; từng record sau **reCAPTCHA** → chỉ tra tay | Bản ghi sâu (chứng nhận, lịch sử, BCTC) là sản phẩm trả phí (~20k–150kđ). API máy chỉ cơ quan nhà nước |
| **NBRS/NDXP (chính thức)** | **CHỈ cơ quan nhà nước** (kết nối CPNET, công văn) | Cá nhân/tổ chức tư không dùng được — đừng tính vào pipeline |
| VietnamCredit · VIRAC · FiinGroup | Thương mại (trả phí) | Khi cần dữ liệu tài chính/tín dụng chuyên sâu |

### 1c. Địa danh / hành chính / địa chỉ (2025 — 34 tỉnh, 2 cấp, bỏ huyện)

| Nguồn | Truy cập | Ghi chú |
|---|---|---|
| **provinces.open-api.vn** | Free API — `/api/v2/` (34 tỉnh hậu sáp nhập) + `/api/v1/` (63 tỉnh cũ) | Nguồn chính cho địa danh — đã verify sống từ máy này |
| **open-admin-data/vietnam-administrative-divisions** | GitHub, CC-BY-4.0 | 34 tỉnh + 3.321 phường/xã + tọa độ, JSON/CSV/NDJSON |
| **thanglequoc/vietnamese-provinces-database** | GitHub, SQL + GIS | Kèm **GeoJSON ranh giới** (PostGIS/MySQL/SQLServer), dựa trên GSO |
| **daohoangson/dvhcvn** | GitHub | Dataset 3 cấp cũ (đối chiếu lịch sử) |
| **VNSDI** (Cục Đo đạc, Bản đồ — Bộ NN&MT) | Cổng thông tin không gian địa lý quốc gia | Danh mục địa danh + địa giới chính thức; truy cập cần nghiên cứu thêm (portal cấp tổ chức) |
| **Vpostcode** (Vietnam Post) | Web free; API cho tổ chức (liên hệ) | 23.4M địa chỉ + mã 12 ký tự; chuẩn hóa địa chỉ cũ/mới |
| **Vietmap Convert Address** | API (free tier) | Chuyển đổi địa chỉ cũ ↔ mới theo chuẩn hành chính 2025 |

### 1d. Công cụ scrape sẵn (không cần viết lại)

| Tool | Ghi chú |
|---|---|
| `gosom/google-maps-scraper` | Go/Docker, ~120 places/phút, xuất CSV/JSON/Postgres; có Web UI + REST API; extract cả email từ website DN |
| `local-place-scan` (skill của em) | Quy trình browser CDP đã verify — giữ làm live-lookup chính |
| Apify `haketa/foody-scraper` | Foody theo thành phố, không cần login (theo mô tả) |

## 2. Kiến trúc đề xuất (cho stack hiện tại)

```
LIVE LOOKUP (khi user hỏi "quán ăn gần X"):
  1. maps skill (OSM) — rẻ, thử trước
  2. Google Maps qua browser CDP — chính (đã có quy trình)
  3. [khi có key] Goong/Vietmap API — phủ nông thôn + tốc độ + structured
        ↓ lưu kết quả
BULK / REFRESH (định kỳ):
  - OSM Overpass theo bbox (vn_geo.overpass_poi) → SearchStore
  - Địa danh: provinces.open-api.vn v1+v2 (vn_geo.admin_units) → SearchStore
  - Foody crawl theo khu vực → JSONL → vn_geo.places
  - [phase 2] Geofabrik Vietnam extract → POI toàn quốc offline
        ↓
STORAGE: SearchStore (1 file SQLite)
  - documents: place / admin / poi (provider + format tag)
  - versioning append-only → MỖI LẦN QUÉT LẠI = VERSION MỚI nếu có thay đổi
  - events: places_saved (danh sách key mỗi batch) → diff được mới/đóng/đổi
        ↓
QUERY: FTS tiếng Việt (remove_diacritics 2) — "quán ăn Yên Dũng", "phở"
       + vector tier (sqlite-vec) khi cần tìm ngữ nghĩa
        ↓
REFRESH: cron (Hermes) quét lại khu vực đang theo dõi → diff report:
  mới xuất hiện / biến mất / đổi rating / đổi giờ
```

**Cơ chế "cập nhật" cốt lõi**: searchstore versioning + events (đã build ở Round 3) —
quét lại khu vực định kỳ, so sánh version cũ/mới → biết ngay quán mới mở, quán đóng, đổi thông tin.

## 3. Quyết định cần Sếp (đều có free tier — thứ tự đề xuất theo R4-C: tính theo "đồng/độ sâu dữ liệu")

1. **Vietmap** — test trước: 60k transactions free, 9.36M POI VN-native, địa chỉ cũ+mới → đo chất lượng nông thôn (Bắc Ninh quê mình) là rõ nhất. **Nên test.**
2. **Goong** — ✅ **đã đăng ký 06/10/2026** (qua Google — `xegheplimo@gmail.com`): dashboard đã có **$100 + free tier** (1.000 req/ngày, 120k map loads/tháng). ⏳ **Tạo key đang bị chặn** — nút "Tạo Key" báo *"Bạn chỉ có thể tạo key khi được admin kích hoạt"*. **Chính sách mới từ 04/2026**: Goong **bỏ tự động kích hoạt sau 24h**, **SĐT bắt buộc khi đăng ký**, muốn kích hoạt phải **liên hệ trực tiếp Hỗ trợ Khách hàng** (SĐT liên hệ phải trùng SĐT đã đăng ký). Hotline: **0869 697 502** (kỹ thuật) · **0904 522 538** (kế toán). → **Sếp gọi/Zalo hotline để kích hoạt** (nói: tài khoản đăng ký qua Google bằng email xegheplimo@gmail.com, cung cấp SĐT của Sếp); sau khi active → em tạo key (lưu file local, không vào repo/chat) + test live Bắc Ninh + viết `vn_geo/goong.py`.
3. ~~Google Places API~~ — ❌ **đã loại**: Google chặn Maps Platform cho tài khoản VN (prohibited territory — verify 06/10/2026 bằng console + ToS). Không phải lựa chọn cho pipeline này. (Cùng nhóm thay thế VN: Map4D.)

## 4. Roadmap phase 2 (sau Round 4)

- [ ] Bulk OSM Geofabrik extract → POI toàn quốc (chạy 1 lần/tháng)
- [ ] Vpostcode API (liên hệ Vietnam Post) — chuẩn hóa địa chỉ
- [ ] gosom scraper (Docker) — batch GMaps lớn khi cần (vd: quét cả tỉnh)
- [ ] Auto-refresh cron: khu vực theo dõi (Yên Dũng, Tân An…) quét lại hàng tuần → diff report
- [ ] GeoJSON ranh giới (thanglequoc) → phục vụ "trong phường X" chính xác

## 5. ToS / pháp lý (dùng cá nhân)

- Scraping (Google Maps, Foody, masothue) = vùng xám — dùng cá nhân, không redistribute, không bán.
- API chính thức: theo ToS từng bên (Google cache ≤30 ngày; Goong/Vietmap theo hợp đồng gói).
- OSM: ODbL — ghi nguồn "© OpenStreetMap contributors".
- Registry quốc gia: chỉ cơ quan nhà nước; cá nhân dùng nguồn thay thế (masothue/data.gov.vn).
