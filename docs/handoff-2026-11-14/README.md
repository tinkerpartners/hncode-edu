# Bàn giao HNCode cho đội phát triển

**Ngày bàn giao 14/11/2026 · Trạng thái chờ review và kiểm thử**

**Bắt đầu tại [Tổng kết RC4](00_TONG_KET_BAN_GIAO_RC4.md), [UX/hiệu năng RC3](10_RC3_UX_VA_HIEU_NANG.md) và [Hướng dẫn gửi dev/quản lý nhánh](08_GUI_DEV_VA_QUAN_LY_NHANH.md).** Nhánh bàn giao hiện tại là `mrtee-hncode-v1.0.0-rc.4`, đã push lên GitHub và có [Draft PR #143](https://github.com/tinkerpartners/hncode-edu/pull/143) vào main. RC1–RC3 được giữ làm mốc lịch sử. **Đã có lệnh và đã tạo PR; chờ review/QA trước merge.**

Bản mới nhất bổ sung [kiểm tra hồi quy/chuẩn bị PR RC4](11_RC4_KIEM_TRA_HOI_QUY_VA_CHO_PUSH.md): 1.374 Django test PASS trên checkout review và 12 test JavaScript PASS; runtime giữ nguyên RC3.

Thông tin bàn giao hiện tại: [PR #143 và hướng dẫn dev lead](12_DA_PUSH_VA_PR143.md). Các evidence RC1–RC4 là snapshot trước khi xuất bản.

## 1. Mục tiêu và phiên bản

Gói bàn giao phục vụ review code, tái kiểm thử, quyết định merge và chuẩn bị triển khai VPS. Kết quả local là bằng chứng ban đầu để team đối chiếu. Việc nghiệm thu release được ghi riêng trong checklist.

| Nội dung | Giá trị |
|---|---|
| Repository | [tinkerpartners/hncode-edu](https://github.com/tinkerpartners/hncode-edu) |
| Base của đợt sửa | `e665d7b3642478fbeb102b45411ad56d59197a3f` |
| Mốc code đã bàn giao | `3932b12` |
| Mốc code mới nhất | `b5da9b3` — UX/Ranking và cache Profile RC3; kế thừa `415e672` — nền trang xanh sẫm/editor tối, XLSX Contest, nút Excel gọn và sửa format %; kế thừa UI cột bài tại `9579d6b` |
| Nhánh bàn giao local | `mrtee-hncode-v1.0.0-rc.4` (kế thừa RC1 và `codex/backlog-20261113`) |
| Checkout chứa commit để review | `D:\AI-Code\Build_HNCode\.local\worktrees\backlog-20261113` |
| Checkout đang chạy local | `D:\AI-Code\Build_HNCode` |
| Website trên máy đã cài | `http://localhost:8001/` |
| Schema DB | Diff so với base có 0 migration mới |
| Dependency bổ sung | `openpyxl>=3.1.5,<4` trong requirements.txt |

Checkout đang chạy ứng dụng có các tùy chỉnh vận hành và nhận code qua patch. Khi tạo PR, sử dụng nhánh trong checkout review ở bảng trên. Mốc code backlog đầu là 3932b12; mốc UI đầu là 96acf04. UI lần 4 thay thế thiết kế menu/dải bài ngang của UI lần 3; UI lần 5 tinh gọn cột bài tại 9579d6b. Commit tài liệu nằm sau mốc code. Các lượt kiểm thử ghi SHA và log trong báo cáo tương ứng.

## 2. Cách đọc tài liệu

1. Đọc ma trận phạm vi bên dưới để xác định hạng mục cần kiểm tra.
2. Thực hiện [hướng dẫn kiểm thử và checklist nghiệm thu](01_KIEM_THU_VA_NGHIEM_THU.md).
3. Chọn phạm vi release, review quan hệ với PR hiện có và thực hiện [quy trình branch, PR, merge và VPS](02_QUY_TRINH_GIT_MERGE_VPS.md).
4. Dùng [nội dung Draft PR](PR_DRAFT.md) làm mô tả ban đầu, cập nhật kết quả của team trước khi chuyển Ready for review.
5. Đọc [bổ sung UI và QA lần đầu](03_GIAO_DIEN_IDE_VA_DE_BAI.md), áp dụng cho code sau mốc bàn giao đầu.
6. Áp dụng [cập nhật UI lần 2](04_TINH_GON_IDE_SIDEBAR_FORM.md): gộp toolbar, thêm dấu trạng thái, tổ chức sidebar và form nộp.
7. [UI lần 3](05_MO_IDE_BO_CUC_VA_MENU.md) là lịch sử thiết kế nút IDE cạnh PDF, bố cục và menu.
8. Áp dụng [UI lần 4](06_MENU_CU_VA_COT_BAI_THU_GON.md): menu gốc, cột bài trái thu gọn, bỏ ba tiêu đề cột phải và form nộp gọn; 1.364 test PASS.
9. Áp dụng [UI lần 5 mới nhất](07_COT_BAI_MOT_DONG.md): cột bài 220px, tên dài có dấu ba chấm, một dòng mỗi bài, chỉ giữ nút thu/mở ở header; 24 test liên quan PASS.
10. Lịch sử [RC2](09_RC2_DARK_VA_XUAT_RANKING.md): nền xanh sẫm/editor tối; XLSX Contest, nút Excel gọn và tỷ lệ % đúng; 1.370 test PASS.
11. Áp dụng [RC3](10_RC3_UX_VA_HIEU_NANG.md): UX/Ranking, bỏ N+1 Profile; 1.374 test PASS và benchmark local trước/sau.

Tài liệu chuyên biệt gồm [API Cầy Cốt](../CAY_COT_API.md) và [kiểm kê ngân hàng bài](../PROBLEM_INVENTORY.md).

## 3. Phạm vi và trạng thái chính thức

Ma trận này áp dụng xác nhận mới nhất của chủ dự án. “Có code chuẩn bị” là trạng thái kỹ thuật, còn “đã nghiệm thu issue” phụ thuộc đầy đủ dữ liệu và tiêu chí chấp nhận.

| Issue | Trạng thái | Đã có trong nhánh | Phần còn lại / điều kiện |
|---|---|---|---|
| [#44](https://github.com/tinkerpartners/hncode-edu/issues/44) | Có thể đề xuất Closed | Sửa liên quan đã ở main qua PR #121/#133, nhóm test đạt | Team xác nhận hồi quy và ghi kết quả trên issue |
| [#135 / A16](https://github.com/tinkerpartners/hncode-edu/issues/135) | Đã sửa cốt lõi, chờ nghiệm thu | Token Grades lấy dữ liệu từng dòng, regression test cho thay đổi giữ nguyên tổng | Poll từng tình huống, trạng thái giao diện, đo tải lớp mục tiêu, merge |
| [#140 / A28](https://github.com/tinkerpartners/hncode-edu/issues/140) | Đã sửa scale/format % ở RC2, chờ nghiệm thu | STT, tổng điểm số, XLSX Course/Lesson/Contest, bộ lọc/quyền và nút gọn | Mở bằng Microsoft Excel, kiểm tra trên môi trường QA của team |
| [#39](https://github.com/tinkerpartners/hncode-edu/issues/39) | Đã tối ưu, đã đo toàn trang local | Batch nhiều khóa; RC3 bỏ N+1 Profile; benchmark 28 route/vai trò local | Đo toàn trang trong điều kiện tương đương issue |
| [#110 / A20](https://github.com/tinkerpartners/hncode-edu/issues/110) | Đã sửa, còn nghiệm thu Reset | Nháp theo user/contest/problem/language, offline, AJAX và IDE giám sát | Thử Hủy/Xác nhận Reset bằng trình duyệt bình thường |
| [#138 / A27](https://github.com/tinkerpartners/hncode-edu/issues/138) | API local đã có, chờ nghiệm thu tích hợp | 4 endpoint, token hash, scope, phân trang, che kết quả, quota và tài liệu | Team duyệt contract/scope, kiểm tra client Cầy Cốt và cấu hình môi trường đích |
| [#109 / A19](https://github.com/tinkerpartners/hncode-edu/issues/109) | **Chờ chủ dự án thêm API key** | Chuẩn bị luồng Markdown, xử lý lỗi và quyền đọc bản đề xuất, test giả lập | Thêm key và nghiệm thu provider thật |
| [#124 / A25](https://github.com/tinkerpartners/hncode-edu/issues/124) | **Chờ dữ liệu ngân hàng bài thật trên DB** | Khung kiểm kê, phân loại nguồn, JSONL và prompt v1 | Nhập dữ liệu được cấp quyền, thí điểm và hoàn thiện các giai đoạn chuẩn hóa |
| [#113 / A21](https://github.com/tinkerpartners/hncode-edu/issues/113) | **Chưa triển khai** | Nghiên cứu cấu trúc nguồn/đích | Thiết kế ánh xạ, preview, quyền đích và triển khai chuyển đổi |
| [#112 / A24](https://github.com/tinkerpartners/hncode-edu/issues/112) | **Chưa triển khai** | Nghiên cứu quan hệ dữ liệu | Chính sách xóa, preview tác động, thực hiện/rollback và thử restore |

DB local hiện có bài QA tổng hợp. Những bản ghi này phục vụ kiểm tra chức năng, còn việc nghiệm thu #124 cần ngân hàng bài thật do chủ dự án cung cấp.

## 4. Những thay đổi cần review kỹ

| Nhóm | File tiêu biểu | Điểm cần đối chiếu |
|---|---|---|
| Grades, hiệu năng, Excel | `judge/utils/grades_version.py`, `judge/views/course.py`, `judge/utils/grades_export.py` | Đủ đầu vào token, công thức/trọng số, quyền và query toàn trang |
| IDE/AJAX | `resources/ide.js`, `resources/common.js`, `django_ace/static/django_ace/widget.js`, `templates/problem/submit-form-media-js.html` | Vòng đời editor, draft đúng scope, response tới đúng bài, Reset |
| Strict contest | `judge/views/contests.py`, `resources/contest-strict.js` | Fullscreen, session gate, scope recent submissions và log vi phạm |
| API Cầy Cốt | `judge/views/api/cay_cot.py`, `dmoj/urls.py` | Scope và token, ngữ nghĩa điểm, ẩn kết quả, paging/rejudge, JSON errors |
| AI chuẩn bị | `judge/tasks/llm.py`, `judge/views/tasks.py`, `llm_service/`, `ai_features/` | Quyền đọc task, tương thích caller cũ, timeout và lỗi an toàn |
| Kiểm kê chuẩn bị | `judge/utils/problem_inventory.py`, `judge/views/problem_inventory.py`, command `inventory_problems` | Bài riêng, asset, bản dịch, đọc theo batch và nguồn dữ liệu thật |
| Sửa nền | `judge/models/runtime.py` | ID ngôn ngữ mặc định hợp lệ sau xóa/rollback/thay DB |

Nhánh có nội dung PR [#141](https://github.com/tinkerpartners/hncode-edu/pull/141), PR [#142](https://github.com/tinkerpartners/hncode-edu/pull/142) và các sửa tiếp theo. Maintainer cần thống nhất thứ tự merge để tránh review trùng hoặc bỏ sót phần phụ thuộc.

## 5. Bằng chứng hiện có

| Bằng chứng | Kết quả và giới hạn |
|---|---|
| RC4, test f1c1e7b | **1.374 PASS**, 111.365 giây trên checkout review; **12 JS PASS**; [log](evidence/rc4/review-checkout-full-suite.log) |
| RC3 tại b5da9b3 | **1.374 PASS**, 148,329 giây; [log](evidence/rc3/full-suite-final.log); [UX/hiệu năng](10_RC3_UX_VA_HIEU_NANG.md) |
| RC2 tại 415e672 | **1.370 PASS**, 142,313 giây; [log](evidence/rc2/full-suite-final.log); [workbook mới](evidence/rc2/xlsx-checks.json) |
| QA UI lần 5 tại 9579d6b | **24 PASS**, 5,026 giây; [log](evidence/ide-ui-v5/tests.log); kiểm tra trực tiếp Lesson/Contest và nút thu/mở |
| QA UI lần 4, mới nhất tại 2a19d11 | **1.364 PASS**, 126,956 giây; [log](evidence/ide-ui-v4/full-suite-final.log); nộp #84 AC 100/100 tại lượt sửa form |
| QA UI lần 2 | **1.364 PASS**, 116,201 giây; [log](evidence/ide-ui-v2/full-suite-final.log) |
| Bộ test sau bổ sung giao diện | **1.364 PASS**, 111,539 giây; [log mới nhất](evidence/ide-ui/full-suite-final.log) |
| Bộ test toàn hệ thống tại local | 1.349 test PASS, 110,523 giây; [log](evidence/full-suite-final.log) |
| Ma trận API bổ sung sau lượt test tổng | 9 test PASS, 0,865 giây; [log](evidence/api-verdict-matrix.log) |
| Nhóm các chức năng mới | 108 test PASS; [log](evidence/final-features-tests.log) |
| Bảng điểm | 103 học sinh, chia 100 + 3 dòng; điểm phần, điểm lẻ và đồng hạng |
| XLSX RC2 | [Course 103 dòng](evidence/rc2/ranking-course-103.xlsx), [Lesson 103 dòng](evidence/rc2/ranking-lesson-103.xlsx), [Contest](evidence/rc2/ranking-contest.xlsx); đã sửa format %, chưa nghiệm thu trực tiếp Microsoft Excel |
| Máy chấm thật | Bài nộp #67–78 được giữ trên local, gồm AC và WA 30/100 |
| API HTTP | [Kết quả token/scope/paging](evidence/cay-cot-qa-results.json) |
| Kiểm kê | [Kết quả sáu nguồn đề QA](evidence/inventory-qa-results.json), chưa nghiệm thu ngân hàng thật |

Các con số trên thuộc môi trường local và mốc code đã ghi. Team cần gắn SHA của phiên bản mình kiểm tra vào biên bản. Một số test cố ý phát sinh lỗi và ghi traceback, cần đọc kết quả cuối của log.

Ảnh đối chiếu, danh mục file và giới hạn của từng bằng chứng ở [Evidence QA](evidence/README.md). Dữ liệu QA và bài nộp được giữ trên bộ cài local để chủ dự án kiểm tra; tài liệu công khai không kèm mật khẩu, token hay DB dump.

## 6. Tiêu chí sẵn sàng merge

- [ ] Team thống nhất scope PR và quan hệ với #141/#142.
- [ ] Hoàn tất các kiểm tra của #135, #140, #39 và #110 trong checklist.
- [ ] Các test được chạy lại trên đúng SHA sẽ merge, sau khi giải quyết conflict.
- [ ] Owner API xác nhận contract và phạm vi token.
- [ ] Chủ dự án quyết định giữ phần chuẩn bị #109/#124 trong release này hay chuyển sang PR sau, ghi rõ trong PR.
- [ ] Dev và QA ghi kết quả, maintainer duyệt phần còn tồn đọng.
- [ ] Ops xác nhận môi trường, thời điểm cập nhật và phương án rollback.

Những tiêu chí này triển khai quy trình chủ dự án yêu cầu “dev kiểm tra → test → merge → VPS”. Mỗi issue chỉ đề nghị Closed khi phần được giao đã đạt tiêu chí riêng.
