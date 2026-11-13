# Tổng kết code và tài liệu — mrtee-hncode v1.0.0-rc.2

**Ngày chốt hồ sơ: 14/11/2026. Trạng thái: bàn giao để review và kiểm thử.**

## 1. Định danh bản bàn giao

| Thuộc tính | Giá trị |
|---|---|
| Tên gói | `mrtee-hncode-v1.0.0-rc.2` |
| Nhánh bàn giao local | `mrtee-hncode-v1.0.0-rc.2` |
| Repository đích | [tinkerpartners/hncode-edu](https://github.com/tinkerpartners/hncode-edu) |
| Base | `e665d7b3642478fbeb102b45411ad56d59197a3f` |
| Code chức năng/UI mới nhất | `415e672a1dccf95e3bbefaddb9982c98bbb1f40e` |
| SHA chứa toàn bộ hồ sơ bàn giao | Ghi trong `RELEASE_MANIFEST.json` ở gói ZIP, sau commit tài liệu này |
| Checkout review | `D:\AI-Code\Build_HNCode\.local\worktrees\backlog-20261113` |
| Bộ cài đang chạy | `D:\AI-Code\Build_HNCode`, nhánh `codex/local-build`, nhận các sửa đã chọn từ checkout review |
| Nhánh local cũ | `codex/backlog-20261113`, giữ lại ở mốc `2654cdd` để đối chiếu |
| Migration mới | 0 so với base |
| Dependency bổ sung | `openpyxl>=3.1.5,<4` |

`v1.0.0-rc.2` là phiên bản **gói bàn giao của MrTee**, không thay phiên bản chính thức của upstream và không xác nhận sẵn sàng production. Không tạo release/tag production trong lượt bàn giao.

Đã kiểm tra GitHub bằng tài khoản `tungfint`: quyền `READ` ở repo đích, chưa có nhánh `mrtee-hncode*` tại origin; fork `tungfint/hncode-edu` chưa truy cập được qua API tại thời điểm kiểm tra. Nhánh mới hiện ở local. Chưa push, mở PR mới, merge hoặc triển khai VPS.

## 2. Tổng hợp code theo chức năng

| Nhóm | Thay đổi hiện có | File trọng tâm | Trạng thái |
|---|---|---|---|
| Grades — #135 | Fingerprint theo dữ liệu từng dòng, phát hiện đổi điểm/trọng số dù tổng không đổi | `judge/utils/grades_version.py`, `judge/views/course.py` | Có code và regression test; cần nghiệm thu polling/giao diện/tải |
| Bảng điểm — #140 | STT, tổng điểm số và xuất XLSX theo bộ lọc/quyền | `judge/utils/grades_export.py`, templates Course/Lesson/Contest | RC2 đã sửa scale/format % và thêm XLSX Contest; chờ nghiệm thu Microsoft Excel |
| Hiệu năng khóa học — #39 | Batch tính tiến độ nhiều khóa | `judge/views/course.py` | Helper QA 9 khóa dùng 15 query; chưa benchmark toàn trang theo issue |
| IDE/AJAX — #110, #141 | Sửa Ace sau điều hướng AJAX; nháp riêng theo user/problem/language/context | `resources/ide.js`, `resources/common.js`, Ace widget, submit JS | Đã kiểm tra nộp thật; Reset còn nghiệm thu đầy đủ Hủy/Xác nhận |
| Strict IDE — #142 | Dùng IDE mới trong contest giám sát, giữ session gate/ngữ cảnh | `judge/views/contests.py`, `resources/contest-strict.js` | Có server test và bài nộp qua judge thật; cần QA theo chính sách cuộc thi |
| Điều hướng IDE | Ô số bài Lesson/Contest; màu và dấu ✓/✕/○/trống; Run; ẩn chọn máy chấm, dùng Bất kỳ | `judge/views/ide.py`, `judge/utils/problem_navigation.py`, `resources/ide.*` | Đã áp dụng local và kiểm tra |
| Trang đề cuối cùng | Menu gốc, căn logo; IDE cạnh PDF; cột bài trái mặc định STT/tên/điểm, thu gọn thành ô số; cột 220px, tên dài dùng …, mỗi bài 32px một dòng; sidebar/form gọn | `templates/problem/`, sidebar Contest, `resources/problem-detail.css`, `resources/logo-alignment.css` | UI cột bài tại `9579d6b`; RC2 thêm editor tối/nền xanh sẫm và nút Excel gọn |
| API Cầy Cốt — #138 | 4 endpoint GET dưới `/api/integrations/v1`, token hash/scope, paging, che kết quả và quota | `judge/views/api/cay_cot.py`, `dmoj/urls.py` | Đạt test/HTTP QA; chờ duyệt contract và client tích hợp |
| AI — #109 | Chuẩn bị luồng Markdown, xử lý lỗi provider và quyền đọc draft | `judge/tasks/llm.py`, `judge/views/tasks.py`, `llm_service/`, `ai_features/` | **Chờ API key**, chưa nghiệm thu AI thật |
| Kiểm kê bài — #124 | Phân loại nguồn đề, quản trị, JSONL/prompt và command inventory | `judge/utils/problem_inventory.py`, view/command tương ứng | **Chờ ngân hàng bài thật trên DB**; mới có QA tổng hợp |
| Sửa nền | Chọn Language mặc định hợp lệ theo DB | `judge/models/runtime.py`, `judge/forms.py` | Có regression test |

**#113 và #112 chưa triển khai.** Hồ sơ này không đánh dấu các issue trên là đã hoàn thành chỉ vì có code hoặc test local. #44 có thể đề xuất Closed sau xác nhận hồi quy của team; chưa thay đổi trạng thái issue trong lượt bàn giao.

## 3. Phạm vi QA và giới hạn

| Bằng chứng | Phiên bản/kết quả |
|---|---|
| Full suite gần nhất | `415e672`: **1.370 PASS**, 142,313 giây — [log](evidence/rc2/full-suite-final.log) |
| Kiểm tra UI cột bài trước RC2 | `9579d6b`: **24 PASS**, 5,026 giây — [log](evidence/ide-ui-v5/tests.log) |
| Trình duyệt UI cuối | Lesson 7 bài, Contest 2 bài, mỗi hàng 32px; ellipsis, tooltip tên đầy đủ, mở/thu bằng chuột và phím Space — [báo cáo](07_COT_BAI_MOT_DONG.md) |
| Máy chấm thật | Có các lượt AC/CE/WA và điểm phần; #84 AC 100/100 qua form gọn; giữ dữ liệu trên local |
| Bảng điểm | 103 học sinh, phân trang 100 + 3; điểm lẻ, đồng hạng và quyền xuất |
| API/kiểm kê | Token/scope/paging/verdict và sáu nguồn đề QA; chưa thay thế nghiệm thu client hoặc dữ liệu thật |

Full suite đã chạy lại cho RC2, gồm các test mới về XLSX Contest và phần trăm Excel; ảnh và màu nền cuối được kiểm tra trực tiếp bằng trình duyệt. Team phải chạy lại trên đúng SHA dự kiến merge, đặc biệt sau cập nhật main hoặc giải quyết conflict.

Dữ liệu QA, tài khoản và bài nộp đang ở bộ cài local. Gói bàn giao không kèm DB dump, mật khẩu, token hay cấu hình máy cá nhân. Source có TestCase để team tạo dữ liệu trong test DB của mình; checklist liệt kê dataset thủ công cần dựng thêm.

## 4. Các việc còn lại trước khi duyệt phạm vi tương ứng

1. Nghiệm thu #140 bằng Microsoft Excel: RC2 đã sửa tỷ lệ/format %, cần đối chiếu hiển thị thực tế, tiếng Việt và XLSX Contest.
2. Nghiệm thu Reset IDE: cả Hủy và Xác nhận, giữ đúng bài/ngôn ngữ/context.
3. Benchmark #39 toàn trang và kiểm tra tải/poll #135 theo dữ liệu mục tiêu.
4. Duyệt contract/scope #138 và chạy client Cầy Cốt thật.
5. Chốt giữ hay tách code chuẩn bị #109/#124; không coi đã nghiệm thu khi thiếu key/DB.
6. Đối chiếu PR [#141](https://github.com/tinkerpartners/hncode-edu/pull/141) và [#142](https://github.com/tinkerpartners/hncode-edu/pull/142), đang OPEN khi kiểm tra. Nhánh này đã tích hợp nội dung và sửa tiếp theo, cần thống nhất thứ tự merge.
7. Chạy kiểm tra tự động và checklist trên SHA cuối. Workflow hiện lọc `master` và chỉ lint, chưa phải bằng chứng Django tests cho PR vào `main`.
8. Maintainer duyệt rồi mới merge; Ops triển khai staging/VPS, sao lưu và có phương án rollback.

## 5. Danh mục tài liệu cho team

| Tài liệu | Người dùng / mục đích |
|---|---|
| [Gửi dev và quản lý nhánh](08_GUI_DEV_VA_QUAN_LY_NHANH.md) | Chủ dự án và dev lead: nhận bundle, push đúng repo, mở Draft PR, đặt phiên bản |
| [README bàn giao](README.md) | Dev/QA: trạng thái issue và evidence tổng hợp |
| [Checklist nghiệm thu](01_KIEM_THU_VA_NGHIEM_THU.md) | QA: môi trường, dataset, case và mẫu biên bản |
| [Git, merge và VPS](02_QUY_TRINH_GIT_MERGE_VPS.md) | Dev lead/Ops: quan hệ PR, release và rollback |
| [Mô tả Draft PR](PR_DRAFT.md) | Dùng làm PR body, cập nhật số PR và kết quả team |
| [API Cầy Cốt](../CAY_COT_API.md) | Backend/client tích hợp |
| [Kiểm kê ngân hàng bài](../PROBLEM_INVENTORY.md) | Dev/admin: phần đã có và giai đoạn cần hoàn thiện |
| UI 03–07, báo cáo RC2 09 và evidence | Lịch sử thay đổi; áp dụng UI cuối ở 06 + 07, RC2 tại [báo cáo 09](09_RC2_DARK_VA_XUAT_RANKING.md) cập nhật theme/export; các thiết kế cũ có thể đã được thay thế |

## 6. Cách quản lý sau bàn giao

Một Draft PR là đầu mối review bản tích hợp; dev lead có thể tách PR nhỏ theo phụ thuộc. Mỗi lỗi còn lại cần có issue/case, người phụ trách, SHA sửa và kết quả test. Tiếp tục đẩy commit bình thường lên cùng nhánh review; khi chốt gói RC mới, dùng `mrtee-hncode-v1.0.0-rc.3` từ bản đã cập nhật, ghi manifest mới. Maintainer quyết định tag/release chính thức theo quy ước của dự án sau nghiệm thu.
