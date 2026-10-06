# Node.js LTS trong tháng 10/2026: nhánh hiện hành, lịch hỗ trợ dòng 20–26 và thay đổi chính giữa Node.js 24 với Node.js 22

Tại thời điểm ngày 6 tháng 10 năm 2026, nhánh long-term support (LTS) được dự án khuyến nghị cho các ứng dụng sản xuất là **Node.js 24 "Krypton"**, hiện giữ trạng thái Active LTS[1][2]. Nhánh Node.js 22 "Jod" đã chuyển sang Maintenance LTS từ ngày 21 tháng 10 năm 2025 và sẽ kết thúc vòng đời vào ngày 30 tháng 4 năm 2027[2][3]. Trong khi đó, Node.js 26 "Lithium" — phát hành chính thức ngày 5 tháng 5 năm 2026 — dự kiến được thăng cấp lên LTS vào ngày 28 tháng 10 năm 2026, chỉ hơn ba tuần sau thời điểm viết báo cáo[2][7]. Nhánh Node.js 20 "Iron" đã ngừng nhận hỗ trợ chính thức từ ngày 30 tháng 4 năm 2026 và hiện chỉ còn các gói hỗ trợ thương mại từ bên thứ ba[12][18]. Một thay đổi mang tính bước ngoặt đang được triển khai song song: từ Node.js 27, dự án chuyển sang mô hình một bản major mỗi năm, mọi phiên bản đều được lên LTS, chấm dứt cách phân biệt bản chẵn và bản lẻ tồn tại hơn một thập kỷ[4][13]. So với Node.js 22, Node.js 24 mang theo V8 13.6, npm 11, cơ chế AsyncContextFrame làm mặc định cho AsyncLocalStorage, URLPattern toàn cục và một số thay đổi phá vỡ tương thích cần lưu ý khi nâng cấp[5]. Báo cáo này tổng hợp bản LTS hiện hành, lịch phát hành và cửa sổ hỗ trợ của bốn dòng 20, 22, 24, 26, so sánh chi tiết Node.js 24 với Node.js 22, và điểm lại tình hình bảo mật cùng hệ sinh thái trong năm 2026; toàn bộ nguồn được truy cập ngày 6 tháng 10 năm 2026.

## Bản LTS hiện hành trong tháng 10/2026

### Node.js 24 "Krypton" là Active LTS đang được khuyến nghị

Trang Releases chính thức của nodejs.org ghi nhận Node.js 24 mang trạng thái LTS[1], trong khi bảng theo dõi của Release Working Group ghi rõ đây là nhánh Active LTS với mốc chuyển sang Maintenance dự kiến ngày 20 tháng 10 năm 2026 và hết vòng đời ngày 30 tháng 4 năm 2028[2]. Nhánh 24 ra mắt lần đầu ngày 6 tháng 5 năm 2025 và được thăng cấp LTS ngày 28 tháng 10 năm 2025[2][3]. Theo dõi độc lập của endoflife.date xác nhận giai đoạn hỗ trợ tích cực của dòng 24 kết thúc ngày 20 tháng 10 năm 2026, hỗ trợ bảo mật kéo dài tới ngày 30 tháng 4 năm 2028, với bản phát hành mới nhất được ghi nhận là 24.21.0 (ngày 8 tháng 9 năm 2026)[11]. Chính sách của dự án nêu rõ các ứng dụng sản xuất chỉ nên chạy trên Active LTS hoặc Maintenance LTS, vì vậy cho tới khi Node.js 26 lên LTS, Node.js 24 đóng vai trò nhánh mặc định cho môi trường production[1]. Do mốc ngày 20 tháng 10 năm 2026 đã rất gần, điều cần lưu ý là sau ngày đó dòng 24 chỉ còn nhận các bản vá lỗi nghiêm trọng và bản vá bảo mật thay vì các tính năng mới[2].

### Node.js 26 "Lithium" sẽ tiếp quản vị trí LTS từ ngày 28 tháng 10 năm 2026

