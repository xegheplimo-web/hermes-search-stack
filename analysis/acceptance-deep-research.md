# So sánh các dịch vụ Deep Research của Perplexity, OpenAI và Google trong năm 2026

Các dịch vụ Deep Research của Perplexity, OpenAI và Google bước vào năm 2026 với cùng một định hướng chiến lược: biến tính năng nghiên cứu tự trị từ một ô nhập liệu thành hạ tầng agent có thể lập kế hoạch, tìm kiếm, tổng hợp và xuất bản báo cáo có trích dẫn[27][12]. Ba hệ thống chia sẻ chung một vòng lặp vận hành gồm kế hoạch, tìm kiếm, đọc, lặp lại và tổng hợp, nhưng khác nhau rõ rệt về triết lý sản phẩm: Perplexity tối ưu tốc độ và chi phí trong hệ sinh thái Computer, OpenAI theo đuổi độ sâu với hai phiên bản đầy đủ và rút gọn, còn Google phát huy lợi thế phân phối cùng khả năng tích hợp Workspace và dữ liệu doanh nghiệp[4][22][15]. Về giá, các mức phổ biến cho người dùng cá nhân nằm trong khoảng 20 đến 200 USD mỗi tháng, trong khi API tính theo tác vụ với chi phí ước tính từ khoảng 1 USD đến vài USD cho mỗi báo cáo tùy cấu hình[1][12]. Các bài đo độc lập công bố trong 2025–2026 cho thấy không có người thắng tuyệt đối: Perplexity dẫn đầu trong DRACO do chính họ xây dựng, OpenAI dẫn đầu ở DeepResearch Bench II, còn Gemini tỏa sáng ở các bài kiểm tra về trình bày và dữ liệu doanh nghiệp[27][28]. Điểm chung đáng chú ý là tất cả các hệ thống vẫn còn khoảng cách đáng kể về độ chính xác thực tế và chất lượng trích dẫn, đòi hỏi người dùng phải kiểm chứng chéo trước khi sử dụng cho công việc quan trọng[28][33].

## Bối cảnh: Deep Research trở thành hạ tầng nghiên cứu tự trị

### Deep Research là gì và vận hành ra sao

Deep research là mô hình làm việc trong đó một hệ AI agent phân rã câu hỏi phức tạp thành các quy trình con, tìm kiếm lặp đi lặp lại trên nhiều nguồn bằng chứng khác nhau rồi tổng hợp thành báo cáo có cấu trúc kèm trích dẫn[27]. Khác với hỏi đáp một lần, hệ thống tự đặt kế hoạch, đọc kết quả sau mỗi vòng, đánh giá xem đã đủ bằng chứng chưa, ghi nhận mâu thuẫn giữa các nguồn và chỉ viết khi hội đủ thông tin[4]. Trên phương diện kỹ thuật, tài liệu của Google mô tả quy trình gồm năm bước Plan, Search, Read, Iterate và Output, chạy nền trong nhiều phút và bắt buộc xử lý bất đồng bộ[12]. Mục tiêu cuối cùng là tạo ra những phân tích có độ sâu mà con người thường phải mất hàng giờ để hoàn thành[8].

### Ba giai đoạn hình thành thị trường 2024–2026

Google là bên khai phá khi giới thiệu Deep Research cho người dùng Gemini từ tháng 12/2024, ban đầu chạy trên Gemini 1.5 Pro[25]. OpenAI tung deep research vào tháng 2/2025 trên nền mô hình o3 với tuyên bố có thể làm trong vài chục phút những gì con người mất nhiều giờ[8]. Perplexity ra mắt cùng tháng 2/2025 nhưng theo chiến lược khác khi cho dùng thử miễn phí, nhấn tốc độ và khả năng tiếp cận đại chúng[34]. Đến tháng 4/2025, Anthropic tham gia cuộc chơi với Claude Research[34]. Một khảo sát tổng hợp hơn 80 triển khai thương mại lẫn mã nguồn mở cho thấy Deep Research đã trở thành một phạm trù công nghệ độc lập chỉ trong hơn hai năm[34]. Năm 2026 đánh dấu bước ngoặt API hóa và agent hóa: Google đưa agent ra API từ tháng 12/2025 rồi nâng cấp thành bộ đôi vào tháng 4/2026, OpenAI bổ sung kết nối MCP từ tháng 2/2026, còn Perplexity tích hợp Deep Research vào Computer từ tháng 6/2026[15][8][4].

