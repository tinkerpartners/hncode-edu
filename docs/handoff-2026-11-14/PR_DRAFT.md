## Thay đổi và hành vi

Bản tích hợp `mrtee-hncode-v1.0.0-rc.4` sửa các luồng Grades/Excel, IDE/AJAX và chi phí truy vấn Profile, kèm API Cầy Cốt và khung AI/kiểm kê đang chờ điều kiện nghiệm thu. Base chuẩn bị `e665d7b` (main). Mã runtime cuối `b5da9b3`; commit test `f1c1e7b`; SHA chốt docs trong release manifest.

- Grades fingerprint theo từng dòng phát hiện thay đổi dù tổng điểm giữ nguyên; batch tiến độ nhiều khóa.
- STT/tổng điểm số, XLSX Course/Lesson/Contest theo quyền/bộ lọc; tỷ lệ % Excel đúng, nút export gọn và giữ hạng toàn tập live/virtual khi lọc.
- IDE có nháp theo user/problem/language/context, Ace sau AJAX, strict contest; điều hướng nhanh bằng số/trạng thái, Run/Submit mobile thấy kết quả, rail trái giữ trạng thái thu gọn, form/đề gọn và editor tối trên nền xanh sẫm.
- Điều hướng hủy jqXHR thật và bỏ response cũ; Ranking không chồng poll, dừng khi tab ẩn/rời trang, có empty/error/retry và giữ dữ liệu cũ khi lỗi.
- Cached Profile không tính default Language qua DB cho mỗi proxy; ngôn ngữ ưa thích đọc lazy đúng dữ liệu. Tạo tài khoản mới vẫn kiểm tra default FK theo DB.
- API Cầy Cốt: bốn GET endpoint, token hash/scope/quota/paging/che kết quả. AI Markdown và kiểm kê nguồn đề mới ở mức chuẩn bị, không coi đã nghiệm thu.

Refs #44, #135, #140, #39, #110, #138, #109, #124. Nhánh đã tích hợp nội dung #141/#142 và sửa tiếp; maintainer đối chiếu thứ tự merge với các PR đó.

## Kiểm thử và bằng chứng

- **1.374 Django tests PASS/111.365s trên checkout bàn giao**, dùng DB/cache/thư mục test riêng. Bộ cài local cũng **1.376 PASS/112.121s**.
- **12 JavaScript regression tests PASS**: abort/out-of-order, draft flush, stale completion, error/retry, hidden tab/poll guard, Unicode filter URL và dispose controller. Chạy: `node --test tests/js/async-navigation.test.cjs`.
- **23 cache/default language tests PASS**; mới kiểm tra batch cache lạnh và ngôn ngữ ưa thích khác default.
- Black 4 Python files, syntax 3 JS và `git diff --check` đạt.
- Browser evidence RC3 (runtime RC4 giữ nguyên): 390×844 Run Accepted, submission #86 AC100/100 ngay trong IDE, rail/rapid navigation, Ranking offline/retry/empty và XLSX lọc hạng 2.
- Benchmark RC3 real DB/cache local, 5 mẫu/28 route-vai trò: Grades Course 124→21 SQL, 196,20→132,56ms; Lesson 125→22 SQL, 207,51→119,70ms. Không phải benchmark production/tải đồng thời; không kết luận mọi trang nhanh hơn.

Chi tiết: `docs/handoff-2026-11-14/11_RC4_KIEM_TRA_HOI_QUY_VA_CHO_PUSH.md`; log `evidence/rc4/`, browser/performance `evidence/rc3/` dưới cùng thư mục.

## Phạm vi chưa hoàn tất và gate review

- [ ] Team review/QA trên đúng SHA sau cập nhật main/giải quyết conflict; không tự đóng các issue còn thiếu nghiệm thu.
- [ ] #140 mở Microsoft Excel; Reset Hủy/Xác nhận, mobile thật/keyboard ảo/screen reader và ma trận strict contest.
- [ ] #39/#135 đo tải mục tiêu/polling/UI staging; #138 client Cầy Cốt, contract/scope thật.
- [ ] #109 chờ API key; #124 chờ DB bài thật. Quyết định giữ phần chuẩn bị hay tách release, ghi scope cuối.
- [ ] #113/#112 chưa triển khai, không nằm trong phần nghiệm thu mới.
- [ ] Chốt CI: workflow hiện chỉ trigger master/lint, chưa có Django regression checks cho PR main. Các test trên là local, không phải GitHub checks đã pass.
- [ ] Maintainer chốt phạm vi/quan hệ #141/#142 và duyệt merge; Ops backup/rollback và staging trước VPS.

Không thêm migration. Dependency từ base: `openpyxl>=3.1.5,<4`, đã có từ các RC trước; RC4 không thêm dependency/runtime change. Local settings/Docker overrides và dữ liệu QA cá nhân không thuộc PR.