Node.js 26 được phát hành ngày 5 tháng 5 năm 2026 và hiện đang ở trạng thái Current, tức giai đoạn ổn định hóa kéo dài sáu tháng trước khi lên LTS[1][7]. Theo bảng lịch hỗ trợ của Release Working Group và tệp schedule.json, ngày 28 tháng 10 năm 2026 là mốc dòng 26 chính thức bước vào giai đoạn LTS, tiếp đó chuyển Maintenance ngày 20 tháng 10 năm 2027 và kết thúc vòng đời ngày 30 tháng 4 năm 2029[2][3]. Tệp CODENAMES.md của nhóm Release đã đặt tên mã cho dòng này là "Lithium", tiếp nối chuỗi tên mã theo bảng chữ cái của các dòng LTS gần đây (Iron, Jod, Krypton)[19]. Về công nghệ, Node.js 26 mở sẵn Temporal API, nâng động cơ V8 lên 14.6 và Undici lên 8.0.2, đồng thời dọn dẹp một loạt API cũ như phương thức writeHeader của http.Server và các module _stream_* thuộc chặng legacy[7]. Giới truyền thông công nghệ nhấn mạnh đây là bản phát hành cuối cùng theo mô hình hai bản major mỗi năm trước khi chu kỳ thường niên bắt đầu từ Node.js 27[13].

### Node.js 22 "Jod" ở chế độ Maintenance LTS với hạn cuối tháng 4/2027

Node.js 22 phát hành ngày 24 tháng 4 năm 2024, lên LTS ngày 29 tháng 10 năm 2024 và bước vào giai đoạn Maintenance từ ngày 21 tháng 10 năm 2025[2][3]. Nhánh này sẽ hết vòng đời ngày 30 tháng 4 năm 2027, tức còn khoảng bảy tháng nhận bản vá bảo mật tính từ thời điểm viết báo cáo[2][3]. Các bản phát hành của dòng vẫn diễn ra đều đặn, với bản mới nhất ghi nhận là 22.23.3 (ngày 23 tháng 9 năm 2026)[11]. Theo các đơn vị theo dõi vòng đời phần mềm, các dòng được hỗ trợ đầy đủ hiện chỉ gồm 26, 24 và 22; ngay cả một dòng đang ở chế độ Maintenance như 22 vẫn liên tục nhận bản vá CVE, ví dụ lỗ hổng từ chối dịch vụ CVE-2026-21717 đã được vá trong bản 22.22.2[18].

### Node.js 20 "Iron" đã kết thúc vòng đời

Node.js 20 ra mắt ngày 18 tháng 4 năm 2023, lên LTS ngày 24 tháng 10 năm 2023 và chính thức hết vòng đời ngày 30 tháng 4 năm 2026[2][3]. Khi một dòng đạt EOL, dự án không còn phát hành bất kỳ bản cập nhật nào, kể cả bản vá bảo mật, và mọi lỗ hổng được công bố sau đó đều không được vá[12]. Trang End-Of-Life của Node.js liệt kê dòng 20 kèm danh sách lỗ hổng đã ghi nhận gồm 27 mức High, 24 mức Medium và 9 mức Low[12], trong khi các khung kiểm toán như SOC 2 hay PCI DSS coi việc chạy một runtime hết hỗ trợ là phát hiện nghiêm trọng cần xử lý[18]. Không chỉ tồn đọng lỗ hổng, dòng 20 còn chịu hiệu ứng domino khi hạ tầng bên thứ ba loại bỏ hỗ trợ: Vercel đã vô hiệu hóa Node.js 20 cho builds và functions từ ngày 1 tháng 10 năm 2026, dù các triển khai hiện có vẫn tiếp tục chạy[17]. Các dòng số lẻ như 23 hay 25 chưa từng bước vào LTS và cũng đã EOL, cụ thể dòng 25 ngừng hỗ trợ ngày 1 tháng 6 năm 2026[3].

Bảng dưới đây tóm tắt trạng thái bốn dòng tại ngày 6 tháng 10 năm 2026 theo các nguồn chính thức[1][2][3].