## Perplexity Deep Research: tốc độ, chi phí và hệ sinh thái Computer

### Tính năng và định vị sản phẩm

Perplexity định vị Deep Research là tìm kiếm agentic lặp: hệ thống lập kế hoạch trước khi hành động, chạy nhiều truy vấn thay vì một, đọc kết quả trước khi quyết định tìm gì tiếp theo và tổng hợp thành câu trả lời duy nhất có thẩm quyền[4]. Từ tháng 6/2026, tính năng này nằm trong Computer với kiến trúc Search as Code cho phép hệ thống tự viết chương trình tìm kiếm riêng và chạy hàng nghìn bước song song[4]. Một truy vấn phức tạp được chia thành các tác vụ con định tuyến qua hơn 20 mô hình frontier, sau đó kết xuất thành PDF, deck, dashboard hoặc website thay vì chỉ một đoạn văn bản[4]. Perplexity tuyên bố việc chuyển vào Computer giúp tăng độ chính xác thực tế, độ sâu phân tích và chất lượng trích dẫn theo ba bài đo Humanity's Last Exam, BrowseComp và DeepSearchQA[4]. Trong bản nâng cấp tháng 2/2026, hãng tuyên bố đạt hiệu năng hàng đầu trên Deep Search QA của Google DeepMind và Research Rubric của Scale AI, với Deep Research chạy trên Opus 4.5[5]. Bản Advanced Deep Research ra mắt tháng 9/2026 bổ sung câu hỏi làm rõ trước khi chạy, cho phép đặt câu hỏi nối tiếp ngay trong lúc nghiên cứu, hiển thị tiến độ và chuyển báo cáo thành file có thể chỉnh sửa; thuê bao Max dùng Opus 4.6 Thinking còn Pro dùng 4.5[3].

### Giá và giới hạn sử dụng

Gói miễn phí chỉ được một truy vấn Research mỗi tháng, gói Pro giá 20 USD mỗi tháng với hạn mức tháng ở mức sử dụng trung bình, còn gói Max giá 200 USD mỗi tháng với hạn mức nâng cao[1][2]. Education Pro giá 10 USD mỗi tháng cho sinh viên và giáo viên đã xác minh, trong khi doanh nghiệp có Enterprise Pro từ 40 USD mỗi ghế mỗi tháng kèm 50 truy vấn Research mỗi tháng và Enterprise Max với 500 truy vấn mỗi tháng[2]. Điều đáng lưu ý là Perplexity không công bố con số cụ thể cho hạn mức của Pro và Max mà chỉ ghi định tính là giới hạn theo tháng, khác hẳn giai đoạn đầu năm 2025 khi Pro được quảng cáo tới 500 truy vấn mỗi ngày còn bản miễn phí có 5 truy vấn mỗi ngày[2][22]. Người dùng trả phí được chọn mô hình ưa thích và truy cập các nguồn dữ liệu cao cấp như PitchBook, Statista hay S&P Capital IQ[1]. Về tốc độ, thử nghiệm độc lập của PCMag ghi nhận Perplexity cho ra báo cáo chỉ trong khoảng ba phút, nhanh nhất trong nhóm được thử[22], và số liệu của DRACO cũng cho thấy độ trễ trung bình 245,3 giây, thấp nhất trong các hệ thống được đo[27].

## OpenAI Deep Research: hai phiên bản và hệ sinh thái API

### Tính năng chính

