# RC4 — Kiểm tra hồi quy và chuẩn bị PR theo lệnh chủ dự án

Ngày 14/11/2026 · Nhánh local `mrtee-hncode-v1.0.0-rc.4`.

**Snapshot tại lúc chốt kiểm thử: CHỜ LỆNH PUSH. Sau đó chủ dự án đã ra lệnh và nhánh đã push, Draft PR #143 đã được tạo. Trạng thái hiện tại xem [biên bản PR #143](12_DA_PUSH_VA_PR143.md); chưa merge/VPS.**

## 1. Đánh giá tốc độ và trải nghiệm

Các tối ưu RC3 đã áp dụng local: bỏ N+1 default Language của cached Profile; Grades Course 124→21 SQL và Lesson 125→22 SQL trên dataset đã ghi; Run/Submit mobile hiển thị kết quả, giữ cột bài thu gọn, hủy request cũ, Ranking empty/error/retry và nhãn/bàn phím. Xem [báo cáo RC3](10_RC3_UX_VA_HIEU_NANG.md).

RC4 bổ sung kiểm thử hồi quy và hồ sơ sẵn sàng review; **không thay đổi mã runtime so với RC3**, không thêm migration/dependency. Benchmark và browser evidence RC3 vẫn áp dụng cho mã runtime đó, không phải kết quả đo mới ở RC4.

Không có bằng chứng tối ưu làm sai chức năng trong phạm vi đã kiểm tra. Điều này không chứng minh toàn bộ hệ thống không còn lỗi: QA staging/thiết bị thật/tải mục tiêu và các mục chưa nghiệm thu vẫn phải được thực hiện trước production.

## 2. Kiểm soát hồi quy trên đúng mã bàn giao

| Kiểm tra | Kết quả | Bằng chứng |
|---|---|---|
| Django trên checkout review | **1.374 PASS/111.365s** | [log](evidence/rc4/review-checkout-full-suite.log) |
| Django trên bộ cài local | **1.376 PASS/112.121s** | [log](evidence/rc4/full-suite-final.log) |
| JavaScript request lifecycle | **12 PASS**, không dependency npm | [log](evidence/rc4/javascript-regression.log), `tests/js/async-navigation.test.cjs` |
| Cache/Profile/default Language | **23 PASS/0,576s** | [log](evidence/rc4/cache-regression.log) |
| Kiểm tra tĩnh | Black 4 Python files, node --check 3 JS, git diff --check đạt | Đã chạy trên checkout review |
| Local smoke | Trang chủ, Course list, Problem list HTTP 200; 4 static assets đúng source | [JSON](evidence/rc4/live-source-smoke.json) |
| So source review/local | 56/58 file runtime giống; 2 tùy chỉnh local ghi dưới | [JSON](evidence/rc4/runtime-parity.json) |

Hai test Django mới kiểm tra cache lạnh 5 Profile chỉ dùng 1 query và đúng thứ tự; ngôn ngữ ưa thích khác default vẫn được đọc đúng/lazy/cached. Các test hiện có tiếp tục kiểm tra tạo tài khoản/default FK, quyền xuất/điểm ẩn, Grades, API, IDE và strict contest.

12 test JavaScript chạy hàm thật với transport/DOM fake: lưu draft trước abort; response cũ không ghi đè/lái trang/xóa busy của request mới; lỗi hiện tại fallback; forced/hash navigation; tab ẩn không poll; không chồng poll; tìm kiếm mới hủy request cũ; timeout giữ bảng và retry; URL tiếng Việt; rời trang/controller cũ không ghi dữ liệu. Các test này kiểm tra logic, không thay thế browser QA.

Bộ cài local có hai tùy chỉnh ngoài nhánh: `dmoj/urls.py` khôi phục 2 route legacy và `judge/forms.py` cho đường tải file tới judge Docker. Vì vậy đã chạy full suite độc lập tại `/code/.local/worktrees/backlog-20261113`, cùng settings QA riêng được Git ignore. DB `test_hncode`, cache test-cache và thư mục test tách khỏi DB/dữ liệu của website. Hai test API legacy chỉ có ở bộ cài local giải thích chênh lệch 1.376 local và 1.374 checkout review; không bị bỏ sót test của nhánh. Không đưa local_settings.py vào bundle/PR.