| Dòng | Trạng thái (06/10/2026) | Mốc quan trọng kế tiếp |
| --- | --- | --- |
| Node.js 26 "Lithium" | Current | Lên LTS ngày 28/10/2026 |
| Node.js 24 "Krypton" | Active LTS | Sang Maintenance ngày 20/10/2026 |
| Node.js 22 "Jod" | Maintenance LTS | EOL ngày 30/4/2027 |
| Node.js 20 "Iron" | Đã EOL | Ngừng hỗ trợ từ 30/4/2026 |

## Lịch phát hành và cửa sổ hỗ trợ của các dòng LTS 20, 22, 24 và 26

### Bảng lịch hỗ trợ chính thức

Bảng dưới đây tổng hợp từ bảng theo dõi của Release Working Group và tệp schedule.json — hai tài liệu chính thức mà dự án công bố để các đội ngũ lập kế hoạch nâng cấp[2][3]. Cột "Active LTS" là mốc dòng được thăng cấp từ Current lên LTS; cột "Maintenance" là mốc kết thúc giai đoạn hỗ trợ tích cực; cột EOL là ngày dự án ngừng mọi bản cập nhật[2]. Các mốc này được endoflife.date đối chiếu chéo và trùng khớp ở phần lớn giá trị, với độ lệch tối đa một ngày ở một vài mốc phát hành đầu tiên[11].

| Dòng | Tên mã | Phát hành đầu tiên | Bắt đầu Active LTS | Bắt đầu Maintenance | EOL |
| --- | --- | --- | --- | --- | --- |
| 20.x | Iron | 18/04/2023 | 24/10/2023 | 22/10/2024 | 30/04/2026 |
| 22.x | Jod | 24/04/2024 | 29/10/2024 | 21/10/2025 | 30/04/2027 |
| 24.x | Krypton | 06/05/2025 | 28/10/2025 | 20/10/2026 | 30/04/2028 |
| 26.x | Lithium | 05/05/2026 | 28/10/2026 | 20/10/2027 | 30/04/2029 |

### Ba pha của vòng đời: Current, Active LTS và Maintenance

Chính sách phát hành của Node.js chia vòng đời mỗi dòng thành ba pha rõ ràng[1][2]. Sau sáu tháng tồn tại ở dạng Current để các tác giả thư viện kịp bổ sung hỗ trợ, các dòng chẵn được thăng lên Active LTS và hưởng 12 tháng hỗ trợ tích cực trước khi bước vào 18 tháng Maintenance, giai đoạn chỉ còn bản vá lỗi nghiêm trọng và bản vá bảo mật[1][2]. Như vậy một dòng LTS nhận cam kết sửa các lỗi nghiêm trọng trong tổng cộng khoảng 30 tháng kể từ khi vào LTS, tương đương chu kỳ từ bản phát hành đầu tiên tới EOL dài khoảng ba năm[1][2]. Dự án cũng nêu rõ các bản Current phục vụ chủ yếu cho thử nghiệm và cho tác giả thư viện thêm hỗ trợ sớm, còn môi trường sản xuất chỉ nên dùng Active LTS hoặc Maintenance LTS[1]. Một chi tiết vận hành đáng lưu ý là mọi mốc chuyển pha sẽ được chốt muộn nhất vào ngày đầu tiên của tháng diễn ra thay đổi, và nếu lịch phải điều chỉnh thì dự án sẽ thông báo trước tối thiểu 14 ngày[2]. Các ngày tháng trong tài liệu kế hoạch vì thế vẫn được ghi chú "có thể thay đổi", nhưng trên thực tế các mốc LTS của Node.js được giữ ổn định trong nhiều năm[2].

### Mô hình mới từ Node.js 27: một bản major mỗi năm