OpenAI ra mắt deep research như một agent tự trị tìm kiếm, phân tích và tổng hợp hàng trăm nguồn trực tuyến để tạo báo cáo ở mức chuyên gia phân tích[8]. Hệ thống chạy trên phiên bản o3 tối ưu cho duyệt web và phân tích dữ liệu, được huấn luyện end-to-end bằng học tăng cường trên các tác vụ duyệt và suy luận khó, có khả năng trích dẫn tận cấp câu hoặc đoạn[8]. Từ tháng 4/2025, OpenAI vận hành song song hai phiên bản: bản đầy đủ và bản rút gọn dựa trên o4-mini có chi phí thấp hơn, tự động chuyển sang bản nhẹ khi người dùng hết hạn mức[8][22]. Bản cập nhật tháng 2/2026 bổ sung khả năng kết nối deep research với bất kỳ MCP hoặc ứng dụng nào, giới hạn phạm vi tìm kiếm vào các trang tin cậy, theo dõi tiến độ thời gian thực và cho phép can thiệp giữa chừng bằng câu hỏi nối tiếp hoặc nguồn mới[8]. Trong giao diện Chat, kết quả kèm mục lục, phần danh sách nguồn đã dùng và lịch sử hoạt động để người dùng đối chiếu[9]. Deep Research cũng có mặt trong ChatGPT Work và Codex với hạn mức riêng, hỗ trợ các nguồn kết nối như Google Drive hay SharePoint cùng cơ chế phân quyền theo vai trò cho doanh nghiệp[9]. Ở lần ra mắt, hệ thống đạt 26,6% trên Humanity's Last Exam, vượt xa các mô hình cùng thời, nhưng chính OpenAI thừa nhận nó vẫn có thể bịa dữ kiện hoặc suy luận sai và chưa hiệu chỉnh độ tự tin tốt[8].

### Giá và giới hạn sử dụng

Chính sách hạn mức của OpenAI đã thay đổi nhiều lần: tháng 4/2025 họ nâng nhóm Plus, Team, Enterprise và Edu lên 25 truy vấn mỗi tháng, Pro lên 250 còn Free xuống 5[8]. Đến năm 2026, thay vì công bố một con số chung, OpenAI chỉ mô tả định tính Limited cho Free và Go, Expanded cho Plus và Maximum cho Pro, đồng thời yêu cầu người dùng theo dõi số tác vụ còn lại bằng bộ đếm trong sản phẩm, reset theo chu kỳ 30 ngày tính từ lần dùng đầu tiên[7][26]. Bài thử nghiệm của PCMag ghi nhận các mức cụ thể theo từng gói: Free có 15 lượt bản nhẹ mỗi tháng, nhóm Plus, Team và Edu được 10 lượt bản đầy đủ cộng 15 lượt bản nhẹ, Pro được 125 cộng 125[22]. Với doanh nghiệp, mỗi tác vụ vượt hạn mức tính khoảng 50 credit, còn tài khoản Edu có 5 truy vấn mỗi 24 giờ[26]. Về giá gói, Fast.io ghi nhận Go khoảng 8 USD, Plus 20 USD, Pro từ 100 USD mỗi tháng và Business 20 đến 25 USD mỗi ghế, trong khi mức 200 USD trước đây đã tạm dừng đăng ký mới[24][26]. Với nhà phát triển, OpenAI cung cấp API deep research chuyên dụng: o3-deep-research giá 10 USD mỗi triệu token đầu vào và 40 USD mỗi triệu token đầu ra, có thể kết nối dữ liệu riêng qua MCP connectors[10][11]. Chi phí công cụ web search là 10 USD cho 1.000 lần gọi, cộng thêm token nội dung tính theo giá mô hình[11].

## Google Gemini Deep Research: phân phối rộng và dữ liệu doanh nghiệp

### Sản phẩm dành cho người dùng

