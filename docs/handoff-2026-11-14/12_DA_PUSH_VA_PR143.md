# Bàn giao dev lead — Draft PR #143

Ngày 14/11/2026. Chủ dự án đã ra lệnh push nhánh và tạo PR. Đã thực hiện đúng repo/nhánh; chưa merge, đóng issue hay triển khai VPS.

| Nội dung | Giá trị |
|---|---|
| Pull request | [#143 — MrTee HNCode RC4: Grades/Excel, IDE và tối ưu Profile](https://github.com/tinkerpartners/hncode-edu/pull/143) |
| Trạng thái | OPEN, Draft — chờ dev review/QA |
| Nhánh nguồn | `mrtee-hncode-v1.0.0-rc.4` trên `tinkerpartners/hncode-edu` |
| Nhánh đích | `main` |
| Base đã fetch trước push | `e665d7b3642478fbeb102b45411ad56d59197a3f` |
| HEAD ban đầu của PR | `a9634cc8cac1324a5a3f7c64bb98c97d9c108846` |
| Runtime cuối | `b5da9b3`; test cuối `f1c1e7b` |
| Commit sau tạo PR | Chỉ cập nhật tài liệu/link/trạng thái xuất bản; HEAD hiện tại xem Commits của PR |
| Xung đột lúc kiểm tra | GitHub trả MERGEABLE/CLEAN với main; không phải kết luận đã nghiệm thu |
| GitHub checks | Chưa có checks cho PR này; workflow hiện lọc master/lint, không coi là CI xanh |

## Phần dev lead cần xem

1. [Tổng kết RC4](00_TONG_KET_BAN_GIAO_RC4.md) và [QA/hồi quy](11_RC4_KIEM_TRA_HOI_QUY_VA_CHO_PUSH.md).
2. [Checklist nghiệm thu](01_KIEM_THU_VA_NGHIEM_THU.md), [UX/hiệu năng RC3](10_RC3_UX_VA_HIEU_NANG.md), [API Cầy Cốt](../CAY_COT_API.md), [kiểm kê bài](../PROBLEM_INVENTORY.md).
3. 1.374 Django tests PASS trên checkout bàn giao (111,365s), 12 JS regression tests PASS; local có thêm 2 test API legacy, tổng 1.376 PASS. Log nằm ở evidence/rc4. Các commit xuất bản sau mốc QA chỉ đổi tài liệu.
4. Đối chiếu PR #141/#142 vì bản tích hợp chứa nội dung và sửa tiếp theo; chốt scope, reviewer và thứ tự merge.
5. Nghiệm thu Reset, Microsoft Excel, thiết bị thật, tải mục tiêu/polling và client API. #109 thiếu key, #124 thiếu DB bài thật; #113/#112 chưa triển khai. Không ghi các phần này đã hoàn thành.
6. Ghi SHA/kết quả QA, duyệt mới chuyển Ready/merge; Ops backup, staging và rollback trước VPS.

Bộ cài/test data local vẫn ở ổ D, submission #86 AC100/100 được giữ. Source trên PR không có DB dump, local_settings, mật khẩu hoặc token. ZIP RC4 đã gửi trước đó là snapshot trước push; dùng PR làm nơi review hiện tại.

## Mẫu nhắn cho dev lead

> Nhờ anh/team review Draft PR #143: https://github.com/tinkerpartners/hncode-edu/pull/143 — nhánh mrtee-hncode-v1.0.0-rc.4 vào main. Đã có 1.374 Django test và 12 JS test PASS trên bản bàn giao; có báo cáo/evidence/checklist trong docs/handoff-2026-11-14. Nhờ kiểm thử các luồng Grades/Excel, IDE/AJAX/strict contest, quyền/API và đối chiếu #141/#142 trước khi merge. Reset/Excel/tải mục tiêu/client còn nghiệm thu; #109 thiếu key, #124 thiếu DB thật, #113/#112 chưa triển khai. Chưa deploy VPS.