Trong nửa đầu năm 2026, dự án công bố thay đổi căn bản nhất của lịch phát hành trong hơn một thập kỷ, và các phương tiện công nghệ đưa tin rộng rãi vào tháng 6 năm 2026[4][13]. Kể từ Node.js 27, mỗi năm chỉ còn một bản major phát hành vào tháng 4 và được thăng LTS vào tháng 10; mọi phiên bản đều trở thành LTS nên không còn khái niệm bản chẵn dành cho sản xuất và bản lẻ dành cho thử nghiệm[4]. Để thay thế vai trò thử nghiệm trước đây của các bản lẻ, dự án bổ sung kênh Alpha kéo dài sáu tháng (tháng 10 đến tháng 3) cho phép các thay đổi semver-major xuất hiện sớm[4]. Số phiên bản sẽ gắn với năm dương lịch của bản Current đầu tiên, ví dụ 27.0.0 trong năm 2027 và 28.0.0 trong năm 2028[4]. Giai đoạn LTS vẫn kéo dài 30 tháng và tổng cửa sổ hỗ trợ từ bản Current đầu tiên đến EOL là 36 tháng, giữ nguyên độ dài mà các doanh nghiệp đang quen thuộc[4]. Lý do được dự án nêu gồm ba điểm: các bản lẻ hầu như không được ứng dụng thực tế, cách phân biệt chẵn/lẻ gây nhầm lẫn cho người mới, và quan trọng nhất là vấn đề bền vững khi đội ngũ tình nguyện phải duy trì bốn đến năm dòng phát hành song song cùng lúc[4]. Đối với các đội vốn chỉ nâng cấp lên LTS, thông điệp chính sách được tóm gọn trong thông báo chính thức.

> If you already only upgrade to LTS versions, little changes beyond version numbering.[4]

Tệp schedule.json ghi mốc Node.js 27 bắt đầu kênh Alpha ngay ngày 28 tháng 10 năm 2026, lên Current ngày 22 tháng 4 năm 2027 và dự kiến EOL ngày 30 tháng 4 năm 2030[3]. Truyền thông dẫn khuyến nghị của dự án rằng các tác giả thư viện nên đưa bản Alpha vào CI càng sớm càng tốt, bởi nếu chỉ kiểm thử trên LTS thì lỗi sẽ không được phát hiện trước khi ảnh hưởng tới người dùng cuối[4][13]. Với những ai đang dùng LTS, lộ trình chuyển đổi sang mô hình mới được mô tả là khá nhẹ nhàng vì cửa sổ hỗ trợ không đổi[13].

## Những thay đổi chính của Node.js 24 so với Node.js 22

Node.js 24 phát hành ngày 6 tháng 5 năm 2025, cách Node.js 22 (24 tháng 4 năm 2024) khoảng hai năm và hai bậc major[5][8]. Vì giữa hai phiên bản có rất nhiều thay đổi, phần này tập trung vào các điểm được dự án ghi nhận chính thức khi phát hành Node.js 24, đặt cạnh trạng thái tương ứng của dòng 22[5][6].

### V8 13.6 so với V8 12.4: bước tiến của ngôn ngữ

Node.js 24 nâng động cơ V8 lên phiên bản 13.6, mang theo Float16Array, explicit resource management (cặp từ khóa using và await using), RegExp.escape, WebAssembly Memory64 và Error.isError[5][6]. Trong khi đó Node.js 22 khởi hành với V8 12.4 (chính xác là 12.4.254.14), vốn mang đến WebAssembly Garbage Collection, Array.fromAsync, các phương thức mới cho Set và iterator helpers[8][9]. Khoảng cách hai bậc V8 đồng nghĩa các dự án nhảy trực tiếp từ 22 lên 24 được hưởng gần hai năm cải tiến ngôn ngữ và tối ưu hiệu năng từ đội V8[5][8]. Bản tin tổng hợp của OpenJS Foundation tóm gọn nhóm nâng cấp này cùng các thay đổi hệ thống khác như minh chứng cho định hướng hiện đại hóa liên tục của dòng 24[6].

### npm 11, Undici 7 và bộ công cụ đi kèm