Với người dùng phổ thông, Gemini Deep Research tự động duyệt hàng trăm website và có thể đọc cả Gmail, Drive và Chat nếu được cho phép, rồi tổng hợp thành báo cáo nhiều trang trong vài phút[13]. Sản phẩm hỗ trợ chuyển báo cáo thành Audio Overview hoặc nội dung tương tác trong Canvas, hiện có mặt tại hơn 150 quốc gia và hơn 45 ngôn ngữ, bao gồm cả người dùng Google Workspace[13]. Hạn mức được Google tính theo mức tiêu thụ compute phụ thuộc độ phức tạp câu lệnh: gói Free có mức chuẩn, AI Plus gấp đôi, AI Pro gấp bốn, AI Ultra 100 USD gấp năm lần AI Pro và bản Ultra 200 USD gấp hai mươi lần[22]. Các gói Google One mô tả quyền truy cập Deep Research tăng dần theo cấp Plus, Pro và Ultra[14]. Trong bài so sánh độc lập của PCMag, Gemini thắng chung cuộc khi cho ra báo cáo dài và có chiều sâu nhất, kèm bảng biểu và biểu đồ, hoàn thành trong khoảng tám phút[22].

### API và bộ đôi Deep Research Max

Tháng 4/2026, Google ra mắt hai agent nghiên cứu trên nền Gemini 3.1 Pro: Deep Research tối ưu cho độ trễ thấp và Deep Research Max dùng thêm compute suy luận để đạt độ toàn diện cao nhất[15]. Cả hai hỗ trợ giao thức MCP để kết nối dữ liệu độc quyền, tạo biểu đồ và infographic gốc ngay trong báo cáo, và cho phép tắt hoàn toàn web để chỉ tìm trên dữ liệu riêng[15][25]. VentureBeat nhận định đây là bước ngoặt biến Deep Research từ tính năng tiêu dùng thành hạ tầng nền tảng dùng chung với Gemini App, NotebookLM, Google Search và Google Finance[25]. Qua Interactions API, nhà phát triển có thể yêu cầu agent trình kế hoạch nghiên cứu trước khi chạy, truyền tài liệu và hình ảnh đầu vào, tìm kiếm trên kho file riêng và định dạng đầu ra theo ý muốn[12]. Google ước tính một tác vụ Deep Research tiêu thụ khoảng 80 truy vấn tìm kiếm và 250 nghìn token đầu vào với chi phí cỡ 1 đến 3 USD, còn bản Max lên tới 160 truy vấn và 900 nghìn token với chi phí 3 đến 7 USD mỗi báo cáo[12][37]. Trong phân tích của Pillitteri, chi phí thực tế rơi vào khoảng 1,22 USD cho một lượt Deep Research tiêu chuẩn và 4,80 USD cho một lượt Max[37]. Google công bố Deep Research Max đạt 93,3% trên DeepSearchQA, tăng mạnh từ 66,1% hồi tháng 12/2025, và 54,6% trên Humanity's Last Exam[25][37].

## So sánh trực tiếp: tính năng, giá và tốc độ

### Bảng so sánh tổng quan

| Tiêu chí | Perplexity | OpenAI | Google |
|---|---|---|---|
| Sản phẩm chính | Deep Research trong Computer | Deep Research và agent mode | Deep Research trong Gemini, bộ đôi DR và DR Max qua API |
| Bản miễn phí | 1 truy vấn Research mỗi tháng[2] | Giới hạn, bản nhẹ vài lượt mỗi tháng[7][22] | Dùng thử cho cả người dùng miễn phí, hạn mức chuẩn[22] |
| Gói trả phí | Pro 20 USD, Max 200 USD[1] | Plus 20 USD, Pro từ 100 USD[26] | Plus, Pro, Ultra; Ultra từ 100 USD[22] |
| Hạn mức | Theo tháng, không công bố số cụ thể[2] | Bộ đếm 30 ngày, 10 đến 125 tác vụ tùy gói[22][26] | Theo compute, Ultra cao tới 20 lần Pro[22] |
| Thời gian điển hình | Khoảng 3 phút[22] | 5 đến 30 phút bản đầy đủ[9] | Khoảng 8 phút trên ứng dụng[22] |
| API cho nhà phát triển | Agent API và Sonar riêng | o3-deep-research 10 và 40 USD mỗi triệu token[10] | Interactions API, khoảng 1 đến 7 USD mỗi tác vụ[12][37] |
| Thế mạnh nổi bật | Tốc độ, chi phí, hệ sinh thái Computer[4][22] | Độ sâu phân tích, trích dẫn, hệ sinh thái ChatGPT[8][28] | Workspace, MCP, biểu đồ gốc, kênh doanh nghiệp[15][25] |

