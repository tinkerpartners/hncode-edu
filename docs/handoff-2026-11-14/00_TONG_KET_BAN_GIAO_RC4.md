# Tổng kết bàn giao HNCode v1.0.0-rc.4

Ngày 14/11/2026 · **Đã push và tạo [Draft PR #143](https://github.com/tinkerpartners/hncode-edu/pull/143)**.

- Nhánh GitHub: `mrtee-hncode-v1.0.0-rc.4`.
- Repo đích: `tinkerpartners/hncode-edu`, base `main` tại `e665d7b`; quyền ghi của `tungfint` đã xác minh.
- Mã runtime cuối: `b5da9b3` (RC3); commit test `f1c1e7b`; HEAD trước push đọc trong manifest; HEAD hiện tại xem tab Commits của PR, có thêm cập nhật tài liệu sau xuất bản.
- RC4 kế thừa toàn bộ backlog/UI/API/AI chuẩn bị và các sửa RC2/RC3; chỉ bổ sung test và hồ sơ phát hành, không đổi runtime/schema/dependency.
- **1.374 Django test PASS trên checkout bàn giao/111.365s**, đồng thời **1.376 PASS trên local/112.121s**; **12 JavaScript test PASS**, nhóm cache 23 PASS.
- Tốc độ local đã đo ở RC3: Grades Course 124→21 SQL, Lesson 125→22 SQL. Trải nghiệm Run/Submit mobile, cột bài, AJAX, Ranking error/retry và bàn phím đã sửa; không suy rộng benchmark local thành tải production.

Đọc [báo cáo kiểm tra hồi quy và kế hoạch PR](11_RC4_KIEM_TRA_HOI_QUY_VA_CHO_PUSH.md), [PR body](PR_DRAFT.md), [checklist QA](01_KIEM_THU_VA_NGHIEM_THU.md) và [hướng dẫn nhận nhánh](08_GUI_DEV_VA_QUAN_LY_NHANH.md).

Đã push và tạo Draft PR #143 theo lệnh chủ dự án. Chưa merge, đóng issue hay deploy VPS; team review/test trước merge và staging/VPS. Xem [biên bản bàn giao PR](12_DA_PUSH_VA_PR143.md). Mã source ở `D:\AI-Code\Build_HNCode\.local\worktrees\backlog-20261113`; bộ cài ở `D:\AI-Code\Build_HNCode`.

Các giới hạn cần giữ: Reset/Microsoft Excel/mobile thật còn nghiệm thu; CI hiện chỉ master/lint, chưa có Django checks cho PR main; #39/#135 cần tải mục tiêu, #138 cần client thật. #109 thiếu key, #124 thiếu DB bài thật; #113/#112 chưa triển khai. Các phần đó không được ghi là hoàn tất chỉ vì test đạt.

ZIP RC4 chứa bundle, patch, docs/evidence, manifest, lịch sử và checksum; không có local_settings, mật khẩu/token hay DB dump. Dữ liệu test local và submission #86 AC 100/100 được giữ để kiểm tra. Snapshot quyền/branch/QA chỉ phản ánh lúc chuẩn bị; PR thực tế: [#143](https://github.com/tinkerpartners/hncode-edu/pull/143). Gói ZIP RC4 là snapshot trước push; các commit sau đó chỉ cập nhật tài liệu xuất bản.