Node.js 24 đi kèm npm 11 với cải thiện về hiệu năng, bảo mật và tương thích với các gói JavaScript hiện đại[5][6]. Để so sánh, dòng 22 phân phối npm thuộc nhánh 10 — tài liệu runtime của AWS App Runner ghi nhận bản 22.21.1 đi kèm npm 10.9.4[20]. Về phía HTTP client, Node.js 24 chuyển sang Undici 7 với hiệu năng tốt hơn và hỗ trợ các tính năng HTTP mới[5], rồi dòng 26 kế nhiệm tiếp tục nâng lên Undici 8[7]. Nhóm công cụ kiểm thử cũng được cải thiện: test runner tích hợp sẵn giờ tự động chờ các subtest hoàn tất thay vì buộc người viết test tự await, giúp giảm lỗi promise bị bỏ quên[5][6]. Ở chiều ngược lại, Node.js 22 từng đặt nền móng cho hệ công cụ hiện đại với node --run để chạy script trong package.json, watch mode ổn định, WebSocket client và các hàm glob/globSync trên module node:fs[8][9]. Một cột mốc nối tiếp giữa hai dòng là từ bản 22.12.0 (tháng 12 năm 2024), cơ chế require(esm) được bật mặc định thay vì phải bật cờ --experimental-require-module, giúp các gói ESM thuần được require trực tiếp[10].

### AsyncLocalStorage, URLPattern và Permission Model

Ba thay đổi API nổi bật nhất của Node.js 24 nằm ở lớp hạ tầng[5][6]. Thứ nhất, AsyncLocalStorage — cơ chế theo dõi ngữ cảnh bất đồng bộ được dùng nhiều trong observability — chuyển sang mặc định dùng AsyncContextFrame, cách triển khai hiệu quả hơn và ổn định hơn cho các tình huống nâng cao[5][6]. Thứ hai, URLPattern được đưa ra toàn cục: lập trình viên không cần import thủ công nữa mà dùng trực tiếp như một API toàn cầu để khớp và trích xuất thành phần URL[5][6]. Thứ ba, mô hình Permission Model ra mắt từ thời Node.js 20 được cải thiện đáng kể và đổi cờ từ --experimental-permission thành --permission đơn giản hơn, cho phép giới hạn quyền truy cập file system, mạng và biến môi trường ở cấp tiến trình[5][6]. Song song với tiện ích, mô hình này cũng đi kèm trách nhiệm bảo mật: lỗ hổng CVE-2026-58043 công bố trong đợt vá tháng 7 năm 2026 cho thấy cơ chế khớp đường dẫn trong allowlist từng bị lỗi cho phép truy cập file ngoài phạm vi được cấp[15].

### Thay đổi phá vỡ tương thích và lưu ý khi nâng cấp

Trên Windows, Node.js 24 loại bỏ hỗ trợ trình biên dịch MSVC và yêu cầu ClangCL để biên dịch Node.js từ mã nguồn, thay đổi ảnh hưởng trực tiếp tới chuỗi build của các dự án dùng native module[5]. Nhóm thay đổi đáng chú ý khác gồm việc deprecation cách dùng các lớp Zlib mà không có từ khóa new, và việc truyền tham số args vào spawn hoặc execFile của child_process[5]. Danh sách đầy đủ còn dài hơn nhiều so với phần "Notable changes" trong thông báo phát hành, nên các đội ngũ nên đối chiếu changelog đầy đủ trước khi nhảy major[5]. Nhìn rộng ra, hướng dọn dẹp API lỗi thời này được tiếp nối ở Node.js 26 khi nhiều API legacy như writeHeader của http.Server bị xóa hẳn[7]. Bảng dưới đây tóm tắt các khác biệt chính giữa hai dòng theo tài liệu phát hành chính thức cùng nguồn đối chiếu[5][8][20].