### Tốc độ và trải nghiệm người dùng

Về thời gian chờ, khoảng cách giữa ba hệ thống rất lớn: trong thử nghiệm của PCMag, Perplexity trả kết quả sau khoảng ba phút, Gemini khoảng tám phút, còn ChatGPT bản đầy đủ mất tới 49 phút trong khi bản nhẹ chỉ khoảng năm phút[22]. Số liệu độ trễ của DRACO cũng phản ánh tương quan này khi Perplexity chỉ mất trung bình 245 giây so với 1.808 giây của OpenAI o3, dù thời gian chạy dài hơn không mang lại điểm chất lượng cao hơn cho OpenAI trên chính bài đo đó[27]. Một khác biệt đáng chú ý là mức độ minh bạch tiến trình: Gemini và Perplexity đều hiển thị chi tiết các website đang được đọc, còn ChatGPT trả về rất ít thông tin trong lúc chạy và chỉ hiện đầy đủ khi hoàn tất[22]. Ngược lại, tổng hợp của Gradually.ai cho rằng Perplexity có lợi thế ở câu hỏi nối tiếp và cấu trúc báo cáo, trong khi báo cáo của ChatGPT đôi khi quá dài và nặng về các đoạn văn dày đặc[24]. Với doanh nghiệp, Google còn giới hạn cứng thời lượng nghiên cứu 60 phút mỗi tác vụ và yêu cầu chạy nền bất đồng bộ qua tham số background[37].

## Chất lượng qua các benchmark độc lập 2025–2026

### Kết quả không đồng nhất giữa các bài đo

DRACO, bộ 100 tác vụ thực tế do Perplexity công bố tháng 2/2026, xếp Perplexity Deep Research dẫn đầu với 70,5% khi chạy Opus 4.6 và 67,2% với Opus 4.5, tiếp theo là Gemini Deep Research 59,0%, OpenAI o3 52,1% và OpenAI o4-mini 41,9%[27][21]. Trên bốn trục chấm điểm, Perplexity dẫn đầu cả bốn với độ chính xác thực tế 67,9%, độ sâu phân tích 73,1%, chất lượng trình bày 90,3% và chất lượng trích dẫn 64,6%[27]. Tuy nhiên, chính bài đo này do Perplexity xây dựng với tác vụ lấy từ truy vấn người dùng Perplexity, nên giới phân tích khuyến nghị coi thứ hạng là tuyên bố cần kiểm chứng độc lập[21]. Chiều ngược lại, DeepResearch Bench II công bố tháng 9/2026 với 132 tác vụ và 9.430 rubric chấm theo ba trục lại xếp OpenAI o3 Deep Research dẫn đầu với 45,40 điểm, trên Gemini-3-Pro 44,60 và Perplexity Research 38,58[28]. Một khảo sát độc lập khác cũng kết luận OpenAI nổi trội về cấu trúc báo cáo còn Perplexity mạnh về thực hành trích dẫn nguồn[34]. Nghiên cứu về chất lượng cá nhân hóa lưu ý thêm rằng chỉ trang bị công cụ tìm kiếm cho mô hình ngôn ngữ là chưa đủ để đạt chất lượng của agent chuyên dụng[31]. Nhìn chung, các bài đo vẽ nên bức tranh phân hóa theo tiêu chí chứ không có người thắng tuyệt đối[27][28].