## 3. GitHub và nội dung PR đã chuẩn bị

| Nội dung | Giá trị |
|---|---|
| Repository / remote origin | `https://github.com/tinkerpartners/hncode-edu.git` |
| Tài khoản được xác minh | `tungfint`, `permissions.push=true` |
| Base | `main`, protected; fetched SHA `e665d7b3642478fbeb102b45411ad56d59197a3f` |
| Nhánh chờ đẩy | `mrtee-hncode-v1.0.0-rc.4`, chỉ local tại lúc kiểm tra |
| Commit runtime cuối | `b5da9b373374d4e826de7b14ad58a7c71ad1c509` |
| Commit test cuối | `f1c1e7b9cca5fa6fbc629354f9cd0e413f23d5a2` |
| SHA chốt gói | Đọc `RELEASE_MANIFEST.json`; commit tài liệu nằm sau test |
| Kiểu PR | Draft → review/QA → Ready khi team chấp thuận |
| Tiêu đề chuẩn bị | `MrTee HNCode RC4: Grades/Excel, IDE và tối ưu Profile` |
| Mô tả PR | [PR_DRAFT.md](PR_DRAFT.md) |

PR #141/#142 vẫn OPEN tại lần kiểm tra; nhánh tích hợp nội dung và sửa tiếp theo, maintainer cần chốt quan hệ/thứ tự merge. Không dùng từ khóa tự đóng issue khi phần nghiệp vụ còn chưa nghiệm thu.

**Khi có lệnh push**, kiểm tra lại `git status`, fetch main, diff và SHA cuối; nếu main thay đổi có ảnh hưởng thì giải quyết/xác minh lại trước khi đẩy. Nếu nhánh remote đã tồn tại thì đối chiếu SHA; không force-push. Lệnh dưới chỉ là kế hoạch, chưa chạy:

```powershell
git push -u origin mrtee-hncode-v1.0.0-rc.4
gh pr create --repo tinkerpartners/hncode-edu --base main --head mrtee-hncode-v1.0.0-rc.4 --draft --title "MrTee HNCode RC4: Grades/Excel, IDE và tối ưu Profile" --body-file docs/handoff-2026-11-14/PR_DRAFT.md
```

## 4. Điều kiện nghiệm thu còn lại

| Việc | Kết luận hiện tại |
|---|---|
| CI của repository | `.github/workflows/init.yml` chỉ trigger `master` và lint, chưa có Django regression pipeline cho PR vào `main`. Test local không được gọi là GitHub checks đã PASS; team phải ghi kết quả trên SHA merge hoặc bổ sung CI phù hợp |
| Reset IDE | Chưa nghiệm thu đầy đủ Hủy/Xác nhận trong browser bình thường |
| Excel #140 | Đọc workbook/openpyxl đạt; Microsoft Excel manual còn chờ |
| Mobile/accessibility | Chưa test điện thoại thật/bàn phím ảo/screen reader; logo dark và thanh số dài còn review |
| #39/#135 | Benchmark local có kết quả; tải mục tiêu/polling/UI staging chưa nghiệm thu |
| #138 | API tests đạt; client Cầy Cốt/contract/scope còn nghiệm thu |
| #109 | Chưa có API key; provider thật chưa chạy |
| #124 | Chưa có ngân hàng bài thật trên DB |
| #113/#112 | Chưa triển khai |

Kết luận phát hành: **đủ hồ sơ để chờ lệnh push và mở Draft PR cho dev review; chưa đánh dấu production-ready hoặc Closed toàn bộ issue**. Bước đưa lên VPS chỉ sau maintainer duyệt merge, backup và kế hoạch rollback trong [quy trình 02](02_QUY_TRINH_GIT_MERGE_VPS.md).
