## Mục tiêu và phạm vi review

Bản tích hợp phục vụ team kiểm tra các sửa Grades, bảng điểm/Excel, hiệu năng khóa học, lưu nháp IDE và API Cầy Cốt trước khi chọn phạm vi merge/deploy. Base là e665d7b, mốc code backlog đầu là 3932b12; mốc code UI và QA toàn hệ thống mới nhất là 7ce56f8. Các commit tài liệu bổ sung nằm sau mốc code.

Thay đổi chính:

- Grades dùng fingerprint theo dữ liệu từng dòng để phát hiện thay đổi dù tổng điểm/trọng số giữ nguyên.
- Thêm STT và tổng điểm số, xuất XLSX theo bộ lọc/quyền; batch tính tiến độ nhiều khóa.
- Sửa vòng đời Ace sau AJAX và nháp theo user/contest/problem/language; tích hợp strict IDE và sửa chuyển bài/ngôn ngữ.
- Bổ sung thanh chuyển bài bằng số cho IDE Lesson/Contest với bốn màu theo kết quả tốt nhất, cập nhật sau chấm; ẩn lựa chọn máy chấm, dùng nhãn Run và bố cục đề gọn trên desktop/mobile.
- Trang đề Lesson/Contest dùng cột bài trái thu gọn với số và dấu trạng thái, chung quy tắc với IDE; trả menu gốc và căn logo, đặt IDE cạnh PDF, thu gọn sidebar/form nộp.
- Thêm bốn endpoint GET cho Cầy Cốt, token hash/scope, paging, che kết quả và quota; kèm contract.
- Chuẩn bị xử lý AI Markdown/quyền task và khung kiểm kê nguồn đề; sửa chọn Language mặc định theo DB hiện tại.

Refs #44, #135, #140, #39, #110, #138, #109, #124.

## Phụ thuộc và trạng thái chưa hoàn tất

Nhánh đã tích hợp nội dung PR #141 và #142 cùng sửa tiếp theo. Maintainer cần chốt thứ tự merge, cập nhật nhánh theo main và review diff cuối. Một số commit chứa nhiều nhóm thay đổi; nếu tách release cần kiểm tra phụ thuộc và chạy lại test từng nhánh.

| Issue | Trạng thái bàn giao |
|---|---|
| #109 | Chờ chủ dự án thêm API key; mới có code chuẩn bị và test giả lập |
| #124 | Chờ ngân hàng bài thật trên DB; mới kiểm tra khung kiểm kê bằng dữ liệu QA |
| #113 | Chưa triển khai, ngoài phạm vi chức năng mới của PR |
| #112 | Chưa triển khai, ngoài phạm vi chức năng mới của PR |

## Kiểm thử đã thực hiện tại local

- UI lần 4, code 7ce56f8: **1.364 PASS**, 119,343 giây; 55 test liên quan PASS. Menu gốc, cột bài trái thu gọn, bốn trạng thái, sidebar/form gọn. Đã kiểm tra Lesson/Contest, thu/mở cột, viewport 390px và nộp #84 AC 100/100. Chi tiết: `docs/handoff-2026-11-14/06_MENU_CU_VA_COT_BAI_THU_GON.md`. Thiết kế này thay thế menu và dải bài ngang của UI lần 3.
- UI lần 3, code d21c202: **59 test liên quan PASS**, 7,968 giây. Làm nổi IDE cạnh PDF, dải bài ngang Lesson/Contest và menu mới. Đã kiểm tra desktop/tablet/mobile, theme tối, menu con bằng bàn phím và link vào IDE; chưa chạy lại full suite trong lượt template/CSS này. Chi tiết: `docs/handoff-2026-11-14/05_MO_IDE_BO_CUC_VA_MENU.md`.
- UI lần 2, code 5284d4d: **1.364 PASS**, 116,201 giây; 88 test nhóm UI PASS. Toolbar IDE một dòng, thêm dấu ✓/✕/○, sidebar chia nhóm và form “Nộp lời giải”. Tải tệp C++ và nộp #81/#82 AC ngay trong trang. Chi tiết: `docs/handoff-2026-11-14/04_TINH_GON_IDE_SIDEBAR_FORM.md`.
- Sau bổ sung UI tại code 96acf04: **1.364 test toàn hệ thống PASS**, 111,539 giây; 14 test mới kiểm tra trạng thái, quyền, context và máy chấm mặc định. Đã thử Run thật và submission #79 CE/#80 AC. Chi tiết trong `docs/handoff-2026-11-14/03_GIAO_DIEN_IDE_VA_DE_BAI.md`.
- Bộ test toàn hệ thống: **1.349 PASS**, 110,523 giây.
- Bổ sung ma trận API sau lượt test tổng: **9 PASS**, 0,865 giây. Chưa chạy lại bộ tổng sau test bổ sung này.
- Nhóm chức năng mới: **108 PASS**.
- QA có 103 học sinh, hai trang 100 + 3; điểm phần, điểm lẻ, đồng hạng, lọc và quyền xuất.
- Submission #67–78 giữ tại local; nộp sau AJAX #76 AC; strict IDE #77 AC, #78 WA 30/100 qua judge thật.
- HTTP API kiểm tra token/scope/paging; kiểm kê sáu kiểu nguồn đề QA. URL ảnh/PDF QA là fixture tham chiếu, chưa kiểm tra OCR/tệp đề thật.

Evidence, cấu hình test và checklist chi tiết: `docs/handoff-2026-11-14/README.md` và `01_KIEM_THU_VA_NGHIEM_THU.md` cùng thư mục. Môi trường Docker và script seed QA riêng của bộ cài Windows không nằm trong nhánh review; team dùng test fixtures trong source và môi trường QA của mình để tái kiểm thử.

## Điểm cần xử lý/nghiệm thu trước khi merge phạm vi tương ứng

- [ ] #140: sửa phần trăm Excel từ thang 0–100 về tỷ lệ 0–1 với number format %, kiểm tra trực tiếp Microsoft Excel và tiếng Việt.
- [ ] #110: thử đầy đủ Hủy/Xác nhận Reset IDE; kiểm tra draft/các phiên trình duyệt.
- [ ] #39: benchmark toàn trang Courses; 15 query hiện chỉ đo helper trên 9 khóa.
- [ ] #135: nghiệm thu poll/giao diện cho từng tình huống và tải lớp mục tiêu.
- [ ] #138: duyệt contract/scope và kiểm tra client Cầy Cốt.
- [ ] Chốt giữ hay tách phần chuẩn bị #109/#124 khỏi release; giữ nguyên trạng thái chờ điều kiện.
- [ ] Đối chiếu #141/#142 và diff cuối, chạy lại test trên SHA sẽ merge.
- [ ] Rà cache-bust CSS/JS và kế hoạch staging/VPS/rollback.

Workflow lint hiện lọc master và không chạy bộ Django test cho PR vào main; đính kèm log kiểm thử thực tế của team hoặc bổ sung CI được duyệt.

## Triển khai

Dependency mới: `openpyxl>=3.1.5,<4`. Nhánh hiện không thêm migration; kiểm tra lại diff release cuối. Cần build static, cập nhật cache token liên quan và restart site/bridge/celery. API token, scope và AI key dùng cấu hình riêng từng môi trường. Quy trình chi tiết: `docs/handoff-2026-11-14/02_QUY_TRINH_GIT_MERGE_VPS.md`.

PR ở trạng thái Draft để review và test. Việc merge PR không đồng nghĩa hoàn tất các issue còn thiếu key, dữ liệu hoặc chức năng chưa triển khai.