| Bài đo | Dẫn đầu | Vị trí tiếp theo | Lưu ý |
|---|---|---|---|
| DRACO, 2/2026 | Perplexity 70,5%[27] | Gemini 59,0%, OpenAI o3 52,1%[27] | Do Perplexity tự xây dựng[21] |
| DeepResearch Bench II, 9/2026 | OpenAI o3 45,40 điểm[28] | Gemini-3-Pro 44,60, Perplexity 38,58[28] | Không agent nào vượt 50% rubric[28] |
| Humanity's Last Exam thời kỳ đầu | OpenAI 26,6%[8] | Perplexity 21,1%[21] | Số liệu 2025, trước các bản nâng cấp lớn |

### Điểm mạnh và điểm yếu theo từng hệ thống

Điểm mạnh của Perplexity là tốc độ, chi phí và chất lượng trích dẫn tương đối cao so với mức giá, phù hợp cho nghiên cứu nhanh hằng ngày[22][27]. Điểm yếu của nó là các bài đo độc lập như DeepResearch Bench II cho thấy khả năng gợi nhớ thông tin và phân tích còn kém hai đối thủ, và một phần lợi thế trong DRACO đến từ bài đo do chính hãng xây[28][21]. OpenAI nổi bật ở khả năng gợi nhớ thông tin và tổng hợp phân tích sâu, đổi lại thời gian chạy dài, chi phí compute lớn và văn phong đôi khi dài dòng[28][27][24]. Google có điểm trình bày cao nhất trong DeepResearch Bench II với 91,85 điểm cho Gemini 3 Pro và dẫn đầu các kịch bản doanh nghiệp nhờ MCP cùng biểu đồ gốc[28][15]. Trong DRACO, Gemini vẫn xếp sau Perplexity[27] và các con số quảng cáo cần đặt cạnh lưu ý rằng so sánh giữa các phiên bản chưa được chuẩn hóa[37]. Một thử nghiệm trên lĩnh vực y khoa đăng trên JMIR còn cho thấy ngay cả công cụ mạnh nhất trong bốn công cụ họ thử vẫn mắc lỗi thực tế và trích dẫn các bài báo không tồn tại[33].

## Tin tức và cập nhật quan trọng trong 2025–2026

Nửa đầu năm 2025 chứng kiến cuộc đua ra mắt khi OpenAI đưa deep research tới Pro rồi mở rộng cho Plus và nâng hạn mức lên 25 đến 250 truy vấn mỗi tháng[8], còn Perplexity quảng bá bản miễn phí 5 truy vấn mỗi ngày và Pro tới 500 truy vấn mỗi ngày[22]. Tháng 7/2025, OpenAI gộp khả năng duyệt web trực quan vào agent mode của ChatGPT[8]. Tháng 12/2025, Google mở Interactions API cho nhà phát triển cùng bộ dữ liệu DeepSearchQA mã nguồn mở[25]. Tháng 2/2026, Perplexity công bố nâng cấp Deep Research lên Opus 4.5 kèm Model Council cho phép chạy ba mô hình song song để đối chiếu câu trả lời[5], còn OpenAI bổ sung kết nối MCP và cơ chế giới hạn nguồn tin cậy[8]. Tháng 4/2026, Google tung bộ đôi Deep Research và Deep Research Max trên Gemini 3.1 Pro, mở bản xem trước công khai qua API trả phí[15][25]. Tháng 6/2026, Perplexity đưa Deep Research vào Computer với kiến trúc Search as Code và tuyên bố cải thiện điểm số trên ba benchmark lớn[4]. Giai đoạn tháng 8 đến 9/2026 ghi nhận Google triển khai ưu đãi sinh viên với một năm AI Pro miễn phí tại Mỹ[17] và Perplexity ra mắt giao diện Advanced Deep Research với báo cáo dạng file chỉnh sửa được[3]. Xu hướng chung của cả ba nhà cung cấp là mở rộng từ web công khai sang dữ liệu riêng của tổ chức thông qua MCP[25][8][9].

## Hạn chế và rủi ro của cả ba dịch vụ

### Ảo giác và độ tin cậy trích dẫn