| Hạng mục | Node.js 22 (Jod) | Node.js 24 (Krypton) |
| --- | --- | --- |
| Động cơ V8 | 12.4 | 13.6 |
| npm đi kèm | dòng 10.x (ví dụ 10.9.4) | npm 11 |
| AsyncLocalStorage | chưa mặc định dùng AsyncContextFrame | AsyncContextFrame làm mặc định |
| URLPattern | phải import thủ công | có sẵn toàn cục |
| Permission Model | cờ --experimental-permission | cờ --permission, ổn định hơn |
| Test runner | phải tự await các subtest | tự động chờ subtest |
| Toolchain Windows | hỗ trợ MSVC | yêu cầu ClangCL, bỏ MSVC |

## Bảo mật, tính bền vững và hệ sinh thái trong năm 2026

### Đợt vá tháng 7/2026 với ba lỗ hổng mức High trên cả ba dòng

Ngày 21 tháng 7 năm 2026, dự án phát thông báo trước về đợt vá bảo mật dự kiến triển khai từ ngày 27 tháng 7, với mức nghiêm trọng cao nhất là HIGH trên từng dòng 22.x, 24.x và 26.x[14]. Đợt phát hành thực tế trượt hai lần và hạ cánh ngày 29 tháng 7 năm 2026 với 11 CVE được vá, trong đó 3 mức High, 5 mức Medium và 3 mức Low[15]. Ba lỗ hổng mức High gồm CVE-2026-56846 (header HTTP/2 giữ lại vượt qua giới hạn maxSessionMemory, gây cạn kiệt bộ nhớ từ xa) và CVE-2026-56848 (lỗi heap-use-after-free khi gửi HTTP/2 tái nhập) cùng CVE-2026-58043 (Permission Model cấp quá quyền truy cập file system)[15]. Các bản vá tương ứng được phát hành là 22.23.2, 24.18.1 và 26.5.1, kèm theo cập nhật các thư viện nhúng như undici và llhttp[15]. Thông báo chính thức nhấn mạnh các bản EOL luôn bị ảnh hưởng khi có đợt vá bảo mật nhưng sẽ không nhận bản cập nhật — một lý do cứng để không chạy production trên dòng đã hết hạn[14].

### Chương trình bug bounty tạm dừng vì mất nguồn tài trợ

Trong năm 2026, dự án phải tạm dừng chương trình thưởng tiền cho các báo cáo lỗ hổng, hay security bug bounty[16]. Chương trình từng vận hành từ năm 2016 thông qua sáng kiến Internet Bug Bounty của HackerOne, nhưng nguồn quỹ chung đã bị dừng nên dự án tình nguyện như Node.js không thể tiếp tục chi thưởng bằng ngân sách của mình[16]. Dự án khẳng định việc tiếp nhận và xử lý báo cáo lỗ hổng vẫn diễn ra như cũ, chỉ phần thưởng tiền mặt bị ngưng, và chương trình sẽ được xem xét tái khởi động nếu tìm được nhà tài trợ qua OpenJS Foundation[16]. Chi tiết này cộng hưởng với vấn đề bền vững của đội ngũ tình nguyện — một trong các lý do chính đằng sau việc rút gọn lịch phát hành từ năm 2027[4].

### Hạ tầng bên thứ ba tăng tốc dịch chuyển khỏi Node.js 20

Việc Vercel vô hiệu hóa Node.js 20 cho builds và functions từ ngày 1 tháng 10 năm 2026 là ví dụ rõ nhất cho áp lực nâng cấp từ phía nền tảng[17]. Với các dự án chưa kịp nâng cấp, Vercel đề xuất phương án trung gian là đóng gói ứng dụng dưới dạng container image để tự quản lý phiên bản runtime, đổi lại đội ngũ phải tự chịu trách nhiệm về các bản vá bảo mật[17]. Ở tầng hỗ trợ thương mại, các đơn vị như HeroDevs quảng bá gói Never-Ending Support cho những dòng đã EOL gồm cả Node.js 20, phản ánh nhu cầu thực tế của các hệ thống khó nâng cấp trong ngắn hạn[18]. Giới phân tích khuyến nghị các đội đang chạy phiên bản EOL nên chọn giữa việc nâng lên 22 hoặc 24 và việc mua hỗ trợ mở rộng, bởi phương án đắt nhất trong trung hạn là phớt lờ rủi ro[18].

