# Evidence RC4 — kiểm tra trước push

- `review-checkout-full-suite.log`: **1.374 PASS/111.365s**, chạy trực tiếp từ checkout bàn giao, code/test `f1c1e7b`.
- `full-suite-final.log`: **1.376 PASS/112.121s**, chạy trên bộ cài local.
- `cache-regression.log`: 23 PASS/0,576s, cache/Profile/default Language.
- `javascript-regression.log`: 12 PASS bằng Node built-in test runner, callback đến sai thứ tự, abort, polling và lỗi mạng.
- `github-permissions.json`, `github-main.json`, `github-existing-pr.json`: snapshot quyền ghi/default branch/main và chưa có PR RC4. Chỉ là đọc API; không phải chứng cứ đã push.
- `runtime-parity.json`: 58 file runtime được đối chiếu, 56 giống; hai tùy chỉnh local có chủ đích được ghi riêng. Bản bàn giao đã được chạy full suite độc lập.
- `live-source-smoke.json`: HTTP/4 static assets và 19 file runtime RC3 gần nhất trùng. Không thay thế browser QA.
- `review-module-origins.json`, `local-data-after-tests.json`: nơi import module của checkout test và dữ liệu QA giữ sau kiểm tra, nếu có trong gói.

Mã runtime RC4 giống RC3. Evidence browser và benchmark thực tế kế thừa [RC3](../rc3/README.md); không ghi là vừa đo lại. Không có key/token/mật khẩu hoặc DB dump.