Mọi hệ thống đều thừa nhận hoặc bị ghi nhận còn hiện tượng ảo giác: OpenAI nói deep research có thể bịa dữ kiện và chưa hiệu chỉnh độ tự tin tốt[8], còn nghiên cứu trên JMIR ghi nhận các trích dẫn đến bài báo không tồn tại, gán sai bài viết cho nhà nghiên cứu có thật, lỗi thực tế khó phát hiện với người không chuyên và tình trạng thiếu trích dẫn ở phần cuối của các báo cáo dài[33]. Nghiên cứu JMIR cũng chỉ ra thiên lệch nguồn mở do công cụ không truy cập được tài liệu trả phí, cùng nguy cơ diễn giải lại từ các bài tổng quan có sẵn thay vì đọc nguồn gốc[33]. Bản thân DRACO cũng cho thấy chất lượng trích dẫn là trục yếu nhất của tất cả hệ thống khi điểm cao nhất chỉ đạt 64,6%[27]. DeepResearch Bench II kết luận ngay cả agent mạnh nhất cũng chưa vượt quá 50% số rubric trích từ báo cáo chuyên gia, cho thấy khoảng cách lớn so với chuyên gia người[28].

### Giới hạn vận hành, chi phí và minh bạch

Cả ba dịch vụ đều tồn tại những điểm mờ về hạn mức: Perplexity không công bố số truy vấn cụ thể cho Pro và Max[2], OpenAI bỏ con số chung khỏi trang giá và chuyển sang bộ đếm nội bộ[26], còn Google tính theo độ phức tạp nên người dùng khó dự đoán trước chi phí sử dụng[22]. Về chi phí, một tác vụ API của Google tốn khoảng 1 đến 7 USD[12][37]. Còn OpenAI tính 10 USD và 40 USD mỗi triệu token đầu vào và đầu ra cho bản o3-deep-research, với thời gian chạy có thể kéo dài hàng chục phút mỗi lần[10][8]. Quyền riêng tư cũng khác nhau: mặc định nội dung của người dùng Plus và Pro có thể được dùng để cải thiện mô hình trừ khi tắt trong phần Data Controls, trong khi nội dung của các gói doanh nghiệp không dùng để huấn luyện theo mặc định[9][2]. Ngoài ra, các báo cáo dài vẫn cần con người kiểm chứng: PCMag lưu ý người dùng nên đối chiếu ít nhất một vài luận điểm với nguồn gốc[22], còn VentureBeat đặt câu hỏi liệu chất lượng thực tế có đáp ứng chuẩn của ngành tài chính và y sinh hay không[25].

## Kết luận và khuyến nghị

Tổng hợp lại, ba dịch vụ không có người thắng tuyệt đối mà phân hóa theo nhu cầu sử dụng. Perplexity phù hợp khi cần câu trả lời nhanh, rẻ và có trích dẫn rõ ràng[22][27]. OpenAI phù hợp cho nghiên cứu chuyên sâu, báo cáo học thuật và người dùng đã ở trong hệ sinh thái ChatGPT[28][8]. Còn Google phù hợp với doanh nghiệp cần nối dữ liệu riêng, xuất bản báo cáo kèm biểu đồ và tận dụng Workspace[15][25][13]. Với nhà phát triển muốn nhúng năng lực nghiên cứu vào sản phẩm, API của Google hiện là lựa chọn kinh tế nhất tính theo tác vụ với khoảng 1 đến 3 USD cho bản tiêu chuẩn, còn API của OpenAI phù hợp hơn khi cần kiểm soát từng token và tùy biến sâu qua MCP[12][10][9]. Khuyến nghị thực dụng là không tin tuyệt đối vào bảng điểm tự công bố: hãy chạy thử cùng một câu hỏi nghiệp vụ trên cả ba dịch vụ, kiểm tra trích dẫn bằng mắt trước khi dùng cho quyết định quan trọng, và lưu ý rằng cả ba vẫn có thể ảo giác dù tỷ lệ đang giảm dần[21][33][8]. Trong trung hạn, cuộc đua sẽ xoay quanh dữ liệu doanh nghiệp qua MCP, chi phí compute cho mỗi báo cáo và khả năng kiểm chứng tự động, ba lĩnh vực mà cả Perplexity, OpenAI lẫn Google đều đang đầu tư mạnh trong các bản cập nhật gần nhất[4][15][8]. Một số khoảng trống thông tin cần lưu ý khi ra quyết định: giá chính xác của các gói Google AI Plus và AI Pro dành cho cá nhân không được nêu trong các nguồn đã thu thập, và hạn mức cụ thể của Perplexity Pro và Max cũng không được công bố công khai[2][14].