## Hạn chế của báo cáo

Báo cáo dựa gần như hoàn toàn vào các nguồn chính thức của dự án Node.js, với một số nguồn thứ cấp dùng để đối chiếu, và một vài chênh lệch nhỏ giữa các nguồn cần được nêu minh bạch. Cụ thể, trang Releases ghi Node.js 20 phát hành ngày 17 tháng 4 năm 2023[1] trong khi bảng của Release Working Group và schedule.json ghi ngày 18 tháng 4 năm 2023[2][3]. Tương tự, mốc cập nhật cuối của dòng 24 lệch nhau một ngày giữa hai nguồn: 7 và 8 tháng 9 năm 2026[1][11]. Tên mã "Lithium" của dòng 26 hiện chỉ xuất hiện trong tệp CODENAMES.md[19] mà chưa hiển thị trên trang Releases[1], và schedule.json để trống trường codename cho cả dòng 26 lẫn 27[3]. Chi tiết phiên bản npm đi kèm dòng 22 dựa trên một nguồn tài liệu hạ tầng duy nhất[20], còn diễn biến đợt vá tháng 7 năm 2026 được đối chiếu giữa thông báo chính thức[14] và một bài tổng hợp kỹ thuật của bên thứ ba[15] mà chưa có bản tóm tắt hậu phát hành trên nodejs.org. Với mô hình mới, các mốc của Node.js 27 trở đi vẫn có thể được tinh chỉnh trong các bản cập nhật schedule.json tiếp theo, đúng như ghi chú thường trực rằng các ngày tháng có thể thay đổi[2].

## Kết luận và khuyến nghị

Tổng hợp lại, tháng 10 năm 2026 là thời điểm chuyển giao của Node.js khi ba trạng thái LTS khác nhau cùng tồn tại trong vài tuần. Node.js 24 vẫn là nhánh Active LTS nên dùng cho production[1][2]. Node.js 26 sẽ được thăng cấp LTS ngày 28 tháng 10 năm 2026 với V8 14.6, Temporal API và Undici 8[2][7]. Node.js 22 chỉ còn khoảng bảy tháng hỗ trợ bảo mật trước khi EOL ngày 30 tháng 4 năm 2027[2][3]. Song song, dòng 20 đã EOL từ tháng 4 năm 2026 và đang bị các nền tảng hạ tầng lần lượt loại bỏ hỗ trợ[12][17]. Về dài hạn, mô hình phát hành thường niên từ Node.js 27 sẽ giúp các đội ngũ lập kế hoạch dễ dàng hơn với một bản major mỗi năm và mọi phiên bản đều lên LTS[4][13].

Với người dùng production, khuyến nghị cụ thể như sau: các dự án mới hoặc đang chạy 24 nên tiếp tục bám Node.js 24 và bắt đầu đánh giá Node.js 26 để sẵn sàng chuyển khi dòng này ổn định trong vai trò LTS, bởi nhánh 26 có cửa sổ hỗ trợ dài nhất hiện tại kéo dài tới tháng 4 năm 2029[2][3]. Các đội còn ở Node.js 22 nên đưa việc nâng lên 24 vào kế hoạch của năm 2027 muộn nhất là trước mốc EOL, chú ý các thay đổi phá vỡ tương thích đã nêu như yêu cầu ClangCL trên Windows và các API bị deprecation[5]. Các hệ thống còn dính Node.js 20 cần xử lý ngay bằng một trong hai con đường: nâng lên dòng LTS đang được hỗ trợ hoặc mua hỗ trợ thương mại có cam kết vá lỗ hổng[18], đồng thời kiểm tra các nền tảng triển khai đã khóa phiên bản 20 như Vercel[17].

Cho các nhóm phát triển thư viện, dự án khuyến nghị đưa kênh Alpha của Node.js 27 vào CI ngay từ cuối năm 2026 để báo lỗi sớm trước khi chúng ảnh hưởng tới người dùng[4]. Về vận hành bảo mật, các đội nên theo dõi danh sách thông báo nodejs-sec và cập nhật ngay các bản vá mới nhất của nhánh đang dùng — như đợt tháng 7 năm 2026 đã cho thấy các lỗ hổng mức High có thể xuất hiện đồng thời trên mọi dòng được hỗ trợ[14][15]. Cuối cùng, do mọi mốc lịch đều có thể được tinh chỉnh với thông báo trước tối thiểu 14 ngày, việc định kỳ kiểm tra schedule.json và trang Releases là thói quen nên duy trì song song với việc nâng cấp phiên bản[1][2].

## Sources

[1] https://nodejs.org/en/about/previous-releases — Node.js — Node.js Releases (previous releases) (truy cập ngày 06/10/2026)
[2] https://github.com/nodejs/Release/blob/main/README.md — nodejs/Release — README (release schedule) (truy cập ngày 06/10/2026)
[3] https://raw.githubusercontent.com/nodejs/Release/main/schedule.json — nodejs/Release — schedule.json (truy cập ngày 06/10/2026)
[4] https://nodejs.org/en/blog/announcements/evolving-the-nodejs-release-schedule — Node.js — Evolving the Node.js Release Schedule (truy cập ngày 06/10/2026)
[5] https://nodejs.org/en/blog/release/v24.0.0 — Node.js — Node.js 24.0.0 (Current) release notes (truy cập ngày 06/10/2026)
[6] https://openjsf.org/blog/nodejs-24-released — OpenJS Foundation — What's New with Node.js 24 (truy cập ngày 06/10/2026)
[7] https://nodejs.org/en/blog/release/v26.0.0 — Node.js — Node.js 26.0.0 (Current) release notes (truy cập ngày 06/10/2026)
[8] https://nodejs.org/en/blog/announcements/v22-release-announce — Node.js — Node.js 22 is now available! (truy cập ngày 06/10/2026)
[9] https://nodejs.org/en/blog/release/v22.0.0 — Node.js — Node.js 22.0.0 (Current) release notes (truy cập ngày 06/10/2026)
[10] https://nodejs.org/en/blog/release/v22.12.0 — Node.js — Node.js 22.12.0 (LTS) release notes (truy cập ngày 06/10/2026)
[11] https://endoflife.date/nodejs — endoflife.date — Node.js EOL dates & support (truy cập ngày 06/10/2026)
[12] https://nodejs.org/en/about/eol — Node.js — End-Of-Life (truy cập ngày 06/10/2026)
[13] https://www.infoq.com/news/2026/06/nodejs-release-changes — InfoQ — Node.js Moves to One Major Release Per Year, Starting with Node 27 (truy cập ngày 06/10/2026)
[14] https://nodejs.org/en/blog/vulnerability/july-2026-security-releases — Node.js — July 2026 Security Releases (advisory) (truy cập ngày 06/10/2026)
[15] https://www.digitalapplied.com/blog/nodejs-july-2026-security-releases-shipped — Digital Applied — Node.js July Security Releases: What Actually Shipped (truy cập ngày 06/10/2026)
[16] https://nodejs.org/en/blog/announcements/discontinuing-security-bug-bounties — Node.js — Security Bug Bounty Program Paused Due to Loss of Funding (truy cập ngày 06/10/2026)
[17] https://vercel.com/changelog/node-js-20-is-being-deprecated — Vercel — Node.js 20 is being deprecated on October 1, 2026 (truy cập ngày 06/10/2026)
[18] https://www.herodevs.com/blog-posts/node-js-end-of-life-dates-you-should-be-aware-of — HeroDevs — Node.js Version Support: EOL Dates and Latest Releases (July 2026) (truy cập ngày 06/10/2026)
[19] https://raw.githubusercontent.com/nodejs/Release/main/CODENAMES.md — nodejs/Release — CODENAMES.md (truy cập ngày 06/10/2026)
[20] https://docs.aws.amazon.com/apprunner/latest/dg/service-source-code-nodejs-releases.html — AWS App Runner — Node.js runtime release information (truy cập ngày 06/10/2026)