## Sources

[1] https://www.perplexity.ai/hub/pricing — Perplexity - goi dang ky & gia (hub)
[2] https://www.perplexity.ai/help-center/en/articles/11187416-which-perplexity-subscription-plan-is-right-for-you — Perplexity Help - so sanh cac goi dang ky
[3] https://www.perplexity.ai/help-center/en/articles/13600190-what-s-new-in-advanced-deep-research — Perplexity Help - Advanced Deep Research (9/2026)
[4] https://www.perplexity.ai/hub/blog/deep-research-now-in-computer — Perplexity Blog - Deep Research trong Computer (6/2026)
[5] https://www.perplexity.ai/changelog/what-we-shipped---february-6th-2026 — Perplexity Changelog - nang cap Deep Research (2/2026)
[7] https://openai.com/chatgpt/pricing — OpenAI - ChatGPT Pricing
[8] https://openai.com/index/introducing-deep-research — OpenAI - Introducing deep research (cap nhat 2/2026)
[9] https://help.openai.com/en/articles/10500283-research-faq-0 — OpenAI Help - Deep research trong ChatGPT (FAQ)
[10] https://platform.openai.com/docs/models/o3-deep-research — OpenAI Platform - model o3-deep-research
[11] https://platform.openai.com/docs/pricing?latest-pricing=standard — OpenAI Platform - bang gia API
[12] https://ai.google.dev/gemini-api/docs/deep-research — Google - Gemini Deep Research agent (tai lieu API, 4/2026)
[13] https://gemini.google/bf/overview/deep-research/?hl=en-GB — Gemini - Deep Research overview
[14] https://one.google.com/intl/en/about/google-ai-plans — Google One - cac goi Google AI (gia)
[15] https://blog.google/innovation-and-ai/models-and-research/gemini-models/next-generation-gemini-deep-research — Google Blog - Deep Research Max (4/2026)
[17] https://gemini.google/release-notes — Gemini Apps - release notes
[21] https://www.secondtalent.com/resources/perplexity-ai-features-capabilities-2026 — SecondTalent - tinh nang Perplexity 2026
[22] https://www.pcmag.com/explainers/chatgpt-gemini-perplexity-grok-deep-research-one-ai-chatbot-best — PCMag - so sanh thu nghiem Deep Research
[24] https://www.gradually.ai/en/deep-research — Gradually.ai - so sanh Deep Research
[25] https://venturebeat.com/technology/googles-new-deep-research-and-deep-research-max-agents-can-search-the-web-and-your-private-data — VentureBeat - Google ra mat Deep Research & Deep Research Max
[26] https://fast.io/resources/chatgpt-deep-research-limits — Fast.io - gioi han Deep Research cua ChatGPT
[27] https://arxiv.org/html/2602.11685 — arXiv - DRACO benchmark (2026)
[28] https://arxiv.org/html/2601.08536v3 — arXiv - DeepResearch Bench II (2026)
[31] https://arxiv.org/html/2509.25106v3 — arXiv - Personalized Deep Research benchmark (2025)
[33] https://www.jmir.org/2025/1/e75666/PDF — JMIR - Perspective on Deep Research Tools (2025)
[34] https://arxiv.org/html/2506.12594 — arXiv - Comprehensive Survey of Deep Research
[37] https://pasqualepillitteri.it/en/news/1191/google-deep-research-max-gemini-3-1-pro-ai-agents — Pillitteri - Deep Research Max tren Gemini 3.1 Pro (2026)
